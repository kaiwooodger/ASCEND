# ASCEND 1.5.0

Release identifier: `ASCEND-1.5.0-VERTICES-QA-FWHM-20260831`

Release date: 2026-08-31

## Release objective

ASCEND 1.5.0 adds a dedicated Layer 2.1 vertices QA workspace. It exposes stored per-vertex dose, volume, geometry, distance, and FWHM evidence without moving scientific calculations into the GUI or modifying the six locked physical metrics.

## Vertices layout

The third Layer 2.1 section is `Vertices layout`. Its interactive view follows the established Layer 2.2 graph presentation pattern:

- automatic, axial, sagittal, and coronal centroid projections;
- nearest-neighbour vertex connections with physical distance labels;
- wheel/button zoom, drag pan, rotation, and fit controls;
- a low-to-high blue–purple colour gradient encoded by local FWHM and marker size encoded by vertex volume;
- hover menus containing D95, V95 RxH, mean dose, maximum dose, volume, nearest-vertex distance, local FWHM, three native-axis FWHM widths, and QA warnings.

## FWHM definition

For each resolved vertex mask, ASCEND locates the maximum-dose voxel inside the vertex. It samples three profiles through that voxel along the native RTDOSE grid axes and linearly interpolates the two crossings at 50% of the local maximum. The three physical widths are stored separately. `local_fwhm_mm` is their arithmetic mean.

The `Global FWHM` tab reports the arithmetic average and median of valid local FWHM values, plus their observed minimum and maximum. These are descriptive QA values. They are not beam-model commissioning measurements, plan pass/fail criteria, or clinically calibrated endpoints.

## Provenance and compatibility

- Supporting-output schema: `ASCEND-Layer2.1-supporting-v4`.
- Vertex centroids are stored in DICOM patient LPS millimetres.
- Nearest distances are Euclidean distances between stored physical centroids.
- Historical Layer 2.1 records without the new fields remain displayable; unavailable values render explicitly.
- The hash-locked Layer 2.1 six-metric source and validated Layer 2.2 graph source are unchanged.

## Validation

Synthetic tests verify per-vertex centroids, interpolated local FWHM, nearest-neighbour distances, connection records, global mean/median aggregation, hover content, tab ordering, and ASCEND 1.5.0 release identity.

The Layer 1–3 whitespace audit and its verified corrections are recorded in `GUI_WHITESPACE_AUDIT_1.5.0.md`.
