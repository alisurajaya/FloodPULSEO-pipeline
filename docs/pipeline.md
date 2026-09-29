# Pipeline details

The seven geospatial layers are configurable in `scripts/config.py`. `LAYER_TOGGLES` enables or disables each layer, and `N_DAYS_OVERRIDE` sets the daily-series length N for the temporal layers (default 30). New GEE layers can be added by copying a template in `scripts/add_gee_layers.py`. The full-scene files keep their own names: `S1_VV_VH.tif`, `S2_NDVI_NDBI.tif`, `MERIT.tif`, `Soil.tif`, `ESA_WorldCover_PermanentWater.tif`, and the temporal layers carry their antecedent window in the filename, for example `Precipitation_20240714_20240812.tif`. `flood_mask.tif` is produced in Step 3 by rasterizing the CEMS delineation.

Each activation supplies two CEMS vector components: the AOI boundary (`aoi/aoi.shp`) and the flood extent (`flood_extent/event.shp`). Permanent water comes from ESA WorldCover, which covers every event.

Two files configure a run, and five numbered scripts execute it in order.

| File | What it does |
|---|---|
| `config.py` | **Edit first.** Enable or disable layers, set the daily-series length N, set the patch size |
| `add_gee_layers.py` | Layer registry. Copy a template here to add a custom GEE layer |
| **1** `_download_activations.py` | Download EMSR flood activations from Copernicus, reorganize into standardized folders. `--start` / `--end` set the date range |
| **2** `_submit_gee_tasks.py` | Download the enabled layers per activation straight into `data/GEE_exports/`. `--limit N` stops after N activations |
| **3** `_gee_output_preprocessing.py` | Rasterize flood masks and permanent water, add continent, climate and area columns, build the catalog |
| **4** `_make_patches.py` | Cut events into model-ready 2.56 km patch tiles |
| **5** `_make_splits.py` | Assign the basin- and event-exclusive train/val/test split |

Step 2 fetches each layer straight into `data/GEE_exports/` in tiled requests, so Step 3 can run as soon as Step 2 finishes. The download runs locally, so the machine stays busy for the length of the batch. Step 3 downloads HydroBASINS, a continents layer and a Köppen raster on its first run (see **Disk space** under [Setup](../README.md#setup)). Step 5 balances by patch count, so it runs after patching.

```bash
conda activate floodpulseo
python scripts/1_download_activations.py
python scripts/2_submit_gee_tasks.py
python scripts/3_gee_output_preprocessing.py
python scripts/4_make_patches.py
python scripts/5_make_splits.py
```

## Choosing what to build

Step 1 fetches every CEMS flood activation in a date range, which defaults to the
full record the release was built from, **2017-01-01 to 2025-12-31**. Narrow it with
`--start` / `--end` instead of editing the script:

```bash
python scripts/1_download_activations.py --start 2024-01-01 --end 2024-12-31
```

Step 2 is the long step: it downloads seven layers per activation, and a large AOI
can take several minutes per layer. `--limit N` stops after N activations so the
whole pipeline can be exercised before committing to a full batch.

## Try it first on a few events

Every step is resumable — already-downloaded layers and already-written patches are
skipped — so a trial run is not wasted work. Re-running Step 2 without `--limit`
later simply continues from where the trial stopped.

```bash
conda activate floodpulseo
python scripts/1_download_activations.py --start 2026-02-18 --end 2026-02-22 --yes
python scripts/2_submit_gee_tasks.py --limit 3
python scripts/3_gee_output_preprocessing.py
python scripts/4_make_patches.py
python scripts/5_make_splits.py
```

That takes roughly 30-60 minutes, most of it the one-off HydroBASINS download in
Step 3, and produces a complete miniature of the dataset: patches on disk, a
populated `released_events_metadata.csv`, and the three split files. Check
`data/metadata/4_patch_validation_issues.csv` afterwards; an empty file (header
only) means every patch passed its geometry and band checks.

A split computed over a handful of events will not be 70/15/15. The split is
exclusive by basin and by whole event, so with only a few events the constraints
leave nothing to balance and everything may land in `train`. That is expected on a
trial run, not a failure.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Step 2: `ee.Initialize()` fails | Earth Engine needs both a login and a registered Cloud project. See [Setup](../README.md#setup). |
| Step 3: `Could not load Koppen raster` / `Could not load continents` | The upstream download failed. The run continues and the catalog is still built, but the `climate` (or `continent`) column is left empty for every event; Step 5 balances the split per continent. Re-run Step 3 once the download succeeds and it fills the column in place. |
| Step 3: an event is missing from the catalog | An event missing a *core* layer is excluded by design and listed in `3_missing_layers_report.csv`. The usual cause is `S2_indices` marked `NA` in `2_gee_export_status.csv`, meaning Earth Engine held no Sentinel-2 scene for that AOI and window. |
| Step 4: `no GEE export, skipped` | Step 2 has not completed for that event, or Step 3 has not yet catalogued it. |
| Step 4: an event yields 0 patches | The AOI is smaller than one 2.56 km patch in some direction. Reported per event and safe to ignore. |
| A step stops partway | Re-run the same command. Steps 1, 2 and 4 resume from what is already on disk. |
