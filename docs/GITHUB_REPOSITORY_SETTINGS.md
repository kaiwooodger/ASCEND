# Required private-repository settings

These controls are applied in GitHub after the first push. They cannot be enforced by repository files before a remote repository exists.

## Visibility and access

- Repository visibility: private.
- Restrict repository access to authorised retrospective-study personnel.
- Enable private vulnerability reporting.
- Disable public forking.
- Do not enable GitHub Pages for clinical outputs.

## Main branch ruleset

Create a branch ruleset targeting `main` with:

- Require a pull request before merging.
- Require one approval.
- Dismiss stale approvals when new commits are pushed.
- Require review from Code Owners.
- Require conversation resolution.
- Require branches to be up to date for every merge into `main`.
- Block force pushes and deletion.
- Do not allow bypass except a documented emergency administrator pathway.

Require these status checks exactly:

- `ASCEND cross-platform portability gate`
- `Branch naming policy`
- `PR source/base branch policy`
- `Layer 1 formal validation`
- `Layer 2.1 validation`
- `Layer 2.2 validation`
- `Layer 3.1 validation`
- `Layer 3.1D TCP validation`
- `Export schema`
- `Provenance tests`
- `Synthetic formal-validation commands`

Branch naming and source/base requirements are defined in [CONTRIBUTOR_WORKFLOW.md](CONTRIBUTOR_WORKFLOW.md) and should be enabled as required checks before merge.

`ASCEND cross-platform portability gate` is the stable aggregate check. It depends on static quality, security, the Python 3.9 minimum gate, all Ubuntu/Windows/macOS Python 3.11/3.12 jobs, numerical equivalence, and isolated package validation on all three operating systems. Require the aggregate rather than transient matrix-cell names.

## Labels

Create or synchronise the labels defined in `.github/labels.yml`: `validation`, `bug`, `clinical-case`, `radiobiology`, `documentation`, `enhancement`, and `blocked`.

## Retrospective release

After all required checks pass on `main`, create the immutable tag:

```bash
git tag -s v1.3.5-retrospective -m "ASCEND 1.3.5 retrospective-analysis freeze"
git push origin v1.3.5-retrospective
```

The release workflow rejects a tag that does not match the package version, reruns verification, builds the wheel and source archive, creates SHA-256 checksums, and publishes the artifacts to the private GitHub release.

## Analysis rule

Every retrospective result must record:

```text
ascend_version: 1.3.5
git_commit: <40-character SHA>
configuration_hash: <SHA-256>
parameter_set_ids: [<versioned IDs>]
```

The cohort must remain on the frozen tag. Any correction requires a separate branch, pull request, validation cycle, and release tag.

## Automation tooling reliability controls

Apply the automation controls in [CONTRIBUTOR_WORKFLOW.md](CONTRIBUTOR_WORKFLOW.md):
- pinned `code_review` model and fallback order
- required `tools/tooling_preflight.py` before automated review
- canonical automation push/report path and token scope expectations
- retry and escalation runbook for transient auth failures
- mandatory manual-review + `codeql_checker` fallback when `code_review` returns HTTP 400
