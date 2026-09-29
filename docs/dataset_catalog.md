# Dataset catalog

The released catalog is `released_events_metadata.csv`, one row per event across the whole dataset. Its `folder_name` keys into the patches, GEE_exports, and activations_reorganized folders. An activation sometimes maps the same area on the same date more than once, as a delineation, graded, and flood-extent product. Only one of those events is kept, taking the delineation product first, then graded, then flood extent, and within one product type the version with the larger flooded fraction. A pipeline run does not rewrite it; Step 3 writes only the events new in that run to `3_dataset_metadata.csv` and appends them to the released catalog, so prior events and their assigned splits are preserved. Step 3 fills the continent, climate, aoi_area_km2, and flooded_area_km2 columns, and Step 5 fills the split column.

The columns are below.

| column | description |
|---|---|
| `folder_name` | event folder name |
| `basin_id` | HydroBASINS Pfafstetter Level-5 code(s) |
| `event_sensor` | sensor used for the flood delineation |
| `sensor_resolution_m` | resolution of that sensor (m) |
| `resolution_class` | medium, high, or very-high |
| `continent` | continent of the area of interest |
| `climate` | Köppen-Geiger main class |
| `split` | train, val, or test |
| `aoi_area_km2` | area of interest size (km²) |
| `flooded_area_km2` | area under water (km²) |
| `n_patches` | number of patches cut from the event |

## Patch index and splits

Every patch is listed in `released_patches_metadata.csv` in the metadata folder, one row per tile. The three split files in the split_global folder, `train_patches.csv`, `val_patches.csv`, and `test_patches.csv`, are the same table filtered by the `split` column, so each can be loaded directly as a training, validation, or test set. The split is exclusive by HydroBASINS Pfafstetter Level-5 basin and by whole event, so no basin and no event crosses the train, validation, and test sets. The released split contains 427,824 patches (75.5%) from 916 events in training, 68,763 (12.1%) from 307 events in validation, and 70,082 (12.4%) from 342 events in test.

A tile is addressed by `(emsr_code, folder_name, patch_number)`, which locate its files on disk, so the CSV references patches relationally rather than by absolute path. The key columns are below.

| Column | Description |
|---|---|
| `patch_index` | global running index over all patches |
| `emsr_code` | Copernicus activation code, e.g. `EMSR203` |
| `folder_name` | event the patch belongs to |
| `patch_number` | index of the tile within its event (the `NNNN` in the filenames) |
| `crs` | coordinate reference system of the tile (per-event UTM zone) |
| `bounds_minx/miny/maxx/maxy` | tile footprint bounds in `crs` units (m) |
| `flood_pixels` | number of flooded pixels in the tile, from the flood mask |
| `flood_fraction` | fraction of the tile that is flooded, 0-1 (`flood_pixels` / 65,536) |
| `basin_id` | HydroBASINS Pfafstetter Level-5 code(s) of the event |
| `continent` | continent of the event |
| `climate` | Köppen-Geiger main class of the event |
| `sensor_resolution_m` | resolution of the sensor used for the delineation (m) |
| `resolution_class` | medium, high, or very-high |
| `split` | train, val, or test |
