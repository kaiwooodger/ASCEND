## Scope

Describe the bounded change and affected ASCEND layers.

## Scientific impact

- [ ] No scientific calculation changes
- [ ] Scientific changes are explicitly identified and independently validated
- [ ] Locked source hashes remain unchanged

## Verification

- [ ] Ubuntu, Windows, and macOS Python 3.11/3.12 matrix
- [ ] Minimum Python 3.9 compatibility
- [ ] Cross-platform frozen-reference and numerical-equivalence gate
- [ ] DICOM path and geometry portability tests
- [ ] PySide6 and CAD/STL offscreen rendering smoke tests
- [ ] Layer 1 formal validation
- [ ] Layer 2.1 and Layer 2.2 validation
- [ ] Layer 3.1 validation
- [ ] Layer 3.1D TCP validation
- [ ] Export schema and provenance tests
- [ ] Static quality, dependency audit, source security, and package checks

## Data boundary

- [ ] No DICOM, patient identifiers, clinical screenshots, identifiable tables, logs, caches, masks, dose arrays, meshes, or patient-level outputs
- [ ] Only synthetic non-clinical fixtures are included

## Reproducibility

Record configuration/schema changes, parameter-set IDs, acceptance-criteria changes, and expected invalidation behaviour.
