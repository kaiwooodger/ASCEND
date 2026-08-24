"""Deterministic rendering of stored ASCEND results into human-readable artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_treatment_context_report(result: dict[str, Any], destination: str | Path) -> dict[str, str]:
    """Write treatment context report deterministically to disk."""
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    cases_path = output / "treatment_context_cases.json"
    validation_csv = output / "treatment_context_validation.csv"
    matrix_csv = output / "metric_applicability_matrix.csv"
    provenance_path = output / "prescription_provenance.json"
    report_path = output / "TREATMENT_CONTEXT_VALIDATION_REPORT.md"
    cases_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    rows = [metric for case in [*result["cases"], *result["negative_cases"]] for metric in case["metrics"]]
    csv_fields = [
        "scenario", "metric_id", "expected_applicability", "actual_applicability",
        "calculation_status", "interpretation_status", "reason", "warnings",
        "dose_context", "prescription_context", "status",
    ]
    serialised = [{**item, "warnings": ";".join(item["warnings"])} for item in rows]
    for path in (validation_csv, matrix_csv):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=csv_fields)
            writer.writeheader(); writer.writerows(serialised)
    provenance = {
        case["scenario_id"]: {
            "dose_context": case["treatment_context"]["dose_context"],
            "prescription_context": case["treatment_context"]["prescription_context"],
            "prescriptions": case["treatment_context"]["prescriptions"],
            "components": case["treatment_context"]["components"],
        }
        for case in [*result["cases"], *result["negative_cases"]]
    }
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    lines = [
        "# ASCEND treatment-context validation",
        "",
        f"Overall status: **{result['status']}**",
        "",
        "This workstream validates what treatment component, dose object and prescription each metric belongs to. It is separate from numerical dose validation.",
        "",
        "| Scenario | Dose context | Metric | Applicability | Interpretation | Status |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {item['scenario']} | {item['dose_context']} | {item['metric_id']} | {item['actual_applicability']} | {item['interpretation_status']} | {item['status']} |"
        for item in rows
    )
    contrast = result["lrt_vs_course_contrast"]
    lines.extend([
        "",
        "## Component versus course interpretation",
        "",
        f"Synthetic LRT-component DR: **{contrast['lrt_component_dr']}**.",
        f"Synthetic composite-course DR: **{contrast['composite_course_dr']}**.",
        "",
        "The composite ratio is retained as a mathematical course-level result and carries `course_level_dr_not_comparable_to_lrt_component_dr`. Coverage calculations are suppressed when prescription and dose contexts conflict.",
        "",
        "Fraction counts and dose per fraction remain component-specific. Components are not flattened into one total-dose/total-fraction pair.",
    ])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "report": str(report_path), "cases_json": str(cases_path),
        "validation_csv": str(validation_csv), "applicability_matrix_csv": str(matrix_csv),
        "prescription_provenance_json": str(provenance_path),
    }
