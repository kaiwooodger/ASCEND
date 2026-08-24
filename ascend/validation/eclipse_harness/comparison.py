"""Independent comparison calculations for validation evidence."""

from __future__ import annotations

import math
from typing import Any

from ascend.models.case import ASCENDCase

from .matching import match_reference
from .schemas import AcceptanceCriteria, ReferenceRecord


AUDIT_ENDPOINTS = {
    "D2_Gy": "D2",
    "D5_Gy": "D5",
    "D50_Gy": "D50",
    "D90_Gy": "D90",
    "D95_Gy": "D95",
    "D98_Gy": "D98",
    "Dmean_Gy": "Dmean",
    "Volume_cc": "Volume",
}


def _prescription(case: ASCENDCase, role: str | None) -> tuple[float | None, str | None, str | None]:
    key = {"T_L": "Rx_L", "VTV_H": "Rx_H"}.get(str(role))
    if not key:
        return None, None, None
    prescription = case.configuration.prescriptions.get(key)
    if prescription is None:
        return None, None, key
    return prescription.gy, prescription.source, key


def _endpoint_index(case: ASCENDCase) -> dict[tuple[str, str], dict[str, Any]]:
    layer1 = case.layer1.result or {}
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in layer1.get("dvh_summary", []):
        structure = str(item.get("Structure"))
        for endpoint, field, units in (
            ("Volume", "Volume_cc", "cc"),
            ("D95", "DoseCover_D95_Gy", "Gy"),
            ("Dmean", "MeanDose_Gy", "Gy"),
        ):
            value = item.get(field)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                index[(structure, endpoint)] = {
                    "value": float(value), "units": units, "source": f"Layer 1 stored dvh_summary.{field}",
                }
    for item in layer1.get("dvh_audit", []):
        endpoint = AUDIT_ENDPOINTS.get(str(item.get("metric")))
        value = item.get("Layer1_calculated")
        if endpoint and isinstance(value, (int, float)) and math.isfinite(float(value)):
            structure = str(item.get("structure"))
            index[(structure, endpoint)] = {
                "value": float(value), "units": str(item.get("unit") or ("cc" if endpoint == "Volume" else "Gy")),
                "source": f"Layer 1 stored dvh_audit.{item.get('metric')}",
            }
    layer21 = case.layer2_1.result or {}
    metric_by_id = {str(item.get("metric_id")): item for item in layer21.get("harmonised_metrics", [])}
    manifest = layer1.get("manifest", {})
    for role, metric_id in (
        ("T_L", "peripheral_coverage_v95_rxl"),
        ("VTV_H", "high_dose_coverage_v95_rxh"),
    ):
        canonical = manifest.get("effective_structure_roles", {}).get(role)
        metric = metric_by_id.get(metric_id)
        if not canonical or not metric or metric.get("applicability") != "valid":
            continue
        value = metric.get("value")
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            rx_gy, rx_source, rx_key = _prescription(case, role)
            index[(str(canonical), "V95%Rx")] = {
                "value": float(value), "units": "%", "source": f"Stored locked Layer 2.1 metric {metric_id}",
                "rx_gy": rx_gy, "rx_source": rx_source, "rx_key": rx_key,
            }
    return index


def _diagnostic_context(case: ASCENDCase, candidate: dict[str, Any] | None) -> dict[str, Any]:
    layer1 = case.layer1.result or {}
    manifest = layer1.get("manifest", {})
    canonical = candidate.get("canonical_structure") if candidate else None
    summary = next((item for item in layer1.get("dvh_summary", []) if item.get("Structure") == canonical), {})
    audits = {
        item.get("metric"): item.get("Layer1_calculated")
        for item in layer1.get("dvh_audit", []) if item.get("structure") == canonical
    }
    mask = manifest.get("mask_export", {}).get("structures", {}).get(str(canonical), {})
    findings = [
        f"{item.get('level')}: {item.get('check')}: {item.get('detail')}"
        for item in layer1.get("findings", []) if item.get("level") != "PASS"
    ]
    return {
        "sampled_voxel_count": mask.get("voxel_count"),
        "dose_sampled_volume_cc": audits.get("DoseSampledVolume_cc"),
        "contour_stack_volume_cc": audits.get("AnatomicalVolumeContour_cc", summary.get("Volume_cc")),
        "ct_voxelised_volume_cc": audits.get("AnatomicalVolumeCT_cc"),
        "structure_volume_cc": summary.get("Volume_cc"),
        "dose_grid_spacing_mm": manifest.get("dose_grid", {}).get("voxel_spacing_mm"),
        "dose_grid_dimensions": manifest.get("dose_grid", {}).get("dimensions"),
        "rasterisation_status": candidate.get("rasterisation_status") if candidate else None,
        "mapping_status": candidate.get("mapping_status") if candidate else None,
        "layer1_warnings": findings,
    }


def _base_record(
    case: ASCENDCase,
    reference: ReferenceRecord,
    match: Any,
    criteria: AcceptanceCriteria,
) -> dict[str, Any]:
    manifest = (case.layer1.result or {}).get("manifest", {})
    candidate = match.candidate
    diagnostics = _diagnostic_context(case, candidate)
    structure_volume = diagnostics.get("structure_volume_cc") or reference.reference_volume_cc
    return {
        "case_id": reference.case_id,
        "roi_number": candidate.get("roi_number") if candidate else reference.roi_number,
        "roi_name": candidate.get("roi_name") if candidate else reference.roi_name,
        "reference_roi_name": reference.roi_name,
        "reference_volume_cc": reference.reference_volume_cc,
        "structure_identity": candidate.get("structure_identity") if candidate else reference.structure_identity,
        "canonical_structure": candidate.get("canonical_structure") if candidate else None,
        "structure_role": candidate.get("structure_role") if candidate else reference.structure_role,
        "structure_size_class": criteria.size_class(float(structure_volume) if structure_volume is not None else None),
        "endpoint": reference.endpoint,
        "endpoint_type": reference.endpoint_type,
        "ascend_value": None,
        "eclipse_value": reference.eclipse_value,
        "units": reference.units,
        "delta": None,
        "absolute_delta": None,
        "relative_delta_percent": None,
        "delta_semantics": None,
        "acceptance_limit": None,
        "acceptance_limit_units": None,
        "pass_fail": "not_assessed",
        "comparison_status": "not_comparable",
        "reason": None,
        "matching_status": match.status,
        "dose_uid": manifest.get("rtdose_uid"),
        "rtstruct_uid": manifest.get("rtstruct_uid"),
        "rtplan_uid": manifest.get("rtplan_uid"),
        "prescription_gy": reference.rx_gy,
        "prescription_source": None,
        "ascend_algorithm_version": manifest.get("layer1_algorithm_version"),
        "acceptance_criterion_version": criteria.version,
        "warnings": list(match.warnings),
        "diagnostic_context": diagnostics,
        "provenance": {
            "reference_source_file": reference.source_file,
            "reference_source_content_hash": reference.source_content_hash,
            "reference_import_timestamp_utc": reference.import_timestamp_utc,
            "reference_provenance": reference.provenance,
            "reference_record": reference.to_dict(),
            "layer1_run_id": case.layer1.run_id,
            "layer1_result_path": case.layer1.result_path,
            "ascend_endpoint_source": None,
        },
    }


def compare_reference(
    case: ASCENDCase,
    reference: ReferenceRecord,
    criteria: AcceptanceCriteria,
    endpoint_index: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare reference and retain auditable evidence."""
    match = match_reference(case, reference)
    record = _base_record(case, reference, match, criteria)
    if reference.import_status != "valid":
        record.update(comparison_status="invalid_reference", reason=reference.import_reason or "Reference is invalid.")
        return record
    if match.status == "identity_conflict":
        record.update(comparison_status="identity_conflict", reason=match.reason)
        return record
    if match.status == "ambiguous":
        record.update(comparison_status="ambiguous_structure", reason=match.reason)
        return record
    if match.status == "not_found" or not match.candidate:
        record.update(comparison_status="missing_ascend_endpoint", reason=match.reason or "ASCEND structure was not found.")
        return record
    candidate = match.candidate
    canonical = str(candidate.get("canonical_structure"))
    if reference.endpoint_type == "volume_at_prescription":
        rx_gy, rx_source, _rx_key = _prescription(case, candidate.get("structure_role"))
        record["prescription_source"] = rx_source
        if reference.rx_gy is None:
            record.update(comparison_status="not_comparable", reason="missing_prescription")
            return record
        if rx_gy is None:
            record.update(comparison_status="not_comparable", reason="missing_ascend_prescription")
            return record
        if not math.isclose(float(reference.rx_gy), float(rx_gy), rel_tol=1e-9, abs_tol=1e-9):
            record.update(
                comparison_status="not_comparable",
                reason=f"prescription_mismatch: Eclipse {reference.rx_gy:g} Gy, ASCEND {rx_gy:g} Gy",
            )
            return record
    index = endpoint_index if endpoint_index is not None else _endpoint_index(case)
    endpoint = index.get((canonical, reference.endpoint))
    if endpoint is None:
        record.update(
            comparison_status="missing_ascend_endpoint",
            reason=f"ASCEND has no stored {reference.endpoint} endpoint for {canonical}; no endpoint was recalculated.",
        )
        return record
    if endpoint["units"] != reference.units:
        record.update(
            ascend_value=endpoint["value"], comparison_status="unit_mismatch",
            reason=f"ASCEND units {endpoint['units']!r} do not match canonical Eclipse units {reference.units!r}.",
        )
        return record
    ascend_value = float(endpoint["value"])
    eclipse_value = float(reference.eclipse_value)
    delta = ascend_value - eclipse_value
    absolute_delta = abs(delta)
    relative_delta = (
        100.0 * delta / eclipse_value
        if abs(eclipse_value) > criteria.relative_difference_zero_epsilon else None
    )
    if reference.endpoint_type in {"dose_at_volume", "dose_statistic"}:
        limit = max(criteria.dose_absolute_floor_gy, criteria.dose_relative_fraction * abs(eclipse_value))
        limit_units = "Gy"
        semantics = "dose difference in Gy"
    elif reference.endpoint_type in {"volume_at_prescription", "volume_at_absolute_dose"}:
        limit = criteria.percentage_volume_limit_points
        limit_units = "percentage_points"
        semantics = "percentage-point difference"
    else:
        limit = max(
            criteria.structure_volume_absolute_floor_cc,
            criteria.structure_volume_relative_fraction * abs(eclipse_value),
        )
        limit_units = "cc"
        semantics = "structure-volume difference in cc"
    record.update({
        "ascend_value": ascend_value,
        "delta": delta,
        "absolute_delta": absolute_delta,
        "relative_delta_percent": relative_delta,
        "delta_semantics": semantics,
        "acceptance_limit": limit,
        "acceptance_limit_units": limit_units,
        "pass_fail": "pass" if absolute_delta <= limit else "fail",
        "comparison_status": "valid_comparison",
        "reason": None,
    })
    record["provenance"]["ascend_endpoint_source"] = endpoint["source"]
    return record


def compare_references(
    case: ASCENDCase,
    references: list[ReferenceRecord],
    criteria: AcceptanceCriteria,
) -> list[dict[str, Any]]:
    """Compare references and retain auditable evidence."""
    index = _endpoint_index(case)
    return [compare_reference(case, reference, criteria, index) for reference in references]
