# MERIT Hydro layers are not redistributed

`input_80m.tif` is not in the published dataset. MERIT Hydro is released under CC BY-NC 4.0 / ODbL 1.0. Resampling and cropping it produces a derivative rather than an independent product, so the derived patches stay under MERIT's licence and cannot be redistributed under the CC BY 4.0 that covers the rest of FloodPULSEO.

`MERIT_data_prep/` rebuilds them from MERIT Hydro:

```bash
python MERIT_data_prep/1_download_merit.py --out merit_raw
python MERIT_data_prep/2_make_merit_patches.py --merit merit_raw --patches patches
```

Step 1 fetches one `MERIT.tif` per event on that event's own AOI footprint, so the cost is one Earth Engine request per event (1,565) rather than one per patch. Re-running skips events already downloaded, so an interrupted run resumes. Step 2 cuts each one to the patch grid and writes `patch_NNNN_input_80m.tif` beside the other patch files; `--verify` checks existing patches without writing.

The grid geometry comes from `merit_event_grid.csv`, shipped beside the scripts, one row per event. It holds only FloodPULSEO's own tiling coordinates, so it carries no MERIT content. Because the patches are cut on that same grid, the rebuilt files reproduce the ones the paper describes; `--verify` re-checks existing patches against it without writing.

You obtain MERIT Hydro under its own licence. It is not FloodPULSEO data.
