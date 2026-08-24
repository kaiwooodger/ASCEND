"""Strict import of canonical CSV and Eclipse text reference endpoints."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
from typing import Any

from ascend.validation.eclipse_dvh import normalise_eclipse_dvh_source

from .schemas import REFERENCE_SCHEMA_VERSION, ReferenceImportError, ReferenceRecord


REQUIRED_CSV_COLUMNS = {"case_id", "roi_name", "endpoint", "value", "units"}


def sha256_file(path: str | Path) -> str:
    """Handle sha256 file for the enclosing ASCEND workflow."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _integer(value: Any, field: str, row_number: int) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        number = int(text)
    except ValueError as exc:
        raise ReferenceImportError(f"Row {row_number}: {field} must be an integer, received {text!r}.") from exc
    if number <= 0:
        raise ReferenceImportError(f"Row {row_number}: {field} must be greater than zero.")
    return number


def _number(value: Any, field: str, row_number: int, *, required: bool = False) -> tuple[float | None, str | None]:
    text = _text(value)
    if text is None:
        if required:
            raise ReferenceImportError(f"Row {row_number}: {field} is required.")
        return None, None
    try:
        number = float(text)
    except ValueError as exc:
        raise ReferenceImportError(f"Row {row_number}: {field} must be numeric, received {text!r}.") from exc
    if not math.isfinite(number):
        return None, f"{field} is non-finite"
    return number, None


def endpoint_definition(endpoint: str) -> tuple[str, str, dict[str, float | str | None]]:
    """Handle endpoint definition for the enclosing ASCEND workflow."""
    compact = re.sub(r"\s+", "", endpoint).upper()
    if compact == "DMEAN":
        return "Dmean", "dose_statistic", {}
    match = re.fullmatch(r"D(\d+(?:\.\d+)?)", compact)
    if match:
        percentage = float(match.group(1))
        if percentage < 0 or percentage > 100:
            raise ReferenceImportError(f"Unsupported dose-at-volume endpoint {endpoint!r}: percentage must be 0 to 100.")
        label = f"D{percentage:g}"
        return label, "dose_at_volume", {"volume_percent": percentage}
    match = re.fullmatch(r"V(\d+(?:\.\d+)?)%?RX", compact)
    if match:
        percentage = float(match.group(1))
        if percentage <= 0:
            raise ReferenceImportError(f"Unsupported prescription-relative endpoint {endpoint!r}.")
        return f"V{percentage:g}%Rx", "volume_at_prescription", {"prescription_percent": percentage}
    match = re.fullmatch(r"V(\d+(?:\.\d+)?)GY", compact)
    if match:
        dose_gy = float(match.group(1))
        if dose_gy <= 0:
            raise ReferenceImportError(f"Unsupported absolute-dose volume endpoint {endpoint!r}.")
        return f"V{dose_gy:g}Gy", "volume_at_absolute_dose", {"dose_threshold_gy": dose_gy}
    if compact in {"VOLUME", "STRUCTUREVOLUME"}:
        return "Volume", "structure_volume", {}
    raise ReferenceImportError(f"Unsupported Eclipse DVH endpoint {endpoint!r}.")


def _normalise_units(
    endpoint_type: str,
    units: str,
    value: float | None,
    row_number: int,
) -> tuple[str, float | None, str]:
    compact = units.strip().lower().replace(" ", "").replace("³", "3")
    if endpoint_type in {"dose_at_volume", "dose_statistic"}:
        if compact == "gy":
            return "Gy", value, "direct_gy"
        if compact == "cgy":
            return "Gy", None if value is None else value / 100.0, "cgy_to_gy"
        raise ReferenceImportError(f"Row {row_number}: dose endpoint units must be Gy or cGy, received {units!r}.")
    if endpoint_type in {"volume_at_prescription", "volume_at_absolute_dose"}:
        if compact in {"%", "percent", "pct"}:
            return "%", value, "direct_percentage"
        raise ReferenceImportError(f"Row {row_number}: volume-at-dose endpoint units must be %, received {units!r}.")
    if compact in {"cc", "cm3", "ml"}:
        return "cc", value, "direct_volume"
    raise ReferenceImportError(f"Row {row_number}: structure-volume units must be cc, cm3, or mL, received {units!r}.")


def _reference_status(
    endpoint_type: str,
    value: float | None,
    numeric_issue: str | None,
    rx_gy: float | None,
    rx_issue: str | None,
) -> tuple[str, str | None]:
    if numeric_issue:
        return "invalid_reference", numeric_issue
    if value is None:
        return "invalid_reference", "Eclipse reference value is missing"
    if endpoint_type in {"dose_at_volume", "dose_statistic", "structure_volume"} and value < 0:
        return "invalid_reference", "Eclipse reference value is negative"
    if endpoint_type.startswith("volume_at_") and not 0 <= value <= 100:
        return "invalid_reference", "Eclipse percentage volume is outside 0 to 100"
    if endpoint_type == "structure_volume" and value <= 0:
        return "invalid_reference", "Eclipse structure volume must be greater than zero"
    if rx_issue:
        return "invalid_reference", rx_issue
    if rx_gy is not None and rx_gy <= 0:
        return "invalid_reference", "Prescription dose must be greater than zero"
    return "valid", None


def _duplicate_key(record: ReferenceRecord) -> tuple[Any, ...]:
    identity = (record.rtstruct_uid, record.roi_number) if record.rtstruct_uid and record.roi_number else (
        None, re.sub(r"[^A-Z0-9]+", "", record.roi_name.upper()),
    )
    return record.case_id, identity, record.endpoint, record.rx_gy


def _check_duplicates(records: list[ReferenceRecord]) -> None:
    seen: dict[tuple[Any, ...], int] = {}
    for index, record in enumerate(records, 2):
        key = _duplicate_key(record)
        if key in seen:
            raise ReferenceImportError(
                f"Duplicate Eclipse reference row for case={record.case_id!r}, ROI={record.roi_name!r}, "
                f"endpoint={record.endpoint!r}; first row={seen[key]}, duplicate row={index}."
            )
        seen[key] = index


def _add_supplied_volume_references(records: list[ReferenceRecord]) -> list[ReferenceRecord]:
    groups: dict[tuple[Any, ...], list[ReferenceRecord]] = {}
    for record in records:
        identity = (record.rtstruct_uid, record.roi_number) if record.rtstruct_uid and record.roi_number else (
            None, re.sub(r"[^A-Z0-9]+", "", record.roi_name.upper()),
        )
        groups.setdefault((record.case_id, identity), []).append(record)
    added: list[ReferenceRecord] = []
    for group in groups.values():
        if any(item.endpoint == "Volume" for item in group):
            continue
        supplied = sorted({float(item.reference_volume_cc) for item in group if item.reference_volume_cc is not None})
        if not supplied:
            continue
        if len(supplied) > 1:
            raise ReferenceImportError(
                f"Conflicting reference_volume_cc values for case={group[0].case_id!r}, ROI={group[0].roi_name!r}: {supplied}"
            )
        template = group[0]
        added.append(replace(
            template,
            endpoint="Volume",
            endpoint_type="structure_volume",
            eclipse_value=supplied[0],
            units="cc",
            rx_gy=None,
            import_status="valid" if supplied[0] > 0 else "invalid_reference",
            import_reason=None if supplied[0] > 0 else "Reference volume must be greater than zero",
            provenance={
                **template.provenance,
                "endpoint_semantics": {},
                "reference_volume_endpoint_source": "explicit reference_volume_cc column",
            },
        ))
    return [*records, *added]


def import_canonical_csv(path: str | Path) -> dict[str, Any]:
    """Handle import canonical csv for the enclosing ASCEND workflow."""
    source = Path(path).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).isoformat()
    content_hash = sha256_file(source)
    with source.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_CSV_COLUMNS - columns)
        if missing:
            raise ReferenceImportError(f"Canonical Eclipse CSV is missing required columns: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        raise ReferenceImportError("Canonical Eclipse CSV contains no reference rows.")
    records: list[ReferenceRecord] = []
    for row_number, row in enumerate(rows, 2):
        case_id = _text(row.get("case_id"))
        roi_name = _text(row.get("roi_name"))
        endpoint_text = _text(row.get("endpoint"))
        units_text = _text(row.get("units"))
        if not case_id or not roi_name or not endpoint_text or not units_text:
            raise ReferenceImportError(
                f"Row {row_number}: case_id, roi_name, endpoint, value, and units require unambiguous values."
            )
        endpoint, endpoint_type, semantics = endpoint_definition(endpoint_text)
        value, numeric_issue = _number(row.get("value"), "value", row_number, required=True)
        units, value, conversion = _normalise_units(endpoint_type, units_text, value, row_number)
        rx_gy, rx_issue = _number(row.get("rx_gy"), "rx_gy", row_number)
        reference_volume, volume_issue = _number(row.get("reference_volume_cc"), "reference_volume_cc", row_number)
        import_status, import_reason = _reference_status(endpoint_type, value, numeric_issue, rx_gy, rx_issue)
        if volume_issue and import_status == "valid":
            import_status, import_reason = "invalid_reference", volume_issue
        if reference_volume is not None and reference_volume <= 0 and import_status == "valid":
            import_status, import_reason = "invalid_reference", "Reference volume must be greater than zero"
        records.append(ReferenceRecord(
            case_id=case_id,
            rtstruct_uid=_text(row.get("rtstruct_uid")),
            rtdose_uid=_text(row.get("rtdose_uid")),
            rtplan_uid=_text(row.get("rtplan_uid")),
            roi_number=_integer(row.get("roi_number"), "roi_number", row_number),
            roi_name=roi_name,
            endpoint=endpoint,
            endpoint_type=endpoint_type,
            eclipse_value=value,
            units=units,
            rx_gy=rx_gy,
            reference_volume_cc=reference_volume,
            structure_role=_text(row.get("structure_role")),
            eclipse_software=_text(row.get("eclipse_software")),
            eclipse_version=_text(row.get("eclipse_version")),
            source_file=str(source),
            source_content_hash=content_hash,
            import_timestamp_utc=timestamp,
            import_status=import_status,
            import_reason=import_reason,
            provenance={
                "reference_schema_version": REFERENCE_SCHEMA_VERSION,
                "source_row": row_number,
                "source_units": units_text,
                "unit_conversion": conversion,
                "endpoint_semantics": semantics,
            },
        ))
    records = _add_supplied_volume_references(records)
    _check_duplicates(records)
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "format": "ascend_eclipse_dvh_canonical_csv_v1",
        "source_description": f"Canonical Eclipse endpoint CSV: {source.name}",
        "source_files": [{"path": str(source), "sha256": content_hash}],
        "import_timestamp_utc": timestamp,
        "records": records,
        "issues": [],
    }


def _combined_hash(hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(hashes)).encode("ascii")).hexdigest()


def import_eclipse_text(
    source: str | Path,
    structure_roles: dict[str, str | list[str]],
    expected_patient_id: str | None = None,
    expected_plan: str | None = None,
) -> dict[str, Any]:
    """Handle import eclipse text for the enclosing ASCEND workflow."""
    imported = normalise_eclipse_dvh_source(
        source, structure_roles, expected_patient_id=expected_patient_id, expected_plan=expected_plan,
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    file_hashes = {item["name"]: item["sha256"] for item in imported["source_files"]}
    volumes = {
        re.sub(r"[^A-Z0-9]+", "", item["original_structure"].upper()): float(item["value"])
        for item in imported["metrics"] if item["metric"] == "Volume"
    }
    records: list[ReferenceRecord] = []
    skipped: list[dict[str, str]] = []
    for metric in imported["metrics"]:
        try:
            endpoint, endpoint_type, semantics = endpoint_definition(metric["metric"])
        except ReferenceImportError:
            skipped.append({
                "structure": metric["original_structure"],
                "endpoint": metric["metric"],
                "reason": "endpoint_not_in_formal_validation_scope",
            })
            continue
        source_names = list(metric.get("source_files", []))
        hashes = [file_hashes[name] for name in source_names if name in file_hashes]
        value = float(metric["value"])
        status, reason = _reference_status(endpoint_type, value, None, None, None)
        records.append(ReferenceRecord(
            case_id=str(imported["patient_id"]),
            rtstruct_uid=None,
            rtdose_uid=None,
            rtplan_uid=None,
            roi_number=None,
            roi_name=str(metric["original_structure"]),
            endpoint=endpoint,
            endpoint_type=endpoint_type,
            eclipse_value=value,
            units=str(metric["unit"]),
            reference_volume_cc=volumes.get(re.sub(r"[^A-Z0-9]+", "", metric["original_structure"].upper())),
            structure_role=None if metric.get("ascend_role") == "UNMAPPED_SUPPORTING" else metric.get("ascend_role"),
            eclipse_software="Eclipse",
            source_file="|".join(source_names),
            source_content_hash=_combined_hash(hashes),
            import_timestamp_utc=timestamp,
            import_status=status,
            import_reason=reason,
            provenance={
                "reference_schema_version": REFERENCE_SCHEMA_VERSION,
                "adapter": "existing_eclipse_cumulative_dvh_text",
                "plan": imported.get("plan"),
                "course": metric.get("course"),
                "source_units": metric.get("unit"),
                "unit_conversion": metric.get("preferred_conversion"),
                "endpoint_semantics": semantics,
                "corroborating_source_count": metric.get("all_source_count"),
            },
        ))
    _check_duplicates(records)
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "format": "eclipse_cumulative_dvh_text_adapter_v1",
        "source_description": f"Eclipse cumulative DVH text export: {Path(source).name}",
        "source_files": imported["source_files"],
        "import_timestamp_utc": timestamp,
        "records": records,
        "issues": [*imported.get("issues", []), *skipped],
        "adapter_summary": imported.get("summary", {}),
    }


def import_eclipse_reference(
    source: str | Path,
    *,
    structure_roles: dict[str, str | list[str]] | None = None,
    expected_patient_id: str | None = None,
    expected_plan: str | None = None,
) -> dict[str, Any]:
    """Handle import eclipse reference for the enclosing ASCEND workflow."""
    path = Path(source).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".csv":
        return import_canonical_csv(path)
    if path.is_dir() or (path.is_file() and path.suffix.lower() == ".txt"):
        return import_eclipse_text(
            path,
            structure_roles or {},
            expected_patient_id=expected_patient_id,
            expected_plan=expected_plan,
        )
    raise ReferenceImportError(
        "Reference must be a canonical Eclipse endpoint CSV, an Eclipse cumulative-DVH TXT file, "
        "or a directory containing Eclipse cumulative-DVH TXT exports."
    )
