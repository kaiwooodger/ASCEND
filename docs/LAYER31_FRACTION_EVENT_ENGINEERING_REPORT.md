# ASCEND Layer 3.1 fraction-event engineering report

## 1. Branch / implementation identifier

`ASCEND-1.4.1-L3.1-FRACTION-EVENT-RADIOBIOLOGY-20260820`. The supplied workspace is not a Git checkout, so this is an implementation identifier rather than a branch name.

## 2. Files added

- `ascend/layer3/history.py`
- `ascend/layer3/lq/spatial.py`
- `ascend/layer3/response/course.py`
- `ascend/layer3/visualization.py`
- `ascend/gui/layer31_viewer.py`
- `tests/test_layer31_fraction_event_upgrade.py`
- `benchmarks/layer31_fraction_event_benchmark.py`
- `docs/LAYER31_FRACTION_EVENT_ENGINEERING_REPORT.md`
- `docs/RELEASE_1.4.1.md`

## 3. Files modified

- `ascend/models/config.py`
- `ascend/treatment/models.py`
- `ascend/layer3/lq/service.py`
- `ascend/layer3/response/mlq.py`
- `ascend/layer3/nonlocal_effect/service.py`
- `ascend/gui/main_window.py`
- `tests/test_layer31_response.py`
- `tests/test_qt_gui.py`
- `README.md`
- `docs/LAYER31_LQ_BED_EQD2.md`
- `ascend/models/config.py`
- `ascend/models/case.py`
- `ascend/app/controller.py`
- `ascend/cli.py`
- `ascend/reporting/export.py`
- `ascend/web/server.py`
- `ascend/web/static/index.html`
- `ascend/web/static/app.js`

## 4. Architecture implemented

The formal run path is:

`validated Layer 1 dose/masks → TreatmentContext → shared FractionHistory → 3.1A P/Q fields | 3.1B MLQ course effect | 3.1C comparator audit → stored results → Qt viewer/export`.

Each `FractionEvent` stores temporal order, biological fraction index, contributing components, dose and plan identities, geometry reference, delivery-time provenance, and a deterministic field hash. Integrated components are summed physically inside each fraction before the quadratic LQ term or MLQ effect is evaluated. Sequential components remain separate events. No implicit registration, resampling, dose warping, schedule inference, or ROI-name parameter inference is performed.

## 5. Layer 3.1A status

Implemented and computationally tested, not clinically validated. The service stores authoritative `float32` physical dose, maximum fraction dose, spatial BED, spatial EQD2, and LQ high-dose warning-mask arrays in a deterministic NPZ artifact. Alpha/beta parameters remain ROI-identity-bound. Tumour reporting masks cannot silently use inconsistent tumour alpha/beta values. OAR parameter assignments may remain tissue-specific. ROI reductions occur after voxel transformation. The high-dose warning does not switch voxels to MLQ.

## 6. Layer 3.1B status

Implemented and computationally tested, not clinically validated. Guerrero–Li effect is accumulated in effect space for every fraction event. Mean tumour survival uses log-sum-exp over the full validated GTV. The survival-equivalent EUD is inverted with a bounded Brent solver under an explicit reference schedule. The voxel survival and course-effect fields are stored. Regional H, V, and other-GTV survival and survivor-contribution fractions are stored. C1–C3 are standardised sensitivity scenarios, not patient-specific estimates.

## 7. Layer 3.1C status

Implemented and computationally tested, not clinically validated. The theoretical modelled therapeutic ratio is calculated only when the tumour EUD and comparator schedule are defined. Sequential mixed courses without a configured reference schedule return `NOT_APPLICABLE`. A C1–C3 × N1–N3 matrix is stored when both explicit kinetic parameter bases are available. The output is not NTCP, toxicity, clinical benefit, or a treatment-quality pass/fail score.

## 8. Gate matrix and tested failure modes

| Gate | PASS condition | Tested failure state |
|---|---|---|
| GATE_0_UPSTREAM_DATA | Hash-verified component dose, plan/dose identifiers and validated geometry exist | Invalid/missing upstream identity returns BLOCKED |
| GATE_1_FRACTION_HISTORY | Explicit fraction sources or declared repeated-identical total resolve to events | Unknown model or invalid count returns `BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED` |
| GATE_2_SPATIAL_REGISTRATION | All components have the same validated geometry hash | Modified origin returns `BIOLOGICAL_SPATIAL_ACCUMULATION_UNRESOLVED` |
| GATE_3_TISSUE_PARAMETERS | Required explicit parameters validate | Missing optional MLQ set is NOT_ASSESSED; conflicting tumour alpha/beta blocks 3.1A |
| GATE_4_DELIVERY_TIME | Event or explicit parameter-set delivery time exists | Invalid delivery time blocks 3.1B |
| GATE_5_EUD_INVERSION | Root is bracketed and residual meets tolerance | Invalid/non-converged root blocks EUD |
| GATE_6_TR_REFERENCE_SCHEDULE | Comparator fraction count and delivery times are explicit or matched | Sequential mixed course without comparator is NOT_APPLICABLE |
| Display gate | Mesh passes finite/triangle/normal/bounds/scalar-coverage checks | Mesh failure marks 3D unavailable without invalidating biological results |

## 9. GUI components implemented

- Layer 3.1 overview with gate, treatment-history, and model tables.
- 3.1A stored-map selector, tissue/ROI selector, fixed whole-field colour scales, axial/sagittal/coronal views, structure and warning overlays, and a 3D CAD tab.
- C1/C2/C3 and N1/N2/N3 selectors.
- 3.1B result table for survival, EUD, solver evidence, and warnings.
- 3.1C result table and nine-cell sensitivity-scenario matrix.
- Ordered five-tab workspace: Overview/gates, 3.1A, 3.1B, 3.1C, and Provenance/validation.
- Structured identity-bound tissue, MLQ kinetic, warning-threshold, scenario, and comparator controls; no JSON entry.
- Expandable provenance section with fraction history and scenario matrix.
- Background worker execution for initial field loading and mesh generation. Stale worker results are discarded by generation identifier.
- Explicit PASS/WARN/BLOCKED/NOT_APPLICABLE rendering through existing workstation state controls.

No radiobiological calculation was added to GUI code.

## 10. CAD/STL/mesh pipeline implemented

Validated mask → marching-cubes raw surface → degenerate-triangle removal and vertex compaction → deterministic Taubin display smoothing → vertex normals → DICOM-LPS-aware trilinear scalar sampling → per-vertex colour rendering. Raw and display surfaces remain separate. STL is geometry-only. VTP and PLY retain vertex scalar/RGB data; GLB and 3MF retain display colour. Mesh arrays, metadata, raw STL, display STL, and scalar-aware VTP are independently hashed.

## 11. Smoothing method and proof that it is display-only

Default smoothing is deterministic Taubin smoothing with 12 iterations, lambda 0.25, and mu -0.27. The authoritative field hash is recorded before and after mesh processing and must remain identical. The regression test runs different smoothing iteration counts, confirms different display vertices, confirms identical raw vertices, and confirms an unchanged authoritative scientific-field hash. No scientific service accepts a mesh as an input.

## 12. Scientific validation results

Automated tests pass for identical-fraction closed-form BED/EQD2, mixed repeated fractions, integrated cross terms, sequential accumulation, voxelwise-versus-mean-dose ordering, tissue-parameter separation, warning-mask behaviour, unresolved history, registration failure, stable `G(0)=1`, non-reciprocal G regression, repeated-fraction MLQ/EUD, regional-contribution closure, nine C/N scenario combinations, sequential TR non-applicability, and extreme finite MLQ effect.

Published GRID/LATTICE numerical reproduction was not claimed because a complete published input dataset and reconstruction contract were not present in this workspace.

## 13. GUI/visualisation validation results

Off-screen Qt construction and workflow tests pass. Geometry round-trip, DICOM LPS scalar sampling, fixed source-field hash, raw/display separation, mesh smoothing invariance, scalar archive hash rejection, and CAD export creation pass. The viewer loads only the field artifact whose hash matches the formal Layer 3.1 result.

## 14. Upstream regression results

Blank-canvas baseline before this implementation: 139 tests passed. Final complete suite: 175 tests passed in 4.96 seconds on 2026-08-20 with `QT_QPA_PLATFORM=offscreen`. Locked Layer 1/2 scientific regression tests remain passing. Layer 3.2 consumes the same authoritative fraction-event P/Q grouping as the current stored Layer 3.1 result and remains scientifically separate.

## 15. Numerical tolerances used

- EUD root absolute tolerance: `1e-10 Gy`.
- Brent relative tolerance: `1e-14`.
- Maximum EUD iterations: 200.
- Solver bracket expansion limit: 40 doublings.
- Unity-ratio display snap: `1e-10`, with unsnapped ratio retained.
- MLQ small-z series threshold: `1e-4`.
- Degenerate mesh twice-area threshold: `1e-10 mm²`.
- Geometry identity: deterministic exact hash of validated geometry payload; upstream DICOM tolerance handling remains Layer 1 policy.
- Scalar sampling: deterministic first-order trilinear interpolation; out-of-domain vertices receive explicit invalid status.

## 16. Performance measurements

The reproducible synthetic 21×21×21 profile contains 9,261 voxels. On the final direct 2026-08-20 macOS run, one preparation run took 0.02605 s and five warm runs had a 0.03721 s median. Process peak RSS was 78,479,360 bytes as reported by macOS. An isolated PHPROLRT01 329×205×525 real-grid smoke run completed one 3.1A GTV field in 17.73 s. These are implementation measurements, not representative multi-case clinical benchmarks. Large-export peak-memory and GPU rendering benchmarks remain required.

## 17. Known limitations

- Research implementation only; no clinical calibration or clinical validation.
- Classic validated Layer 1 geometry only; no implicit rigid/deformable registration or dose warping.
- No repopulation, TCP, NTCP, toxicity, immune, vascular, or bystander process in Layer 3.1.
- Delivery-time and kinetic data must be explicit.
- STL cannot store biological scalar values; use VTP/PLY or the stored voxel archive.
- Smoothing can place a surface vertex outside the voxel-centre field domain; this is reported through scalar-coverage QC rather than extrapolated silently.
- The selected C/N scenarios are sensitivity cases, not patient radiosensitivity estimates.

## 18. Remaining scientific-validation requirements

- Independent published GRID and LATTICE numerical reproductions using complete source inputs.
- Differential-DVH versus direct-voxel MLQ convergence test with a documented bin-width study.
- Independent implementation cross-check of the Guerrero–Li course equations and parameter conventions.
- Representative and very-large Eclipse case runtime/peak-memory benchmarks.
- Prospective expert review of fraction-event classification for integrated and sequential clinical exports.
- Formal clinical model calibration, uncertainty analysis, and external validation before any clinical interpretation.

## 19. Final commit identifier

Unavailable. The supplied project directory contains no `.git` repository. File-level implementation identifier: `ASCEND-1.4.1-L3.1-FRACTION-EVENT-RADIOBIOLOGY-20260820`.
