"""Presentation and workflow preferences that do not alter locked scientific formulas."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SUPPORTING_OUTPUT_CATEGORIES = {
    "coverage": (
        "high_dose_coverage_context",
        "high_dose_volume_fraction_context",
    ),
    "peak_valley": ("peak_valley_dose_context", "ratio_context"),
    "per_vertex": ("vertex_analysis", "per_vertex_qa", "vertex_connections", "global_fwhm_summary"),
    "protocol_native": ("protocol_native_endpoint_status", "protocol_native_metrics"),
    "oar_geometry": ("oar_vertex_geometry",),
    "integrity": ("metric_descriptors", "integrity_and_interpretability_qa"),
}
DEFAULT_SUPPORTING_OUTPUT_CATEGORIES = tuple(SUPPORTING_OUTPUT_CATEGORIES)


def selected_supporting_outputs(
    payload: dict[str, Any],
    enabled: bool,
    categories: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Filter a stored supporting payload for display/export without recalculation."""
    if not enabled or not payload:
        return {}
    selected = set(categories)
    keys = {key for category in selected for key in SUPPORTING_OUTPUT_CATEGORIES.get(category, ())}
    return {
        key: value for key, value in payload.items()
        if key in {"schema_version", "derivation"} or key in keys
    }


def normalise_vertex_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise historical presentation aliases without deriving missing values."""
    output: list[dict[str, Any]] = []
    for item in records:
        record = dict(item)
        if record.get("volume_cc") is None:
            for alias in ("vertex_volume_cc", "dose_sampled_volume_cc", "volume"):
                if record.get(alias) is not None:
                    record["volume_cc"] = record[alias]
                    break
        output.append(record)
    return output


def _endpoint_id(role: str, kind: str, value: float) -> str:
    role_id = role.lower()
    value_id = f"{value:g}".replace(".", "p")
    if kind == "d_percent":
        label = f"d{value_id}"
    elif kind == "coverage_relative_rx":
        label = f"v{value * 100:g}rx".replace(".", "p")
    else:
        label = f"v{value_id}gy"
    return re.sub(r"[^a-z0-9_]+", "_", f"{role_id}_{label}")


def protocol_endpoint_record(role: str, kind: str, value: float, source: str = "user_selected") -> dict[str, Any]:
    """Handle protocol endpoint record for the enclosing ASCEND workflow."""
    return {"id": _endpoint_id(role, kind, value), "role": role, "kind": kind, "value": value, "source": source}


def eclipse_endpoint_suggestions(
    source: str | Path,
    structure_roles: dict[str, str | list[str]],
    expected_patient_id: str | None = None,
    expected_plan: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Map supported supplied Eclipse endpoints to Layer 2.1 configuration records."""
    from ascend.validation.eclipse_harness.reference_import import import_eclipse_reference

    imported = import_eclipse_reference(
        source,
        structure_roles=structure_roles,
        expected_patient_id=expected_patient_id,
        expected_plan=expected_plan,
    )
    suggestions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    supplied_records = [
        reference.to_dict() if hasattr(reference, "to_dict") else dict(reference)
        for reference in imported.get("records", [])
    ]
    for record in supplied_records:
        if record.get("import_status") != "valid":
            continue
        role = str(record.get("structure_role") or "")
        if role not in {"GTV", "T_L", "VTV_H", "VTV_L"}:
            continue
        endpoint_type = record.get("endpoint_type")
        semantics = record.get("provenance", {}).get("endpoint_semantics", {})
        if endpoint_type == "dose_at_volume":
            kind = "d_percent"
            value = float(semantics["volume_percent"])
        elif endpoint_type == "volume_at_prescription":
            kind = "coverage_relative_rx"
            value = float(semantics["prescription_percent"]) / 100.0
        elif endpoint_type == "volume_at_absolute_dose":
            kind = "coverage_absolute_gy"
            value = float(semantics["dose_threshold_gy"])
        else:
            continue
        key = (role, kind, value)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append({
            "id": _endpoint_id(role, kind, value),
            "role": role,
            "kind": kind,
            "value": value,
            "source": "eclipse_reference_auto_fill",
            "eclipse_endpoint": record.get("endpoint"),
            "eclipse_structure": record.get("roi_name"),
            "source_content_hash": record.get("source_content_hash"),
        })
    suggestions.sort(key=lambda item: (item["role"], item["kind"], float(item["value"])))
    summary = {
        "schema_version": imported.get("schema_version"),
        "format": imported.get("format"),
        "source_description": imported.get("source_description"),
        "supplied_record_count": len(imported.get("records", [])),
        "auto_filled_endpoint_count": len(suggestions),
        "supplied_records": supplied_records,
        "issues": imported.get("issues", []),
    }
    return suggestions, summary


def merge_endpoint_suggestions(
    configured: list[dict[str, Any]], suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retain user choices and add only semantically new imported endpoints."""
    output = [dict(item) for item in configured]
    semantic = {
        (str(item.get("role")), str(item.get("kind")), float(item.get("value")))
        for item in output
    }
    ids = {str(item.get("id")) for item in output}
    for suggestion in suggestions:
        key = (suggestion["role"], suggestion["kind"], float(suggestion["value"]))
        if key in semantic:
            continue
        item = dict(suggestion)
        base = str(item["id"])
        suffix = 2
        while item["id"] in ids:
            item["id"] = f"{base}_{suffix}"
            suffix += 1
        output.append(item)
        semantic.add(key)
        ids.add(str(item["id"]))
    return output
