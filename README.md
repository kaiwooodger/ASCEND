# ASCEND 1.3.5

Production-robust DICOM, physical LRT, and fraction-resolved research-radiobiology workstation  
Validated physical workflow through Layer 2.2; Layer 3.1 is computationally verified and not clinically validated

ASCEND is a modular LRT analysis engine with a native PySide6/Qt workstation, an optional localhost browser adapter, and a CLI. Layer 3.1 uses one gated fraction-event history to feed parallel spatial LQ BED/EQD2, Guerrero–Li tumour survival/EUD, and therapeutic-ratio branches. Its outputs are research quantities, not TCP, NTCP, toxicity, or clinical recommendations.

Release record: [docs/RELEASE_1.3.5.md](docs/RELEASE_1.3.5.md).

Retrospective freeze controls: [validation/validation_protocol.md](validation/validation_protocol.md) and [docs/GITHUB_REPOSITORY_SETTINGS.md](docs/GITHUB_REPOSITORY_SETTINGS.md).

## Repository safety

Clinical DICOM, ASCEND case directories, validated outputs, caches, exported arrays, meshes, logs, screenshots, and crash reports are runtime data and are excluded from source control. Only synthetic non-clinical fixtures may be committed. Read [SECURITY.md](SECURITY.md) before publishing or sharing a repository clone.

## Launch

Dependencies: Python 3.9 or later, NumPy, pydicom, and PySide6/Qt 6.

```bash
cd /path/to/ASCEND_PROJECT
python3 run_ascend.py
```

The launcher opens the native Qt workstation. It does not start a network listener. PySide6 has been installed for the current macOS Python environment.

Optional isolated installation:

```bash
cd /path/to/ASCEND_PROJECT
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
ascend gui
```

Optional localhost browser adapter:

```bash
cd /path/to/ASCEND_PROJECT
python3 -m ascend.cli web-gui
```

Formal Eclipse DVH software-agreement validation:

```bash
cd /path/to/ASCEND_PROJECT
python3 -m ascend.cli \
  validate-eclipse-dvh \
  --case /path/to/ascend_case.json \
  --reference /path/to/eclipse-reference.csv \
  --output /path/to/validation/eclipse_dvh/results \
  --criteria configs/eclipse_dvh_acceptance_v1.json
```

The comparison harness reads stored ASCEND results and never recalculates endpoints. Reference format, identity matching, agreement criteria, statuses, and outputs are documented in [docs/ECLIPSE_DVH_VALIDATION.md](docs/ECLIPSE_DVH_VALIDATION.md).

Diagnostic investigation of preserved `all_vertices` and `all_valleys` volume failures:

```bash
cd /path/to/ASCEND_PROJECT
python3 -m ascend.cli \
  diagnose-eclipse-volumes \
  --case runs/all/ascend_case.json
```

This command writes analysis-only geometry diagnostics under `validation/eclipse_dvh/volume_diagnostics`. It does not change formal comparison statuses, acceptance limits, or locked scientific algorithms.

## GUI workflow

1. Open `Import`, select the directory containing RTPLAN, RTSTRUCT, RTDOSE, and the complete referenced CT series, then press `Import case`.
2. Open `Case configuration`. Select treatment approach, dose context, and structured treatment components. Enter only documented prescriptions, fractionation, dates, and gaps. Prescription is never inferred from maximum dose.
3. Open `Structure-role mapping`. Confirm GTV, T_L, VTV_H, VTV_L, and optional individual vertices. Save mappings.
4. Open `Layer 1 validation` and press `Validate case`. Review every finding and the Layer 2 eligibility gate.
5. Open `Layer 2.1 LRT metrics` and run the locked six-metric engine, or use `Run physical analysis` to coordinate Layers 2.1 and 2.2.
6. Open `Layer 2.2 Spatial PVDR`. Inspect the graph result, then build the hash-verified 3D masks/dose viewer. Select a connection to inspect its local iPVDR, 3 mm midpoint sphere, and synchronized axial, sagittal, and coronal native-dose planes.
7. Open `Layer 3.1 Radiobiology`. Review gates, assign identity-bound tissue alpha/beta values, configure the optional C1–C3/N1–N3 kinetic bases and comparator, then run the complete gated workflow. Inspect 3.1A, 3.1B, 3.1C, and provenance in order.
8. Build the Layer 3.1 field viewer for linked axial/sagittal/coronal views and display-only CAD/STL-compatible surfaces. Enable Layer 3.2 explicitly only when the optional non-local research model is required; it is excluded from calculation and export while disabled.
9. Review statuses, warnings, applicability, interpretation, graph summary, and provenance. A disconnected graph remains a warning requiring geometric inspection.
10. Open `Export` and generate authoritative JSON, CSV derivatives, and the stored Layer 3.2 field archive.

Older ASCEND cases remain readable. Current Layer 3.1 results retain explicit fraction history, tissue parameters, gates, source identities, array hashes, and display-only mesh provenance.

## CLI workflow

```bash
cd /path/to/ASCEND_PROJECT
python3 -m ascend.cli run /path/to/dicom_case \
  --case-root /path/to/new_ascend_case \
  --config configs/ascend_case_config.example.json
```

Resume an existing case:

```bash
cd /path/to/ASCEND_PROJECT
python3 -m ascend.cli resume /path/to/ascend_case.json --layer physical
```

The Qt workstation, optional browser adapter, and CLI call the same controller and services. Tkinter is no longer used by the application GUI; references inside the frozen legacy Layer 1 snapshot remain untouched to preserve its source hash.

## Regression testing

```bash
python3 -m pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
```

GitHub Actions runs the complete regression suite on every push and pull request. Scientific source-integrity tests remain part of that suite.

Named formal checks separately verify DICOM geometry, Layers 1–3.1, Layer 3.1D TCP, canonical export schemas, provenance, lint, typing, and synthetic validation commands. Tagged retrospective releases rerun verification and publish versioned wheel/source artifacts with SHA-256 checksums.

## Layer 2.2 visual evidence

The native workstation renders the validated GTV as a transparent 3D envelope, each validated vertex mask as a patient-coordinate surface mesh, nearest-neighbour connections coloured by local iPVDR, and the locked 3 mm midpoint sampling spheres. Axial, sagittal, and coronal native-dose views can be shown independently with dose, GTV, and vertex-mask overlays. The selected connection drives the evidence panel and all three slice locations. GTV and vertex surfaces can be exported as binary STL files in DICOM patient LPS millimetres with a JSON provenance manifest. The same export also creates `Full_vertex_graph_connections_LPS_mm.stl`, containing all vertex-mask surfaces and every stored graph edge as a capped physical-scale connection tube.

## Case layout

```text
case_root/
├── raw/                 # immutable input inventory/references
├── cache/layer1/        # case-local immutable, hash-verified cache
├── validated/           # Layer 1 native dose, masks, geometry, volumes, findings
├── derived/
│   ├── layer2_1/
│   ├── layer2_2/
│   ├── layer3_1/        # spatial BED/EQD2, survival fields, gated results
│   └── layer3_2/        # separate non-local research reinterpretation
├── exports/             # canonical JSON and CSV derivatives
├── logs/                # text log and structured JSONL events
└── ascend_case.json     # authoritative case state
```

## Status and invalidation

Calculation status, interpretation status, and metric applicability are separate. Missing and not-applicable values remain null, never zero. Configuration changes mark dependent completed results `STALE`. Prescription changes do not rebuild Layer 1. RTSTRUCT, RTDOSE, image-series, or canonical-mapping changes invalidate Layer 1 and all downstream results.

## Scope and limitations

- Research and technical validation software; no clinical recommendation or approval decision.
- Layer 2.2 is limited to the validated isotropic 1 mm and 2 mm native RTDOSE grids in the selected configuration.
- Import resolves RTDOSE→RTPLAN→RTSTRUCT→planning-image chains by referenced UIDs. One unique complete chain is selected automatically; multiple complete chains require explicit selection. Incomplete chains require a recorded identity-consistent override.
- Layer 1 binds structures by RTSTRUCT SOP Instance UID plus ROI number. Names are display and legacy-migration metadata, not calculation identities.
- Layer 1 accepts uniform anisotropic dose grids. Layer 2.2 reports `outside_validated_scope` for anisotropic grids or isotropic spacing outside its locked 1 mm/2 mm contract; this does not invalidate successful Layer 1 or Layer 2.1 results.
- Non-uniform RTDOSE frame spacing is blocked because the current scalar voxel-volume model is not valid for it.
- Complete ingestion, geometry, ROI inventory, cache, and benchmark contracts are documented in [docs/DICOM_INGESTION_V2.md](docs/DICOM_INGESTION_V2.md) and [docs/PERFORMANCE_BASELINE.md](docs/PERFORMANCE_BASELINE.md).
- Protocol compliance thresholds are not inferred. Protocol interpretation requires explicit confirmations.
- Layer 2.1 supporting-output v3 records per-vertex physical QA, treatment-context applicability, coverage/volume/peak/valley/ratio context, and integrity provenance without changing the six locked metric formulas. See [docs/LAYER21_SUPPORTING_OUTPUTS.md](docs/LAYER21_SUPPORTING_OUTPUTS.md).
- Optional OAR–vertex geometry is a separate descriptive module, accepts only explicit Layer 1-validated structures, and performs no OAR compliance or clinical pass/fail assessment.
- Layer 3.1 is an established-radiobiology research interpretation layer. It is computationally tested but not clinically calibrated or clinically validated. It excludes TCP, NTCP, immune, vascular, bystander, abscopal, and non-local signalling effects.
- Spatial accumulation requires one validated physical geometry or an explicit validated registration. Missing fraction history, geometry correspondence, tissue parameters, delivery time, or comparator schedules block only the dependent branch rather than triggering inferred values.

Source selection and hashes are recorded in [SOURCE_AUDIT.md](SOURCE_AUDIT.md). Regression evidence is recorded in [docs/REGRESSION_REPORT.md](docs/REGRESSION_REPORT.md).
