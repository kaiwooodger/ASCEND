"""Aggregate validation statistics and Bland–Altman summaries."""

from __future__ import annotations

import math
import statistics
from typing import Any

from .schemas import PLANNED_ENDPOINTS


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Handle summarize records for the enclosing ASCEND workflow."""
    valid = [item for item in records if item.get("comparison_status") == "valid_comparison"]
    passing = [item for item in valid if item.get("pass_fail") == "pass"]
    failing = [item for item in valid if item.get("pass_fail") == "fail"]
    deltas = [float(item["delta"]) for item in valid]
    absolute = [float(item["absolute_delta"]) for item in valid]
    relative = [float(item["relative_delta_percent"]) for item in valid if item.get("relative_delta_percent") is not None]
    return {
        "n_total_references": len(records),
        "n_valid_comparisons": len(valid),
        "n_excluded_or_not_comparable": len(records) - len(valid),
        "n_passing": len(passing),
        "n_failing": len(failing),
        "pass_percentage": (100.0 * len(passing) / len(valid)) if valid else None,
        "mean_signed_difference": statistics.fmean(deltas) if deltas else None,
        "median_signed_difference": statistics.median(deltas) if deltas else None,
        "mean_absolute_difference": statistics.fmean(absolute) if absolute else None,
        "median_absolute_difference": statistics.median(absolute) if absolute else None,
        "maximum_absolute_difference": max(absolute) if absolute else None,
        "percentile_95_absolute_difference": _percentile(absolute, 0.95),
        "mean_relative_difference_percent": statistics.fmean(relative) if relative else None,
        "median_relative_difference_percent": statistics.median(relative) if relative else None,
        "maximum_absolute_relative_difference_percent": max(map(abs, relative)) if relative else None,
    }


def _groups(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    values = sorted({str(item.get(field) or "unknown") for item in records})
    output: list[dict[str, Any]] = []
    for value in values:
        selected = [item for item in records if str(item.get(field) or "unknown") == value]
        endpoints = sorted({str(item.get("endpoint")) for item in selected})
        for endpoint in endpoints:
            endpoint_records = [item for item in selected if item.get("endpoint") == endpoint]
            output.append({"stratum": value, "endpoint": endpoint, **summarize_records(endpoint_records)})
    return output


def bland_altman(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Handle bland altman for the enclosing ASCEND workflow."""
    valid = [item for item in records if item.get("comparison_status") == "valid_comparison"]
    data = [{
        "case_id": item.get("case_id"),
        "roi_number": item.get("roi_number"),
        "roi_name": item.get("roi_name"),
        "endpoint": item.get("endpoint"),
        "units": item.get("units"),
        "mean_pair": (float(item["ascend_value"]) + float(item["eclipse_value"])) / 2.0,
        "difference": float(item["delta"]),
        "ascend_value": item.get("ascend_value"),
        "eclipse_value": item.get("eclipse_value"),
    } for item in valid]
    summaries: list[dict[str, Any]] = []
    for endpoint in sorted({str(item["endpoint"]) for item in data}):
        selected = [item for item in data if item["endpoint"] == endpoint]
        differences = [float(item["difference"]) for item in selected]
        mean_difference = statistics.fmean(differences)
        standard_deviation = statistics.stdev(differences) if len(differences) >= 2 else 0.0
        summaries.append({
            "endpoint": endpoint,
            "units": selected[0]["units"],
            "n": len(selected),
            "mean_difference": mean_difference,
            "sample_standard_deviation_of_differences": standard_deviation,
            "upper_limit_of_agreement": mean_difference + 1.96 * standard_deviation,
            "lower_limit_of_agreement": mean_difference - 1.96 * standard_deviation,
        })
    return {
        "definition": {
            "mean_pair": "(ASCEND + Eclipse) / 2",
            "difference": "ASCEND - Eclipse",
            "limits_of_agreement": "mean difference +/- 1.96 * sample standard deviation",
        },
        "data": data,
        "by_endpoint": summaries,
    }


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build summary from validated inputs."""
    endpoints = sorted({str(item.get("endpoint")) for item in records})
    by_endpoint = [
        {"endpoint": endpoint, "units": next((item.get("units") for item in records if item.get("endpoint") == endpoint), None),
         **summarize_records([item for item in records if item.get("endpoint") == endpoint])}
        for endpoint in endpoints
    ]
    status_counts = {
        status: sum(item.get("comparison_status") == status for item in records)
        for status in sorted({str(item.get("comparison_status")) for item in records})
    }
    matching_counts = {
        status: sum(item.get("matching_status") == status for item in records)
        for status in sorted({str(item.get("matching_status")) for item in records})
    }
    largest = []
    for endpoint in endpoints:
        selected = [
            item for item in records
            if item.get("endpoint") == endpoint and item.get("comparison_status") == "valid_comparison"
        ]
        selected.sort(key=lambda item: float(item["absolute_delta"]), reverse=True)
        largest.extend({
            "endpoint": endpoint,
            "case_id": item.get("case_id"),
            "roi_number": item.get("roi_number"),
            "roi_name": item.get("roi_name"),
            "ascend_value": item.get("ascend_value"),
            "eclipse_value": item.get("eclipse_value"),
            "absolute_delta": item.get("absolute_delta"),
            "units": item.get("units"),
            "pass_fail": item.get("pass_fail"),
        } for item in selected[:5])
    return {
        "overall_counts": {
            "n_total_references": len(records),
            "n_valid_comparisons": sum(item.get("comparison_status") == "valid_comparison" for item in records),
            "n_excluded_or_not_comparable": sum(item.get("comparison_status") != "valid_comparison" for item in records),
            "n_passing": sum(item.get("pass_fail") == "pass" for item in records),
            "n_failing": sum(item.get("pass_fail") == "fail" for item in records),
        },
        "number_of_cases": len({item.get("case_id") for item in records}),
        "number_of_reference_structures": len({
            (item.get("case_id"), item.get("rtstruct_uid"), item.get("roi_number"), item.get("reference_roi_name"))
            for item in records
        }),
        "matching_status_counts": matching_counts,
        "comparison_status_counts": status_counts,
        "by_endpoint": by_endpoint,
        "stratified": {
            "by_structure_size_class_and_endpoint": _groups(records, "structure_size_class"),
            "by_structure_role_and_endpoint": _groups(records, "structure_role"),
            "by_case_and_endpoint": _groups(records, "case_id"),
            "by_dose_grid_and_endpoint": _groups([
                {**item, "dose_grid": "x".join(map(str, item.get("diagnostic_context", {}).get("dose_grid_spacing_mm") or [])) or "unknown"}
                for item in records
            ], "dose_grid"),
            "by_structure_name_and_endpoint": _groups(records, "roi_name"),
        },
        "largest_discrepancies_by_endpoint": largest,
        "planned_endpoint_availability": {
            "available_in_reference": [item for item in PLANNED_ENDPOINTS if item in endpoints],
            "unavailable_in_reference": [item for item in PLANNED_ENDPOINTS if item not in endpoints],
            "additional_absolute_dose_volume_endpoints": [item for item in endpoints if item.startswith("V") and item.endswith("Gy")],
        },
        "systematic_trend_assessment": {
            "status": "descriptive_only",
            "statement": "Mean signed differences are reported by endpoint; no inferential trend claim is made by this harness.",
        },
    }
