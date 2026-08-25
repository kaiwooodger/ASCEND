# GitHub-hosted cross-platform validation

## Status

- Date: 2026-08-25 (Australia/Sydney)
- Successful workflow run: [32805688752](https://github.com/kaiwooodger/ASCEND/actions/runs/32805688752)
- Validated successor commit: `df150469259522e37d22dd7f986db0cf6dc2d6d3`
- Original workflow implementation: `c6eaaf732b847ba23589185c4374c4e95c279113`
- Frozen scientific target: `4a2353867764c3e1d6b98267a52e78ba7c63aaac`
- Branch: `ci/github-hosted-phase2`
- Workflow conclusion: `success`
- Aggregate required check: `ASCEND cross-platform portability gate` — PASS

`CI_IMPLEMENTED = TRUE`

`CI_EXECUTED = TRUE`

`GITHUB_RUNNER_ALLOCATION = PASS`

`ASCEND_GITHUB_CROSS_PLATFORM_GATE = VALIDATED`

GitHub allocated all required hosted runners. The successful run executed the complete workflow from clean checkouts: quality and security checks, the Python 3.9 minimum gate, six primary OS/Python jobs, frozen numerical comparison, three isolated wheel validations, and the aggregate portability gate.

## Hosted matrix

Every primary job performed a clean non-editable install, import and CLI smoke test, complete 229-test execution, scientific report generation, Qt offscreen tests, and PyVista/VTK/STL offscreen rendering tests. The 28 subtests are included in the primary-job results.

| Environment | Python | Install | Tests | Scientific comparison | GUI | 3D | Package | Result |
|---|---:|---|---|---|---|---|---|---|
| Ubuntu x64 | 3.11.16 | PASS | 229 + 28 subtests PASS, 12.24 s | BASELINE / reference PASS | PASS | PASS | PASS | PASS |
| Ubuntu x64 | 3.12.14 | PASS | 229 + 28 subtests PASS, 10.34 s | PASS | PASS | PASS | N/A | PASS |
| Windows x64 | 3.11.9 | PASS | 229 + 28 subtests PASS, 18.07 s | PASS | PASS | PASS | PASS | PASS |
| Windows x64 | 3.12.10 | PASS | 229 + 28 subtests PASS, 18.39 s | PASS | PASS | PASS | N/A | PASS |
| macOS ARM64 | 3.11.9 | PASS | 229 + 28 subtests PASS, 28.66 s | PASS | PASS | PASS | PASS | PASS |
| macOS ARM64 | 3.12.10 | PASS | 229 + 28 subtests PASS, 34.93 s | PASS | PASS | PASS | N/A | PASS |
| Ubuntu x64 minimum gate | 3.9.25 | PASS | 229 PASS, 9.91 s | N/A | PASS | PASS | N/A | PASS |

Primary job durations, including provisioning, installation, reports, tests, and artifact upload, were 1m25s/1m10s on Ubuntu 3.11/3.12, 2m12s/2m21s on Windows 3.11/3.12, and 1m33s/1m36s on macOS 3.11/3.12. The Python 3.9 gate took 1m09s.

### Job inventory

| Job ID | Job name | Hosted target | Duration | Result |
|---:|---|---|---:|---|
| `97675239477` | Static quality and test collection | Ubuntu / Python 3.11 | 1m03s | PASS |
| `97675239599` | Dependency and source security checks | Ubuntu / Python 3.11 | 1m00s | PASS |
| `97675427345` | Minimum Python 3.9 compatibility | Ubuntu x64 / Python 3.9.25 | 1m09s | PASS |
| `97675427439` | ubuntu-latest / Python 3.11 | Ubuntu x64 / Python 3.11.16 | 1m25s | PASS |
| `97675427347` | ubuntu-latest / Python 3.12 | Ubuntu x64 / Python 3.12.14 | 1m10s | PASS |
| `97675427393` | windows-latest / Python 3.11 | Windows x64 / Python 3.11.9 | 2m12s | PASS |
| `97675427423` | windows-latest / Python 3.12 | Windows x64 / Python 3.12.10 | 2m21s | PASS |
| `97675427391` | macos-latest / Python 3.11 | macOS ARM64 / Python 3.11.9 | 1m33s | PASS |
| `97675427373` | macos-latest / Python 3.12 | macOS ARM64 / Python 3.12.10 | 1m36s | PASS |
| `97675851287` | Cross-platform numerical equivalence | Ubuntu | 8s | PASS |
| `97675881413` | Package / ubuntu-latest / Python 3.11 | Ubuntu x64 | 1m13s | PASS |
| `97675881427` | Package / windows-latest / Python 3.11 | Windows x64 | 2m27s | PASS |
| `97675881378` | Package / macos-latest / Python 3.11 | macOS ARM64 | 57s | PASS |
| `97676311291` | ASCEND cross-platform portability gate | Ubuntu | 4s | PASS |

## Observed environments

The workflow's explicit architecture assertions passed. Runner reports recorded:

| Runner | Architecture | NumPy / SciPy | Qt / PySide6 | PyVista / VTK |
|---|---|---|---|---|
| Linux 3.11 | X64 / `x86_64` | 2.4.6 / 1.17.1 | 6.11.2 / 6.11.2 | 0.48.4 / 9.6.2 |
| Linux 3.12 | X64 / `x86_64` | 2.5.2 / 1.18.1 | 6.11.2 / 6.11.2 | 0.48.4 / 9.6.2 |
| Windows 3.11 | X64 / `AMD64` | 2.4.6 / 1.17.1 | 6.11.2 / 6.11.2 | 0.48.4 / 9.6.2 |
| Windows 3.12 | X64 / `AMD64` | 2.5.2 / 1.18.1 | 6.11.2 / 6.11.2 | 0.48.4 / 9.6.2 |
| macOS 3.11 | ARM64 / `arm64` | 2.4.6 / 1.17.1 | 6.11.2 / 6.11.2 | 0.48.4 / 9.6.2 |
| macOS 3.12 | ARM64 / `arm64` | 2.5.2 / 1.18.1 | 6.11.2 / 6.11.2 | 0.48.4 / 9.6.2 |

The complete platform strings and dependency versions are preserved in the six scientific JSON reports under [`artifacts`](artifacts/).

## Scientific comparison

- Reference case: `SYNTHETIC-CROSS-PLATFORM-V1`
- Baseline: Linux/X64/Python 3.11.16
- Reports compared: 6 of 6
- Metrics per report: 14
- Missing values: 0
- Nonfinite values: 0
- Outside-tolerance values: 0
- Maximum absolute difference: `8.881784197001252e-16`
- Maximum relative difference: `1.7760835124330015e-16`
- Metric producing the maximum difference: `mlq_eud_gy`, Linux Python 3.12
- Classification: `WITHIN_TOLERANCE`
- All other baseline comparisons: `EXACT`
- Comparison result: PASS

The comparator also checked every environment independently against `validation/synthetic_reference_cases/cross_platform_expected.json`. Only Linux Python 3.12 `mlq_eud_gy` differed, by less than one quadrillionth and far inside its frozen tolerance. No reference value, formula, or tolerance changed during triage.

The exact tolerance set was:

| Metric | Absolute tolerance | Relative tolerance |
|---|---:|---:|
| `dose_d95_gy` | 1e-10 | 0 |
| `dose_d50_gy` | 1e-10 | 0 |
| `mean_dose_gy` | 1e-12 | 1e-10 |
| `physical_ipvdr` | 1e-10 | 1e-10 |
| `vertex_count` | 0 | 0 |
| `valley_volume_cc` | 1e-12 | 0 |
| `voxel_volume_cc` | 1e-12 | 0 |
| `sbed_d95_gy` | 1e-10 | 1e-8 |
| `sbed_mean_gy` | 1e-12 | 1e-8 |
| `seqd2_d95_gy` | 1e-10 | 1e-8 |
| `seqd2_mean_gy` | 1e-12 | 1e-8 |
| `mean_mlq_survival` | 1e-12 | 1e-8 |
| `mlq_eud_gy` | 1e-10 | 1e-8 |
| `modelled_therapeutic_ratio` | 1e-12 | 1e-8 |

Machine-readable evidence is [`cross-platform-comparison.json`](artifacts/cross-platform-comparison.json).

## Package validation

Source and wheel distributions were built independently on Ubuntu x64, Windows x64, and macOS ARM64 using Python 3.11. Each wheel was installed into a clean isolated environment outside the checkout. All three environments:

- imported the installed `ascend` package from `site-packages`;
- reported ASCEND 1.3.5;
- constructed the GUI offscreen;
- reproduced BED `[7.5, 60.0]` and EQD2 `[6.25, 50.0]`.

Package jobs passed in 1m13s on Ubuntu, 2m27s on Windows, and 57s on macOS. Their reports are preserved under [`artifacts`](artifacts/).

## Static and security gates

- repository publication boundary: PASS, 283 tracked files inspected;
- clean dependency resolution and `pip check`: PASS;
- Python compilation: PASS;
- Ruff: PASS;
- targeted mypy: PASS, no issues in three source files;
- repository, workflow, issue-form, citation, and project schemas: PASS;
- pytest collection: PASS, 229 tests collected;
- Bandit medium/high scan: PASS;
- strict pip-audit on the Python 3.11 requirements resolution: PASS, no known vulnerabilities.

No security advisory was suppressed. Python 3.11 and newer resolve `pydicom>=3.0.2`, which is audit-clean. Python 3.9 cannot install pydicom 2.4.5 because that release requires Python 3.10 or newer, so the minimum-version marker resolves pydicom 2.4.4. That environment passed installation and all 229 tests but remains exposed to `PYSEC-2026-2266`. ASCEND does not invoke the affected `FileSet`/DICOMDIR path-resolution API. This is a declared minimum-version dependency limitation, not a suppressed finding; moving the project minimum to Python 3.10 is the available route to an audit-clean pydicom dependency.

## Failure triage history

The successful run followed evidence-driven remediation. Each failed run retained the full matrix and scientific gates.

| Run | Result | Root cause | Narrow fix |
|---:|---|---|---|
| [32795942005](https://github.com/kaiwooodger/ASCEND/actions/runs/32795942005) | `startup_failure`, 0 jobs | GitHub payment authorization failure | External billing condition later cleared; no ASCEND change |
| [32796223979](https://github.com/kaiwooodger/ASCEND/actions/runs/32796223979) | `startup_failure`, 0 jobs | Same account-level billing block | No ASCEND change |
| [32801516677](https://github.com/kaiwooodger/ASCEND/actions/runs/32801516677) | runner allocated; quality failed | Committed raw scanner/STL evidence violated repository boundary | Removed prohibited raw artifacts in `37223e8` |
| [32801704134](https://github.com/kaiwooodger/ASCEND/actions/runs/32801704134) | quality failed | Ubuntu Qt import lacked `libEGL.so.1` | Installed `libgl1` and `libegl1` in `0513563` |
| [32801883755](https://github.com/kaiwooodger/ASCEND/actions/runs/32801883755) | partial matrix failure | pydicom 2.4.5 excludes Python 3.9; top-level `fcntl` import excluded Windows | Added Python-version dependency markers and Linux-only `fcntl` use in `f0c2199` |
| [32802780720](https://github.com/kaiwooodger/ASCEND/actions/runs/32802780720) | Windows failure | PowerShell report-path expansion and native Windows OpenGL access violation | Used shell-independent runner-temp expression and pinned Mesa action in `d184880` |
| [32804329201](https://github.com/kaiwooodger/ASCEND/actions/runs/32804329201) | Windows setup failure | Mesa 26.1.7 archive no longer contained the action's required `libglapi.dll` | Selected the action's tested Mesa 23.3.5 payload in `e42d6d5` |
| [32804579723](https://github.com/kaiwooodger/ASCEND/actions/runs/32804579723) | 10 Windows test failures | Windows directory/fsync semantics, retained NumPy file maps, and separator-specific relocation | Portable filesystem, loading, and relocation fixes in `51978cf` |
| [32804947015](https://github.com/kaiwooodger/ASCEND/actions/runs/32804947015) | 3 Windows test failures | Layer 1 returned open scratch-file memory maps | Detached arrays and closed maps before cleanup in `a2ede6a` |
| [32805360275](https://github.com/kaiwooodger/ASCEND/actions/runs/32805360275) | 1 Windows test failure | Immutable cache file attribute propagated into formal-run staging | Materialized owner-writable independent formal copies in `df15046` |
| [32805688752](https://github.com/kaiwooodger/ASCEND/actions/runs/32805688752) | PASS | All demonstrated defects resolved | No further change |

The hosted target is a documented successor to `4a2353867764c3e1d6b98267a52e78ba7c63aaac`. Commit `c552f4f` introduced the pre-existing GUI/Layer 1 architecture work and PyVista dependency before hosted execution. Commits `282c7b4` and `37223e8` recorded Linux evidence and enforced the repository boundary. Commits `0513563`, `f0c2199`, `d184880`, `e42d6d5`, `51978cf`, `a2ede6a`, and `df15046` contain only dependency/runtime portability fixes. The locked scientific implementation, frozen expected metrics, and tolerance definitions were not altered.

## Portability flags

The executed hosted evidence establishes:

- `SOURCE_PORTABILITY_VALIDATED = TRUE`
- `DEPENDENCY_PORTABILITY_VALIDATED = TRUE`, subject to the declared Python 3.9 advisory limitation
- `PYTHON_PORTABILITY_VALIDATED = TRUE`
- `NUMERICAL_PORTABILITY_VALIDATED = TRUE`
- `DICOM_PORTABILITY_VALIDATED = TRUE`
- `PACKAGE_PORTABILITY_VALIDATED = TRUE`
- `HEADLESS_GUI_PORTABILITY_VALIDATED = TRUE`
- `HEADLESS_3D_PORTABILITY_VALIDATED = TRUE`

The following remain intentionally unassigned by hosted CI:

- `INTERACTIVE_GUI_PORTABILITY_VALIDATED`
- `GPU_RENDERING_VALIDATED`
- `FULL_LINUX_DEPLOYMENT_VALIDATED`
- `CLINICAL_DEPLOYMENT_VALIDATED`

Windows used Mesa llvmpipe and Linux used hosted headless software rendering. These results validate offscreen construction and frame production, not hardware GPU/driver behavior or interactive desktop behavior. macOS Intel, Ubuntu ARM64, and Windows ARM64 were outside the required Tier 1 matrix. No clinical dataset or clinical deployment claim is made.

GitHub emitted deprecation annotations because `actions/checkout@v4`, `actions/setup-python@v5`, and related current action majors target Node.js 20 while the 2026 hosted service forces them onto Node.js 24. Every affected action completed successfully. This is an upstream action-runtime maintenance warning, not an ASCEND defect or failed validation gate.

## Branch protection

The stable required-check name is `ASCEND cross-platform portability gate`, and it now has a successful check context. Repository documentation targets this aggregate gate rather than transient matrix job names. GitHub's branch-protection API previously returned HTTP 403 because this private repository/account combination did not expose branch protection. No repository setting was changed.

## Evidence index

The GitHub artifacts have 30-day hosted retention. Durable repository copies of all six scientific reports, the complete comparison, and all three package reports are under [`validation/cross_platform/artifacts`](artifacts/). The workflow run and per-job logs remain linked from [run 32805688752](https://github.com/kaiwooodger/ASCEND/actions/runs/32805688752).
