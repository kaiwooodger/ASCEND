"""Biological counterparts and contextual mappings of stored Layer 2.1 metrics."""

from __future__ import annotations

from typing import Any

import numpy as np

from .metrics import bed_values, eqd2_values_from_bed
from .models import LQBiologicalBasis, ROIParameterAssignment


BIOLOGICAL_SIX_METRIC_VERSION = "ASCEND-L3.1-L2.1-biological-mapping-v2.0"
BIOLOGICAL_METRIC_IDS = (
    "peripheral_coverage_v95_rxl",
    "high_dose_coverage_v95_rxh",
    "high_dose_volume_fraction",
    "mean_peak_dose",
    "mean_valley_dose",
    "structure_based_dose_ratio",
)


def _endpoint(value: float | None, units: str, **evidence: Any) -> dict[str, Any]:
    return {"value": None if value is None else float(value), "units": units, **evidence}


def _not_assessed(
    metric_id: str,
    warning: str,
    definition: str,
    physical: dict[str, Any] | None,
    mapping_type: str,
) -> dict[str, Any]:
    geometry_mapping = mapping_type == "geometry_carried_forward"
    return {
        "metric_id": metric_id,
        "source_layer2_1_metric_id": metric_id,
        "mapping_type": mapping_type,
        "applicability": "not_assessed",
        "calculation_status": "not_calculated",
        "interpretation_status": "not_interpretable",
        "warnings": [warning],
        "definition": definition,
        "geometry": _endpoint(None, "%" if geometry_mapping else "not_applicable"),
        "bed": _endpoint(None, "not_applicable" if geometry_mapping else "Gy BED"),
        "eqd2": _endpoint(None, "not_applicable" if geometry_mapping else "Gy EQD2"),
        "physical_metric_reference": physical,
    }


def _mask_for_role(masks: dict[str, np.ndarray], roles: dict[str, Any], role: str) -> np.ndarray | None:
    configured = roles.get(role)
    if isinstance(configured, str):
        mask = masks.get(configured)
        return np.asarray(mask, dtype=bool) if mask is not None and mask.any() else None
    if isinstance(configured, list):
        selected = [np.asarray(masks[name], dtype=bool) for name in configured if name in masks and masks[name].any()]
        if selected:
            return np.logical_or.reduce(selected)
    return None


def _assignment_for_role(
    assignments: list[ROIParameterAssignment], role: str,
) -> tuple[ROIParameterAssignment | None, str | None]:
    matched = [item for item in assignments if item.canonical_role == role]
    if not matched:
        return None, f"missing_alpha_beta_assignment_for_{role.lower()}"
    if len(matched) > 1:
        return None, f"ambiguous_alpha_beta_assignment_for_{role.lower()}"
    return matched[0], None


def _physical_reference(layer21_result: dict[str, Any] | None, metric_id: str) -> dict[str, Any] | None:
    if not layer21_result:
        return None
    metric = next((
        item for item in layer21_result.get("harmonised_metrics", [])
        if item.get("metric_id") == metric_id
    ), None)
    if not metric:
        return None
    return {
        "run_id": layer21_result.get("run_id"),
        "parent_layer1_run_id": layer21_result.get("parent_layer1_run_id"),
        "value": metric.get("value"),
        "units": metric.get("units"),
        "applicability": metric.get("applicability"),
        "warnings": metric.get("warnings", []),
    }


def _component_prescription(
    component: dict[str, Any], role: str, sole_component: bool, fallback_gy: float | None,
) -> float | None:
    role_field = "rx_low_gy" if role == "T_L" else "rx_high_gy"
    role_value = component.get(role_field)
    if role_value is not None:
        return float(role_value)
    component_type = str(component.get("component_type") or "unknown")
    if component_type == "conventional_rt" and component.get("prescription_gy") is not None:
        return float(component["prescription_gy"])
    if sole_component and fallback_gy is not None:
        return float(fallback_gy)
    return None


def _biological_prescription_threshold(
    basis: LQBiologicalBasis,
    treatment_components: list[dict[str, Any]],
    role: str,
    fallback_gy: float | None,
    alpha_beta_gy: float,
    relative_factor: float = 0.95,
) -> tuple[dict[str, Any] | None, str | None]:
    """Transform the complete component prescription history before thresholding.

    Applying 95% to physical dose before the nonlinear BED transform is not
    generally equivalent.  ASCEND first accumulates prescription P/Q, converts
    it to BED/EQD2, and only then applies the relative coverage factor.
    """
    configured = {str(item.get("component_id")): item for item in treatment_components}
    evidence: list[dict[str, Any]] = []
    prescription_p = 0.0
    prescription_q = 0.0
    sole = len(basis.components) == 1
    for component in basis.components:
        item = configured.get(component.component_id, {})
        if treatment_components:
            prescription = _component_prescription(item, role, sole, fallback_gy)
        else:
            prescription = fallback_gy if sole else None
        if prescription is None:
            return None, f"incomplete_component_prescription_history_for_{role.lower()}"
        prescription = float(prescription)
        p_contribution = prescription
        q_contribution = prescription * prescription / float(component.fraction_count)
        prescription_p += p_contribution
        prescription_q += q_contribution
        evidence.append({
            "component_id": component.component_id,
            "component_type": item.get("component_type", component.treatment_component_type),
            "fraction_count": component.fraction_count,
            "role_prescription_gy": prescription,
            "prescription_p_contribution_gy": p_contribution,
            "prescription_q_contribution_gy2": q_contribution,
        })
    bed_prescription = prescription_p + prescription_q / alpha_beta_gy
    eqd2_prescription = bed_prescription / (1.0 + 2.0 / alpha_beta_gy)
    bed_threshold = relative_factor * bed_prescription
    eqd2_threshold = relative_factor * eqd2_prescription
    return {
        "bed_threshold_gy": bed_threshold,
        "eqd2_threshold_gy": eqd2_threshold,
        "bed_prescription_gy": bed_prescription,
        "eqd2_prescription_gy": eqd2_prescription,
        "prescription_p_gy": prescription_p,
        "prescription_q_gy2": prescription_q,
        "relative_factor": relative_factor,
        "components": evidence,
        "rule": "Accumulate the full component-specific prescription P/Q history, transform to BED/EQD2, then apply the relative factor to the biological prescription.",
        "inequality_bed": "BED(x) >= relative_factor * BED_Rx",
        "inequality_eqd2": "EQD2(x) >= relative_factor * EQD2_Rx",
    }, None


def _role_values(
    basis: LQBiologicalBasis,
    masks: dict[str, np.ndarray],
    roles: dict[str, Any],
    assignments: list[ROIParameterAssignment],
    role: str,
    precomputed_role_values: dict[str, tuple[np.ndarray, np.ndarray, ROIParameterAssignment]] | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None, ROIParameterAssignment | None, str | None]:
    if precomputed_role_values and role in precomputed_role_values:
        bed, eqd2, assignment = precomputed_role_values[role]
        return bed, eqd2, assignment, None
    mask = _mask_for_role(masks, roles, role)
    if mask is None:
        return None, None, None, f"missing_validated_mask_for_{role.lower()}"
    assignment, issue = _assignment_for_role(assignments, role)
    if issue:
        return None, None, None, issue
    bed = bed_values(basis.p_map[mask], basis.q_map[mask], assignment.alpha_beta_gy)
    eqd2 = eqd2_values_from_bed(bed, assignment.alpha_beta_gy)
    return bed, eqd2, assignment, None


def _coverage_metric(
    metric_id: str,
    role: str,
    rx_gy: float | None,
    basis: LQBiologicalBasis,
    treatment_components: list[dict[str, Any]],
    masks: dict[str, np.ndarray],
    roles: dict[str, Any],
    assignments: list[ROIParameterAssignment],
    physical: dict[str, Any] | None,
    precomputed_role_values: dict[str, tuple[np.ndarray, np.ndarray, ROIParameterAssignment]] | None = None,
) -> dict[str, Any]:
    mapping_type = "biological_coverage_analogue"
    definition = "Percentage of the validated role mask satisfying BED(x) >= 0.95 BED_Rx or the corresponding EQD2 threshold."
    bed, eqd2, assignment, issue = _role_values(
        basis, masks, roles, assignments, role, precomputed_role_values,
    )
    if issue:
        return _not_assessed(metric_id, issue, definition, physical, mapping_type)
    threshold, issue = _biological_prescription_threshold(
        basis, treatment_components, role, rx_gy, assignment.alpha_beta_gy,
    )
    if issue:
        return _not_assessed(metric_id, issue, definition, physical, mapping_type)
    bed_coverage = 100.0 * float(np.count_nonzero(bed >= threshold["bed_threshold_gy"])) / float(bed.size)
    eqd2_coverage = 100.0 * float(np.count_nonzero(eqd2 >= threshold["eqd2_threshold_gy"])) / float(eqd2.size)
    warnings = sorted(set(basis.warnings) | set(assignment.warnings))
    return {
        "metric_id": metric_id,
        "source_layer2_1_metric_id": metric_id,
        "mapping_type": mapping_type,
        "applicability": "valid",
        "calculation_status": "completed_with_warnings" if warnings else "completed",
        "interpretation_status": "provisional" if warnings else "protocol_interpretable",
        "warnings": warnings,
        "definition": definition,
        "structure_role": role,
        "alpha_beta_gy": assignment.alpha_beta_gy,
        "geometry": _endpoint(None, "not_applicable"),
        "bed": _endpoint(bed_coverage, "%", threshold_gy_bed=threshold["bed_threshold_gy"]),
        "eqd2": _endpoint(eqd2_coverage, "%", threshold_gy_eqd2=threshold["eqd2_threshold_gy"]),
        "biological_prescription": threshold,
        "physical_metric_reference": physical,
    }


def _mean_metric(
    metric_id: str,
    role: str,
    basis: LQBiologicalBasis,
    masks: dict[str, np.ndarray],
    roles: dict[str, Any],
    assignments: list[ROIParameterAssignment],
    physical: dict[str, Any] | None,
    extra_warnings: set[str] | None = None,
    precomputed_role_values: dict[str, tuple[np.ndarray, np.ndarray, ROIParameterAssignment]] | None = None,
) -> tuple[dict[str, Any], tuple[np.ndarray, np.ndarray, ROIParameterAssignment] | None]:
    mapping_type = "biological_transformation"
    definition = f"Arithmetic mean of the voxelwise BED and EQD2 maps in the validated {role} mask."
    bed, eqd2, assignment, issue = _role_values(
        basis, masks, roles, assignments, role, precomputed_role_values,
    )
    if issue:
        return _not_assessed(metric_id, issue, definition, physical, mapping_type), None
    warnings = sorted(set(basis.warnings) | set(assignment.warnings) | (extra_warnings or set()))
    record = {
        "metric_id": metric_id,
        "source_layer2_1_metric_id": metric_id,
        "mapping_type": mapping_type,
        "applicability": "valid",
        "calculation_status": "completed_with_warnings" if warnings else "completed",
        "interpretation_status": "provisional" if warnings else "protocol_interpretable",
        "warnings": warnings,
        "definition": definition,
        "structure_role": role,
        "alpha_beta_gy": assignment.alpha_beta_gy,
        "aggregation_method": "mean_of_voxelwise_biological_map",
        "nonlinear_transform_of_physical_mean": False,
        "geometry": _endpoint(None, "not_applicable"),
        "bed": _endpoint(float(bed.mean()), "Gy BED"),
        "eqd2": _endpoint(float(eqd2.mean()), "Gy EQD2"),
        "physical_metric_reference": physical,
    }
    return record, (bed, eqd2, assignment)


def _volume_fraction_metric(
    layer1: dict[str, Any], masks: dict[str, np.ndarray], roles: dict[str, Any], physical: dict[str, Any] | None,
) -> dict[str, Any]:
    """Carry geometry-only high-dose volume fraction into biological context."""
    mapping_type = "geometry_carried_forward"
    definition = "VTV_H volume divided by GTV volume; geometry-only and unchanged by biological dose transformation."
    high = _mask_for_role(masks, roles, "VTV_H")
    gtv = _mask_for_role(masks, roles, "GTV")
    if high is None or gtv is None:
        return _not_assessed(
            "high_dose_volume_fraction", "missing_validated_geometry_mask", definition, physical, mapping_type,
        )
    volume_definitions = layer1.get("manifest", {}).get("rasterisation", {}).get("volume_definitions", {})
    high_key, gtv_key = roles.get("VTV_H"), roles.get("GTV")
    high_record = volume_definitions.get(high_key, {}) if isinstance(high_key, str) else {}
    gtv_record = volume_definitions.get(gtv_key, {}) if isinstance(gtv_key, str) else {}
    high_cc = high_record.get("anatomical_volume_contour_cc")
    gtv_cc = gtv_record.get("anatomical_volume_contour_cc")
    basis_name = "contour_stack"
    if high_cc is None or gtv_cc is None:
        spacing = layer1.get("manifest", {}).get("dose_grid", {}).get("voxel_spacing_mm", [1.0, 1.0, 1.0])
        voxel_cc = float(np.prod(spacing) / 1000.0)
        high_cc, gtv_cc = float(high.sum() * voxel_cc), float(gtv.sum() * voxel_cc)
        basis_name = "dose_sampled_fallback"
    if float(gtv_cc) <= 0:
        return _not_assessed("high_dose_volume_fraction", "empty_gtv_volume", definition, physical, mapping_type)
    value = 100.0 * float(high_cc) / float(gtv_cc)
    evidence = {
        "unchanged_by_biological_dose_transformation": True,
        "volume_basis": basis_name,
        "vtvh_volume_cc": float(high_cc),
        "gtv_volume_cc": float(gtv_cc),
    }
    return {
        "metric_id": "high_dose_volume_fraction",
        "source_layer2_1_metric_id": "high_dose_volume_fraction",
        "mapping_type": mapping_type,
        "applicability": "valid",
        "calculation_status": "completed",
        "interpretation_status": "contextual_only",
        "warnings": [],
        "definition": definition,
        "geometry": _endpoint(value, "%", **evidence),
        "bed": _endpoint(None, "not_applicable"),
        "eqd2": _endpoint(None, "not_applicable"),
        "physical_metric_reference": physical,
    }


def _whole_gtv_context(
    basis: LQBiologicalBasis,
    masks: dict[str, np.ndarray],
    roles: dict[str, Any],
    assignments: list[ROIParameterAssignment],
    precomputed_role_values: dict[str, tuple[np.ndarray, np.ndarray, ROIParameterAssignment]] | None = None,
) -> dict[str, Any]:
    definition = "Additional whole-GTV biological context calculated directly from voxelwise BED/EQD2 maps."
    bed, eqd2, assignment, issue = _role_values(
        basis, masks, roles, assignments, "GTV", precomputed_role_values,
    )
    if issue:
        return {
            "mapping_type": "additional_contextual_biological_endpoints",
            "applicability": "not_assessed",
            "calculation_status": "not_calculated",
            "interpretation_status": "not_interpretable",
            "definition": definition,
            "warnings": [issue],
            "endpoints": {},
        }
    warnings = sorted(set(basis.warnings) | set(assignment.warnings))
    return {
        "mapping_type": "additional_contextual_biological_endpoints",
        "applicability": "valid",
        "calculation_status": "completed_with_warnings" if warnings else "completed",
        "interpretation_status": "provisional" if warnings else "protocol_interpretable",
        "definition": definition,
        "structure_role": "GTV",
        "alpha_beta_gy": assignment.alpha_beta_gy,
        "aggregation_method": "direct_voxelwise_biological_map_summary",
        "warnings": warnings,
        "endpoints": {
            "bed_mean": _endpoint(float(bed.mean()), "Gy BED"),
            "bed_d95": _endpoint(float(np.percentile(bed, 5.0)), "Gy BED"),
            "eqd2_mean": _endpoint(float(eqd2.mean()), "Gy EQD2"),
            "eqd2_d95": _endpoint(float(np.percentile(eqd2, 5.0)), "Gy EQD2"),
        },
    }


def build_biological_six_metrics(
    basis: LQBiologicalBasis,
    layer1: dict[str, Any],
    masks: dict[str, np.ndarray],
    roles: dict[str, Any],
    assignments: list[ROIParameterAssignment],
    treatment_components: list[dict[str, Any]],
    prescriptions: dict[str, Any],
    layer21_result: dict[str, Any] | None = None,
    precomputed_role_values: dict[str, tuple[np.ndarray, np.ndarray, ROIParameterAssignment]] | None = None,
) -> dict[str, Any]:
    """Map Layer 2.1 metrics to valid biological analogues or explicit context.

    Not every physical metric has a literal BED/EQD2 version.  Coverage and
    dose endpoints use voxelwise biological maps, volume fraction remains a
    geometry metric, and peak/valley contrast is a derived biological ratio.
    """
    def prescription(key: str) -> float | None:
        item = prescriptions.get(key)
        value = item.gy if hasattr(item, "gy") else (item or {}).get("gy")
        return float(value) if value is not None else None

    physical = {metric_id: _physical_reference(layer21_result, metric_id) for metric_id in BIOLOGICAL_METRIC_IDS}
    records = [
        _coverage_metric(
            "peripheral_coverage_v95_rxl", "T_L", prescription("Rx_L"), basis,
            treatment_components, masks, roles, assignments, physical["peripheral_coverage_v95_rxl"],
            precomputed_role_values,
        ),
        _coverage_metric(
            "high_dose_coverage_v95_rxh", "VTV_H", prescription("Rx_H"), basis,
            treatment_components, masks, roles, assignments, physical["high_dose_coverage_v95_rxh"],
            precomputed_role_values,
        ),
        _volume_fraction_metric(layer1, masks, roles, physical["high_dose_volume_fraction"]),
    ]
    component_types = {item.treatment_component_type for item in basis.components}
    cert_warnings = {"cert_background_included_in_biological_valley"} if "conventional_rt" in component_types else set()
    peak_record, peak_values = _mean_metric(
        "mean_peak_dose", "VTV_H", basis, masks, roles, assignments, physical["mean_peak_dose"],
        precomputed_role_values=precomputed_role_values,
    )
    valley_record, valley_values = _mean_metric(
        "mean_valley_dose", "VTV_L", basis, masks, roles, assignments,
        physical["mean_valley_dose"], cert_warnings,
        precomputed_role_values,
    )
    records.extend((peak_record, valley_record))
    mapping_type = "derived_biological_contrast"
    definition = "Derived BED peak–valley contrast and EQD2 peak–valley contrast from voxelwise biological means."
    if peak_values is None or valley_values is None:
        ratio_issue = "missing_peak_or_valley_biological_metric"
        ratio = _not_assessed(
            "structure_based_dose_ratio", ratio_issue, definition,
            physical["structure_based_dose_ratio"], mapping_type,
        )
    else:
        peak_bed, peak_eqd2, peak_assignment = peak_values
        valley_bed, valley_eqd2, valley_assignment = valley_values
        warnings = set(basis.warnings) | cert_warnings
        if peak_assignment.alpha_beta_gy != valley_assignment.alpha_beta_gy:
            warnings.add("ratio_uses_different_role_specific_alpha_beta_values")
        bed_denominator, eqd2_denominator = float(valley_bed.mean()), float(valley_eqd2.mean())
        if bed_denominator <= 0 or eqd2_denominator <= 0:
            ratio = _not_assessed(
                "structure_based_dose_ratio", "nonpositive_biological_valley_mean", definition,
                physical["structure_based_dose_ratio"], mapping_type,
            )
        else:
            same_alpha_beta = peak_assignment.alpha_beta_gy == valley_assignment.alpha_beta_gy
            ratio = {
                "metric_id": "structure_based_dose_ratio",
                "source_layer2_1_metric_id": "structure_based_dose_ratio",
                "mapping_type": mapping_type,
                "applicability": "valid",
                "calculation_status": "completed_with_warnings" if warnings else "completed",
                "interpretation_status": "provisional" if warnings else "protocol_interpretable",
                "warnings": sorted(warnings),
                "interpretive_flags": (
                    ["bed_eqd2_contrasts_mathematically_redundant_same_alpha_beta"]
                    if same_alpha_beta else []
                ),
                "definition": definition,
                "geometry": _endpoint(None, "not_applicable"),
                "bed": _endpoint(
                    float(peak_bed.mean()) / bed_denominator, "ratio",
                    numerator_mean_gy_bed=float(peak_bed.mean()), denominator_mean_gy_bed=bed_denominator,
                ),
                "eqd2": _endpoint(
                    float(peak_eqd2.mean()) / eqd2_denominator, "ratio",
                    numerator_mean_gy_eqd2=float(peak_eqd2.mean()), denominator_mean_gy_eqd2=eqd2_denominator,
                ),
                "alpha_beta_gy": {"VTV_H": peak_assignment.alpha_beta_gy, "VTV_L": valley_assignment.alpha_beta_gy},
                "mathematical_redundancy": {
                    "bed_and_eqd2_contrasts_redundant": same_alpha_beta,
                    "reason": (
                        "EQD2 is a constant scaling of BED when VTV_H and VTV_L use the same alpha/beta."
                        if same_alpha_beta else
                        "Role-specific alpha/beta values differ, so BED and EQD2 contrasts are not constant-scaled equivalents."
                    ),
                },
                "physical_metric_reference": physical["structure_based_dose_ratio"],
            }
    records.append(ratio)
    whole_gtv_context = _whole_gtv_context(
        basis, masks, roles, assignments, precomputed_role_values,
    )
    return {
        "schema_version": "ASCEND-L3.1-biological-metric-mapping-v2",
        "algorithm_version": BIOLOGICAL_SIX_METRIC_VERSION,
        "title": "Layer 3.1 biological counterparts and contextual mappings of the six Layer 2.1 metrics",
        "scope": "Conventional LQ biological coverage analogues, biological transformations, derived contrasts, and geometry carried forward.",
        "mapping_taxonomy": [
            "biological_coverage_analogue",
            "biological_transformation",
            "derived_biological_contrast",
            "geometry_carried_forward",
        ],
        "coverage_threshold_policy": "Accumulate full component-specific prescription P/Q, calculate BED_Rx and EQD2_Rx, then assess 95% of those biological prescription values.",
        "geometry_metric_policy": "High-dose volume fraction is geometry-only; unchanged by biological dose transformation; no BED or EQD2 value is generated.",
        "records": records,
        "whole_gtv_biological_context": whole_gtv_context,
        "validation_claim": "computational_verification_only_not_clinical_validation",
        "status": (
            "completed_with_unassessed_metrics"
            if any(item["applicability"] != "valid" for item in records) else
            "completed_with_warnings"
            if any(item.get("warnings") for item in records) else
            "completed"
        ),
    }
