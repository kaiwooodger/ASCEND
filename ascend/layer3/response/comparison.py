"""Strict paired-course comparison for Layer 3.1B research results."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ascend.validation.provenance import canonical_hash, file_hash


SCHEMA_VERSION = "ASCEND-L3.1B-paired-course-comparison-v1"
ALGORITHM_VERSION = "ASCEND-L3.1B-paired-course-comparison-v1.0"


def _arm(payload: dict[str, Any]) -> str | None:
    approach = str((payload.get("treatment_context") or {}).get("treatment_approach") or "")
    if approach == "LRT_ALONE":
        return "LRT"
    if approach in {"LRT_SEQUENTIAL_CERT", "LRT_INTEGRATED"}:
        return "LRT_PLUS_CERT"
    return None


def _schedule_contract(branch: dict[str, Any]) -> dict[str, Any]:
    schedule = branch.get("reference_schedule") or {}
    return {
        "fraction_count": schedule.get("fraction_count"),
        "delivery_times": schedule.get("delivery_times"),
        "time_unit": schedule.get("time_unit"),
    }


def not_configured() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
        "status": "NOT_ASSESSED", "calculation_status": "not_run",
        "applicability_status": "NOT_CONFIGURED",
        "reason": "PAIRED_COURSE_REFERENCE_NOT_CONFIGURED", "gate_results": [],
        "arms": {}, "comparison": None,
    }


def compare_course_results(current: dict[str, Any], reference: dict[str, Any], reference_path: Path) -> dict[str, Any]:
    """Compare LRT and LRT+cERT only after all scientific identity gates pass."""
    current_branch = current.get("layer3_1b_high_dose_sfrt_response") or {}
    reference_branch = reference.get("layer3_1b_high_dose_sfrt_response") or {}
    gates: list[dict[str, Any]] = []

    def gate(gate_id: str, passed: bool, reason: str, evidence: Any) -> None:
        gates.append({"gate_id": gate_id, "status": "PASS" if passed else "BLOCKED", "reason_code": None if passed else reason, "evidence": evidence})

    applicable = current_branch.get("applicability_status") == "APPLICABLE" and reference_branch.get("applicability_status") == "APPLICABLE"
    gate("BOTH_3_1B_RESULTS_APPLICABLE", applicable, "PAIRED_3_1B_RESULT_NOT_APPLICABLE", {
        "current": current_branch.get("applicability_status"), "reference": reference_branch.get("applicability_status")})
    current_arm, reference_arm = _arm(current), _arm(reference)
    arms_valid = {current_arm, reference_arm} == {"LRT", "LRT_PLUS_CERT"}
    gate("LRT_AND_LRT_PLUS_CERT_ARMS", arms_valid, "PAIRED_COURSE_ARMS_NOT_LRT_AND_LRT_PLUS_CERT", {
        "current": current_arm, "reference": reference_arm})
    parameter_match = current_branch.get("parameter_hash") is not None and current_branch.get("parameter_hash") == reference_branch.get("parameter_hash")
    gate("SAME_TUMOUR_PARAMETER_SET", parameter_match, "PAIRED_TUMOUR_PARAMETER_SET_MISMATCH", {
        "current": current_branch.get("parameter_hash"), "reference": reference_branch.get("parameter_hash")})
    current_schedule, reference_schedule = _schedule_contract(current_branch), _schedule_contract(reference_branch)
    schedule_match = canonical_hash(current_schedule) == canonical_hash(reference_schedule)
    gate("SAME_EUD_REFERENCE_SCHEDULE", schedule_match, "PAIRED_EUD_REFERENCE_SCHEDULE_MISMATCH", {
        "current": current_schedule, "reference": reference_schedule})
    current_geometry = (current.get("basis") or {}).get("geometry_identity")
    reference_geometry = (reference.get("basis") or {}).get("geometry_identity")
    geometry_match = current_geometry is not None and current_geometry == reference_geometry
    gate("COMPATIBLE_VALIDATED_GEOMETRY", geometry_match, "PAIRED_GEOMETRY_MISMATCH", {
        "current": current_geometry, "reference": reference_geometry})
    mask_match = current_branch.get("mask_hash") is not None and current_branch.get("mask_hash") == reference_branch.get("mask_hash")
    gate("SAME_VALIDATED_GTV_MASK", mask_match, "PAIRED_GTV_MASK_MISMATCH", {
        "current": current_branch.get("mask_hash"), "reference": reference_branch.get("mask_hash")})
    failed = [item["reason_code"] for item in gates if item["status"] != "PASS"]
    base = {
        "schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
        "gate_results": gates, "reference_result_path": str(reference_path),
        "reference_result_sha256": file_hash(reference_path),
        "comparison_scope": "modelled_paired_course_difference_not_clinical_outcome",
    }
    if failed:
        return {**base, "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
                "reason": failed[0], "blocking_reasons": failed, "arms": {}, "comparison": None}
    records: dict[str, Any] = {}
    for payload, branch, arm in ((current, current_branch, current_arm), (reference, reference_branch, reference_arm)):
        assert arm is not None
        sf = float(branch["mean_tumour_survival_fraction"]); eud = float(branch["tumour_eud_gy"])
        if not math.isfinite(sf) or sf <= 0 or sf > 1 or not math.isfinite(eud) or eud < 0:
            return {**base, "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
                    "reason": "PAIRED_COURSE_NUMERIC_RESULT_INVALID", "blocking_reasons": ["PAIRED_COURSE_NUMERIC_RESULT_INVALID"], "arms": {}, "comparison": None}
        records[arm] = {"run_id": payload.get("run_id"), "treatment_approach": (payload.get("treatment_context") or {}).get("treatment_approach"),
                        "mean_tumour_survival_fraction": sf, "equivalent_log_survival_effect": -math.log(sf), "tumour_eud_gy": eud}
    lrt, combined = records["LRT"], records["LRT_PLUS_CERT"]
    comparison = {
        "sf_difference_lrt_plus_cert_minus_lrt": combined["mean_tumour_survival_fraction"] - lrt["mean_tumour_survival_fraction"],
        "sf_ratio_lrt_plus_cert_over_lrt": combined["mean_tumour_survival_fraction"] / lrt["mean_tumour_survival_fraction"],
        "equivalent_log_survival_effect_difference": combined["equivalent_log_survival_effect"] - lrt["equivalent_log_survival_effect"],
        "eud_difference_gy_lrt_plus_cert_minus_lrt": combined["tumour_eud_gy"] - lrt["tumour_eud_gy"],
    }
    return {**base, "status": "WARN", "calculation_status": "completed_with_warnings", "applicability_status": "APPLICABLE",
            "interpretation_status": "provisional", "warnings": ["research_model_not_clinical_outcome"],
            "arms": records, "comparison": comparison,
            "input_hash": canonical_hash({"gates": gates, "arms": records})}


def load_and_compare(current: dict[str, Any], configured_path: str | None) -> dict[str, Any]:
    if not configured_path:
        return not_configured()
    path = Path(configured_path).expanduser().resolve()
    if not path.is_file():
        return {**not_configured(), "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
                "reason": "PAIRED_COURSE_REFERENCE_RESULT_NOT_FOUND", "reference_result_path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**not_configured(), "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
                "reason": "PAIRED_COURSE_REFERENCE_RESULT_INVALID", "detail": str(exc), "reference_result_path": str(path)}
    if not isinstance(payload, dict):
        return {**not_configured(), "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
                "reason": "PAIRED_COURSE_REFERENCE_RESULT_INVALID", "reference_result_path": str(path)}
    return compare_course_results(current, payload, path)
