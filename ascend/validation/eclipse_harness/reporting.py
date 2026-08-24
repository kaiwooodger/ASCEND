"""Deterministic rendering of stored ASCEND results into human-readable artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .reference_import import sha256_file


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def _display(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _write_comparisons_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "case_id", "rtstruct_uid", "dose_uid", "rtplan_uid", "roi_number", "roi_name", "reference_roi_name", "reference_volume_cc",
        "canonical_structure", "structure_role", "structure_size_class", "endpoint", "endpoint_type",
        "ascend_value", "eclipse_value", "units", "delta", "absolute_delta", "relative_delta_percent",
        "delta_semantics", "acceptance_limit", "acceptance_limit_units", "pass_fail", "comparison_status",
        "reason", "matching_status", "prescription_gy", "prescription_source", "ascend_algorithm_version",
        "acceptance_criterion_version", "warnings", "structure_identity", "diagnostic_context", "provenance",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                **{key: record.get(key) for key in fields},
                "warnings": "|".join(map(str, record.get("warnings", []))),
                "structure_identity": json.dumps(record.get("structure_identity", {}), sort_keys=True),
                "diagnostic_context": json.dumps(record.get("diagnostic_context", {}), sort_keys=True),
                "provenance": json.dumps(record.get("provenance", {}), sort_keys=True),
            })


def _write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    rows = summary.get("by_endpoint", [])
    fields = [
        "endpoint", "units", "n_total_references", "n_valid_comparisons", "n_excluded_or_not_comparable",
        "n_passing", "n_failing", "pass_percentage", "mean_signed_difference", "median_signed_difference",
        "mean_absolute_difference", "median_absolute_difference", "maximum_absolute_difference",
        "percentile_95_absolute_difference", "mean_relative_difference_percent",
        "median_relative_difference_percent", "maximum_absolute_relative_difference_percent",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in rows)


def _write_bland_altman_csv(path: Path, bland_altman: dict[str, Any]) -> None:
    fields = ["case_id", "roi_number", "roi_name", "endpoint", "units", "mean_pair", "difference", "ascend_value", "eclipse_value"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in bland_altman.get("data", []))


def _report(run: dict[str, Any]) -> str:
    summary = run["summary"]
    counts = summary["overall_counts"]
    criteria = run["acceptance_criteria"]
    reference = run["reference_import"]
    lines = [
        "# Eclipse DVH software-agreement validation report",
        "",
        f"Generated: {run['created_utc']}  ",
        f"ASCEND version: {run['ascend_version']}  ",
        f"Case: {run['case_id']}  ",
        f"Acceptance criterion version: {criteria['version']}",
        "",
        "## Scientific source integrity",
        "",
        "| Layer | Locked source SHA-256 |",
        "|---|---|",
    ]
    for layer, digest in run["locked_scientific_source_hashes"].items():
        lines.append(f"| {layer} | `{digest}` |")
    lines.extend([
        "",
        "## Eclipse reference",
        "",
        f"Source: {reference['source_description']}  ",
        f"Format: `{reference['format']}`  ",
        f"Imported reference rows: {reference['record_count']}  ",
        f"Cases: {summary['number_of_cases']}  ",
        f"Reference structures: {summary['number_of_reference_structures']}",
        "",
        "## Comparison coverage",
        "",
        f"- Total endpoint references: {counts['n_total_references']}",
        f"- Valid numerical comparisons: {counts['n_valid_comparisons']}",
        f"- Excluded or not comparable: {counts['n_excluded_or_not_comparable']}",
        f"- Passing: {counts['n_passing']}",
        f"- Failing: {counts['n_failing']}",
        "",
        "Matching outcomes: " + ", ".join(
            f"{key}={value}" for key, value in summary["matching_status_counts"].items()
        ) + ".",
        "",
        "## Acceptance criteria",
        "",
        f"- Dose endpoints: `{criteria['dose_endpoints']['rule']}` with {criteria['dose_endpoints']['absolute_floor_gy']} Gy and relative fraction {criteria['dose_endpoints']['relative_fraction']}.",
        f"- Percentage volume-at-dose endpoints: {_display(criteria['percentage_volume_endpoints']['limit_percentage_points'])} percentage point maximum absolute difference.",
        f"- Structure volume: `{criteria['structure_volume']['rule']}` with {criteria['structure_volume']['absolute_floor_cc']} cc and relative fraction {criteria['structure_volume']['relative_fraction']}.",
        "",
        "These are ASCEND software-agreement criteria. They are not clinical treatment-plan tolerances and are not protocol-compliance thresholds.",
        "",
        "## Endpoint-wise agreement",
        "",
        "| Endpoint | Unit | References | Valid | Excluded | Pass | Fail | Pass % | Mean difference | Median difference | Maximum absolute difference | 95th percentile absolute difference |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for item in summary["by_endpoint"]:
        lines.append(
            f"| {item['endpoint']} | {item.get('units') or '—'} | {item['n_total_references']} | "
            f"{item['n_valid_comparisons']} | {item['n_excluded_or_not_comparable']} | {item['n_passing']} | "
            f"{item['n_failing']} | {_display(item['pass_percentage'], 2)} | {_display(item['mean_signed_difference'])} | "
            f"{_display(item['median_signed_difference'])} | {_display(item['maximum_absolute_difference'])} | "
            f"{_display(item['percentile_95_absolute_difference'])} |"
        )
    lines.extend(["", "## Largest discrepancies", ""])
    largest = summary.get("largest_discrepancies_by_endpoint", [])
    if largest:
        lines.extend([
            "| Endpoint | Case | ROI | ASCEND | Eclipse | Absolute difference | Unit | Result |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ])
        for item in largest:
            lines.append(
                f"| {item['endpoint']} | {item['case_id']} | {item['roi_name']} | {_display(item['ascend_value'])} | "
                f"{_display(item['eclipse_value'])} | {_display(item['absolute_delta'])} | {item['units']} | {item['pass_fail']} |"
            )
    else:
        lines.append("No valid numerical comparisons were available.")
    lines.extend(["", "## Structure-volume context", ""])
    volume_rows = [item for item in summary["by_endpoint"] if item["endpoint"] == "Volume"]
    if volume_rows:
        item = volume_rows[0]
        lines.append(
            f"Structure volume: {item['n_valid_comparisons']} valid comparison(s), {item['n_failing']} failure(s), "
            f"median absolute difference {_display(item['median_absolute_difference'])} cc."
        )
    else:
        lines.append("No formal structure-volume reference endpoint was supplied.")
    lines.extend(["", "## Bland–Altman summary", ""])
    ba = run["bland_altman"]
    if ba["by_endpoint"]:
        lines.extend([
            "| Endpoint | N | Mean difference | SD of differences | Lower LoA | Upper LoA | Unit |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for item in ba["by_endpoint"]:
            lines.append(
                f"| {item['endpoint']} | {item['n']} | {_display(item['mean_difference'])} | "
                f"{_display(item['sample_standard_deviation_of_differences'])} | "
                f"{_display(item['lower_limit_of_agreement'])} | {_display(item['upper_limit_of_agreement'])} | {item['units']} |"
            )
    else:
        lines.append("No valid comparison pairs were available.")
    availability = summary["planned_endpoint_availability"]
    lines.extend([
        "",
        "## Systematic trends",
        "",
        summary["systematic_trend_assessment"]["statement"],
        "",
        "## Planned endpoint availability",
        "",
        "Available in the supplied reference: " + (", ".join(availability["available_in_reference"]) or "none") + ".",
        "",
        "Unavailable in the supplied reference: " + (", ".join(availability["unavailable_in_reference"]) or "none") + ".",
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in run.get("limitations", []))
    lines.extend([
        "- Excluded and non-comparable rows are retained with explicit status and reason and are not included in agreement statistics.",
        "- This harness does not alter ASCEND calculations in response to observed discrepancies.",
        "- Correlation is not used as the primary agreement metric.",
        "",
    ])
    return "\n".join(lines)


def write_validation_outputs(run: dict[str, Any], output_directory: str | Path) -> dict[str, dict[str, str]]:
    """Write validation outputs deterministically to disk."""
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "comparisons_json": output / "eclipse_dvh_comparisons.json",
        "comparisons_csv": output / "eclipse_dvh_comparisons.csv",
        "summary_json": output / "eclipse_dvh_summary.json",
        "summary_csv": output / "eclipse_dvh_summary.csv",
        "bland_altman_csv": output / "eclipse_dvh_bland_altman.csv",
        "report_markdown": output / "ECLIPSE_DVH_VALIDATION_REPORT.md",
    }
    _json_write(paths["comparisons_json"], {
        "schema_version": run["schema_version"],
        "created_utc": run["created_utc"],
        "case_id": run["case_id"],
        "reference_import": run["reference_import"],
        "acceptance_criteria": run["acceptance_criteria"],
        "records": run["comparisons"],
    })
    _write_comparisons_csv(paths["comparisons_csv"], run["comparisons"])
    _json_write(paths["summary_json"], {
        "schema_version": run["summary_schema_version"],
        "created_utc": run["created_utc"],
        "case_id": run["case_id"],
        "summary": run["summary"],
        "bland_altman_summary": run["bland_altman"]["by_endpoint"],
        "acceptance_criteria": run["acceptance_criteria"],
    })
    _write_summary_csv(paths["summary_csv"], run["summary"])
    _write_bland_altman_csv(paths["bland_altman_csv"], run["bland_altman"])
    paths["report_markdown"].write_text(_report(run), encoding="utf-8")
    return {
        name: {"path": str(path), "sha256": sha256_file(path)}
        for name, path in paths.items()
    }
