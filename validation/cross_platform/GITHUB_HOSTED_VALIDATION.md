# GitHub-hosted cross-platform validation

## Status

This is a blocked Phase 2 evidence snapshot, not a completed portability validation.

- Date: 2026-08-25 (Australia/Sydney)
- Evidence commit: `c6eaaf732b847ba23589185c4374c4e95c279113`
- Branch: `ci/github-hosted-phase2`
- Phase 2 workflow run: [32795942005](https://github.com/kaiwooodger/ASCEND/actions/runs/32795942005)
- Workflow conclusion: `startup_failure`
- Jobs scheduled: 0
- Jobs executed: 0
- Run duration: 0 seconds (`createdAt` and `updatedAt` were both `2026-08-25T01:01:37Z`)

`GITHUB_RUNNER_ALLOCATION = BLOCKED`

`ASCEND_GITHUB_CROSS_PLATFORM_GATE = NOT VALIDATED`

GitHub displayed this run annotation before job allocation:

> The job was not started because recent account payments have failed or your spending limit needs to be increased.

The account billing page also displayed `Your payment authorization has failed` and directed the account owner to contact the bank. This establishes an account-level GitHub Actions billing authorization failure as the immediate cause. The failure occurred before checkout, dependency installation, test execution, or any ASCEND code ran. It is not evidence of a workflow, dependency, scientific, GUI, VTK, or packaging defect.

## Hosted environment matrix

`NOT EXECUTED` means GitHub did not allocate a runner. No portability flag is inferred from local execution.

| Environment | Python | Install | Tests | Scientific comparison | GUI | 3D | Package | Result |
|---|---:|---|---|---|---|---|---|---|
| Ubuntu x64 | 3.11 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED (required baseline) | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | BLOCKED |
| Ubuntu x64 | 3.12 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | N/A | BLOCKED |
| Windows x64 | 3.11 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | BLOCKED |
| Windows x64 | 3.12 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | N/A | BLOCKED |
| macOS ARM64 | 3.11 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | BLOCKED |
| macOS ARM64 | 3.12 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | N/A | BLOCKED |
| Ubuntu x64 minimum-version gate | 3.9 | NOT EXECUTED | NOT EXECUTED | N/A | NOT EXECUTED | NOT EXECUTED | N/A | BLOCKED |

No runner-reported operating system, architecture, Python version, Qt version, PySide6 version, NumPy version, SciPy version, PyVista version, or VTK version exists for this run. No job logs or artifacts were produced.

## Workflow run history

The current and preceding repository runs all failed before runner allocation:

| Run ID | Event/branch | Result | Executed jobs |
|---:|---|---|---:|
| [32795942005](https://github.com/kaiwooodger/ASCEND/actions/runs/32795942005) | push / `ci/github-hosted-phase2` | `startup_failure` | 0 |
| 32720095691 | pull request / `fix/release-checksum-manifest` | `startup_failure` | 0 |
| 32719944867 | pull request / `fix/release-checksum-manifest` | `startup_failure` | 0 |
| 32719929170 | push / `fix/release-checksum-manifest` | `startup_failure` | 0 |
| 32714190280 | push / `main` | `startup_failure` | 0 |
| 32714012582 | push / `main` | `startup_failure` | 0 |
| 32713845128 | push / `main` | `startup_failure` | 0 |

## Implemented workflow controls

Commit `c6eaaf732b847ba23589185c4374c4e95c279113` added or hardened:

- a six-environment primary matrix for Ubuntu x64, Windows x64, and macOS ARM64 on Python 3.11 and 3.12;
- a separate Ubuntu/Python 3.9 minimum-version gate;
- explicit runner architecture checks;
- clean non-editable project installation and full test execution on every primary environment;
- scientific/runtime report generation with explicit nonfinite-value rejection;
- cross-platform comparison against Ubuntu x64/Python 3.11;
- wheel build and isolated wheel validation on Ubuntu, Windows, and macOS;
- Ruff, targeted mypy, Bandit, and pip-audit jobs;
- the stable aggregate job name `ASCEND cross-platform portability gate`;
- branch-protection documentation targeting the stable aggregate job.

No scientific formula, frozen reference value, tolerance, or test was modified to hide a failure. No platform was disabled or marked `xfail`.

## Scientific comparison

No hosted scientific report exists, so no hosted comparison classification or maximum observed difference can be reported.

- Baseline: Ubuntu x64/Python 3.11 — NOT EXECUTED
- Compared reports: 0 of 6
- Maximum observed absolute difference: N/A
- Maximum observed relative difference: N/A
- Cross-platform equivalence: NOT VALIDATED

The frozen tolerance set that the unexecuted comparison job would use is:

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

The comparator classifies values as `EXACT`, `WITHIN_TOLERANCE`, `OUTSIDE_TOLERANCE`, `MISSING`, or `NONFINITE`, and fails on the last three classifications. Values are compared without pre-rounding.

## Local preflight evidence

Local checks establish workflow readiness but do not replace hosted evidence:

- the exact committed tree passed 216 tests under an isolated Python 3.9 environment;
- the full dirty worktree passed 230 tests and 28 subtests, with unrelated uncommitted viewer work preserved outside the CI commit;
- Ruff, targeted mypy, workflow schema validation, Bandit, and the Python 3.11 requirements audit passed locally;
- the source and wheel built successfully;
- the isolated wheel installed outside the checkout, imported ASCEND, constructed `MainWindow` offscreen, and reproduced BED `[7.5, 60.0]` and EQD2 `[6.25, 50.0]`;
- repository searches found no runtime dependency on `/Users/`, `C:\\`, or a specific `/home/<user>/` path.

These results are not assigned to any GitHub-hosted environment.

## Dependency advisories

A separate local Python 3.9 resolved-environment audit identified advisories that were not suppressed:

- `pydicom 2.4.4`: `PYSEC-2026-2266`; reported fixed version `3.0.2`;
- `Pillow 11.3.0`: 2026 advisories reported fixed across Pillow 12.1 through 12.3 releases.

These versions were selected in the Python 3.9 environment, and newer fixed releases may have different minimum-Python requirements. The hosted Python 3.11 security job did not execute, so hosted dependency status remains unvalidated. Remediation requires compatibility evaluation and an explicit dependency policy change; no advisory was ignored or suppressed merely to make CI green.

## Branch protection

The repository documentation specifies `ASCEND cross-platform portability gate` as the stable required check. No GitHub branch-protection setting was changed. The branch-protection API returned HTTP 403 with `Upgrade to GitHub Pro or make this repository public to enable this feature.` The aggregate job has also never executed, so there is no successful stable check context to require yet.

## Portability flags

None of the Phase 2 portability flags are assigned because no GitHub-hosted job executed:

- `SOURCE_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `DEPENDENCY_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `PYTHON_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `NUMERICAL_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `DICOM_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `PACKAGE_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `HEADLESS_GUI_PORTABILITY_VALIDATED` — NOT ASSIGNED
- `HEADLESS_3D_PORTABILITY_VALIDATED` — NOT ASSIGNED

The following later-phase flags are intentionally not assigned: `INTERACTIVE_GUI_PORTABILITY_VALIDATED`, `GPU_RENDERING_VALIDATED`, `FULL_LINUX_DEPLOYMENT_VALIDATED`, and `CLINICAL_DEPLOYMENT_VALIDATED`.

## Required external resolution

The GitHub account owner must restore payment authorization or otherwise restore eligible GitHub Actions usage. After that account-level condition is cleared, run `.github/workflows/tests.yml` at the evidence commit or its reviewed successor and retain every matrix report, comparison artifact, package report, and job log. Phase 2 remains incomplete until every acceptance gate passes on actual GitHub-hosted runners.
