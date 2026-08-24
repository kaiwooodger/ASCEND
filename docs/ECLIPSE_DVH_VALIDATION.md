# Formal Eclipse DVH comparison harness

ASCEND 0.6.x includes a validation-only harness that compares supplied Eclipse DVH endpoint values with endpoints already stored by the validated ASCEND pathway. It does not calculate new DVH endpoints and does not modify Layer 1, Layer 2.1, or Layer 2.2.

## Validation question

Given an Eclipse reference endpoint and the corresponding stored ASCEND endpoint from the same RTDOSE and ROI, how closely do the two values agree?

The acceptance criteria are software-agreement criteria. They are not clinical treatment-plan tolerances or protocol-compliance thresholds.

## Reference inputs

The preferred format is the canonical CSV shown in [eclipse_dvh_reference.example.csv](../configs/eclipse_dvh_reference.example.csv). Required columns are:

- `case_id`
- `roi_name`
- `endpoint`
- `value`
- `units`

Identity and context columns should be supplied whenever available:

- `rtstruct_uid`, `rtdose_uid`, `rtplan_uid`, `roi_number`
- `rx_gy`, `reference_volume_cc`, `structure_role`
- `eclipse_software`, `eclipse_version`

The importer also accepts one Eclipse cumulative-DVH TXT export or a directory of the existing Eclipse TXT exports. TXT exports without DICOM UIDs and ROI numbers use an explicitly recorded unique fallback; ambiguous names are excluded.

Supported endpoint syntax is `Dxx`, `Dmean`, `Vxx%Rx`, and `VxGy`, plus `Volume`. The planned initial endpoints are D2, D5, D50, D90, D95, D98, Dmean, V95%Rx, and V100%Rx. A supplied endpoint is never calculated merely because it appears in this list. If the locked ASCEND result did not store it, the comparison status is `missing_ascend_endpoint`.

## Matching precedence

1. Exact RTSTRUCT SOP Instance UID and ROI number.
2. Unique ROI number or validated ASCEND role mapping when complete reference identity is unavailable.
3. Unique name-only fallback, explicitly flagged in the comparison record.

Ambiguous, missing, and identity-conflicting matches are retained as exclusions and are not included in numerical statistics.

## Acceptance criteria

The versioned defaults are stored in [eclipse_dvh_acceptance_v1.json](../configs/eclipse_dvh_acceptance_v1.json):

- Dose endpoints: absolute difference at most `max(0.2 Gy, 0.02 × |Eclipse dose|)`.
- Percentage volume-at-dose endpoints: absolute difference at most 1.0 percentage point.
- Structure volume: absolute difference at most `max(0.1 cc, 0.02 × Eclipse volume)`.

Structure-size bins are explicit validation strata only and have no clinical meaning.

## CLI

```bash
cd /path/to/ASCEND_PROJECT
PYTHONPATH=. /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 -m ascend.cli \
  validate-eclipse-dvh \
  --case /path/to/ascend_case.json \
  --reference /path/to/eclipse-reference.csv \
  --output /path/to/validation/eclipse_dvh/results \
  --criteria configs/eclipse_dvh_acceptance_v1.json
```

The command exits `0` after a completed run with no failed valid comparisons, `4` after a completed run containing agreement failures, and `2` when the run cannot be constructed.

## Outputs

- `eclipse_dvh_comparisons.json`
- `eclipse_dvh_comparisons.csv`
- `eclipse_dvh_summary.json`
- `eclipse_dvh_summary.csv`
- `eclipse_dvh_bland_altman.csv`
- `ECLIPSE_DVH_VALIDATION_REPORT.md`
- `eclipse_dvh_validation_run.json`

CSV and Markdown files are presentation derivatives of the canonical structured validation run. Reporting does not recalculate endpoints.
