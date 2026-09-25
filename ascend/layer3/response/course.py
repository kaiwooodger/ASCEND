"""Fraction-event Guerrero–Li tumour survival, EUD and modelled TR services."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import logsumexp

from ascend import __version__
from ascend.layer3.history import FractionHistory, GateResult
from ascend.layer3.lq.basis import _deterministic_npz
from ascend.validation.provenance import canonical_hash, file_hash

from .mlq import (
    MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, MLQ_SOURCE,
    TR_FORMALISM_ID, TR_FORMALISM_VERSION,
    NORMAL_SCENARIOS, TUMOUR_SCENARIOS,
    mlq_effect, solve_effect_eud, validate_mlq_parameter_set, with_scenario,
)

LAYER31B_SCOPE_EXCLUSIONS = [
    "TCP", "NTCP", "clonogen_density_modelling", "repopulation", "reoxygenation",
    "bystander_nonlocal_signalling", "vascular_effects", "immune_response",
    "distinct_peak_valley_survival_laws", "clinical_outcome_prediction",
]


def _blocked(formalism_id: str, version: str, reason: str, gates: list[GateResult], *, applicability: str = "BLOCKED") -> dict[str, Any]:
    not_run = applicability in {"NOT_APPLICABLE", "NOT_ASSESSED"}
    return {
        "formalism_id": formalism_id, "formalism_version": version,
        "status": applicability if not_run else "BLOCKED",
        "calculation_status": "not_run" if not_run else "blocked",
        "applicability_status": applicability, "interpretation_status": "not_interpretable",
        "gate_results": [gate.to_dict() for gate in gates],
        "warnings": [], "blocking_reasons": [reason], "reason": reason,
        "limitations": ["research_model", "not_tcp", "not_clinical_outcome_prediction"],
    }


def _target_mask(case: Any, masks: dict[str, np.ndarray]) -> tuple[str | None, np.ndarray | None]:
    key = case.effective_structure_roles.get("GTV")
    if not isinstance(key, str) or key not in masks:
        return None, None
    mask = np.asarray(masks[key], dtype=bool)
    return (key, mask) if mask.any() else (None, None)


def _roi_identity(layer1: dict[str, Any], key: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    for item in layer1.get("manifest", {}).get("roi_inventory", []):
        if item.get("canonical_mapping") == key and item.get("rasterisation_status") == "rasterised":
            mask_hash = layer1.get("manifest", {}).get("mask_export", {}).get("structures", {}).get(key, {}).get("mask_sha256")
            return item.get("roi_identity"), item.get("original_name"), mask_hash
    return None, None, None


def _event_delivery_times(history: FractionHistory, parameters: dict[str, Any]) -> tuple[list[float], list[dict[str, Any]]]:
    units = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    target_unit = parameters["time_unit"]
    result: list[float] = []
    evidence: list[dict[str, Any]] = []
    for event in history.events:
        if event.delivery_time is not None and event.delivery_time_unit:
            seconds = float(event.delivery_time) * units[event.delivery_time_unit]
            value = seconds / units[target_unit]
            source = "treatment_event"
        else:
            value = float(parameters["treatment_delivery_time"])
            source = str(parameters.get("delivery_time_source") or "explicit_parameter_set")
        result.append(value)
        evidence.append({
            "event_id": event.event_id, "delivery_time": value, "time_unit": target_unit,
            "source": source, "multiplicity": event.multiplicity,
        })
    return result, evidence


def _reference_schedule(case: Any, history: FractionHistory, parameters: dict[str, Any]) -> dict[str, Any] | None:
    configured = dict(case.configuration.layer31_tr_reference_schedule or {})
    component_sets = {tuple(event.physical_components) for event in history.events}
    sequential_mixed = history.treatment_approach == "LRT_SEQUENTIAL_CERT" and len(component_sets) > 1
    if sequential_mixed and not configured:
        return None
    event_times, _evidence = _event_delivery_times(history, parameters)
    if configured:
        count = int(configured.get("fraction_count") or sum(event.multiplicity for event in history.events))
        tau = configured.get("delivery_time")
        explicit_times = configured.get("delivery_times")
        times = [float(item) for item in explicit_times] if explicit_times is not None else (
            [float(tau)] * count if tau is not None else (
                [time for time, event in zip(event_times, history.events) for _ in range(event.multiplicity)]
                if count == sum(event.multiplicity for event in history.events)
                else [float(parameters["treatment_delivery_time"])] * count
            )
        )
        return {
            **configured, "fraction_count": count, "delivery_times": times,
            "time_unit": parameters["time_unit"], "source": configured.get("source") or "explicit_case_configuration",
        }
    return {
        "schedule_type": "matched_single_fraction" if sum(event.multiplicity for event in history.events) == 1 else "matched_fractionation",
        "fraction_count": sum(event.multiplicity for event in history.events),
        "delivery_times": [time for time, event in zip(event_times, history.events) for _ in range(event.multiplicity)],
        "time_unit": parameters["time_unit"], "source": "matched_reconstructed_fraction_history",
    }


def _course_effect(history: FractionHistory, parameters: dict[str, Any]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    times, evidence = _event_delivery_times(history, parameters)
    shape = history.events[0].combined_fraction_dose_field.shape
    total = np.zeros(shape, dtype=np.float64)
    total_flat = total.reshape(-1)
    for event, tau in zip(history.events, times):
        dose_flat = np.asarray(event.combined_fraction_dose_field, dtype=np.float32).reshape(-1)
        for start in range(0, dose_flat.size, 1_000_000):
            stop = min(start + 1_000_000, dose_flat.size)
            contribution = mlq_effect(dose_flat[start:stop], parameters, delivery_time=tau)
            total_flat[start:stop] += contribution * event.multiplicity
    return total, evidence


def _configured_oar_masks(
    case: Any,
    layer1: dict[str, Any],
    masks: dict[str, np.ndarray],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select exact identity-bound masks from the current Layer 1 archive.

    Layer 1 creates anatomical voxel masks.  Layer 3.1 never creates,
    reconstructs, propagates, or name-resolves anatomical masks; it only
    applies biological models to immutable Layer 1 masks.
    """
    inventory = layer1.get("manifest", {}).get("roi_inventory", [])
    by_identity = {
        (
            str((item.get("roi_identity") or {}).get("rtstruct_sop_instance_uid", "")),
            int((item.get("roi_identity") or {}).get("roi_number", -1)),
        ): item
        for item in inventory
        if (
            item.get("roi_identity")
            and item.get("rasterisation_status") == "rasterised"
            and item.get("dvh_verification_status") == "verified"
        )
    }
    structures = layer1.get("manifest", {}).get("mask_export", {}).get("structures", {})
    volume_definitions = layer1.get("manifest", {}).get("rasterisation", {}).get("volume_definitions", {})
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for configured in case.configuration.layer31c_oar_rois:
        identity = dict(configured)
        identity_key = (
            str(identity.get("rtstruct_sop_instance_uid", "")),
            int(identity.get("roi_number", -1)),
        )
        item = by_identity.get(identity_key)
        canonical = str((item or {}).get("canonical_mapping") or "")
        mask = masks.get(canonical)
        name = str((item or {}).get("original_name") or f"ROI {identity.get('roi_number', 'unresolved')}")
        if item is None or mask is None or not np.asarray(mask, dtype=bool).any():
            unresolved.append({
                "oar_name": name,
                "roi_identity": identity or None,
                "reason": "ROI_REQUIRES_IMPORTED_DVH_VERIFICATION_AND_LAYER1_RASTERISATION",
            })
            continue
        resolved_identity = dict(item["roi_identity"])
        volume = volume_definitions.get(canonical, {})
        resolved.append({
            "oar_name": name,
            "roi_identity": resolved_identity,
            "classification": "layer31c_selected_oar",
            "canonical_mapping": canonical,
            "mask": np.asarray(mask, dtype=bool),
            "mask_sha256": (structures.get(canonical) or {}).get("mask_sha256"),
            "dose_sampled_volume_cc": volume.get("dose_sampled_volume_cc"),
        })
    return resolved, unresolved


def _oar_eud_summary(
    oars: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    normal_effect: np.ndarray,
    parameters: dict[str, Any],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    """Summarise survival-equivalent normal-tissue EUD for each OAR."""
    records: list[dict[str, Any]] = []
    delivery_times = list(schedule["delivery_times"])
    for oar in oars:
        values = np.asarray(normal_effect[oar["mask"]], dtype=np.float64)
        log_mean = float(logsumexp(-values) - math.log(values.size))
        equivalent_effect = -log_mean
        solved = solve_effect_eud(equivalent_effect, parameters, delivery_times)
        records.append({
            "oar_name": oar["oar_name"],
            "roi_identity": oar["roi_identity"],
            "classification": oar.get("classification"),
            "canonical_mapping": oar["canonical_mapping"],
            "voxel_count": int(values.size),
            "dose_sampled_volume_cc": oar.get("dose_sampled_volume_cc"),
            "mean_normal_tissue_survival_fraction": float(math.exp(max(log_mean, math.log(np.finfo(np.float64).tiny)))),
            "log_mean_normal_tissue_survival": log_mean,
            "equivalent_log_survival_effect": equivalent_effect,
            "normal_tissue_eud_gy": float(solved["eud_gy"]),
            "solver": solved,
            "mask_sha256": oar.get("mask_sha256"),
        })
    return {
        "status": "WARN" if unresolved else "PASS",
        "calculation_status": "completed_with_warnings" if unresolved else "completed",
        "applicability_status": "APPLICABLE",
        "definition": "Per-OAR Guerrero–Li normal-tissue survival-equivalent EUD under the declared reference schedule",
        "reference_schedule": schedule,
        "normal_parameter_set_id": parameters["parameter_set_id"],
        "normal_parameter_hash": parameters["parameter_hash"],
        "records": records,
        "unresolved_oars": unresolved,
        "limitations": ["research_model", "not_ntcp", "not_toxicity_prediction", "not_clinical_constraint"],
    }


def _normal_tissue_display_fields(
    case: Any,
    history: FractionHistory,
) -> tuple[dict[str, np.ndarray], dict[str, Any] | None]:
    """Calculate OAR-display MLQ fields only from a complete normal model.

    Incomplete normal kinetics block 3.1C and OAR MLQ display without
    blocking the independent 3.1B tumour calculation.
    """
    raw = dict(case.configuration.layer31_mlq_normal_parameters or {})
    if not raw:
        return {}, None
    try:
        raw = with_scenario(raw, case.configuration.layer31_normal_scenario, tissue="normal")
        parameters = validate_mlq_parameter_set(raw, "normal tissue")
        effect, delivery_evidence = _course_effect(history, parameters)
    except ValueError:
        return {}, None
    survival = np.exp(np.clip(-effect, math.log(np.finfo(np.float64).tiny), 0.0))
    return {
        "voxel_survival_MLQ_normal_tissue": np.asarray(survival, dtype=np.float32),
    }, {
        "parameter_set_id": parameters["parameter_set_id"],
        "parameter_source": parameters["parameter_source"],
        "parameter_hash": parameters["parameter_hash"],
        "scenario_id": parameters.get("scenario_id"),
        "delivery_time_provenance": delivery_evidence,
    }


def run_fraction_resolved_tumour_response(
    case: Any,
    basis: Any,
    layer1: dict[str, Any],
    masks: dict[str, np.ndarray],
    history: FractionHistory,
    run_id: str,
    materialise_fields: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Calculate whole-GTV course survival, EUD and regional survivor decomposition."""
    gates = list(history.gate_results)
    raw = dict(case.configuration.layer31_mlq_tumour_parameters or {})
    if not raw:
        gate = GateResult("GATE_3_TISSUE_PARAMETERS", "BLOCKED", "MISSING_TUMOUR_PARAMETER_SET")
        return _blocked(
            MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, gate.reason_code or "", [*gates, gate],
            applicability="NOT_ASSESSED",
        ), None
    try:
        raw = with_scenario(raw, case.configuration.layer31_tumour_scenario, tissue="tumour")
        parameters = validate_mlq_parameter_set(raw, "tumour")
    except ValueError as exc:
        gate = GateResult("GATE_3_TISSUE_PARAMETERS", "BLOCKED", "INVALID_TUMOUR_PARAMETER_SET", str(exc))
        return _blocked(MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, f"INVALID_TUMOUR_PARAMETER_SET: {exc}", [*gates, gate]), None
    gtv_key, gtv = _target_mask(case, masks)
    if gtv_key is None or gtv is None:
        gate = GateResult("GATE_0_UPSTREAM_DATA", "BLOCKED", "MISSING_VALIDATED_GTV_MASK")
        return _blocked(MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, gate.reason_code or "", [*gates, gate]), None
    gates.append(GateResult("GATE_3_TISSUE_PARAMETERS", "PASS", evidence={
        "parameter_set_id": parameters["parameter_set_id"], "scenario_id": parameters.get("scenario_id"),
        "scenario_scope": parameters.get("scenario_scope"),
    }))
    try:
        effect, delivery_evidence = _course_effect(history, parameters)
    except ValueError as exc:
        gate = GateResult("GATE_4_DELIVERY_TIME", "BLOCKED", "DELIVERY_TIME_UNRESOLVED", str(exc))
        return _blocked(MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, gate.reason_code or "", [*gates, gate]), None
    gates.append(GateResult("GATE_4_DELIVERY_TIME", "PASS", evidence={"events": delivery_evidence}))
    effect_values = np.asarray(effect[gtv], dtype=np.float64)
    log_mean_sf = float(logsumexp(-effect_values) - math.log(effect_values.size))
    equivalent_log_survival_effect = -log_mean_sf
    mean_sf = float(math.exp(max(log_mean_sf, math.log(np.finfo(np.float64).tiny))))
    warnings: list[str] = ["high_dose_sfrt_formalism_provisional"]
    if log_mean_sf < math.log(np.finfo(np.float64).tiny):
        warnings.append("mean_survival_underflow_clipped_for_display")
    schedule = _reference_schedule(case, history, parameters)
    eud_record: dict[str, Any] | None = None
    if schedule is None:
        warnings.append("EUD_REFERENCE_SCHEDULE_UNDEFINED")
    else:
        try:
            eud_record = solve_effect_eud(equivalent_log_survival_effect, parameters, list(schedule["delivery_times"]))
        except (ValueError, RuntimeError) as exc:
            gate = GateResult("GATE_5_EUD_INVERSION", "BLOCKED", "EUD_INVERSION_FAILED", str(exc))
            return _blocked(MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, f"EUD_INVERSION_FAILED: {exc}", [*gates, gate]), None
        gates.append(GateResult("GATE_5_EUD_INVERSION", "PASS", evidence=eud_record))
    survival = np.exp(np.clip(-effect, math.log(np.finfo(np.float64).tiny), 0.0))
    regional = _regional_survival(case, masks, gtv, survival, mean_sf)
    artifacts: dict[str, Any] = {"materialisation_status": "not_materialised"}
    if materialise_fields:
        artifact = case.root / "derived" / "layer3_1" / f"{run_id}_tumour_survival_fields.npz"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        # Effect is exactly -log(SF) and is reconstructed for display.  Store
        # only the authoritative survival field and avoid a duplicate volume.
        stored_arrays = {
            "voxel_survival_MLQ": np.asarray(survival, dtype=np.float32),
            "GTV_mask": gtv.astype(np.uint8),
        }
        normal_fields, normal_provenance = _normal_tissue_display_fields(case, history)
        stored_arrays.update(normal_fields)
        _deterministic_npz(artifact, stored_arrays)
        stored_fields = list(stored_arrays)
        derived_display_fields = {"course_effect_MLQ": "-ln(voxel_survival_MLQ)"}
        if normal_fields:
            derived_display_fields["course_effect_MLQ_normal_tissue"] = "-ln(voxel_survival_MLQ_normal_tissue)"
        artifacts = {
            "materialisation_status": "materialised_on_request",
            "survival_fields_path": str(artifact),
            "survival_fields_sha256": file_hash(artifact),
            "stored_fields": stored_fields,
            "derived_display_fields": derived_display_fields,
            "normal_tissue_field_provenance": normal_provenance,
        }
    identity, roi_name, mask_hash = _roi_identity(layer1, gtv_key)
    result = {
        "formalism_id": MLQ_FORMALISM_ID, "formalism_version": MLQ_FORMALISM_VERSION,
        "status": "WARN", "calculation_status": "completed_with_warnings",
        "applicability_status": "APPLICABLE", "interpretation_status": "provisional",
        "gate_results": [gate.to_dict() for gate in gates], "blocking_reasons": [], "warnings": warnings,
        "mean_tumour_survival_fraction": mean_sf, "log_mean_tumour_survival": log_mean_sf,
        "equivalent_log_survival_effect": equivalent_log_survival_effect,
        "equivalent_log_survival_effect_definition": "K_T,eq = -ln(SF_T)",
        "tumour_eud_gy": eud_record["eud_gy"] if eud_record else None,
        "eud_applicability": "APPLICABLE" if eud_record else "NOT_APPLICABLE",
        "eud_reason": None if eud_record else "EUD_REFERENCE_SCHEDULE_UNDEFINED",
        "solver": eud_record, "reference_schedule": schedule,
        "regional_survival": regional,
        "model_parameters": parameters, "parameter_set_id": parameters["parameter_set_id"],
        "parameter_source": parameters["parameter_source"], "parameter_hash": parameters["parameter_hash"],
        "scenario_id": parameters.get("scenario_id"), "scenario_scope": parameters.get("scenario_scope"),
        "delivery_time_provenance": delivery_evidence,
        "fraction_history": history.metadata(), "fraction_history_hash": history.history_hash,
        "dose_hash": canonical_hash(basis.source_hashes), "roi_identity": identity, "roi_name": roi_name,
        "mask_hash": mask_hash, "software_version": __version__, "model_source": MLQ_SOURCE,
        "artifacts": artifacts,
        "limitations": ["research_model", "not_patient_specific_radiosensitivity", *LAYER31B_SCOPE_EXCLUSIONS],
        "provenance": {
            "basis_hash": basis.basis_hash, "geometry_hash": basis.geometry_identity,
            "calculation_version": MLQ_FORMALISM_VERSION, "effect_space_accumulation": True,
            "voxel_volume_weighting": "uniform_validated_grid_voxel_volume",
        },
    }
    # Preserve the stable v1 presentation contract while the authoritative
    # implementation now evaluates a reconstructed fraction-event course.
    result.update({
        "solver_status": eud_record.get("solver_status") if eud_record else None,
        "solver_iterations": eud_record.get("solver_iterations") if eud_record else None,
        "residual": eud_record.get("residual") if eud_record else None,
        "solver_tolerance": eud_record.get("solver_tolerance") if eud_record else None,
        "dose_distribution_source": "fraction_event_voxel_effect_accumulation",
        "dose_distribution_summary": {
            "voxel_count": int(effect_values.size),
            "dose_min_gy": float(np.min(np.asarray(basis.p_map)[gtv])),
            "dose_mean_gy": float(np.mean(np.asarray(basis.p_map)[gtv])),
            "dose_max_gy": float(np.max(np.asarray(basis.p_map)[gtv])),
        },
        "input_hash": canonical_hash({
            "fraction_history_hash": history.history_hash,
            "parameter_hash": parameters["parameter_hash"],
            "mask_hash": mask_hash,
            "reference_schedule": schedule,
        }),
    })
    result["result_id"] = f"{run_id}:3.1B:{result['input_hash'][:16]}"
    state = {"history": history, "gtv_mask": gtv, "effect": effect, "survival": survival, "parameters": parameters,
             "reference_schedule": schedule, "eud": result["tumour_eud_gy"], "result": result}
    return result, state


def run_tumour_alpha_beta_sensitivity(
    case: Any,
    masks: dict[str, np.ndarray],
    history: FractionHistory,
    tumour_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Sweep a declared tumour-site alpha/beta range without changing dose or kinetics."""
    config = dict(case.configuration.layer31_tumour_alpha_beta_sensitivity or {})
    if not config.get("enabled"):
        return {
            "status": "NOT_ASSESSED", "calculation_status": "not_run",
            "applicability_status": "NOT_ASSESSED", "reason": "TUMOUR_ALPHA_BETA_SENSITIVITY_DISABLED",
            "enabled": False, "records": [],
        }
    if not tumour_state:
        return {
            "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
            "reason": "VALID_LAYER_3_1B_TUMOUR_STATE_REQUIRED", "enabled": True, "records": [],
        }
    try:
        minimum = float(config["minimum_alpha_beta_gy"])
        maximum = float(config["maximum_alpha_beta_gy"])
        sample_count = int(config["sample_count"])
        scaling = str(config["parameter_scaling"])
        tumour_site = str(config["tumour_site"]).strip()
        source = str(config["source"]).strip()
        if not tumour_site or not source or scaling not in {"hold_alpha", "hold_beta"}:
            raise ValueError("Tumour site, source, and a valid parameter-scaling rule are required.")
        if minimum <= 0 or maximum <= minimum or sample_count < 2 or sample_count > 101:
            raise ValueError("Sensitivity range requires 0 < minimum < maximum and 2-101 samples.")
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
            "reason": f"INVALID_TUMOUR_ALPHA_BETA_SENSITIVITY: {exc}", "enabled": True, "records": [],
        }
    baseline = dict(tumour_state["parameters"])
    baseline_alpha = float(baseline["alpha_per_gy"])
    baseline_beta = float(baseline["beta_per_gy2"])
    baseline_ratio = baseline_alpha / baseline_beta
    gtv = np.asarray(tumour_state["gtv_mask"], dtype=bool)
    if not gtv.any():
        return {
            "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
            "reason": "MISSING_VALIDATED_GTV_MASK", "enabled": True, "records": [],
        }
    records: list[dict[str, Any]] = []
    try:
        for index, ratio in enumerate(np.linspace(minimum, maximum, sample_count), 1):
            alpha = baseline_alpha if scaling == "hold_alpha" else baseline_beta * float(ratio)
            beta = baseline_alpha / float(ratio) if scaling == "hold_alpha" else baseline_beta
            raw = {
                key: value for key, value in baseline.items()
                if key not in {
                    "parameter_hash", "scenario_id", "scenario_scope", "scenario_parameter_doi",
                    "scenario_parameter_override", "scenario_parameter_source",
                }
            }
            raw.update({
                "parameter_set_id": f"{baseline['parameter_set_id']}:alpha-beta-sensitivity:{index}",
                "parameter_source": f"{baseline['parameter_source']}; sensitivity rationale: {source}",
                "alpha_per_gy": alpha, "beta_per_gy2": beta, "alpha_beta_gy": float(ratio),
                "scenario_sf2": math.exp(-2.0 * alpha - 4.0 * beta),
                "scenario_scope": "user_declared_tumour_site_alpha_beta_sensitivity",
            })
            parameters = validate_mlq_parameter_set(raw, "tumour alpha/beta sensitivity")
            effect, _delivery = _course_effect(history, parameters)
            effect_values = np.asarray(effect[gtv], dtype=np.float64)
            log_mean = float(logsumexp(-effect_values) - math.log(effect_values.size))
            equivalent_effect = -log_mean
            mean_sf = float(math.exp(max(log_mean, math.log(np.finfo(np.float64).tiny))))
            schedule = _reference_schedule(case, history, parameters)
            eud = None
            solver_status = "not_assessed"
            if schedule is not None:
                solved = solve_effect_eud(equivalent_effect, parameters, list(schedule["delivery_times"]))
                eud = float(solved["eud_gy"])
                solver_status = str(solved["solver_status"])
            survival = np.exp(np.clip(-effect, math.log(np.finfo(np.float64).tiny), 0.0))
            records.append({
                "alpha_beta_gy": float(ratio), "alpha_per_gy": alpha, "beta_per_gy2": beta,
                "sf2": raw["scenario_sf2"], "mean_tumour_survival_fraction": mean_sf,
                "equivalent_log_survival_effect": equivalent_effect, "tumour_eud_gy": eud,
                "solver_status": solver_status,
                "regional_survival": _regional_survival(case, masks, gtv, survival, mean_sf),
                "parameter_hash": parameters["parameter_hash"],
                "is_baseline_ratio": math.isclose(float(ratio), baseline_ratio, rel_tol=1.0e-9, abs_tol=1.0e-12),
            })
    except (ValueError, RuntimeError) as exc:
        return {
            "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
            "reason": f"TUMOUR_ALPHA_BETA_SENSITIVITY_FAILED: {exc}", "enabled": True, "records": records,
        }
    return {
        "status": "WARN", "calculation_status": "completed_with_warnings",
        "applicability_status": "APPLICABLE", "interpretation_status": "provisional",
        "enabled": True, "tumour_site": tumour_site, "source": source,
        "parameter_scaling": scaling,
        "fixed_parameter": "alpha_per_gy" if scaling == "hold_alpha" else "beta_per_gy2",
        "baseline_alpha_beta_gy": baseline_ratio,
        "baseline_alpha_per_gy": baseline_alpha, "baseline_beta_per_gy2": baseline_beta,
        "minimum_alpha_beta_gy": minimum, "maximum_alpha_beta_gy": maximum,
        "sample_count": sample_count, "records": records,
        "fraction_history_hash": history.history_hash,
        "limitations": [
            "exploratory_sensitivity_analysis", "tumour_site_label_not_patient_specific_radiosensitivity",
            "one_parameter_held_constant", "not_clinical_outcome_prediction",
        ],
        "input_hash": canonical_hash({
            "configuration": config, "baseline_parameter_hash": baseline["parameter_hash"],
            "fraction_history_hash": history.history_hash,
        }),
    }


def _regional_survival(case: Any, masks: dict[str, np.ndarray], gtv: np.ndarray, survival: np.ndarray, mean_total: float) -> dict[str, Any]:
    high_key = case.effective_structure_roles.get("VTV_H")
    valley_key = case.effective_structure_roles.get("VTV_L")
    high = np.asarray(masks.get(high_key, np.zeros_like(gtv)), dtype=bool) & gtv if isinstance(high_key, str) else np.zeros_like(gtv)
    valley = np.asarray(masks.get(valley_key, np.zeros_like(gtv)), dtype=bool) & gtv & ~high if isinstance(valley_key, str) else np.zeros_like(gtv)
    other = gtv & ~high & ~valley
    records = []
    for region_id, mask in (("H", high), ("V", valley), ("O", other)):
        fraction = float(mask.sum()) / float(gtv.sum())
        mean = float(np.mean(survival[mask])) if mask.any() else None
        contribution = fraction * mean / mean_total if mean is not None and mean_total > 0 else 0.0
        records.append({
            "region_id": region_id, "voxel_count": int(mask.sum()), "tumour_volume_fraction": fraction,
            "mean_surviving_fraction": mean, "survivor_contribution_fraction": contribution,
        })
    total = sum(item["survivor_contribution_fraction"] for item in records)
    high_fraction_pct = 100.0 * float(high.sum()) / float(gtv.sum())
    layer21_metric = next((item for item in (case.layer2_1.result or {}).get("harmonised_metrics", [])
                           if item.get("metric_id") == "high_dose_volume_fraction"), None)
    reconciliation: dict[str, Any] = {
        "status": "NOT_ASSESSED", "reason": "LAYER2_1_HIGH_DOSE_VOLUME_FRACTION_UNAVAILABLE",
        "layer3_1b_value_pct": high_fraction_pct,
        "layer3_1b_basis": "validated_uniform_dose_grid_voxels_within_GTV",
    }
    if layer21_metric and layer21_metric.get("value") is not None:
        descriptors = layer21_metric.get("descriptors") or {}
        reported = float(layer21_metric["value"])
        dose_sampled = descriptors.get("high_dose_volume_fraction_dose_sampled_pct")
        reconciliation = {
            "status": "VALID", "layer2_1_reported_value_pct": reported,
            "layer2_1_reported_basis": descriptors.get("volume_basis") or "unspecified",
            "layer2_1_dose_sampled_value_pct": float(dose_sampled) if dose_sampled is not None else None,
            "layer3_1b_value_pct": high_fraction_pct,
            "layer3_1b_basis": "validated_uniform_dose_grid_voxels_within_GTV",
            "reported_difference_percentage_points_layer31b_minus_layer21": high_fraction_pct - reported,
            "dose_sampled_difference_percentage_points_layer31b_minus_layer21": (
                high_fraction_pct - float(dose_sampled) if dose_sampled is not None else None
            ),
            "interpretation": "Layer 3.1B decomposition requires dose-grid voxel weights; Layer 2.1 may report a contour-stack physical volume fraction.",
        }
    return {"records": records, "contribution_sum": total, "sum_residual": abs(total - 1.0),
            "high_dose_fraction_reconciliation": reconciliation}


def run_fraction_resolved_therapeutic_ratio(
    case: Any,
    tumour_state: dict[str, Any] | None,
    layer1: dict[str, Any] | None = None,
    masks: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Calculate theoretical normal-cell survival ratio under an audited comparator schedule."""
    if tumour_state is None:
        return _blocked(TR_FORMALISM_ID, TR_FORMALISM_VERSION, "TUMOUR_MLQ_RESULT_UNAVAILABLE", [], applicability="NOT_APPLICABLE")
    tumour_result = tumour_state["result"]
    history: FractionHistory = tumour_state["history"]
    schedule = tumour_state["reference_schedule"]
    if schedule is None or tumour_state["eud"] is None:
        gate = GateResult("GATE_6_TR_REFERENCE_SCHEDULE", "NOT_APPLICABLE", "TR_REFERENCE_SCHEDULE_UNDEFINED")
        return _blocked(TR_FORMALISM_ID, TR_FORMALISM_VERSION, gate.reason_code or "", [gate], applicability="NOT_APPLICABLE")
    if not case.configuration.layer31c_oar_rois:
        gate = GateResult("GATE_7_NORMAL_TISSUE_SCOPE", "NOT_ASSESSED", "NO_LAYER31C_OAR_CONFIGURED")
        return _blocked(
            TR_FORMALISM_ID, TR_FORMALISM_VERSION, gate.reason_code or "", [gate],
            applicability="NOT_ASSESSED",
        )
    raw = dict(case.configuration.layer31_mlq_normal_parameters or {})
    if not raw:
        gate = GateResult("GATE_3_TISSUE_PARAMETERS", "BLOCKED", "MISSING_NORMAL_TISSUE_PARAMETER_SET")
        return _blocked(
            TR_FORMALISM_ID, TR_FORMALISM_VERSION, gate.reason_code or "", [gate],
            applicability="NOT_ASSESSED",
        )
    try:
        raw = with_scenario(raw, case.configuration.layer31_normal_scenario, tissue="normal")
        parameters = validate_mlq_parameter_set(raw, "normal tissue")
    except ValueError as exc:
        gate = GateResult("GATE_3_TISSUE_PARAMETERS", "BLOCKED", "INVALID_NORMAL_TISSUE_PARAMETER_SET", str(exc))
        return _blocked(TR_FORMALISM_ID, TR_FORMALISM_VERSION, f"INVALID_NORMAL_TISSUE_PARAMETER_SET: {exc}", [gate])
    normal_effect, delivery_evidence = _course_effect(history, parameters)
    configured_oars: list[dict[str, Any]] = []
    unresolved_oars: list[dict[str, Any]] = []
    if layer1 is not None and masks is not None:
        configured_oars, unresolved_oars = _configured_oar_masks(case, layer1, masks)
    if unresolved_oars:
        gate = GateResult(
            "GATE_7_NORMAL_TISSUE_SCOPE", "BLOCKED", "ROI_REQUIRES_LAYER1_RASTERISATION",
            evidence={"unresolved_oars": unresolved_oars},
        )
        return _blocked(TR_FORMALISM_ID, TR_FORMALISM_VERSION, gate.reason_code or "", [gate])
    normal_scope_mask = np.logical_or.reduce([item["mask"] for item in configured_oars])
    normal_scope = "union_of_validated_configured_oars"
    values = normal_effect[normal_scope_mask]
    log_actual = float(logsumexp(-values) - math.log(values.size))
    log_tiny = math.log(np.finfo(np.float64).tiny)
    log_max = math.log(np.finfo(np.float64).max)
    actual = float(math.exp(max(log_actual, log_tiny)))
    count = int(schedule["fraction_count"])
    uniform_dose = float(tumour_state["eud"]) / count
    times = list(schedule["delivery_times"])
    reference_effect = float(sum(mlq_effect(np.asarray([uniform_dose]), parameters, delivery_time=tau)[0] for tau in times))
    log_reference = -reference_effect
    if not math.isfinite(log_reference):
        return _blocked(TR_FORMALISM_ID, TR_FORMALISM_VERSION, "TR_REFERENCE_SURVIVAL_INVALID", [])
    reference = float(math.exp(max(log_reference, log_tiny)))
    log_ratio = log_actual - log_reference
    if log_ratio > log_max:
        ratio = None
        numerical_status = "OVERFLOW_REPORTED_IN_LOG_DOMAIN"
    elif log_ratio < log_tiny:
        ratio = 0.0
        numerical_status = "UNDERFLOW_REPORTED_IN_LOG_DOMAIN"
    else:
        ratio = float(math.exp(log_ratio))
        numerical_status = "FINITE"
    raw_ratio = ratio
    raw_log_ratio = log_ratio
    if ratio is not None and abs(ratio - 1.0) <= 1.0e-10:
        ratio = 1.0
        log_ratio = 0.0
    oar_summary = _oar_eud_summary(configured_oars, [], normal_effect, parameters, schedule)
    warnings = ["theoretical_modelled_therapeutic_ratio", "not_clinical_benefit"]
    return {
        "formalism_id": TR_FORMALISM_ID, "formalism_version": TR_FORMALISM_VERSION,
        "status": "WARN", "calculation_status": "completed_with_warnings",
        "applicability_status": "APPLICABLE", "interpretation_status": "provisional",
        "gate_results": [GateResult("GATE_6_TR_REFERENCE_SCHEDULE", "PASS", evidence=schedule).to_dict()],
        "blocking_reasons": [], "warnings": warnings,
        "modelled_therapeutic_ratio": ratio,
        "modelled_therapeutic_ratio_unsnapped": raw_ratio,
        "log_modelled_therapeutic_ratio": log_ratio,
        "log10_modelled_therapeutic_ratio": log_ratio / math.log(10.0),
        "log_modelled_therapeutic_ratio_unsnapped": raw_log_ratio,
        "numerical_status": numerical_status,
        "unity_snap_tolerance": 1.0e-10,
        "tumour_eud_gy": tumour_state["eud"],
        "tumour_mean_survival_fraction": tumour_result["mean_tumour_survival_fraction"],
        "normal_mean_survival_lrt": actual, "normal_log_mean_survival_lrt": log_actual,
        "normal_tissue_scope": normal_scope,
        "normal_tissue_voxel_count": int(values.size),
        "normal_survival_at_tumour_eud": reference,
        "normal_log_survival_at_tumour_eud": log_reference,
        "oar_eud_summary": oar_summary,
        "reference_schedule": schedule,
        "tumour_parameter_set": tumour_result["model_parameters"], "normal_parameter_set": parameters,
        "tumour_scenario": tumour_result.get("scenario_id"), "normal_scenario": parameters.get("scenario_id"),
        "parameter_set_id": parameters["parameter_set_id"], "parameter_source": parameters["parameter_source"],
        "parameter_hash": parameters["parameter_hash"], "delivery_time_provenance": delivery_evidence,
        "fraction_history_hash": history.history_hash,
        "limitations": ["model_derived_comparison", "not_clinical_therapeutic_ratio", "not_ntcp", "not_toxicity_prediction", "no_pass_fail"],
        "provenance": {"formalism_source": MLQ_SOURCE, "calculation_version": TR_FORMALISM_VERSION,
                       "input_hash": canonical_hash({"tumour": tumour_result.get("parameter_hash"), "normal": parameters["parameter_hash"], "history": history.history_hash, "schedule": schedule})},
    }


def run_sensitivity_scenario_matrix(
    case: Any,
    masks: dict[str, np.ndarray],
    history: FractionHistory,
) -> dict[str, Any]:
    """Evaluate the standard C1–C3 × N1–N3 sensitivity grid.

    The scenarios replace only alpha and beta. Kinetic and delivery-time
    parameters remain explicit case inputs and are never populated here.
    """
    tumour_base = dict(case.configuration.layer31_mlq_tumour_parameters or {})
    normal_base = dict(case.configuration.layer31_mlq_normal_parameters or {})
    if not tumour_base or not normal_base:
        return {
            "status": "NOT_ASSESSED", "applicability_status": "NOT_ASSESSED",
            "reason": "MISSING_SCENARIO_KINETIC_PARAMETER_BASE", "records": [],
        }
    gtv_key, gtv = _target_mask(case, masks)
    if gtv_key is None or gtv is None:
        return {"status": "BLOCKED", "applicability_status": "BLOCKED", "reason": "MISSING_VALIDATED_GTV_MASK", "records": []}
    excluded = {
        "alpha_per_gy", "beta_per_gy2", "alpha_beta_gy", "scenario_id", "scenario_sf2", "scenario_scope",
        "scenario_parameter_override", "scenario_parameter_source", "scenario_parameter_doi", "parameter_hash",
    }
    tumour_base = {key: value for key, value in tumour_base.items() if key not in excluded}
    normal_base = {key: value for key, value in normal_base.items() if key not in excluded}
    records: list[dict[str, Any]] = []
    try:
        for tumour_scenario in TUMOUR_SCENARIOS:
            tumour = validate_mlq_parameter_set(with_scenario(tumour_base, tumour_scenario, tissue="tumour"), "tumour")
            tumour_effect, _tumour_delivery = _course_effect(history, tumour)
            tumour_values = tumour_effect[gtv]
            tumour_log_mean = float(logsumexp(-tumour_values) - math.log(tumour_values.size))
            schedule = _reference_schedule(case, history, tumour)
            if schedule is None:
                for normal_scenario in NORMAL_SCENARIOS:
                    records.append({
                        "tumour_scenario": tumour_scenario, "normal_scenario": normal_scenario,
                        "applicability_status": "NOT_APPLICABLE", "reason": "TR_REFERENCE_SCHEDULE_UNDEFINED",
                        "therapeutic_ratio": None,
                    })
                continue
            eud_record = solve_effect_eud(-tumour_log_mean, tumour, list(schedule["delivery_times"]))
            for normal_scenario in NORMAL_SCENARIOS:
                normal = validate_mlq_parameter_set(with_scenario(normal_base, normal_scenario, tissue="normal"), "normal tissue")
                normal_effect, _normal_delivery = _course_effect(history, normal)
                normal_values = normal_effect[gtv]
                normal_log_actual = float(logsumexp(-normal_values) - math.log(normal_values.size))
                count = int(schedule["fraction_count"])
                uniform = float(eud_record["eud_gy"]) / count
                reference_effect = float(sum(
                    mlq_effect(np.asarray([uniform]), normal, delivery_time=tau)[0]
                    for tau in schedule["delivery_times"]
                ))
                log_ratio = normal_log_actual + reference_effect
                ratio = float(math.exp(np.clip(log_ratio, -700.0, 700.0)))
                if abs(ratio - 1.0) <= 1.0e-10:
                    ratio = 1.0
                records.append({
                    "tumour_scenario": tumour_scenario, "normal_scenario": normal_scenario,
                    "applicability_status": "APPLICABLE", "reason": None,
                    "therapeutic_ratio": ratio,
                    "tumour_eud_gy": float(eud_record["eud_gy"]),
                    "tumour_mean_survival_fraction": float(math.exp(max(tumour_log_mean, math.log(np.finfo(np.float64).tiny)))),
                    "normal_mean_survival_actual": float(math.exp(max(normal_log_actual, math.log(np.finfo(np.float64).tiny)))),
                    "normal_survival_reference": float(math.exp(max(-reference_effect, math.log(np.finfo(np.float64).tiny)))),
                    "reference_schedule": schedule,
                    "tumour_parameter_hash": tumour["parameter_hash"],
                    "normal_parameter_hash": normal["parameter_hash"],
                })
    except (ValueError, RuntimeError) as exc:
        return {"status": "BLOCKED", "applicability_status": "BLOCKED", "reason": str(exc), "records": records}
    applicable = sum(item["applicability_status"] == "APPLICABLE" for item in records)
    return {
        "status": "PASS" if applicable == 9 else "NOT_APPLICABLE",
        "applicability_status": "APPLICABLE" if applicable else "NOT_APPLICABLE",
        "reason": None if applicable else "TR_REFERENCE_SCHEDULE_UNDEFINED",
        "records": records,
        "scenario_scope": "standardised_sensitivity_scenarios_not_patient_specific",
        "fraction_history_hash": history.history_hash,
    }
