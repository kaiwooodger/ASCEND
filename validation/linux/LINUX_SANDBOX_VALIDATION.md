# ASCEND Linux sandbox validation

## Final status

Validation date: 2026-08-25 (Australia/Sydney)

Evidence was executed from the exact committed tree:

`4a2353867764c3e1d6b98267a52e78ba7c63aaac`

`ASCEND_LINUX_SANDBOX_GATE = VALIDATED`

`ASCEND_GITHUB_CROSS_PLATFORM_GATE = NOT VALIDATED`

The Linux result is an independent evidence stream. It does not establish Windows, macOS, GitHub-hosted, cross-platform-equivalence, hardware-GPU, or clinical validation.

## Status flags

- `LINUX_CLEAN_INSTALL_VALIDATED = TRUE`
- `LINUX_SCIENTIFIC_EXECUTION_VALIDATED = TRUE`
- `LINUX_DICOM_VALIDATED = TRUE`
- `LINUX_INTERACTIVE_GUI_VALIDATED = TRUE` for an interactive-capable X11/xcb session under Xvfb/Openbox
- `LINUX_3D_RENDERING_VALIDATED = TRUE` for Mesa llvmpipe software OpenGL; physical GPU execution was not tested
- `LINUX_ASCEND_E2E_VALIDATED = TRUE`
- `GUI_ENGINE_CONSISTENCY = PASS`
- `LINUX_FULL_TEST_SUITE = PASS`
- `LINUX_SCIENTIFIC_REFERENCE = PASS`
- `LINUX_DICOM_E2E = PASS`
- `LINUX_REALTIME_3D_RENDERING = PASS`

The following flags remain unassigned:

- `WINDOWS_PORTABILITY_VALIDATED`
- `MACOS_PORTABILITY_VALIDATED`
- `CROSS_PLATFORM_EQUIVALENCE_VALIDATED`
- `GITHUB_HOSTED_VALIDATED`
- `GPU_RENDERING_VALIDATED`
- `CLINICAL_VALIDATED`

## Environment

The sandbox was a newly created `linux/amd64` Docker container. The exact source archive was mounted read-only and copied into the container. No developer-machine Python environment was copied. Docker Desktop emulated x86-64 on an Apple host; this is x86-64 Linux software execution, not native x86-64 hardware performance evidence.

| Item | Value |
|---|---|
| Distribution | Debian GNU/Linux 12 (bookworm) |
| Kernel | `6.12.76-linuxkit` |
| Architecture | `x86_64` / Docker `linux/amd64` |
| CPU | `VirtualApple @ 2.50GHz`, 8 logical CPUs |
| RAM | 3918.3 MiB |
| Python | CPython 3.12.14 |
| Display | Xvfb 21.1.7 `:99`, Openbox 3.6.1, Qt `xcb` platform |
| OpenGL | Mesa 22.3.6, OpenGL 4.5 compatibility profile |
| Renderer | llvmpipe LLVM 15.0.6, direct rendering, not hardware accelerated |
| Qt / PySide6 | 6.11.2 / 6.11.2 |
| NumPy | 2.5.2 |
| SciPy | 1.18.1 |
| pydicom | 3.0.2; security-backport regression also run with 2.4.5 |
| PyVista | 0.48.4 |
| VTK | 9.6.2 |

Full GLX evidence is in [`glxinfo.txt`](artifacts/glxinfo.txt).

## Clean build and installation

The container received a `git archive` of commit `4a235386`, built the wheel in `/opt/build-env`, and installed the non-editable wheel into the separate `/opt/ascend` environment. Import and CLI checks ran from `/tmp` with `PYTHONPATH` empty.

- Wheel: `ascend_lrt-1.3.5-py3-none-any.whl`
- Wheel SHA-256: `f59f0fc582e7c5a7ddd687797c06b8083cc4dc62c820877f8626c9cb948ede7b`
- Build: PASS, 5.28 seconds
- Wheel installation: PASS
- `pip check`: PASS, no broken requirements
- Imported module: `/opt/ascend/lib/python3.12/site-packages/ascend/__init__.py`
- Installed version: 1.3.5
- Installed CLI help: PASS

PyVista was installed as an explicit validation dependency, matching the frozen workflow's explicit PyVista installation. It was not sourced from the checkout.

Evidence: [`clean-wheel-verification.txt`](artifacts/clean-wheel-verification.txt), [`package-build.time`](artifacts/package-build.time), and [`wheel-install.time`](artifacts/wheel-install.time).

`LINUX_CLEAN_INSTALL = PASS`

## Full committed test suite

The full committed test suite ran from the copied exact tree while importing the wheel-installed package. Qt used `xcb`, not the offscreen platform.

| Result | Count |
|---|---:|
| Passed tests | 216 |
| Passed subtests | 28 |
| Failed | 0 |
| Errors | 0 |
| Skipped | 0 |
| Xfailed | 0 |
| Warnings | 355 |
| Pytest duration | 17.32 s |
| Measured wall time | 20.09 s |
| Maximum RSS | 850,948 KiB |

The 355 warnings are pydicom 3.0 API deprecations and NumPy 2.5 shape-assignment deprecations emitted by VTK/scikit-image. No Linux-specific skip was accepted.

Evidence: [`full-pytest.log`](artifacts/full-pytest.log), [`full-pytest-junit.xml`](artifacts/full-pytest-junit.xml), and [`full-pytest.time`](artifacts/full-pytest.time).

`LINUX_FULL_TEST_SUITE = PASS`

## Frozen scientific reference

`tests/cross_platform_report.py` ran without modification. Every value was finite, the frozen geometry matched, and all 14 metrics passed their existing tolerances.

- Exact matches: 13 of 14 metrics
- Maximum absolute difference: `8.881784197001252e-16`
- Maximum relative difference: `1.7760835124330015e-16`
- Metric producing both maxima: `mlq_eud_gy`
- Expected `mlq_eud_gy`: `5.000769465414592`
- Observed `mlq_eud_gy`: `5.000769465414593`
- Frozen combined tolerance at that value: `5.0107694654145926e-08`

Geometry, Layer 1 dose endpoints, Layer 2.1 metrics, iPVDR, s-BED, s-EQD2, MLQ survival, EUD, and modelled therapeutic ratio passed.

Evidence: [`linux-scientific-report.json`](artifacts/linux-scientific-report.json) and [`linux-scientific-comparison.json`](artifacts/linux-scientific-comparison.json).

`LINUX_SCIENTIFIC_REFERENCE = PASS`

## Interactive GUI and full workflow

ASCEND ran in a visible, window-managed X11 session with `QT_QPA_PLATFORM=xcb`. The application did not use Qt's offscreen platform. The main window was visible, all ten major pages responded to navigation, a file-selector dialog opened and closed, layouts remained usable after resize to 1283×720, and the application closed without a Qt plugin failure.

A separate visible workflow used actual Qt button clicks in this order:

1. Import case
2. Validate case
3. Run Layer 2.1
4. Run Layer 2.2
5. Run complete Layer 3.1
6. Export JSON and CSV

Results were Layer 1 `completed_with_warnings`, Layer 2.1 `completed_with_warnings`, Layer 2.2 `completed`, and Layer 3.1 `completed_with_warnings`. Nine export artifacts were produced. The warnings are explicit research/validation classifications, not execution failures.

Representative GUI values were compared directly with stored engine results. Mean peak dose, iPVDR, s-BED, s-EQD2, mean survival, EUD, and therapeutic ratio all matched. Refreshing the GUI did not mutate stored scientific results.

This validates an interactive-capable virtual X11 desktop controlled by the harness. It is not evidence of a physical monitor, a human usability study, or a native desktop compositor.

Screenshots:

- [`ascend-main-window.png`](artifacts/ascend-main-window.png)
- [`ascend-layer31-results.png`](artifacts/ascend-layer31-results.png)
- [`ascend-gui-driven-workflow.png`](artifacts/ascend-gui-driven-workflow.png)

`LINUX_INTERACTIVE_GUI = PASS`

`LINUX_ASCEND_E2E = PASS`

`GUI_ENGINE_CONSISTENCY = PASS`

## VTK, OpenGL, STL, and CAD

PyVista created a real on-display VTK render window with `off_screen=False`. GLX provided an OpenGL 4.5 context through Mesa llvmpipe.

- STL reload: 842 points, 1680 cells
- s-BED scalar range: 20.0–40.0 Gy
- s-EQD2 scalar range: 16.666666666666668–33.333333333333336 Gy
- MLQ survival scalar range: 0.1353352832366127–0.36787944117144233
- Scalar mapping and colour bar: PASS
- Camera azimuth, elevation, and zoom: PASS
- Rotation-equivalent camera interaction: PASS
- Clipping: PASS, 950 clipped cells
- Opacity: PASS
- Mesh smoothing: PASS
- Vertex overlay: PASS
- Resize: PASS
- Ten renderer open/close cycles: PASS
- Mesh creation: 0.067 s
- Render and screenshot: 2.305 s
- Segmentation faults, framebuffer errors, Mesa errors, and Qt plugin errors: none

The renderer is software OpenGL. `GPU_RENDERING_VALIDATED` is not assigned.

Evidence: [`ascend-vtk-realtime.png`](artifacts/ascend-vtk-realtime.png) and [`linux-validation-harness.json`](artifacts/linux-validation-harness.json). The generated synthetic STL was inspected in the sandbox but is not committed because repository publication policy prohibits runtime STL artifacts.

`LINUX_REALTIME_3D_RENDERING = PASS`

## DICOM end to end

The approved test input was a generated, non-clinical synthetic CT/RTSTRUCT/RTPLAN/RTDOSE dataset. The source directory contained Unicode and spaces; RTDOSE and RTSTRUCT filenames were renamed with mixed case to exercise Linux case sensitivity.

- Discovery: PASS
- Complete UID-linked chain: PASS
- Frame-of-reference and geometry validation: PASS
- 48-image CT series, RTSTRUCT, RTPLAN, and RTDOSE: PASS
- ROI selection and rasterisation: PASS
- Layer 1: `completed_with_warnings`
- Layer 2.1: `completed_with_warnings`
- Layer 2.2: `completed`
- Layer 3.1: `completed_with_warnings`
- GUI presentation: PASS
- Result export: PASS

The synthetic RTPLAN fixture does not encode a Fraction Group Sequence. The validation workflow therefore declared the intended one-fraction synthetic LRT treatment context explicitly before Layer 3.1. Without that declaration, ASCEND correctly blocks the calculation with `BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED`.

Evidence: [`ascend-dicom-e2e.png`](artifacts/ascend-dicom-e2e.png) and [`linux-validation-harness.json`](artifacts/linux-validation-harness.json).

`LINUX_DICOM_E2E = PASS`

## Repeated sessions and case switching

Ten complete synthetic calculation-and-GUI cycles produced identical iPVDR, survival, and EUD fingerprints. Cycle durations ranged from 0.745 to 0.901 seconds. Every closed session left zero live Qt widgets. RSS after the first cycle increased by only 0.0039 MiB over the remaining nine cycles.

Within one process, Case A → Case B → Case A preserved case-local results, replaced the dose-grid/case root, retained correct ROI identity, and showed the active case in the GUI.

`REPEATED_SESSION_TESTING = PASS`

`CASE_SWITCHING = PASS`

## Error handling

All required malformed-input probes produced controlled states without a crash or hang:

| Input | Controlled result |
|---|---|
| Missing RTDOSE | Import rejected: no candidate DICOM-RT chain |
| Missing RTSTRUCT | Layer 1 failed explicitly because chain selection was unresolved |
| Mismatched FrameOfReferenceUID | Chain classified `invalid` |
| Negative tumour alpha | Tumour MLQ branch blocked with `INVALID_TUMOUR_PARAMETER_SET` |
| Missing `treatment_delivery_time` | Tumour MLQ branch blocked with explicit missing-field reason |
| Invalid STL | VTK reader rejected it as an empty 0-point/0-cell mesh and logged the reader diagnostic |
| Empty/unrasterised ROI | Layer 3.1A branch blocked because the ROI was not rasterised |
| Unsupported treatment mode | Configuration validation raised `ValueError` |

Independent Layer 3.1 branches can still complete when one branch is blocked. The overall record is therefore `completed_with_warnings` while the invalid branch is explicitly `blocked`; this is intentional partial-branch behavior, not silent continuation.

`ERROR_HANDLING = PASS`

## Performance baseline

These measurements are execution baselines for this emulated x86-64 container, not native-hardware performance claims.

| Operation | Seconds |
|---|---:|
| Application startup | 0.442 |
| DICOM discovery | 0.040 |
| DICOM import | 0.056 |
| Layer 1 rasterisation | 0.364 |
| Layer 2.1 | 0.029 |
| Layer 2.2 iPVDR | 0.031 |
| Layer 3.1 s-BED/s-EQD2/MLQ/EUD/TR | 0.128 DICOM case; 0.057 reference case |
| Result export | 0.054 |
| CAD mesh creation | 0.067 |
| 3D render and screenshot | 2.305 |

## Memory baseline

| Point | RSS MiB |
|---|---:|
| Process start | 357.51 |
| After QApplication startup | 366.23 |
| After reference Layer 3 | 373.26 |
| After GUI close | 473.37 |
| After VTK close | 697.69 |
| After DICOM E2E | 705.63 |
| Final after all probes | 714.25 |

VTK/Mesa establishes a renderer high-water allocation and retains approximately 224 MiB after the first full render. Ten subsequent VTK open/close cycles plateaued near 698 MiB. The repeated whole-session series was stable after proper Qt destruction: zero live widgets each cycle and 0.0039 MiB growth after the first cycle. No uncontrolled progressive session growth remained.

The first harness version retained closed `MainWindow` Python objects and incorrectly showed 333.7 MiB of growth. Adding `WA_DeleteOnClose`, `deleteLater`, event draining, and garbage collection eliminated the growth. This was a harness lifecycle defect, not an ASCEND source change.

## Dependency security

No advisory was suppressed.

The installed Python 3.12 environment was audited with pip-audit 2.10.1 after excluding only the unpublished local `ascend-lrt` wheel from the PyPI lookup. All 66 third-party distributions were audited: zero known vulnerabilities. pydicom 3.0.2 and Pillow 12.3.0 both reported no advisory.

`PYSEC-2026-2266` / CVE-2026-32711 is a pydicom `FileSet`/DICOMDIR path-traversal issue affecting 2.x through 2.4.4 and 3.0.0–3.0.1. Upstream fixed it in 2.4.5 and 3.0.2. ASCEND contains no `FileSet`, `pydicom.fileset`, DICOMDIR, or `ReferencedFileID` code path. ASCEND does process untrusted DICOM files, so retaining a vulnerable pydicom floor is still unacceptable even though the specific API is unused.

The dependency floor was raised from `pydicom>=2.4` to `pydicom>=2.4.5` in `pyproject.toml` and `requirements.txt`. This preserves the Python 3.9-compatible 2.4 line while excluding 2.4.4. Full Linux regression on pydicom 2.4.5 passed: 216 tests, 28 subtests, 0 failures, 111 warnings in 14.13 seconds. Full Linux execution on pydicom 3.0.2 also passed as recorded above.

There is an unresolved advisory-metadata conflict. The pydicom upstream advisory and 2.4.5 release notes identify 2.4.5 as a backported fix, but the current PyPA `PYSEC-2026-2266` record still lists only 3.0.2 as fixed. pip-audit therefore reports one finding for 2.4.5. No ignore or suppression was added. pydicom 3.0.2 is audit-clean but requires Python 3.10 or newer. The validated Python 3.12 Linux environment is clean on 3.0.2; the preserved Python 3.9 minimum path uses the upstream-fixed 2.4.5 backport but cannot be called pip-audit-clean until the advisory metadata is corrected or the Python minimum is changed in a separately reviewed decision.

Pillow 12.3.0 contains the current 2026 security fixes. ASCEND contains no direct PIL/Pillow import. Pillow is present transitively in the validation rendering stack. Full tests and graphical validation passed with 12.3.0. No direct Pillow dependency was added solely to force an unused API into ASCEND.

Upstream references:

- [pydicom GHSA-v856-2rf8-9f28](https://github.com/pydicom/pydicom/security/advisories/GHSA-v856-2rf8-9f28)
- [pydicom 2.4.5 release notes](https://pydicom.github.io/pydicom/2.4/release_notes/index.html)
- [Pillow 12.3.0 security release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)

Evidence: [`pip-audit.json`](artifacts/pip-audit.json), [`pip-audit.log`](artifacts/pip-audit.log), [`pydicom-2.4.5-pip-audit.json`](artifacts/pydicom-2.4.5-pip-audit.json), [`pydicom-2.4.5-pytest.log`](artifacts/pydicom-2.4.5-pytest.log), and [`pydicom-2.4.5-pytest.time`](artifacts/pydicom-2.4.5-pytest.time).

Bandit 1.9.4 reported no medium- or high-severity findings. It reported 18 low-severity items: eight `B105` false positives on status labels/colour hex values and ten low-severity review items covering broad exception handling, asserts, XML parsing, and fixed-argument subprocess use. A code-free count summary is retained in [`bandit-summary.json`](artifacts/bandit-summary.json); none was suppressed or reclassified to make the gate pass. The raw scanner JSON remains outside Git because repository credential-pattern controls correctly reject embedded source snippets.

## Warnings and defects

ASCEND source defects discovered by Linux execution: none.

Validation and environment findings:

1. Initial Docker installation failed after the macOS data volume reached approximately 450 MiB free. Removing only task-created corrupted containers/images restored 4.1 GiB and the clean sandbox succeeded. This was host sandbox infrastructure, not ASCEND.
2. The first DICOM Layer 3.1 probe omitted explicit synthetic fraction history and was correctly blocked. The fixture setup was corrected; no ASCEND calculation code changed.
3. The first repeated-session probe retained closed window objects. Correct object destruction produced stable memory; no ASCEND source code changed.
4. The pydicom dependency floor admitted vulnerable 2.4.4. The floor is now the upstream-fixed 2.4.5 backport and both fixed branches were regression tested. pip-audit's advisory metadata still flags 2.4.5 and names only 3.0.2 as fixed; this discrepancy remains explicit and unsuppressed.
5. Openbox logged only a missing optional Debian menu file. No fonts, icons, Qt plugins, rendering, or application behavior were impaired.
6. Invalid-STL diagnostics in the harness log are expected evidence from the deliberate malformed-input probe.

## Hosted CI preservation

The GitHub Actions workflow was not redesigned or reduced. Its matrix, tests, tolerances, quality checks, package checks, reports, and aggregate gate remain frozen.

Hosted runs `32795942005` and `32796223979` both ended in `startup_failure` before job allocation. Each had zero jobs, zero execution seconds, and no ASCEND code execution because GitHub payment authorization failed.

`CI_IMPLEMENTED = TRUE`

`CI_EXECUTED = FALSE`

`GITHUB_RUNNER_ALLOCATION = BLOCKED`

`ASCEND_GITHUB_CROSS_PLATFORM_GATE = NOT VALIDATED`

The exact scientific implementation executed here is commit `4a2353867764c3e1d6b98267a52e78ba7c63aaac`. The pydicom floor and this evidence record are later non-scientific changes and must be documented as such when the hosted rerun target is selected.

## Artifact index

The executable harness and complete machine-readable record are [`linux_validation_harness.py`](artifacts/linux_validation_harness.py) and [`linux-validation-harness.json`](artifacts/linux-validation-harness.json). The harness log is [`linux-validation-harness.log`](artifacts/linux-validation-harness.log). All evidence files are under [`validation/linux/artifacts`](artifacts/).
