# Data layout

```
data/
  activations/
    activations_raw/          raw Copernicus downloads
    activations_reorganized/  standardized shapefiles (aoi/, flood_extent/)
  GEE_exports/
    {EMSR}/{folder_name}/     one folder per activation
      S1_VV_VH.tif                       2 bands  Sentinel-1 VV/VH
      S2_NDVI_NDBI.tif                   2 bands  NDVI + NDBI
      MERIT.tif                          4 bands  elevation, flow direction, UDA, HAND
      Soil.tif                           2 bands  clay + sand (ISRIC SoilGrids v2.0)
      ESA_WorldCover_PermanentWater.tif  1 band   permanent water mask (ESA WorldCover)
      Precipitation_{first}_{last}.tif   N bands  GPM-IMERG daily (N days pre-event)
      SoilMoisture_{first}_{last}.tif    N bands  SMAP daily (N days pre-event)
      flood_mask.tif                     1 band   rasterized CEMS flood extent
  patches/
    {EMSR}/{folder_name}/     2.56 km tiles, 5 GeoTIFFs per patch
      patch_NNNN_input_10m.tif      5 bands   256x256  S1 VV, S1 VH, NDVI, NDBI, permanent water
      patch_NNNN_input_80m.tif      5 bands   32x32    MERIT elev, flowdir sin/cos, UDA, HAND (rebuilt locally)
      patch_NNNN_input_160m.tif     2 bands   16x16    clay, sand
      patch_NNNN_input_2560m.tif    2N bands  1x1      precipitation (N days) then soil moisture (N days)
      patch_NNNN_flood_mask.tif     1 band    256x256  CEMS flood label
  metadata/
    1_activation_catalog.csv        activation catalog (Script 1)
    1_activation_status.csv         per-product download + reorganization status (Script 1)
    2_gee_export_status.csv         per-layer GEE export status (Script 2)
    2_composite_registry.csv        S1/S2 composite provenance: acquisition window, cloud threshold, image count (Script 2)
    3_dataset_metadata.csv          events new in the latest run (Script 3)
    released_events_metadata.csv    full accumulated dataset catalog, one row per event (Script 3)
    3_missing_layers_report.csv     missing enabled layers per activation (Script 3)
    released_patches_metadata.csv   one row per patch tile (Script 4; split added in Script 5)
    4_patch_validation_issues.csv   per-patch QC findings (Script 4)
    split_global/
      train_patches.csv             patch index filtered to the train split (Script 5)
      val_patches.csv               patch index filtered to the validation split (Script 5)
      test_patches.csv              patch index filtered to the test split (Script 5)
  plots/
    splits/                         split balance plots (Script 5)
```
