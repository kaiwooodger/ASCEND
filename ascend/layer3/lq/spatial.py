"""Authoritative voxelwise Layer 3.1A spatial BED/EQD2 fields and warning masks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from ascend.layer3.history import FractionHistory, GateResult
from ascend.validation.provenance import canonical_hash, file_hash

from .basis import _deterministic_npz
from .models import LQBiologicalBasis, ROIParameterAssignment


SPATIAL_LQ_SCHEMA_VERSION = "ASCEND-L3.1A-spatial-fields-v1"
SPATIAL_LQ_ALGORITHM_VERSION = "ASCEND-L3.1A-spatial-LQ-v1.0"
TUMOUR_REPORTING_ROLES = {"GTV", "VTV_H", "VTV_L"}


def _field_key(prefix: str, alpha_beta: float) -> str:
    digest = hashlib.sha256(f"{float(alpha_beta):.17g}".encode()).hexdigest()[:12]
    return f"{prefix}_ab_{digest}"


def build_spatial_lq_result(
    case_root: Path,
    run_id: str,
    basis: LQBiologicalBasis,
    fraction_history: FractionHistory,
    assignments: list[ROIParameterAssignment],
    assignment_masks: dict[tuple[str, int], np.ndarray],
    roi_results: list[dict[str, Any]],
    geometry: dict[str, Any],
    high_dose_warning_threshold_gy: float | None,
    high_dose_warning_provenance: dict[str, Any] | None = None,
    materialise_fields: bool = True,
) -> dict[str, Any]:
    """Build spatial maps before ROI reduction and publish a deterministic array archive."""
    gate_results = list(fraction_history.gate_results)
    if not assignments:
        gate_results.append(GateResult("GATE_3_TISSUE_PARAMETERS", "BLOCKED", "BIOLOGICAL_TISSUE_PARAMETERS_UNRESOLVED"))
        return _blocked(gate_results, "BIOLOGICAL_TISSUE_PARAMETERS_UNRESOLVED")
    tumour_values = {
        float(item.alpha_beta_gy) for item in assignments if item.canonical_role in TUMOUR_REPORTING_ROLES
    }
    if len(tumour_values) > 1:
        gate_results.append(GateResult(
            "GATE_3_TISSUE_PARAMETERS", "BLOCKED", "TUMOUR_TISSUE_PARAMETER_INCONSISTENT",
            "GTV, vertex and valley reporting masks cannot silently define different tumour radiosensitivity.",
            {"tumour_alpha_beta_values_gy": sorted(tumour_values)},
        ))
        return _blocked(gate_results, "TUMOUR_TISSUE_PARAMETER_INCONSISTENT")
    gate_results.append(GateResult(
        "GATE_3_TISSUE_PARAMETERS", "PASS", evidence={
            "parameter_set_count": len({float(item.alpha_beta_gy) for item in assignments}),
            "tumour_reporting_masks_share_parameter": len(tumour_values) <= 1,
        },
    ))
    arrays: dict[str, np.ndarray] = {}
    max_fraction = np.asarray(fraction_history.maximum_fraction_dose_field, dtype=np.float32)
    if materialise_fields:
        arrays["maximum_fraction_dose_gy"] = max_fraction
    field_records: list[dict[str, Any]] = []
    for alpha_beta in sorted({float(item.alpha_beta_gy) for item in assignments}):
        bed_key = _field_key("spatial_BED_LQ", alpha_beta)
        eqd2_key = _field_key("spatial_EQD2_LQ", alpha_beta)
        if materialise_fields:
            bed = np.asarray(basis.p_map, dtype=np.float64) + np.asarray(basis.q_map, dtype=np.float64) / alpha_beta
            eqd2 = bed / (1.0 + 2.0 / alpha_beta)
            if not np.isfinite(bed).all() or not np.isfinite(eqd2).all():
                return _blocked(gate_results, "SPATIAL_LQ_NONFINITE_RESULT")
            arrays[bed_key] = np.asarray(bed, dtype=np.float32)
            arrays[eqd2_key] = np.asarray(eqd2, dtype=np.float32)
        bound = [item for item in assignments if float(item.alpha_beta_gy) == alpha_beta]
        field_records.append({
            "tissue_parameter_hash": canonical_hash({
                "alpha_beta_gy": alpha_beta,
                "assignments": [item.to_dict() for item in bound],
            }),
            "alpha_beta_gy": alpha_beta,
            "spatial_BED_LQ_array_key": bed_key,
            "spatial_EQD2_LQ_array_key": eqd2_key,
            "BED_units": "Gy BED",
            "EQD2_units": "Gy EQD2",
            "bound_roi_identities": [item.roi_identity for item in bound],
            "parameter_sources": sorted({item.parameter_source for item in bound}),
            "calculation_order": "fraction_event_voxel_transform_then_roi_sampling",
        })
    warning_configured = high_dose_warning_threshold_gy is not None
    warning_provenance = dict(high_dose_warning_provenance or {})
    warning_mask = (
        max_fraction >= float(high_dose_warning_threshold_gy)
        if warning_configured else None
    )
    if warning_mask is not None:
        arrays["LQ_high_dose_warning_mask"] = warning_mask.astype(np.uint8)
    warning_by_roi = []
    for assignment in assignments:
        key = (str(assignment.roi_identity["rtstruct_sop_instance_uid"]), int(assignment.roi_identity["roi_number"]))
        mask = np.asarray(assignment_masks.get(key), dtype=bool) if key in assignment_masks else None
        if warning_mask is None or mask is None or mask.shape != warning_mask.shape or not mask.any():
            continue
        flagged = mask & warning_mask
        warning_by_roi.append({
            "roi_identity": assignment.roi_identity, "roi_name": assignment.roi_name,
            "flagged_voxel_count": int(flagged.sum()),
            "flagged_volume_percent": 100.0 * float(flagged.sum()) / float(mask.sum()),
            "maximum_fraction_dose_in_flagged_region_gy": float(np.max(max_fraction[flagged])) if flagged.any() else None,
        })
    artifacts: dict[str, Any] = {
        "materialisation_status": "not_materialised",
        "physical_course_dose_reference": {
            "basis_cache_path": basis.cache_path,
            "array_key": "P_gy",
            "basis_hash": basis.basis_hash,
            "array_sha256": hashlib.sha256(
                np.ascontiguousarray(basis.p_map, dtype=np.float32).view(np.uint8)
            ).hexdigest(),
        },
    }
    if materialise_fields:
        output = case_root / "derived" / "layer3_1" / f"{run_id}_spatial_fields.npz"
        output.parent.mkdir(parents=True, exist_ok=True)
        _deterministic_npz(output, arrays)
        artifacts.update({
            "materialisation_status": "materialised_on_request",
            "spatial_fields_path": str(output), "spatial_fields_sha256": file_hash(output),
            "authoritative_representation": "validated_voxel_grid_float32_arrays",
        })
    warnings = []
    if warning_mask is not None and warning_mask.any():
        warnings.extend([
            "LQ_HIGH_DOSE_EXTRAPOLATION", "BIOLOGICAL_MODEL_DOMAIN_WARNING",
            "configured_high_dose_sensitivity_flag",
        ])
    return {
        "schema_version": SPATIAL_LQ_SCHEMA_VERSION,
        "formalism_id": "CONVENTIONAL_LQ_REFERENCE",
        "formalism_version": SPATIAL_LQ_ALGORITHM_VERSION,
        "status": "WARN" if warnings else "PASS",
        "calculation_status": "completed_with_warnings" if warnings else "completed",
        "applicability_status": "APPLICABLE",
        "interpretation_status": "provisional" if warnings else "protocol_interpretable",
        "gate_results": [item.to_dict() for item in gate_results],
        "blocking_reasons": [], "warnings": warnings,
        "spatial_fields": field_records,
        "roi_summaries": roi_results,
        "high_dose_warning": {
            "array_key": "LQ_high_dose_warning_mask" if warning_configured else None,
            "configured": warning_configured,
            "threshold_gy_per_fraction": high_dose_warning_threshold_gy,
            "configured_sensitivity_threshold_gy_per_fraction": high_dose_warning_threshold_gy,
            "threshold_source": warning_provenance.get("source") or ("explicit_case_configuration" if warning_configured else None),
            "threshold_mode": warning_provenance.get("mode") if warning_configured else None,
            "flagged_voxel_count": int(warning_mask.sum()) if warning_mask is not None else None,
            "threshold_triggered": bool(warning_mask.any()) if warning_mask is not None else False,
            "validity_cutoff": False,
            "message": (
                "Conventional LQ reference — configured high-dose model-domain warning criterion was exceeded."
                if warning_mask is not None and warning_mask.any() else
                "Conventional LQ reference — configured high-dose model-domain warning criterion was not exceeded."
                if warning_configured else
                "Conventional LQ reference — high-dose model-domain warning criterion is not configured."
            ),
            "roi_summary": warning_by_roi,
            "model_switching": False,
            "explanation": (
                "Conventional LQ reference calculation retained. Highlighted regions exceed the configured "
                "high-dose model-domain warning criterion. These voxels are not automatically replaced by the 3.1B MLQ model."
                if warning_configured else
                "No operational high-dose warning criterion is configured; no warning-mask array was materialised."
            ),
        },
        "fraction_history": fraction_history.metadata(),
        "registration_state": fraction_history.registration_state,
        "geometry": geometry,
        "artifacts": artifacts,
        "provenance": {
            "calculation_version": SPATIAL_LQ_ALGORITHM_VERSION,
            "basis_hash": basis.basis_hash,
            "fraction_history_hash": fraction_history.history_hash,
            "geometry_hash": basis.geometry_identity,
            "quantitative_field_smoothing": False,
            "roi_reduction_after_voxelwise_transformation": True,
        },
    }


def _blocked(gates: list[GateResult], reason: str) -> dict[str, Any]:
    return {
        "schema_version": SPATIAL_LQ_SCHEMA_VERSION,
        "formalism_id": "CONVENTIONAL_LQ_REFERENCE",
        "formalism_version": SPATIAL_LQ_ALGORITHM_VERSION,
        "status": "BLOCKED", "calculation_status": "blocked",
        "applicability_status": "BLOCKED", "interpretation_status": "not_interpretable",
        "gate_results": [item.to_dict() for item in gates],
        "blocking_reasons": [reason], "warnings": [], "spatial_fields": [],
    }
