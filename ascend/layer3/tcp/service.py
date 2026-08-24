"""Layer 3.1D Poisson TCP over an authoritative Layer 3.1B survival field.

This module intentionally has no DICOM, dose, LQ, MLQ, EUD, rasterisation, or
registration code.  Its only scientific input field is validated upstream
tumour survival.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ascend import __version__
from ascend.layer3.lq.basis import _deterministic_npz
from ascend.validation.provenance import canonical_hash, file_hash


TCP_MODEL_NAME = "TCP_MLQ_POISSON"
TCP_MODEL_VERSION = "ASCEND-L3.1D-spatial-MLQ-Poisson-v1.0"
TCP_RESULT_SCHEMA_VERSION = "ASCEND-L3.1D-result-v1"
TCP_ASSUMPTIONS = [
    "POISSON_CLONOGEN_STATISTICS", "ZERO_SURVIVING_CLONOGENS_REQUIRED_FOR_CONTROL",
    "SPATIALLY_INDEPENDENT_SUBVOLUMES", "DIRECT_RADIATION_KILL",
    "NO_NONLOCAL_BYSTANDER_COUPLING", "NO_IMMUNE_MEDIATED_KILL",
    "NO_VASCULAR_MEDIATED_KILL",
]
TCP_WARNINGS = [
    "DIRECT_KILL_MODEL_ONLY", "UNIFORM_CLONOGEN_DENSITY_ASSUMED",
    "TCP_PARAMETERS_NOT_CLINICALLY_VALIDATED", "SPATIAL_INDEPENDENCE_ASSUMED",
    "NONLOCAL_SFRT_EFFECTS_NOT_INCLUDED",
]


def _gate(gate_id: str, passed: bool, reason: str | None = None, evidence: Any = None, *, optional: bool = False) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "status": "PASS" if passed else ("UNAVAILABLE" if optional else "BLOCKED"),
        "reason_code": None if passed else reason,
        "evidence": evidence,
    }


def _blocked(reason: str, gates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": TCP_RESULT_SCHEMA_VERSION, "model_name": TCP_MODEL_NAME,
        "model_version": TCP_MODEL_VERSION, "status": "BLOCKED",
        "calculation_status": "blocked", "applicability_status": "BLOCKED",
        "interpretation_status": "not_interpretable", "reason": reason,
        "blocking_reasons": [reason], "gate_results": gates, "warnings": [],
        "validation_status": ["SOFTWARE_VERIFIED", "MODEL_CONSISTENCY_VERIFIED", "BIOLOGICALLY_UNVALIDATED"],
    }


def _positive_parameter(parameters: dict[str, Any], key: str) -> float:
    try:
        value = float(parameters[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} is required and must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and greater than zero")
    return value


def _nonnegative_parameter(parameters: dict[str, Any], key: str) -> float:
    try:
        value = float(parameters[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} is required and must be numeric") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} must be finite and non-negative")
    return value


def compute_poisson_tcp(
    survival_field: np.ndarray,
    tumour_mask: np.ndarray,
    voxel_volume_cm3: float,
    clonogen_density_per_cm3: float,
    *,
    phi_repopulation: float = 0.0,
    spatial_partition: dict[str, np.ndarray] | None = None,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Aggregate validated survival into Poisson TCP without recalculating MLQ."""
    survival = np.asarray(survival_field, dtype=np.float64)
    tumour = np.asarray(tumour_mask, dtype=bool)
    if survival.shape != tumour.shape:
        raise ValueError("Survival field and tumour mask shapes differ.")
    if not tumour.any():
        raise ValueError("Tumour mask is empty.")
    selected = survival[tumour]
    if not np.isfinite(selected).all() or np.any(selected < 0.0) or np.any(selected > 1.0):
        raise ValueError("Tumour survival contains non-finite values or values outside [0,1].")
    if not math.isfinite(voxel_volume_cm3) or voxel_volume_cm3 <= 0:
        raise ValueError("Physical voxel volume must be finite and positive.")
    if not math.isfinite(clonogen_density_per_cm3) or clonogen_density_per_cm3 <= 0:
        raise ValueError("Clonogen density must be finite and positive.")
    if not math.isfinite(phi_repopulation) or phi_repopulation < 0:
        raise ValueError("Repopulation exponent must be finite and non-negative.")

    initial_per_voxel = clonogen_density_per_cm3 * voxel_volume_cm3
    with np.errstate(divide="ignore"):
        psi_selected = -np.log(selected)
    if np.any(psi_selected < 0) or np.isnan(psi_selected).any():
        raise ValueError("Derived tumour log-survival effect is invalid.")
    radiation_mu = np.zeros(survival.shape, dtype=np.float64)
    radiation_mu[tumour] = np.exp(math.log(initial_per_voxel) - psi_selected)
    corrected_mu = np.zeros_like(radiation_mu)
    corrected_mu[tumour] = np.exp(math.log(initial_per_voxel) - psi_selected + phi_repopulation)
    initial = float(initial_per_voxel * int(tumour.sum()))
    residual_radiation = float(np.sum(radiation_mu[tumour], dtype=np.float64))
    residual_corrected = float(np.sum(corrected_mu[tumour], dtype=np.float64))

    def endpoint(residual: float) -> dict[str, Any]:
        return {
            "expected_surviving_clonogens": residual,
            "log10_expected_surviving_clonogens": math.log10(residual) if residual > 0 else None,
            "tcp": math.exp(-residual) if residual < 745.0 else 0.0,
            "ln_tcp": -residual,
        }

    spatial: dict[str, Any] = {"status": "UNAVAILABLE", "reason": "VALID_SPATIAL_PARTITION unavailable", "records": []}
    if spatial_partition is not None:
        high = np.asarray(spatial_partition.get("vertex"), dtype=bool)
        valley = np.asarray(spatial_partition.get("valley"), dtype=bool)
        if high.shape == tumour.shape and valley.shape == tumour.shape:
            high = high & tumour
            valley = valley & tumour & ~high
            remainder = tumour & ~high & ~valley
            records = []
            for region_id, mask in (("VERTEX", high), ("VALLEY", valley), ("REMAINDER", remainder)):
                burden = float(np.sum(corrected_mu[mask], dtype=np.float64))
                records.append({
                    "region_id": region_id, "voxel_count": int(mask.sum()),
                    "volume_cm3": float(mask.sum() * voxel_volume_cm3),
                    "mean_radiation_survival_fraction": float(np.mean(survival[mask])) if mask.any() else None,
                    "expected_residual_clonogens": burden,
                    "residual_fraction": burden / residual_corrected if residual_corrected > 0 else 0.0,
                    "p0": math.exp(-burden) if burden < 745.0 else 0.0,
                })
            reconstruction = sum(item["expected_residual_clonogens"] for item in records)
            fraction_sum = sum(item["residual_fraction"] for item in records)
            spatial = {
                "status": "VALID", "records": records,
                "reconstruction_residual": abs(reconstruction - residual_corrected),
                "residual_fraction_sum": fraction_sum,
                "region_semantics": "P0 values are compartment zero-survivor probabilities, not clinical compartment TCPs.",
            }
    fields = {
        "residual_clonogen_field_radiation_only": radiation_mu.astype(np.float32),
        "residual_clonogen_field_repopulation_corrected": corrected_mu.astype(np.float32),
        "residual_clonogen_density_repopulation_corrected": (corrected_mu / voxel_volume_cm3).astype(np.float32),
        "net_clonogenic_multiplier": (survival * math.exp(phi_repopulation)).astype(np.float32),
    }
    fraction_field = np.zeros_like(corrected_mu, dtype=np.float64)
    if residual_corrected > 0:
        fraction_field[tumour] = corrected_mu[tumour] / residual_corrected
    log_field = np.full_like(corrected_mu, np.nan, dtype=np.float64)
    log_field[tumour] = np.log10(np.maximum(corrected_mu[tumour], np.finfo(np.float32).tiny))
    fields["residual_clonogen_fraction_of_total"] = fraction_field.astype(np.float32)
    fields["log10_residual_clonogen_burden"] = log_field.astype(np.float32)
    return {
        "initial_clonogens": initial,
        "radiation_only": endpoint(residual_radiation),
        "repopulation_corrected": endpoint(residual_corrected),
        "repopulation_multiplier": math.exp(phi_repopulation),
        "spatial_decomposition": spatial,
    }, fields


def run_layer31d_tcp(
    case: Any,
    basis: Any,
    layer1: dict[str, Any],
    masks: dict[str, np.ndarray],
    tumour_state: dict[str, Any] | None,
    run_id: str,
    materialise_fields: bool | None = None,
) -> dict[str, Any]:
    """Gate, calculate, provenance-bind, and optionally materialise Layer 3.1D."""
    gates: list[dict[str, Any]] = []
    branch = tumour_state.get("result") if tumour_state else None
    valid_branch = bool(branch and branch.get("applicability_status") == "APPLICABLE" and tumour_state.get("survival") is not None)
    gates.append(_gate("VALID_LAYER_3_1B_SURVIVAL", valid_branch, "VALID_LAYER_3_1B_SURVIVAL_FAILED",
                       {"source_result_id": case.layer3_1.run_id if getattr(case, "layer3_1", None) else None}))
    if not valid_branch:
        return _blocked("VALID_LAYER_3_1B_SURVIVAL_FAILED", gates)
    survival = np.asarray(tumour_state["survival"], dtype=np.float64)
    tumour = np.asarray(tumour_state["gtv_mask"], dtype=bool)
    mask_valid = tumour.shape == survival.shape and tumour.any()
    gates.append(_gate("VALID_TUMOUR_MASK", mask_valid, "VALID_TUMOUR_MASK_FAILED", {"shape": list(tumour.shape), "voxel_count": int(tumour.sum())}))
    if not mask_valid:
        return _blocked("VALID_TUMOUR_MASK_FAILED", gates)
    parameters = dict(case.configuration.layer31_tcp_parameters or {})
    try:
        density = _positive_parameter(parameters, "clonogen_density_per_cm3")
        units = str(parameters.get("units") or "").strip()
        source = str(parameters.get("source") or "").strip()
        parameter_set_id = str(parameters.get("parameter_set_id") or "").strip()
        if units not in {"clonogens/cm3", "clonogens/cm^3"} or not source or not parameter_set_id:
            raise ValueError("Clonogen units, source, and parameter-set ID are required.")
    except ValueError as exc:
        gates.append(_gate("VALID_CLONOGEN_DENSITY", False, "VALID_CLONOGEN_DENSITY_FAILED", str(exc)))
        return _blocked("VALID_CLONOGEN_DENSITY_FAILED", gates)
    gates.append(_gate("VALID_CLONOGEN_DENSITY", True, evidence={"value": density, "units": units, "source": source, "parameter_set_id": parameter_set_id}))
    spacing = tuple(float(item) for item in basis.dose_grid_spacing_mm)
    voxel_cm3 = float(np.prod(spacing) / 1000.0)
    voxel_valid = math.isfinite(voxel_cm3) and voxel_cm3 > 0
    gates.append(_gate("VALID_VOXEL_VOLUME", voxel_valid, "VALID_VOXEL_VOLUME_FAILED", {"spacing_mm": list(spacing), "voxel_volume_cm3": voxel_cm3}))
    if not voxel_valid:
        return _blocked("VALID_VOXEL_VOLUME_FAILED", gates)
    survival_valid = np.isfinite(survival[tumour]).all() and np.all((survival[tumour] >= 0) & (survival[tumour] <= 1))
    gates.append(_gate("VALID_SURVIVAL_FIELD", bool(survival_valid), "VALID_SURVIVAL_FIELD_FAILED", {"shape": list(survival.shape)}))
    if not survival_valid:
        return _blocked("VALID_SURVIVAL_FIELD_FAILED", gates)
    provenance_valid = branch.get("mask_hash") is not None and branch.get("fraction_history_hash") == tumour_state["history"].history_hash
    gates.append(_gate("VALID_PROVENANCE_CHAIN", provenance_valid, "VALID_PROVENANCE_CHAIN_FAILED", {
        "mask_hash": branch.get("mask_hash"), "fraction_history_hash": branch.get("fraction_history_hash"), "geometry_id": basis.geometry_identity}))
    if not provenance_valid:
        return _blocked("VALID_PROVENANCE_CHAIN_FAILED", gates)

    repop_enabled = bool(parameters.get("repopulation_enabled", False))
    phi = 0.0
    repopulation: dict[str, Any]
    if not repop_enabled:
        repopulation = {"status": "DISABLED", "model": "NONE", "phi_rep": 0.0}
        gates.append(_gate("VALID_REPOPULATION_PARAMETERS", True, evidence={"status": "DISABLED"}, optional=True))
    else:
        try:
            overall = _nonnegative_parameter(parameters, "overall_treatment_time_days")
            kickoff = _nonnegative_parameter(parameters, "kickoff_time_days")
            doubling = _positive_parameter(parameters, "potential_doubling_time_days")
            phi = math.log(2.0) / doubling * max(0.0, overall - kickoff)
            status = "APPLIED" if overall > kickoff else "ZERO_BY_MODEL"
            repopulation = {"status": status, "model": "DELAYED_EXPONENTIAL", "overall_treatment_time_days": overall,
                            "kickoff_time_days": kickoff, "potential_doubling_time_days": doubling, "phi_rep": phi,
                            "timing_semantics": "course-level time; distinct from MLQ intra-fraction delivery time"}
            gates.append(_gate("VALID_REPOPULATION_PARAMETERS", True, evidence=repopulation))
        except ValueError as exc:
            repopulation = {"status": "UNAVAILABLE", "model": "DELAYED_EXPONENTIAL", "reason": str(exc), "phi_rep": None}
            gates.append(_gate("VALID_REPOPULATION_PARAMETERS", False, "VALID_REPOPULATION_PARAMETERS_FAILED", str(exc), optional=True))

    high_key = case.effective_structure_roles.get("VTV_H")
    valley_key = case.effective_structure_roles.get("VTV_L")
    partition = None
    if isinstance(high_key, str) and isinstance(valley_key, str) and high_key in masks and valley_key in masks:
        partition = {"vertex": masks[high_key], "valley": masks[valley_key]}
    gates.append(_gate("VALID_SPATIAL_PARTITION", partition is not None, "VALID_SPATIAL_PARTITION_UNAVAILABLE",
                       {"vertex_key": high_key, "valley_key": valley_key}, optional=True))
    summary, fields = compute_poisson_tcp(survival, tumour, voxel_cm3, density, phi_repopulation=phi, spatial_partition=partition)
    if repopulation["status"] == "UNAVAILABLE":
        summary["repopulation_corrected"] = None
    artifacts: dict[str, Any] = {"materialisation_status": "not_materialised"}
    should_materialise = case.configuration.layer31_materialise_full_maps_on_run if materialise_fields is None else bool(materialise_fields)
    if should_materialise:
        path = case.root / "derived" / "layer3_1" / f"{run_id}_tcp_fields.npz"
        path.parent.mkdir(parents=True, exist_ok=True)
        _deterministic_npz(path, fields)
        artifacts = {"materialisation_status": "materialised_on_request", "tcp_fields_path": str(path),
                     "tcp_fields_sha256": file_hash(path), "stored_fields": list(fields)}
    warnings = list(TCP_WARNINGS)
    if not repop_enabled:
        warnings.append("REPOPULATION_NOT_MODELLED")
    if repopulation["status"] == "UNAVAILABLE":
        warnings.append("REPOPULATION_PARAMETERS_UNAVAILABLE")
    result_id = f"{run_id}:3.1D:{canonical_hash({'branch': branch.get('input_hash'), 'tcp': parameters})[:16]}"
    sensitivity = _sensitivity(summary, survival, tumour, voxel_cm3, parameters, partition)
    return {
        "schema_version": TCP_RESULT_SCHEMA_VERSION, "result_id": result_id,
        "model_name": TCP_MODEL_NAME, "model_version": TCP_MODEL_VERSION,
        "status": "WARN", "calculation_status": "completed_with_warnings",
        "applicability_status": "APPLICABLE", "interpretation_status": "provisional",
        "gate_results": gates, "blocking_reasons": [], "warnings": warnings,
        "tcp_model": {"type": "POISSON_CLONOGEN", "radiation_survival_model": "MLQ", "spatial_aggregation": "VOXELWISE",
                      "clonogen_distribution": "UNIFORM", "repopulation_model": repopulation["model"]},
        "clonogen_model": {"type": "UNIFORM", "density": density, "units": units, "source": source,
                           "parameter_set_id": parameter_set_id, "parameter_hash": canonical_hash(parameters)},
        "repopulation": repopulation, "endpoints": summary,
        "active_tcp_endpoint": "TCP_MLQ_POISSON_REPOPULATION_CORRECTED" if repopulation["status"] in {"APPLIED", "ZERO_BY_MODEL"} else "TCP_MLQ_POISSON_RADIATION_ONLY",
        "source_context": {"mean_tumour_survival_fraction": branch.get("mean_tumour_survival_fraction"),
                           "tumour_eud_gy": branch.get("tumour_eud_gy"), "equivalent_log_survival_effect": branch.get("equivalent_log_survival_effect")},
        "assumptions": TCP_ASSUMPTIONS, "validation_status": ["SOFTWARE_VERIFIED", "MODEL_CONSISTENCY_VERIFIED", "BIOLOGICALLY_UNVALIDATED"],
        "sensitivity_analysis": sensitivity, "artifacts": artifacts,
        "provenance": {
            "source_layer_3_1B_result_id": branch.get("result_id") or branch.get("input_hash"), "source_survival_model": branch.get("formalism_id"),
            "source_survival_parameter_set_id": branch.get("parameter_set_id"), "source_survival_field_id": (branch.get("artifacts") or {}).get("survival_fields_sha256") or f"in_memory:{branch.get('input_hash')}",
            "source_eud_result_id": f"{branch.get('input_hash')}:EUD", "source_fractionation_history_id": branch.get("fraction_history_hash"),
            "source_treatment_timing_id": canonical_hash(branch.get("delivery_time_provenance")),
            "source_layer_3_1A_result_id": f"{run_id}:3.1A", "source_sbed_result_id": f"{run_id}:3.1A:sBED_LQ",
            "source_seqd2_result_id": f"{run_id}:3.1A:sEQD2_LQ",
            "source_tumour_mask_id": branch.get("mask_hash"), "source_reference_geometry_id": basis.geometry_identity,
            "software_version": __version__, "cache_key": canonical_hash({"source": branch.get("input_hash"), "mask": branch.get("mask_hash"),
                "parameters": parameters, "partition": [high_key, valley_key], "model_version": TCP_MODEL_VERSION}),
        },
    }


def _sensitivity(summary: dict[str, Any], survival: np.ndarray, tumour: np.ndarray, voxel_cm3: float,
                 parameters: dict[str, Any], partition: dict[str, np.ndarray] | None) -> dict[str, Any]:
    if not parameters.get("sensitivity_enabled"):
        return {"enabled": False, "status": "NOT_ASSESSED", "records": [], "reason": "TCP_SENSITIVITY_DISABLED"}
    densities = parameters.get("sensitivity_clonogen_density_values") or []
    records = []
    for raw in densities:
        density = float(raw)
        if not math.isfinite(density) or density <= 0:
            raise ValueError("TCP sensitivity clonogen densities must be finite and positive.")
        values, _fields = compute_poisson_tcp(survival, tumour, voxel_cm3, density, spatial_partition=partition)
        records.append({"parameter": "clonogen_density_per_cm3", "value": density,
                        "tcp_radiation_only": values["radiation_only"]["tcp"],
                        "expected_surviving_clonogens": values["radiation_only"]["expected_surviving_clonogens"]})
    return {"enabled": True, "status": "PASS", "records": records,
            "interpretation": "Deterministic parameter sensitivity range; not a statistical confidence interval."}
