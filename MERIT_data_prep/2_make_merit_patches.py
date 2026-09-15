#!/usr/bin/env python3
"""
Step 2 of 2 - cut the downloaded MERIT Hydro into FloodPULSEO `input_80m` patches.

Reads the MERIT.tif that Step 1 fetched per event and writes one
patch_NNNN_input_80m.tif beside every patch_NNNN_input_10m.tif the dataset
already gave you. Runs entirely offline: no Earth Engine, no network.

Which patches to write is taken from the input_10m files themselves, not from a
list shipped here. That is what keeps the two in step - the released patch set is
not a full rows x cols tiling (100 of the 1,565 events dropped cells that were
mostly nodata at 10 m), so mirroring the files on disk reproduces exactly the
released set, including those gaps, with no extra metadata to drift out of date.

Each patch is 32x32 at 80 m, 5 bands, float32, nodata 9999:

  1  Elevation        MERIT elv          cubic
  2  FlowDir sin      MERIT dir -> angle nearest
  3  FlowDir cos      MERIT dir -> angle nearest
  4  UDA              MERIT upa          cubic
  5  HAND             MERIT hnd          cubic

Flow direction is stored as the sine and cosine of the D8 compass angle so that
the discontinuity between codes 128 and 1 (315 deg and 0 deg) does not appear to
the model as a large numeric jump. The geometry of each patch is copied from its
input_10m partner, so the two grids share an origin exactly.

Input
  merit_raw/{folder_name}/MERIT.tif                     from Step 1
  patches/{EMSR}/{folder_name}/patch_NNNN_input_10m.tif from the FloodPULSEO release

Output
  patches/{EMSR}/{folder_name}/patch_NNNN_input_80m.tif

Usage
  python 2_make_merit_patches.py --merit merit_raw --patches patches
  python 2_make_merit_patches.py ... --only EMSR429_AOI09_DEL_MONIT01_20200304
  python 2_make_merit_patches.py ... --verify      # check, write nothing
"""

import argparse
import math
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.coords import BoundingBox
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds

# Patch geometry. The 10 m patch is 256 px over 2560 m, so the 80 m patch is 32.
PATCH_SIZE_M = 2560.0
RES_10M = 10.0
RES_80M = 80.0
PX_80M = int(PATCH_SIZE_M / RES_80M)          # 32
# The released patches use POSITIVE 9999 as nodata (config.PATCH_NODATA in the
# generating pipeline), and tag it in the GeoTIFF. Do not "correct" this to the
# more usual -9999: the value is compared directly by downstream loaders.
NODATA = 9999.0

# D8 flow direction code -> compass angle, degrees clockwise from East.
# MERIT Hydro encodes direction as powers of two going clockwise from East.
D8_ANGLE = {1: 0, 2: 45, 4: 90, 8: 135, 16: 180, 32: 225, 64: 270, 128: 315}

PATCH_RE = re.compile(r"patch_(\d+)_input_10m\.tif$")


def log(msg: str) -> None:
    print(msg, flush=True)


def flowdir_sin_cos(codes: np.ndarray):
    """
    D8 codes -> (sin, cos) of the flow angle.

    Cells whose value is not one of the eight D8 codes (ocean, no-data, or the
    flat/pit codes MERIT uses at basin mouths) stay at NODATA in both bands, so
    they are never mistaken for a real direction pointing East.
    """
    sin_a = np.full(codes.shape, NODATA, dtype=np.float32)
    cos_a = np.full(codes.shape, NODATA, dtype=np.float32)
    for code, angle in D8_ANGLE.items():
        m = codes == code
        if m.any():
            sin_a[m] = math.sin(math.radians(angle))
            cos_a[m] = math.cos(math.radians(angle))
    return sin_a, cos_a


def build_event_stack(merit_path: Path, crs, transform, width, height):
    """
    Reproject one event's MERIT.tif onto its 80 m grid as a (5, H, W) array.

    Done once per event rather than once per patch: the patches of an event share
    a grid, so resampling the whole footprint once and slicing it is both faster
    and free of edge effects at the patch borders.
    """
    stack = np.full((5, height, width), NODATA, dtype=np.float32)
    with rasterio.open(merit_path) as src:
        if src.count < 4:
            raise ValueError(f"{merit_path} has {src.count} bands, expected 4 "
                             "(elv, dir, upa, hnd)")

        # src_nodata is whatever the download actually tags (normally none: the
        # export fills masked sea with 0, a real value). Passing NODATA as
        # src_nodata instead would mask every genuine 9999 in the source.
        src_nd = src.nodata

        def warp(source, slot, resampling):
            reproject(
                source=source, destination=stack[slot],
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=transform, dst_crs=crs,
                resampling=resampling, src_nodata=src_nd, dst_nodata=NODATA,
            )

        # Continuous fields: cubic, matching how the released stacks were built.
        warp(src.read(1).astype(np.float32), 0, Resampling.cubic)   # elevation
        warp(src.read(3).astype(np.float32), 3, Resampling.cubic)   # UDA
        warp(src.read(4).astype(np.float32), 4, Resampling.cubic)   # HAND

        # Direction is converted to sin/cos BEFORE reprojection, and resampled
        # nearest: interpolating the raw powers-of-two codes would invent
        # directions that do not exist (e.g. averaging 1 and 4 gives 2.5).
        # These two are built here with NODATA already in them, so unlike the
        # bands above they carry NODATA as their own source nodata value.
        sin_a, cos_a = flowdir_sin_cos(src.read(2).astype(np.float32))
        for arr, slot in ((sin_a, 1), (cos_a, 2)):
            reproject(
                source=arr, destination=stack[slot],
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=transform, dst_crs=crs,
                resampling=Resampling.nearest,
                src_nodata=NODATA, dst_nodata=NODATA,
            )

    return stack


def event_grid_from_patches(patch_files):
    """
    Derive the event's 80 m grid from the geometry of its input_10m patches.

    Anchoring on the patches themselves (rather than recomputing an AOI origin)
    guarantees the 80 m output lands on the same ground as the 10 m input, which
    is the property the released dataset depends on.
    """
    crs = None
    minx = maxy = None
    maxx = miny = None
    for path in patch_files:
        with rasterio.open(path) as src:
            if crs is None:
                crs = src.crs
            elif src.crs != crs:
                raise ValueError(f"mixed CRS within one event at {path}")
            b = src.bounds
            minx = b.left if minx is None else min(minx, b.left)
            maxy = b.top if maxy is None else max(maxy, b.top)
            maxx = b.right if maxx is None else max(maxx, b.right)
            miny = b.bottom if miny is None else min(miny, b.bottom)

    width = int(round((maxx - minx) / RES_80M))
    height = int(round((maxy - miny) / RES_80M))
    transform = Affine(RES_80M, 0.0, minx, 0.0, -RES_80M, maxy)
    return crs, transform, width, height, (minx, maxy)


def process_event(merit_path: Path, event_dir: Path, verify: bool) -> tuple:
    """Write (or check) every input_80m patch for one event. Returns counts."""
    patch_files = sorted(p for p in event_dir.iterdir() if PATCH_RE.search(p.name))
    if not patch_files:
        return 0, 0, "no input_10m patches found"

    crs, transform, width, height, (ox, oy) = event_grid_from_patches(patch_files)

    # The MERIT download must cover the patch footprint. A download that was
    # truncated (interrupted Step 1, or an AOI grid that disagrees with the
    # patches) would otherwise reproject to silent nodata rather than failing.
    with rasterio.open(merit_path) as src:
        mb = src.bounds
        src_crs = src.crs
    # The download may be in a different CRS from the patches - 30 events were
    # exported in EPSG:4326 - so compare footprints in the patch CRS.
    if src_crs != crs:
        mb = BoundingBox(*transform_bounds(src_crs, crs, *mb))
    tol = RES_80M
    if (mb.left > ox + tol or mb.top < oy - tol
            or mb.right < ox + width * RES_80M - tol
            or mb.bottom > oy - height * RES_80M + tol):
        raise ValueError(f"{merit_path} does not cover the patch footprint; "
                         "delete it and re-run Step 1 for this event")

    stack = build_event_stack(merit_path, crs, transform, width, height)

    written = problems = 0
    for path in patch_files:
        idx = int(PATCH_RE.search(path.name).group(1))
        with rasterio.open(path) as src:
            b10 = src.bounds
        # Offset of this patch into the event grid, in 80 m cells.
        col = int(round((b10.left - ox) / RES_80M))
        row = int(round((oy - b10.top) / RES_80M))
        if col < 0 or row < 0 or col + PX_80M > width or row + PX_80M > height:
            problems += 1
            log(f"    patch {idx:04d}: outside the event grid, skipped")
            continue

        tile = stack[:, row:row + PX_80M, col:col + PX_80M]
        if tile.shape[1:] != (PX_80M, PX_80M):
            problems += 1
            log(f"    patch {idx:04d}: window {tile.shape[1:]} is short of "
                f"{PX_80M}x{PX_80M}, skipped")
            continue
        out_path = event_dir / f"patch_{idx:04d}_input_80m.tif"

        if verify:
            if out_path.exists():
                written += 1
            else:
                problems += 1
                log(f"    patch {idx:04d}: input_80m missing")
            continue

        profile = {
            "driver": "GTiff", "height": PX_80M, "width": PX_80M, "count": 5,
            "dtype": "float32", "crs": crs,
            "transform": transform * Affine.translation(col, row),
            "compress": "lzw", "nodata": NODATA,
        }
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(tile.astype(np.float32))
            dst.descriptions = ("Elevation", "FlowDir_sin", "FlowDir_cos",
                                "UDA", "HAND")
        written += 1

    return written, problems, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merit", required=True, type=Path,
                    help="merit_raw directory written by Step 1")
    ap.add_argument("--patches", required=True, type=Path,
                    help="FloodPULSEO patches root ({EMSR}/{event}/patch_*.tif)")
    ap.add_argument("--only", nargs="*", default=[],
                    help="restrict to these folder_name values")
    ap.add_argument("--verify", action="store_true",
                    help="report what is missing without writing anything")
    args = ap.parse_args()

    events = sorted(d for d in args.merit.iterdir() if d.is_dir())
    if args.only:
        events = [d for d in events if d.name in args.only]
    if not events:
        sys.exit(f"ERROR: no event directories under {args.merit}")

    total_written = total_problems = 0
    for i, merit_dir in enumerate(events, 1):
        name = merit_dir.name
        merit_path = merit_dir / "MERIT.tif"
        if not merit_path.exists():
            log(f"[{i}/{len(events)}] {name}  -- no MERIT.tif, run Step 1 first")
            total_problems += 1
            continue

        # The release nests patches one level under the EMSR activation code.
        event_dir = args.patches / name.split("_")[0] / name
        if not event_dir.is_dir():
            candidates = list(args.patches.glob(f"*/{name}"))
            if not candidates:
                log(f"[{i}/{len(events)}] {name}  -- no patch directory, skipped")
                total_problems += 1
                continue
            event_dir = candidates[0]

        try:
            written, problems, err = process_event(merit_path, event_dir, args.verify)
        except Exception as exc:                      # noqa: BLE001
            log(f"[{i}/{len(events)}] {name}  FAILED: {exc}")
            total_problems += 1
            continue

        if err:
            log(f"[{i}/{len(events)}] {name}  -- {err}")
            total_problems += 1
            continue
        verb = "checked" if args.verify else "wrote"
        log(f"[{i}/{len(events)}] {name}  {verb} {written} patch(es)"
            + (f", {problems} problem(s)" if problems else ""))
        total_written += written
        total_problems += problems

    log(f"\n{'checked' if args.verify else 'wrote'} {total_written} patch(es), "
        f"{total_problems} problem(s)")
    if total_problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
