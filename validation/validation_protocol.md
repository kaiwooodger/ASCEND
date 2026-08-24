# ASCEND retrospective validation protocol

## Frozen boundary

The retrospective cohort must use one tagged release and one frozen acceptance-criteria version. Every result must record the ASCEND version, Git commit, configuration hash, selected DICOM chain, input hashes, and biological parameter-set identities.

## Required pre-analysis checks

1. Verify the release tag resolves to the recorded commit.
2. Verify all required GitHub status checks passed on that commit.
3. Install from the tagged source or its GitHub release wheel in a clean environment.
4. Run the complete automated regression and formal-validation suites.
5. Confirm the case contains one explicitly selected UID-resolved DICOM chain.
6. Confirm prescriptions, fractionation, structure identities, dose context, and treatment components.
7. Preserve the generated configuration hash and input hashes before interpretation.

## Agreement criteria

The prospective software-agreement limits are stored in `validation/acceptance_criteria.json`. Criteria must not be altered after cohort results are inspected. A revised criterion requires a new version, rationale, pull request, release tag, and separate reporting of previously generated results.

## Clinical data boundary

Clinical DICOM, patient identifiers, screenshots, case manifests, logs, exports, masks, dose arrays, meshes, and patient-level result tables remain in the approved clinical environment. GitHub contains code, synthetic tests, non-identifiable validation contracts, and documentation only.

## Discrepancies

Every Eclipse/ASCEND discrepancy receives a private GitHub issue labelled `validation` and, where applicable, `bug`, `blocked`, or `clinical-case`. The issue stores no identifiable clinical content. Evidence remains in the clinical environment and is referenced by an opaque local audit identifier.

## Change control

No feature development occurs on the retrospective release tag. Corrections are implemented on a branch, reviewed through a pull request, validated, and released under a new tag. Existing analyses are never silently overwritten.
