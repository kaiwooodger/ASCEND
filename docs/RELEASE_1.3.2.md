# ASCEND 1.3.2 — Layer 3.1 blank-canvas reset

Reset date: 20 August 2026, Australia/Sydney.

## Active scientific scope

- Layer 1: retained.
- Layer 2.1: retained.
- Layer 2.2: retained.
- Layer 3.1: `not_implemented`; blank canvas.
- Layer 3.2: `not_implemented`; unavailable because the former Layer 3.1 dependency was removed.

## Removed Layer 3.1 material

- P/Q, BED and EQD2 calculations.
- Fraction-event reconstruction used by Layer 3.1.
- High-dose survival, EUD and modelled therapeutic-ratio calculations.
- C1–C3 and N1–N3 sensitivity models.
- ROI biological summaries and histograms.
- Parameter editors and sensitivity-sweep controls.
- Two-dimensional and three-dimensional biological viewers.
- Biological mesh and result exports.
- Layer 3.1 CLI and validation commands.
- Dedicated tests, benchmarks and implementation documentation.
- Stored results and case-local caches.

Older case files are migration-safe: removed Layer 3.1 configuration keys are discarded, stored Layer 3.1 results are not loaded, and the active case record is reset to `not_implemented` with no result payload.

## Recovery

The removed source and artifacts were moved to:

```text
<local-archive>/ASCEND_Layer3_1_removed_20260820_162500
```

This archive is approximately 1.1 GB and includes the previous source, tests, documentation, benchmark, case manifests, derived results and cache.

## Verification

```text
139 passed in 3.04s
```

The surviving regression suite covers the physical workflow, DICOM ingestion, UID chain resolution, geometry, ROI identity, cache, Layer 2.1, Layer 2.2, Qt workstation, exports, and the explicit Layer 3 blank-canvas failure state.
