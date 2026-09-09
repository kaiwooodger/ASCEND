"""Bind imported TPS DVHs to the only RTSTRUCT ROIs ASCEND may rasterise."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ascend.dicom.roi import identity_key, rtstruct_roi_lookup


REQUIRED_DVH_ENDPOINTS = frozenset({"D2", "D95"})
DVH_ELIGIBILITY_SCHEMA_VERSION = "ASCEND-TPS-DVH-ROI-eligibility-v1"


def _normalise(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def _record_dict(value: Any) -> dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def imported_reference_records(imported: dict[str, Any]) -> list[dict[str, Any]]:
    """Return imported reference records in one serialisable representation."""
    return [_record_dict(item) for item in imported.get("records", [])]


def _structure_group_key(record: dict[str, Any]) -> tuple[Any, ...]:
    case_id = _normalise(str(record.get("case_id") or ""))
    uid = str(record.get("rtstruct_uid") or "")
    number = record.get("roi_number")
    if uid and number is not None:
        return case_id, "identity", uid, int(number)
    return case_id, "name", _normalise(str(record.get("roi_name") or ""))


def require_core_dvh_endpoints(imported: dict[str, Any]) -> None:
    """Reject every imported structure DVH that lacks valid D2 or D95 data."""
    records = imported_reference_records(imported)
    if not records:
        raise ValueError("TPS_DVH_EMPTY: the imported source contains no usable structure DVH records.")
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(_structure_group_key(record), []).append(record)

    failures: list[str] = []
    for group in grouped.values():
        valid_endpoints = {
            str(item.get("endpoint") or "")
            for item in group
            if item.get("import_status") == "valid"
        }
        missing = sorted(REQUIRED_DVH_ENDPOINTS - valid_endpoints)
        if missing:
            name = str(group[0].get("roi_name") or f"ROI {group[0].get('roi_number')}")
            failures.append(f"{name}: missing valid {', '.join(missing)}")
    if failures:
        raise ValueError(
            "TPS_DVH_REQUIRED_ENDPOINTS: every imported structure DVH must contain valid D2% and D95% dose "
            f"endpoints; {'; '.join(failures)}."
        )


def require_core_normalised_metrics(metrics: list[dict[str, Any]]) -> None:
    """Apply the D2/D95 contract to normalised Eclipse text metrics."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for metric in metrics:
        grouped.setdefault(_normalise(str(metric.get("original_structure") or "")), []).append(metric)
    if not grouped:
        raise ValueError("TPS_DVH_EMPTY: the imported source contains no usable structure DVH metrics.")
    failures: list[str] = []
    for group in grouped.values():
        supplied = {str(item.get("metric") or "") for item in group}
        missing = sorted(REQUIRED_DVH_ENDPOINTS - supplied)
        if missing:
            failures.append(f"{group[0].get('original_structure')}: missing valid {', '.join(missing)}")
    if failures:
        raise ValueError(
            "TPS_DVH_REQUIRED_ENDPOINTS: every imported structure DVH must contain valid D2% and D95% dose "
            f"endpoints; {'; '.join(failures)}."
        )


def bind_imported_dvh_rois(imported: dict[str, Any], structure: Any) -> list[dict[str, Any]]:
    """Resolve each complete imported DVH to one immutable RTSTRUCT ROI identity."""
    require_core_dvh_endpoints(imported)
    records = imported_reference_records(imported)
    rtstruct_uid = str(getattr(structure, "SOPInstanceUID", ""))
    by_number, _by_name = rtstruct_roi_lookup(structure)
    by_normalised_name: dict[str, list[int]] = {}
    for number, name in by_number.items():
        by_normalised_name.setdefault(_normalise(name), []).append(number)

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(_structure_group_key(record), []).append(record)

    output: list[dict[str, Any]] = []
    resolved_numbers: dict[int, str] = {}
    for group in grouped.values():
        first = group[0]
        imported_name = str(first.get("roi_name") or "").strip()
        group_names = {_normalise(str(item.get("roi_name") or "")) for item in group}
        if len(group_names) != 1:
            raise ValueError(
                "TPS_DVH_STRUCTURE_IDENTITY_CONFLICT: one imported ROI identity uses multiple structure names: "
                f"{sorted(str(item.get('roi_name') or '') for item in group)}."
            )
        supplied_uid = str(first.get("rtstruct_uid") or "").strip()
        supplied_number = first.get("roi_number")
        if bool(supplied_uid) != (supplied_number is not None):
            raise ValueError(
                "TPS_DVH_IDENTITY_INCOMPLETE: imported DVH identity must supply both RTSTRUCT UID and ROI "
                f"number, or neither, for {imported_name!r}."
            )
        if supplied_uid and supplied_number is not None:
            if supplied_uid != rtstruct_uid:
                raise ValueError(
                    "TPS_DVH_RTSTRUCT_IDENTITY: imported DVH for "
                    f"{imported_name!r} references RTSTRUCT {supplied_uid}, not selected RTSTRUCT {rtstruct_uid}."
                )
            number = int(supplied_number)
            if number not in by_number:
                raise ValueError(
                    f"TPS_DVH_ROI_NOT_FOUND: imported DVH for {imported_name!r} references absent ROI {number}."
                )
            method = "rtstruct_uid_and_roi_number"
        else:
            matches = by_normalised_name.get(_normalise(imported_name), [])
            if len(matches) != 1:
                state = "not found" if not matches else f"ambiguous across ROI numbers {sorted(matches)}"
                raise ValueError(
                    f"TPS_DVH_ROI_MATCH: imported DVH structure {imported_name!r} is {state} in the selected RTSTRUCT."
                )
            number = matches[0]
            method = "unique_normalised_structure_name"
        previous = resolved_numbers.get(number)
        if previous is not None and _normalise(previous) != _normalise(imported_name):
            raise ValueError(
                f"TPS_DVH_ROI_MATCH: multiple imported DVH structures resolve to RTSTRUCT ROI {number}: "
                f"{previous!r} and {imported_name!r}."
            )
        resolved_numbers[number] = imported_name
        valid_endpoints = sorted({
            str(item.get("endpoint")) for item in group if item.get("import_status") == "valid"
        })
        output.append({
            "rtstruct_sop_instance_uid": rtstruct_uid,
            "roi_number": number,
            "display_name": by_number[number],
            "dvh_structure_name": imported_name,
            "dvh_verification_status": "verified",
            "required_endpoints": sorted(REQUIRED_DVH_ENDPOINTS),
            "supplied_endpoints": valid_endpoints,
            "binding_method": method,
            "source_content_hashes": sorted({
                str(item.get("source_content_hash"))
                for item in group if item.get("source_content_hash")
            }),
        })
    output.sort(key=lambda item: int(item["roi_number"]))
    return output


def load_and_bind_dvh_rois(
    source: str | Path,
    structure: Any,
    *,
    structure_roles: dict[str, str | list[str]] | None = None,
    expected_patient_id: str | None = None,
    expected_plan: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Import, validate, and identity-bind one TPS DVH source."""
    from ascend.validation.eclipse_harness.reference_import import import_eclipse_reference

    imported = import_eclipse_reference(
        source,
        structure_roles=structure_roles or {},
        expected_patient_id=expected_patient_id,
        expected_plan=expected_plan,
    )
    records = imported_reference_records(imported)
    case_ids = sorted({_normalise(str(item.get("case_id") or "")) for item in records if item.get("case_id")})
    if expected_patient_id and case_ids and case_ids != [_normalise(expected_patient_id)]:
        raise ValueError(
            f"TPS_DVH_PATIENT_IDENTITY: imported Patient IDs {case_ids} do not match ASCEND case "
            f"{expected_patient_id!r}."
        )
    return imported, bind_imported_dvh_rois(imported, structure)


def verified_identity_keys(records: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Return identities carrying explicit TPS-DVH verification evidence."""
    return {
        identity_key(item)
        for item in records
        if item.get("dvh_verification_status") == "verified"
    }
