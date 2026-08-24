"""Service-layer orchestration for the enclosing ASCEND package."""

from __future__ import annotations

from typing import Any

from ascend.scientific.legacy.layer21_validated import PRIMARY_IDS
from ascend.treatment.applicability import resolve_all_metric_applicability

from .fixtures import STRUCTURES, mismatch_scenarios, scenarios


def _validate_scenario(scenario: Any) -> dict[str, Any]:
    decisions = resolve_all_metric_applicability(scenario.context, STRUCTURES, PRIMARY_IDS)
    records = []
    for decision in decisions:
        expected_applicability, expected_reason_or_warning = scenario.expected[decision.metric_id]
        evidence = set(decision.warnings)
        if decision.reason:
            evidence.add(decision.reason)
        status = (
            "PASS"
            if decision.applicability == expected_applicability
            and (expected_reason_or_warning is None or expected_reason_or_warning in evidence)
            else "FAIL"
        )
        records.append({
            "scenario": scenario.scenario_id,
            "metric_id": decision.metric_id,
            "expected_applicability": expected_applicability,
            "actual_applicability": decision.applicability,
            "calculation_status": decision.calculation_status,
            "interpretation_status": decision.interpretation_status,
            "reason": decision.reason,
            "warnings": list(decision.warnings),
            "dose_context": scenario.context.dose_context,
            "prescription_context": scenario.context.prescription_context,
            "status": status,
        })
    return {
        "scenario_id": scenario.scenario_id,
        "treatment_context": scenario.context.to_dict(),
        "synthetic_dose_ground_truth": {
            "peak_gy": scenario.peak_gy,
            "valley_gy": scenario.valley_gy,
            "structure_based_dose_ratio": scenario.dose_ratio,
        },
        "status": "PASS" if all(item["status"] == "PASS" for item in records) else "FAIL",
        "metrics": records,
    }


def run_treatment_context_validation() -> dict[str, Any]:
    """Execute treatment context validation and return its explicit calculation state and evidence."""
    main = [_validate_scenario(item) for item in scenarios()]
    negative = [_validate_scenario(item) for item in mismatch_scenarios()]
    all_cases = main + negative
    lrt = next(item for item in main if item["scenario_id"] == "sequential_lrt_boost")
    composite = next(item for item in main if item["scenario_id"] == "composite_course")
    contrast = {
        "lrt_component_dr": lrt["synthetic_dose_ground_truth"]["structure_based_dose_ratio"],
        "composite_course_dr": composite["synthetic_dose_ground_truth"]["structure_based_dose_ratio"],
        "status": "PASS" if (
            lrt["synthetic_dose_ground_truth"]["structure_based_dose_ratio"] == 4.0
            and composite["synthetic_dose_ground_truth"]["structure_based_dose_ratio"] == 1.6
        ) else "FAIL",
    }
    return {
        "schema_version": "ASCEND-treatment-context-validation-v1",
        "scope": "Semantic dose-component, prescription and metric-applicability validation outside locked numerical kernels.",
        "status": "PASS" if all(item["status"] == "PASS" for item in all_cases) and contrast["status"] == "PASS" else "FAIL",
        "component_specific_fractionation": True,
        "lrt_vs_course_contrast": contrast,
        "cases": main,
        "negative_cases": negative,
    }
