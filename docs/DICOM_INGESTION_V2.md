# ASCEND DICOM ingestion v2

## Chain resolution

`ASCEND-DICOM-inventory-v2` records every discovered RTPLAN, RTSTRUCT, RTDOSE, and classic planning-image object. Candidate chains are resolved from RTDOSE→RTPLAN, RTPLAN→RTSTRUCT, and RTSTRUCT→image-series references. Contour-level referenced image SOP Instance UIDs are retained where supplied.

Every chain has independent `validity_status` and `selection_status` fields. A unique complete chain is selected automatically. Several complete chains remain `selection_required`. An incomplete chain can be selected only with `--allow-incomplete-chain --override-reason "..."` when patient and Frame of Reference identities agree. Layer 1 is blocked until selection is resolved. Chain IDs are hashes of canonical UID payloads and contain no patient or structure text.

## ROI identity and inventory

The authoritative structure key is the pair `rtstruct_sop_instance_uid` and `roi_number`. `structure_bindings`, `validation_structures`, and OAR configuration persist these identities. Legacy names are resolved exactly once against the selected RTSTRUCT; absent or duplicate names reject migration. Selecting another RTSTRUCT invalidates the old bindings.

The Layer 1 manifest keeps a complete `roi_inventory` separate from calculated results. Every ROI records its identity, original name, generation algorithm, contour availability/count/types, referenced contour-image SOP UIDs, canonical mapping, selection reason, and one of:

- `rasterised`: selected and successfully represented on native RTDOSE;
- `not_rasterised`: intentionally unselected, with no DVH, volume, or dose record;
- `rasterisation_failed`: selected but not processable, with no invented metric record and a blocking finding.

## Strict RTDOSE geometry

Layer 1 requires Rows, Columns, NumberOfFrames, ImagePositionPatient, ImageOrientationPatient, PixelSpacing, GridFrameOffsetVector for multi-frame dose, DoseGridScaling, DoseUnits, complete pixel metadata, and decoded dimensions matching declared dimensions. Missing, non-finite, non-positive, or inconsistent values are rejected without defaults.

The manifest stores these tolerances:

| Comparison | Tolerance |
|---|---:|
| Orientation vector norm error | `1e-4` |
| Orientation orthogonality dot product | `1e-4` |
| Orientation components between images | `1e-4` each |
| Position and frame offset | `1e-3 mm` |
| Spacing | max(`1e-3 mm`, `1e-4` relative) |

Relative Grid Frame Offset Vector values beginning at zero and permitted axial absolute patient-Z values are supported. Frame offsets remain in source pixel-frame order. Each normalized frame records `source_frame_index`; no offsets are independently sorted.

Uniform anisotropic grids are valid for Layers 1 and 2.1. Non-uniform frame spacing blocks Layer 1. Layer 2.2 returns `outside_validated_scope` for anisotropy and for isotropic spacing outside the locked 1 mm/2 mm values. CLI exit code 3 denotes this scope result while retaining successful upstream outputs.

## Selective rasterisation

Only required role bindings, individual vertices, configured OARs, and explicit validation structures are copied into the calculation RTSTRUCT. Each canonical structure is rasterised, transferred, evaluated, staged, and released before the next structure. Deterministic unions are retained when several identities map to one canonical structure. The NPZ handoff uses sorted keys and normalized ZIP metadata.

## Cache and publication

Layer 1 cache entries are case-local under `cache/layer1/<opaque-key>/`. Keys include input hashes and relevant UIDs, chain selection, identity-bound selections, Layer 1 configuration, Eclipse reference hashes, and independent schema/algorithm versions. PHI and readable ROI names are excluded.

Entries are atomically published, read-only after publication, and hash-verified before reuse. Corrupt or incomplete entries are rejected. Formal runs are materialized by reflink where supported or independent `copy2`; hard links are never used. Destination hashes are verified. Clearing cache cannot remove formal validated runs.

Formal Layer 1 runs use sibling staging directories, file and directory `fsync`, and atomic rename. `ascend_case.json` is updated only after a complete run is visible. Abandoned `.tmp-*` directories are removed on later startup.

Independent provenance fields are `software_version`, `dicom_inventory_schema_version`, `layer1_result_schema_version`, `layer1_algorithm_version`, `geometry_normalisation_version`, `rasterisation_algorithm_version`, and `cache_schema_version`.

## Interfaces

```bash
python3 -m ascend.cli discover /path/to/eclipse-export
python3 -m ascend.cli run /path/to/eclipse-export --case-root /path/to/case --config config.json --chain-id chain_...
python3 -m ascend.cli run /path/to/eclipse-export --case-root /path/to/case --config config.json --chain-id chain_... --allow-incomplete-chain --override-reason "documented export omission"
python3 -m ascend.cli cache-inspect /path/to/case/ascend_case.json
python3 -m ascend.cli cache-clear /path/to/case/ascend_case.json --confirm
```

Qt provides chain selection, override reason, cache inspection, and confirmed clearing. The browser adapter exposes `/api/select-chain`, `/api/cache/inspect`, and `/api/cache/clear`.
