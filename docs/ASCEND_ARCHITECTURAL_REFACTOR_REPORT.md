# ASCEND architectural refactor report

## Baseline

- Git commit: `4a2353867764c3e1d6b98267a52e78ba7c63aaac`
- Complete suite: 229 tests passed; 28 subtests passed
- Formal validation: Layer 1 and Layer 3.1 named suites passed
- Ruff: passed
- Targeted mypy: passed
- Workflow and repository schemas: passed
- Actionlint: passed
- Repository boundary: failed before the refactor because a tracked evidence document contained a literal example matching the machine-home-path detector
- Hotspots: `ascend/gui/main_window.py` (3,397 lines), `ascend/gui/layer31_viewer.py` (2,122 lines), and the byte-locked `ascend/scientific/legacy/layer1_validated.py` (920 lines)
- CodeScene: the CodeScene CLI and project analysis service were unavailable offline. No Code Health score is claimed.

## Files changed

- `ascend/gui/main_window.py`
- `ascend/gui/workstation_widgets.py`
- `ascend/gui/layer3_presenters.py`
- `ascend/gui/layer31_viewer.py`
- `ascend/gui/layer31_viewer_models.py`
- `ascend/gui/layer31_field_adapter.py`
- `ascend/gui/layer31_cad_projector.py`
- `ascend/gui/layer31_slice_renderer.py`
- `ascend/gui/layer31_result_widgets.py`
- `ascend/gui/layer31_legacy_renderers.py`
- `ascend/layer1/service.py`
- `ascend/layer1/preparation.py`
- `ascend/layer1/execution.py`
- `validation/cross_platform/GITHUB_HOSTED_VALIDATION.md`
- `docs/ASCEND_ARCHITECTURAL_REFACTOR_REPORT.md`

## Architectural changes

### Main window

Before: `main_window.py` owned shell construction, reusable graph/table rendering, and large Layer 3.1/3.2 result-presentation branches.

After:

- `main_window.py` remains the application shell, navigation, case/session coordinator, service router, and compatibility API.
- `workstation_widgets.py` owns reusable graph rendering, table adaptation, and supporting-output flattening.
- `layer3_presenters.py` owns presentation of stored Layer 3.1 and Layer 3.2 branch results. It contains no biological calculation.
- File size decreased from 3,397 to 2,775 lines. The `MainWindow` class decreased from 2,893 to 2,604 lines.

### Layer 3.1 viewer

Before: `layer31_viewer.py` combined hash-verified artifact loading, display-field adaptation, CAD projection, slice painting, two legacy 3D backends, result widgets, and viewer orchestration.

After:

- `layer31_field_adapter.py` verifies and adapts authoritative upstream arrays.
- `layer31_viewer_models.py` owns display contracts, including immutable `CADProjectionOptions`.
- `layer31_cad_projector.py` owns display-only mesh projection. Its internal entry point accepts four coherent arguments; the former long signature remains only as a compatibility wrapper.
- `layer31_slice_renderer.py` owns 2D slice rendering.
- `layer31_result_widgets.py` owns regional result presentation.
- `layer31_legacy_renderers.py` isolates retained Qt raster/Qt3D compatibility backends.
- `layer31_viewer.py` remains the high-level interactive viewer.
- File size decreased from 2,122 to 1,035 lines.

The voxel evidence panel now reads the prepared `negative_log10_survival_MLQ` display field. It no longer reproduces the logarithmic transform inside the viewer class.

### Layer 1

`ascend/scientific/legacy/layer1_validated.py` is a formal byte-locked scientific snapshot. Editing or mechanically splitting it would change the source identity used by validation provenance. It remains 920 lines with SHA-256 `dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490`.

The active architecture around that snapshot was decomposed instead:

- `layer1/preparation.py` owns DICOM selection, ROI identity validation, strict geometry preparation, source hashes, and cache-input identity in an immutable `PreparedLayer1Inputs` contract.
- `layer1/execution.py` owns the normalized RTDOSE and identity-filtered RTSTRUCT execution boundary for the locked validator.
- `layer1/service.py` retains high-level cache, provenance, validation-result decoration, atomic publication, and workflow routing.
- Active service size decreased from 414 to 287 lines; its `run` method decreased from 189 to 128 lines.

This preserves one authoritative Layer 1 scientific implementation and retains existing geometry, rasterization, cache, result, and provenance contracts.

## Duplicate calculations found

- The Layer 3.1 voxel evidence panel recomputed negative log10 survival from stored MLQ survival. It now consumes the prepared display field from the field adapter.
- No duplicate BED, EQD2, MLQ, TCP, clonogen, voxel-volume, mask-generation, dose-resampling, or coordinate-transform implementation was introduced.
- Layer 1 continues to use the established DICOM geometry, ROI selection, incremental rasterization, artifact, and cache modules.

## Verification

- Complete suite: 229 tests passed; 28 subtests passed
- Layer 3.1 plus TCP formal subset: 64 tests passed
- Layer 1 formal/geometry/cache subset: 28 tests passed; 19 subtests passed
- Layer 1 downstream Layers 2/3 subset: 87 tests passed; 28 subtests passed
- Synthetic anisotropic validation: PASS
- Synthetic treatment-context validation: PASS
- Ruff: PASS
- Targeted mypy: PASS
- Python compilation: PASS
- Workflow/schema validation: PASS
- Actionlint: PASS
- Repository boundary for the scoped refactor: PASS after rewording the self-triggering documentation literal (`233 tracked files inspected`)
- Current shared-worktree boundary: FAIL after a concurrent task staged `validation/linux/` evidence. The audit rejects its synthetic STL and a credential-pattern match in `bandit.json`; this refactor did not alter or remove that staged evidence.

## Numerical equivalence

No scientifically meaningful numerical differences were observed. All locked source-integrity, DICOM geometry, anisotropic-grid, cache-equivalence, Layer 2, Layer 3.1, TCP, viewer spatial, CAD, workflow, export, and end-to-end tests retain their existing tolerances and pass.

CodeScene before/after scores are not reported because CodeScene was unavailable offline. Measured file and method reductions are reported instead.
