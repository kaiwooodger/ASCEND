# ASCEND 0.5.0

Release series: ASCEND 0.5.x  
Release baseline: Production-robust DICOM + Qt workstation baseline  
Validation scope: Validated through Layer 2.2  
Release date: 2026-08-11 Australia/Sydney

## Baseline contents

- UID-resolved Eclipse RTDOSE, RTPLAN, RTSTRUCT, and planning-image ingestion.
- Identity-bound ROI configuration, selective rasterisation, complete ROI inventory, strict RTDOSE geometry validation, immutable case-local caching, and atomic Layer 1 publication.
- Locked Layer 1, Layer 2.1, and Layer 2.2 scientific implementations.
- Native PySide6/Qt workstation with persistent workflow navigation, explicit calculation and interpretation states, Layer 2.1 metric and supporting-output views, and Layer 2.2 graph and 3D dose/mask views.
- CLI and optional localhost browser adapters over the same controller and case model.

## Verification

- Complete automated suite after the Eclipse DVH validation harness and volume-diagnostic addition: 100 tests passed.
- Layer 1 locked source SHA-256: `dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490`.
- Layer 2.1 locked source SHA-256: `4ddfa7eef71118db8edb40eba7331c3ee70a07021cd5386caf6f5f7c00cb3621`.
- Layer 2.2 locked source SHA-256: `2a45da69f21428078ec227fb69e0175168f0528d39432bdc60a3724b313eeb24`.

## Scope boundary

Layer 3.1 now provides conventional LQ-derived voxelwise BED/EQD2 from validated physical dose, explicit fraction history, ROI history, and identity-bound alpha/beta parameters. Layer 3.2 remains not implemented. This is research and technical-validation software and is not a clinical-use release.
