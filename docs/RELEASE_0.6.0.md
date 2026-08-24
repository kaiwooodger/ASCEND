# ASCEND 0.6.0

Release series: ASCEND 0.6.x  
Release baseline: Layer 3.1 conventional LQ workstation baseline  
Validation scope: Validated through Layer 3.1  
Release date: 2026-08-12 Australia/Sydney

## Baseline contents

- Production-robust UID-resolved Eclipse DICOM ingestion and identity-bound ROI configuration.
- Locked Layer 1, Layer 2.1, and Layer 2.2 scientific implementations.
- Conventional voxelwise Layer 3.1 BED/EQD2 using reusable P/Q biological-basis maps.
- Explicit fraction-dose history and ROI history with common validated-geometry enforcement.
- Identity-bound alpha/beta assignments, ROI endpoints, biological histograms, sensitivity sweeps, deterministic cache artifacts, and JSON/CSV/NPZ export.
- Native PySide6/Qt Layer 3.1 workstation page over the controller and scientific service.

## Verification

- Complete automated suite: 124 tests passed.
- Layer 1 locked source SHA-256: `dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490`.
- Layer 2.1 locked source SHA-256: `4ddfa7eef71118db8edb40eba7331c3ee70a07021cd5386caf6f5f7c00cb3621`.
- Layer 2.2 locked source SHA-256: `2a45da69f21428078ec227fb69e0175168f0528d39432bdc60a3724b313eeb24`.
- Existing PHPROLRT01 Layer 1–2.2 result, native-mask, and native-dose hashes remain unchanged.

## Scope boundary

Layer 3.1 is a conventional LQ-derived reference transformation. It is not TCP, NTCP, a complete model of Lattice Radiotherapy biology, or a clinical outcome predictor. Layer 3.2 remains unimplemented.
