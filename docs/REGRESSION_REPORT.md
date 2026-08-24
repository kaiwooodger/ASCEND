# Regression and end-to-end validation report

> Sections dated before 20 August 2026 are historical records. The current Layer 3.1 implementation and its dependent Layer 3.2 calculation have been removed. The final blank-canvas regression update at the end of this document supersedes earlier biological-workflow statements.

Run date: 2026-08-11 Australia/Sydney.

## Automated suite

Command:

```bash
cd /path/to/ASCEND_PROJECT
/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 -m unittest discover -v
```

Result after adding the formal Eclipse DVH comparison harness and isolated volume-discrepancy diagnostics to the ASCEND 0.5.0 baseline: 100 tests passed.

The 13 volume-diagnostic tests cover three-volume records, aggregate/union agreement and disagreement, component overlap, symmetric difference, Dice, absent component ROIs, contour-plane grouping, repeated positions, Eclipse source precision, overlap/containment, controlled classification serialization, and preservation of formal failure status.

Coverage includes UID-resolved chain selection and overrides, ROI identity migration and inventory separation, strict relative/absolute/descending RTDOSE geometry, non-uniform rejection, anisotropic scope handling, selective rasterisation, cache corruption and immutability, deterministic artifacts, atomic publication, cache-versus-uncached equivalence, physical dose scaling, six-metric and graph primitives, both vertex-provenance paths, source integrity, Layer 2.1/2.2 handoff execution, serialization, exports, browser assets, the native Qt workflow shell, canonical Eclipse reference import, ROI-identity matching, endpoint semantics, agreement boundaries, exclusions, summary statistics, Bland–Altman data, and validation CLI/controller integration.

The PySide6 workstation was rendered on macOS and in the off-screen Qt test platform. All ten pages, the persistent workflow navigation, compact status header, status indicators, Layer 2.1 metric cards and expandable outputs, ROI binding table, warning banners, Layer 2.2 graph controls and graph surface, responsive scrolling, and the functional Layer 3.1 BED/EQD2 page were present. GUI presentation was verified not to mutate stored scientific result records or contain Layer 3.1 numerical calculations. The optional localhost workstation remains available as a secondary adapter.

## Real DICOM end-to-end run

Input alias: `PHPROLRT01 representative Eclipse export`.

Command:

```bash
python3 -m ascend.cli run /path/to/PHPROLRT01-export \
  --case-root /path/to/new-ascend-case \
  --config configs/ascend_case_config.example.json
```

Result:

- Layer 1: `WARN`; Layer 2 eligible; native-dose mask gate `PASS`.
- Layer 2.1: `completed_with_warnings`; all six metrics calculated with valid applicability.
- Layer 2.2: `completed_with_warnings`; 8 nodes, 5 valid edges, 0 excluded edges.
- JSON, configuration JSON, summary CSV, Layer 2.1 metrics/per-vertex CSVs, and Layer 2.2 node/edge CSVs generated.

## Numerical equivalence

Layer 1 was compared with the preserved local validation baseline `layer1_PHPROLRT01_20260808_121740`:

- Full native dose array: bitwise identical.
- Every selected native structure mask: bitwise identical to the same mask from the locked all-ROI calculation.
- Selected-structure DVH rows: identical.
- Selected contour-stack, CT-voxel, and dose-sampled volume definitions: identical.
- Numerical differences: none.
- Intentionally unselected RTSTRUCT ROIs are inventory-only `not_rasterised` records and no longer produce calculated rows.

Layer 2.1 is invoked through the selected locked module's `load_handoff()` and `analyse()` functions. Adapter output adds run/provenance fields after calculation. Locked metric, supporting, applicability, and per-vertex records are unchanged.

Layer 2.2 was compared with the selected integrated source running against the same RTDOSE and hash-verified Layer 1 masks through a compatibility manifest:

- Plan iPVDR summary: exactly equal.
- Node records: exactly equal.
- Scientific edge fields: exactly equal.
- Numerical differences: none.

ASCEND stores the full-precision validated physical dose during Layer 1 for Layer 2.2. The existing float32 NPZ dose remains unchanged for byte-compatible locked Layer 2.1 behavior. This removes downstream DICOM reopening while retaining both validated numerical contracts.

Cache-hit and uncached formal runs produce identical native float64 dose hashes, identical selected-mask arrays, identical deterministic NPZ hashes, and identical canonical Layer 1 scientific payloads after excluding run/cache publication metadata.

## Performance regression gate

The representative Eclipse profile uses three uncached and five cache-hit subprocess measurements. The machine-readable result is [benchmark_representative_eclipse.json](benchmark_representative_eclipse.json). Baseline and execution details are in [PERFORMANCE_BASELINE.md](PERFORMANCE_BASELINE.md).

## Formal Eclipse DVH software-agreement harness

The available PHPROLRT01 Eclipse cumulative-DVH text exports produced 36 in-scope reference rows across nine structures. The current selective Layer 1 run stored corresponding endpoints for four structures, giving 13 valid numerical comparisons and 23 explicit `missing_ascend_endpoint` exclusions. Eleven valid comparisons passed and two structure-volume comparisons failed. All valid D2, D95, and Dmean comparisons passed.

The two preserved failures were:

- `all_vertices` volume: ASCEND 12.77655 cc versus Eclipse 12.0 cc; absolute difference 0.77655 cc; limit 0.24 cc.
- `all_valleys` volume: ASCEND 4.7555 cc versus Eclipse 4.3 cc; absolute difference 0.4555 cc; limit 0.1 cc.

The Eclipse TXT evidence did not supply RTDOSE or RTSTRUCT/ROI-number identities. All matches therefore used uniquely proven name or configured-role fallbacks and remain explicitly flagged. D5, D50, D90, D98, V95%Rx, and V100%Rx were unavailable in the supplied Eclipse reference. The harness did not calculate or invent them.

## Additional explicit-vertex DICOM fixture

Input: synthetic non-clinical fixture `synthetic_lrt_5v5_layer22b_case`.

Result:

- Layer 1: `WARN`; Layer 2 eligible.
- Layer 2.1: `completed_with_warnings`; absent prescriptions remain null and invalid where required.
- Layer 2.2: `completed`; five explicit RTSTRUCT vertices, four valid edges, zero excluded edges, one connected component.
- Vertex provenance: `explicit_rtstruct_vertices`.
- Plan graph iPVDR median: `2.1741505895692144`.

The related earlier 5V5 visualisation fixture was also exercised and correctly blocked Layer 2.2 because its vertices were not contained by the configured GTV mask. It is not a valid fixture for the frozen edge-valley sampling definition.

## Real-case warnings

Layer 1 retained the original findings: unapproved RTPLAN; Lung_L and HighDensityCTV CT/dose-sampling volume warnings; TPS agreement not assessed because no TPS DVH CSV was supplied.

Layer 2.1 retained `manual_prescription_input` and all inherited Layer 1 warnings. Interpretation is provisional.

Layer 2.2 retained `individual_vertices_unavailable_components_used` and `graph_disconnected`. Interpretation is provisional and research-only.
# ASCEND 1.3.2 blank-canvas regression update

Complete surviving suite on 20 August 2026: `139 passed in 3.04s`.

Layer 3.1 and its dependent Layer 3.2 scientific tests were removed with the implementations they exercised. Replacement tests verify that Layer 3.1 is visibly blank, legacy configuration is discarded, stored biological results are not loaded or exported, and removed operations fail closed. Locked Layer 1/2.1/2.2 source-integrity and regression tests remain unchanged.

# ASCEND 1.4.1 fraction-event Layer 3.1 regression update

Pre-implementation blank baseline on 20 August 2026: `139 passed in 3.30s`.

Final complete suite with `QT_QPA_PLATFORM=offscreen`: `175 passed in 4.96s`.

Added verification covers closed-form and mixed-fraction spatial LQ transforms, integrated cross-terms, sequential events, fraction-history and registration gates, identity-bound tissue parameters, non-switching high-dose warning masks, stable Guerrero–Li G(x), effect-space accumulation, EUD inversion, regional survivor closure, C1–C3 and N1–N3 separation, TR applicability, deterministic field archives, DICOM-LPS scalar sampling, display-only smoothing invariance, CAD/STL/VTP artifacts, Qt workspace construction, browser/CLI routes, and Layer 3.2 consumption of the current Layer 3.1 basis.

Locked Layer 1/2.1/2.2 source-integrity and scientific regression tests remain passing. No upstream scientific result was changed to satisfy Layer 3.1.
