#!/usr/bin/env python3
"""
Step 1 of 2 - download the MERIT Hydro layer for each FloodPULSEO event.

FloodPULSEO does not redistribute the MERIT Hydro derived layers. MERIT Hydro is
released under CC BY-NC 4.0 / ODbL 1.0, and its author (Dai Yamazaki) confirmed
that resampling, cropping and re-encoding it does not make the result an
independent product. So the `input_80m` patches are not in the published dataset;
this tool rebuilds them locally from MERIT Hydro obtained under its own licence.

This script fetches one MERIT.tif per event, on the event's own AOI footprint.
Step 2 cuts it into patches. The split is deliberate: the network cost is one
request per EVENT (1,565 total), not one per patch (566,669 would be abusive to
the Earth Engine API).

Source
  ee.Image("MERIT/Hydro/v1_0_1"), bands elv, dir, upa, hnd.
  Licence: CC BY-NC 4.0 / ODbL 1.0 - Yamazaki et al. (2019).
  You obtain it here under those terms; it is NOT FloodPULSEO data.

Input
  merit_event_grid.csv   one row per event (shipped beside this script). It holds
                         only the geometry needed to re-cut MERIT: the patch grid
                         (crs, origin_minx, origin_maxy, grid_rows, grid_cols),
                         the AOI the layer was originally cut to (aoi_maxx,
                         aoi_miny), and - for the 30 events exported in
                         EPSG:4326 - that export's own footprint
                         (merit_export_crs, export_minx/maxy/cols/rows).
                         These are coordinates of FloodPULSEO's own tiling, not
                         MERIT data, so the file carries no MERIT content.

Output
  merit_raw/{folder_name}/MERIT.tif   4 bands (elv, dir, upa, hnd), 90 m native

Usage
  python 1_download_merit.py --out merit_raw
  python 1_download_merit.py --out merit_raw --only EMSR429_AOI09_DEL_MONIT01_20200304

Re-running skips events already downloaded, so an interrupted run resumes and a
failed one retries only what failed.
"""

import argparse
import csv
import math
import sys
import time
from pathlib import Path

# The four MERIT Hydro bands used, in the order Step 2 expects them.
MERIT_ASSET = "MERIT/Hydro/v1_0_1"
MERIT_BANDS = ["elv", "dir", "upa", "hnd"]

# MERIT Hydro is 3 arcsec (~90 m). The patch grid is 80 m, so the download is
# requested at native resolution and Step 2 does the reprojection - resampling
# twice would blur the elevation and corrupt the discrete D8 direction codes.
MERIT_NATIVE_M = 90.0
# The same native resolution expressed in degrees, for events whose MERIT was
# exported in EPSG:4326 (3 arcsec = 1/1200 deg, as the original exports used).
MERIT_NATIVE_DEG = 0.0008108108108108108

# NOTE: the footprint below is the ORIGINAL AOI, which is slightly larger than
# the patch grid. This matters and must not be "simplified" to the patch extent.
# The patch grid floors the AOI to whole 2560 m cells, leaving a remainder of
# 20-2540 m on the right and bottom (measured over 60 events; the left and top
# edges are always flush). The released input_80m was reprojected from a MERIT
# export covering that full AOI, so the cubic kernel along the last row and
# column of patches saw source pixels beyond the patch grid. Cutting the download
# at the patch grid instead starves those kernels and changes the edge values of
# every border patch - measured as the sole cause of a 13% patch mismatch, which
# became an exact match (max diff 0.0) once the true AOI extent was restored.

# One event's AOI can exceed getDownloadURL's response limit. Requests that come
# back too large are split into tiles and merged. The tiles overlap INSIDE the
# AOI so the mosaic has no seam; this is interior overlap only and never extends
# the footprint past the AOI, so it does not disturb the edge behaviour above.
MAX_TILE_PX = 4000
TILE_OVERLAP_M = 4 * MERIT_NATIVE_M


def log(msg: str) -> None:
    print(msg, flush=True)


def read_events(csv_path: Path, only: list) -> list:
    """Event rows that carry a usable AOI grid, optionally filtered by name."""
    required = ("crs", "origin_minx", "origin_maxy", "grid_rows", "grid_cols")
    # aoi_maxx / aoi_miny are optional; see event_bounds() for the fallback.
    # merit_export_crs is optional; see fetch_merit() for why it matters. When
    # it is set, export_minx/maxy/cols/rows must be set too.
    rows = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(
                f"ERROR: {csv_path} has no {', '.join(missing)} column(s).\n"
                "This must be merit_event_grid.csv, shipped with this script."
            )
        for row in reader:
            if only and row["folder_name"] not in only:
                continue
            if not row.get("crs") or not row.get("origin_minx"):
                log(f"  {row['folder_name']}: no grid recorded, skipped")
                continue
            if row.get("merit_export_crs") and not row.get("export_minx"):
                log(f"  {row['folder_name']}: merit_export_crs is set but the "
                    "export footprint columns are missing, skipped")
                continue
            rows.append(row)
    return rows


def event_bounds(row: dict):
    """
    AOI extent in the event's own projected CRS.

    Anchored at the top-left corner (origin_minx, origin_maxy). The right and
    bottom edges come from aoi_maxx / aoi_miny, the original AOI, so that the
    resampling along the last row and column of patches has the same source
    pixels the released dataset was built from.

    Those two columns are only absent for an event whose AOI was never recorded;
    the fallback is the patch grid itself, which reproduces every interior patch
    exactly and leaves only the outermost ring slightly different.
    """
    patch_m = 2560.0
    minx = float(row["origin_minx"])
    maxy = float(row["origin_maxy"])
    grid_maxx = minx + int(row["grid_cols"]) * patch_m
    grid_miny = maxy - int(row["grid_rows"]) * patch_m
    maxx = float(row["aoi_maxx"]) if row.get("aoi_maxx") else grid_maxx
    miny = float(row["aoi_miny"]) if row.get("aoi_miny") else grid_miny
    # Never let a bad value shrink the footprint below the patch grid.
    return minx, min(miny, grid_miny), max(maxx, grid_maxx), maxy


def fetch_merit(ee, row: dict, dest: Path) -> str:
    """
    Download MERIT Hydro over one event's AOI. Returns a short status string.

    The image is pulled in the event's own CRS at MERIT's native resolution, so
    the only resampling is the one Step 2 performs onto the 80 m patch grid.
    """
    import rasterio
    from rasterio.merge import merge

    crs = row["crs"]
    minx, miny, maxx, maxy = event_bounds(row)

    # The download is written in the CRS the event was originally exported in,
    # which is usually the event's own UTM zone but is EPSG:4326 for the 30
    # events of EMSR851 and EMSR853. Step 2 reprojects to UTM either way, and
    # resampling from a geographic grid does not give the same numbers as
    # resampling from a projected one - so downloading those events in UTM
    # instead reproduces the geometry but not the values.
    export_crs = row.get("merit_export_crs") or crs
    scale = MERIT_NATIVE_DEG if export_crs == "EPSG:4326" else MERIT_NATIVE_M

    # The rectangle is stated in the export CRS so Earth Engine puts the pixel
    # grid where the original export had it. A geographic footprint is then
    # snapped outwards onto MERIT's own global 3-arcsec lattice (origin -180/90)
    # and requested by explicit `dimensions`: Earth Engine reads `scale` in
    # metres only, and asking for `dimensions` over an unsnapped rectangle
    # stretches the pixels to fill it (0.00081436 deg instead of 0.00081081).
    dims = None
    if export_crs != crs:
        # These events carry their exported footprint verbatim (export_minx and
        # friends). It is not recomputed: the original export snapped its corner
        # onto MERIT's 3-arcsec lattice by a rule that round-to-nearest
        # reproduces for only 21 of the 30, and a corner one cell out changes
        # every cubic-resampled value along that edge.
        minx = float(row["export_minx"])
        maxy = float(row["export_maxy"])
        n_x = int(row["export_cols"])
        n_y = int(row["export_rows"])
        maxx, miny = minx + n_x * scale, maxy - n_y * scale
        dims = [n_x, n_y]

    region = ee.Geometry.Rectangle(
        [minx, miny, maxx, maxy],
        proj=ee.Projection(export_crs), geodesic=False, evenOdd=True,
    )

    # unmask(0), NOT unmask(-9999). MERIT masks the sea, and the released export
    # filled those pixels with 0 - so a coastal event's released input_80m has
    # genuine 0.0 over water, not nodata. Unmasking to -9999 instead makes every
    # sea patch come out all-nodata and disagree with the release (measured on
    # Brisighella: 35 of 539 patches, entirely along the Adriatic coast).
    image = ee.Image(MERIT_ASSET).select(MERIT_BANDS).toFloat().unmask(0)

    # Pixel counts decide whether the request must be split. A geographic export
    # already knows its own size; a projected one is measured in metres.
    if dims is not None:
        width_px, height_px = dims
    else:
        width_px = int((maxx - minx) / MERIT_NATIVE_M) + 1
        height_px = int((maxy - miny) / MERIT_NATIVE_M) + 1

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Small AOIs download in one request; large ones are tiled and merged.
    if width_px <= MAX_TILE_PX and height_px <= MAX_TILE_PX:
        _download_region(ee, image, region, export_crs, scale, dest, dims)
        return f"1 request, {width_px}x{height_px} px"

    n_x = (width_px + MAX_TILE_PX - 1) // MAX_TILE_PX
    n_y = (height_px + MAX_TILE_PX - 1) // MAX_TILE_PX
    step_x = (maxx - minx) / n_x
    step_y = (maxy - miny) / n_y
    tiles = []
    tmp_dir = dest.parent / "_tiles"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for iy in range(n_y):
        for ix in range(n_x):
            tx0 = minx + ix * step_x
            ty1 = maxy - iy * step_y
            # Overlap the neighbouring tile, then clamp back to the AOI so the
            # mosaic edge stays exactly where an untiled download would put it.
            sub_minx = max(minx, tx0 - TILE_OVERLAP_M)
            sub_maxy = min(maxy, ty1 + TILE_OVERLAP_M)
            sub = ee.Geometry.Rectangle(
                [sub_minx,
                 max(miny, ty1 - step_y - TILE_OVERLAP_M),
                 min(maxx, tx0 + step_x + TILE_OVERLAP_M),
                 sub_maxy],
                proj=ee.Projection(crs), geodesic=False, evenOdd=True,
            )
            tile_path = tmp_dir / f"tile_{iy}_{ix}.tif"
            _download_region(ee, image, sub, export_crs, scale, tile_path)
            tiles.append(tile_path)

    srcs = [rasterio.open(t) for t in tiles]
    mosaic, transform = merge(srcs)
    profile = srcs[0].profile.copy()
    profile.update(height=mosaic.shape[1], width=mosaic.shape[2],
                   transform=transform, compress="lzw")
    with rasterio.open(dest, "w", **profile) as dst:
        dst.write(mosaic)
    for s in srcs:
        s.close()
    for t in tiles:
        t.unlink()
    tmp_dir.rmdir()
    return f"{n_x * n_y} tiles, {width_px}x{height_px} px"


def _download_region(ee, image, region, crs, scale, dest: Path, dims=None) -> None:
    """Pull one rectangle as a GeoTIFF, retrying transient Earth Engine errors."""
    import requests

    params = {
        "region": region, "crs": crs,
        "format": "GEO_TIFF", "filePerBand": False,
    }
    # `dimensions` pins the pixel count for a grid-snapped geographic region;
    # everything else is sized by `scale`, which Earth Engine reads as metres.
    if dims is not None:
        params["dimensions"] = dims
    else:
        params["scale"] = scale
    last = None
    for attempt in range(5):
        try:
            url = image.getDownloadURL(params)
            resp = requests.get(url, timeout=600)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return
        except Exception as exc:                      # noqa: BLE001 - EE raises broadly
            last = exc
            # Rate limits and transient 5xx are worth backing off on; a bad
            # region or a missing asset will simply fail again and surface.
            time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed after 5 attempts: {last}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", type=Path,
                    default=Path(__file__).with_name("merit_event_grid.csv"),
                    help="merit_event_grid.csv (default: beside this script)")
    ap.add_argument("--out", required=True, type=Path,
                    help="output directory for merit_raw/{event}/MERIT.tif")
    ap.add_argument("--only", nargs="*", default=[],
                    help="restrict to these folder_name values")
    ap.add_argument("--project", default=None,
                    help="Earth Engine cloud project, if your account needs one")
    args = ap.parse_args()

    try:
        import ee
    except ImportError:
        sys.exit("ERROR: earthengine-api is not installed (pip install earthengine-api)")

    try:
        ee.Initialize(project=args.project) if args.project else ee.Initialize()
    except Exception as exc:                          # noqa: BLE001
        sys.exit(f"ERROR: could not initialise Earth Engine ({exc}).\n"
                 "Run `earthengine authenticate` first.")

    events = read_events(args.events, args.only)
    log(f"{len(events)} event(s) to fetch -> {args.out}")

    done = failed = skipped = 0
    for i, row in enumerate(events, 1):
        name = row["folder_name"]
        dest = args.out / name / "MERIT.tif"
        if dest.exists() and dest.stat().st_size > 0:
            log(f"[{i}/{len(events)}] {name}  -- already downloaded, skipped")
            skipped += 1
            continue
        t0 = time.time()
        try:
            how = fetch_merit(ee, row, dest)
            log(f"[{i}/{len(events)}] {name}  OK  ({how}, {time.time() - t0:.1f}s)")
            done += 1
        except Exception as exc:                      # noqa: BLE001
            log(f"[{i}/{len(events)}] {name}  FAILED: {exc}")
            failed += 1

    log(f"\ndownloaded {done}, skipped {skipped}, failed {failed}")
    if failed:
        log("Re-run the same command to retry only the failures.")
        sys.exit(1)


if __name__ == "__main__":
    main()
