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
    return {
        "formalism_id": formalism_id, "formalism_version": version,
        "status": "NOT_APPLICABLE" if applicability == "NOT_APPLICABLE" else "BLOCKED",
        "calculation_status": "not_run" if applicability == "NOT_APPLICABLE" else "blocked",
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
        evidence.append({"event_id": event.event_id, "delivery_time": value, "time_unit": target_unit, "source": source})
    return result, evidence


def _reference_schedule(case: Any, history: FractionHistory, parameters: dict[str, Any]) -> dict[str, Any] | None:
    configured = dict(case.configuration.layer31_tr_reference_schedule or {})
    component_sets = {tuple(event.physical_components) for event in history.events}
    sequential_mixed = history.treatment_approach == "LRT_SEQUENTIAL_CERT" and len(component_sets) > 1
    if sequential_mixed and not configured:
        return None
    event_times, _evidence = _event_delivery_times(history, parameters)
    if configured:
        count = int(configured.get("fraction_count") or len(history.events))
        tau = configured.get("delivery_time")
        explicit_times = configured.get("delivery_times")
        times = [float(item) for item in explicit_times] if explicit_times is not None else (
            [float(tau)] * count if tau is not None else (
                event_times if count == len(event_times) else [float(parameters["treatment_delivery_time"])] * count
            )
        )
        return {
            **configured, "fraction_count": count, "delivery_times": times,
            "time_unit": parameters["time_unit"], "source": configured.get("source") or "explicit_case_configuration",
        }
    return {
        "schedule_type": "matched_single_fraction" if len(history.events) == 1 else "matched_fractionation",
        "fraction_count": len(history.events), "delivery_times": event_times,
        "time_unit": parameters["time_unit"], "source": "matched_reconstructed_fraction_history",
    }


def _course_effect(history: FractionHistory, parameters: dict[str, Any]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    times, evidence = _event_delivery_times(history, parameters)
    shape = history.events[0].combined_fraction_dose_field.shape
    total = np.zeros(shape, dtype=np.float64)
    for event, tau in zip(history.events, times):
        np.add(total, mlq_effect(event.combined_fraction_dose_field, parameters, delivery_time=tau), out=total)
    return total, evidence


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
        _deterministic_npz(artifact, {
            "voxel_survival_MLQ": np.asarray(survival, dtype=np.float32),
            "GTV_mask": gtv.astype(np.uint8),
        })
        artifacts = {
            "materialisation_status": "materialised_on_request",
            "survival_fields_path": str(artifact),
            "survival_fields_sha256": file_hash(artifact),
            "stored_fields": ["voxel_survival_MLQ", "GTV_mask"],
            "derived_display_fields": {"course_effect_MLQ": "-ln(voxel_survival_MLQ)"},
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
    values = normal_effect[tumour_state["gtv_mask"]]
    log_actual = float(logsumexp(-values) - math.log(values.size))
    actual = float(math.exp(max(log_actual, math.log(np.finfo(np.float64).tiny))))
    count = int(schedule["fraction_count"])
    uniform_dose = float(tumour_state["eud"]) / count
    times = list(schedule["delivery_times"])
    reference_effect = float(sum(mlq_effect(np.asarray([uniform_dose]), parameters, delivery_time=tau)[0] for tau in times))
    reference = float(math.exp(-reference_effect))
    if reference <= 0 or not math.isfinite(reference):
        return _blocked(TR_FORMALISM_ID, TR_FORMALISM_VERSION, "TR_REFERENCE_SURVIVAL_INVALID", [])
    ratio = actual / reference
    raw_ratio = ratio
    if abs(ratio - 1.0) <= 1.0e-10:
        ratio = 1.0
    return {
        "formalism_id": TR_FORMALISM_ID, "formalism_version": TR_FORMALISM_VERSION,
        "status": "WARN", "calculation_status": "completed_with_warnings",
        "applicability_status": "APPLICABLE", "interpretation_status": "provisional",
        "gate_results": [GateResult("GATE_6_TR_REFERENCE_SCHEDULE", "PASS", evidence=schedule).to_dict()],
        "blocking_reasons": [], "warnings": ["theoretical_modelled_therapeutic_ratio", "not_clinical_benefit"],
        "modelled_therapeutic_ratio": ratio,
        "modelled_therapeutic_ratio_unsnapped": raw_ratio,
        "unity_snap_tolerance": 1.0e-10,
        "tumour_eud_gy": tumour_state["eud"],
        "tumour_mean_survival_fraction": tumour_result["mean_tumour_survival_fraction"],
        "normal_mean_survival_lrt": actual, "normal_log_mean_survival_lrt": log_actual,
        "normal_survival_at_tumour_eud": reference,
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
    excluded = {"alpha_per_gy", "beta_per_gy2", "alpha_beta_gy", "scenario_id", "scenario_sf2", "scenario_scope", "parameter_hash"}
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
