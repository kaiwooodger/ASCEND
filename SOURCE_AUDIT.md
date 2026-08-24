# Validated source selection audit

Audit date: 2026-08-09 Australia/Sydney.

## Layer 1

Selected source filename: `LRT_Layer_1.py` (original local path withheld from the public repository).

- Declared version: `0.3.3-layer1-volume-gated`.
- Source modified: 2026-08-08 12:01:54.
- SHA-256: `dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490`.
- Evidence: later than `ASCEND-LRT/ascend_lrt_layer1.py` and all pre-CT-raster/gap-safe versions; two saved real-case runs identify version 0.3.3 and retain an open native-dose mask gate.
- Preserved snapshot: `ascend/scientific/legacy/layer1_validated.py`.

This version contains the current identity/link validation, DoseGridScaling reconstruction, physical geometry, CT-grid half-open XOR/even-odd rasterisation, gap-aware contour handling, nearest-neighbour CT-to-native-RTDOSE transfer, contour-stack/CT/dose-sampled volumes, TPS comparison, mask hashing, and volume-representation gate.

## Layer 2.1

Selected source filename: `Layer_2_1_ASCEND_Locked.py` (original local path withheld from the public repository).

- Declared version: `ASCEND-Layer2.1-locked-v1.0`.
- Schema: `ASCEND-Layer2.1-locked-schema-v1`.
- Source modified: 2026-08-07 08:29:00.
- SHA-256: `4ddfa7eef71118db8edb40eba7331c3ee70a07021cd5386caf6f5f7c00cb3621`.
- Evidence: later than `Layer_2_1_LRT_Metrics.py`; explicitly locks the six required metrics, high-to-low ratio direction, applicability states, prescription provenance, anatomical/dose-sampled fraction, protocol-native endpoints, and per-vertex QA.
- Preserved snapshot: `ascend/scientific/legacy/layer21_validated.py`.

The earlier 100-variation technical robustness result passed, but it targets the earlier Layer 2.1 script. The locked module's own deterministic primitive suite is therefore the regression authority used here.

## Layer 2.2

Selected source filename: `ascend_lrt_layer2_ipvdr.py` (original local path withheld from the public repository).

- Declared version: `ASCEND-LRT-1.0.0-layer2.2B`.
- Source modified: 2026-08-03 13:00:11.
- SHA-256: `2a45da69f21428078ec227fb69e0175168f0528d39432bdc60a3724b313eeb24`.
- Configuration SHA-256: `a1282cdd571d7d214a48039d95c425a91ba17df9cb83fac07c8f4384a8cdcd4e`.
- Evidence: later integrated revision of `Layer_2_2B_iPVDR.py`; consumes Layer 1 native masks, validates 1 mm and 2 mm isotropic grids, converts the validated 0.056 cc support rule into a grid-specific voxel threshold, and retains tied nearest neighbours, edge midpoints, native sampling, endpoint D50 peaks, and graph summaries.
- Preserved snapshot: `ascend/scientific/legacy/layer22_validated.py`.

The earlier frozen `Layer_2_2B_iPVDR.py` and configuration are retained as `layer22_reference_validated.py` and `Layer_2_2B_reference_frozen_config.json` because their nine exact synthetic ground-truth tests are the available independent regression evidence for the shared scientific definition.

## Preservation mechanism

The selected files are byte-identical copies. `tests/test_source_integrity.py` locks their hashes. GUI, controller, state, provenance, invalidation, and reporting code are separate adapters. No selected source file was edited.
