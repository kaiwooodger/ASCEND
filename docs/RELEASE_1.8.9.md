# ASCEND 1.8.9

Release identifier: `ASCEND-1.8.9-REGULAR-COARSE-GRID-CONTINUITY-20260925`

## Regular native RTDOSE grids

Layer 2.2 now continues calculation on a regular native RTDOSE grid when an axis spacing exceeds 2 mm. This replaces the previous `outside_validated_scope` stop. The calculation remains on native voxels without interpolation. Physical spacing continues to determine centroids, graph distances, midpoint-sphere membership, support volumes, and structure volumes.

The locked validation evidence remains limited to isotropic 1 mm and 2 mm grids. The existing bounded extension remains grids at or below 2 mm on every axis. A result above that extension is stored as `completed_with_warnings`, classified as `regular_native_grid_above_2mm_unvalidated`, and includes `rtdose_grid_above_2mm_outside_layer2_2_validation_evidence`.

## Downstream contract

Layer 3.1 remains dependent on current Layer 1 evidence and does not use Layer 2.2 as a biological-calculation prerequisite. When Layer 2.2 evidence is available, its grid classification and grid warnings are copied into the non-causal Layer 3.1 research-association record.

Layer 3.2 continues to require current completed Layer 1, Layer 2.2, and Layer 3.1 results. It accepts the coarse-grid Layer 2.2 result because the result is completed with warnings, and it carries the coarse-grid warning into its own stored warnings.

## Layer 3.1 memory correction

Repeated identical fractions are stored as one float32 dose-per-fraction field with an explicit multiplicity. P/Q and MLQ accumulation applies that multiplicity in bounded chunks and retains the established numerical results. Layer 3 services load verified masks without also decoding an unused full dose array. The stored fraction-history metadata retains the biological fraction count and separately records the number of stored dose fields.

For a 51,313,680-voxel grid, the previous implementation requested approximately 391.5 MiB for each float64 fraction copy. The compact representation uses one approximately 195.7 MiB float32 field for an identical-fraction group regardless of its fraction count.

## Scope

This release expands calculation availability. It does not expand clinical or scientific validation. Results from grids above 2 mm on any axis remain provisional research outputs outside the locked Layer 2.2 validation evidence.

## Verification

Targeted regular-grid, DICOM pipeline, Layer 2.2, Layer 3.1, Layer 3.2, workflow, interface, provenance, and release-identity suites passed. Ruff passed for the changed Python files. Source and wheel distributions built successfully as version 1.8.9. The unrestricted combined run reached the native PyVista offscreen screenshot test and the VTK process crashed in the restricted graphics environment; the same non-rendering Layer 3.2 suite passed with that native screenshot test excluded.
