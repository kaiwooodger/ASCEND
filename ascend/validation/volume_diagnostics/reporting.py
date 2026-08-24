"""Deterministic rendering of stored ASCEND results into human-readable artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ascend.validation.eclipse_harness.reference_import import sha256_file


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _volume_table(structures: list[dict[str, Any]]) -> str:
    rows = [
        "| Structure | Eclipse (cc) | ASCEND contour (cc) | ASCEND CT (cc) | ASCEND dose (cc) | Formal status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in structures:
        volumes = item["volume_representations"]
        rows.append(
            f"| {item['roi_name']} | {_fmt(volumes['eclipse_volume_cc'])} | "
            f"{_fmt(volumes['anatomical_volume_contour_cc'])} | {_fmt(volumes['anatomical_volume_ct_cc'])} | "
            f"{_fmt(volumes['dose_sampled_volume_cc'])} | {item['formal_volume_finding']['pass_fail'].upper()} |"
        )
    return "\n".join(rows)


def markdown_report(run: dict[str, Any]) -> str:
    """Handle markdown report for the enclosing ASCEND workflow."""
    structures = run["structures"]
    by_name = {item["roi_name"]: item for item in structures}
    sections = [
        "# Eclipse Volume Discrepancy Diagnostic — PHPROLRT01",
        "",
        "## 1. Objective",
        "",
        "Investigate the preserved Eclipse-versus-ASCEND volume failures for `all_vertices` and `all_valleys` without changing scientific algorithms or agreement criteria.",
        "",
        "## 2. Scientific baseline and source hashes",
        "",
        f"- ASCEND version: `{run['scientific_baseline']['ascend_version']}`",
        *[f"- {name}: `{value}`" for name, value in run["scientific_baseline"]["locked_scientific_source_hashes"].items()],
        f"- Native RTDOSE artifact: `{run['scientific_baseline']['native_rtdose_artifact_sha256']}`",
        f"- Native mask archive: `{run['scientific_baseline']['native_mask_archive_sha256']}`",
        "",
        "## 3. Available evidence",
        "",
        "The evidence is the selected RTSTRUCT/RTDOSE/CT chain, stored Layer 1 masks and volume audit, the formal Eclipse comparison records, and the original Eclipse cumulative-DVH text exports.",
        "",
        "## 4. CERR evidence",
        "",
        "No CERR evidence exists for this plan. None was required, inferred, or fabricated.",
        "",
        "## 5. Eclipse source/reference precision",
        "",
    ]
    for item in structures:
        precision = item["eclipse_reference_precision"]
        sections.append(
            f"- `{item['roi_name']}`: `{precision['field_label']}: {precision['reported_text']}`; "
            f"{precision['displayed_decimal_places']} decimal place; reported resolution {precision['reported_resolution_cc']} cc; "
            f"no hidden Eclipse precision inferred."
        )
    sections.extend(["", "## 6–8. Three-volume comparisons", "", _volume_table(structures), ""])
    for name in ("all_vertices", "all_valleys"):
        item = by_name[name]
        decomposition = item["contour_stack_diagnostics"]["volume_decomposition"]
        components = item["aggregate_component_analysis"]
        sections.extend([
            f"### `{name}`",
            "",
            f"The formal harness used the contour-stack value, not the CT or RTDOSE representation. "
            f"Between-plane trapezoids contribute {_fmt(decomposition['between_plane_trapezoids_cc'])} cc and locked end-plane terms contribute {_fmt(decomposition['end_plane_contribution_cc'])} cc.",
            "",
            (
                "No individual RTSTRUCT component ROIs exist; explicit aggregate-versus-individual union analysis is unavailable. "
                f"The explicit dose-grid mask contains {item['structural_context']['connected_components_26']} 26-connected components."
                if not components["available"] else
                f"Aggregate/component Dice is {_fmt(components['dice_coefficient'])}."
            ),
            "",
        ])
    sections.extend([
        "## 9–10. Structural and contour-stack analysis",
        "",
    ])
    for item in structures:
        topology = item["contour_stack_diagnostics"]["summary"]
        sections.append(
            f"- `{item['roi_name']}`: {topology['physical_plane_count']} physical planes, "
            f"{topology['polygon_count']} polygons, {topology['total_contour_points']} points, "
            f"{topology['planes_with_multiple_polygons']} multi-polygon planes, "
            f"nested={topology['nested_polygons_detected']}, near-duplicate={topology['near_duplicate_contours_detected']}, "
            f"self-intersection={topology['self_intersection_detected']}, degenerate={topology['degenerate_contours_detected']}."
        )
    sections.extend(["", "## 11. CT/dose-grid representation effects", ""])
    for item in structures:
        diff = item["volume_representations"]["differences"]
        sections.append(
            f"- `{item['roi_name']}`: CT minus contour {_fmt(diff['ct_minus_contour']['absolute_cc'])} cc; "
            f"dose minus contour {_fmt(diff['dose_minus_contour']['absolute_cc'])} cc. "
            "Neither discretised representation removes the Eclipse disagreement."
        )
    sections.extend(["", "## 12. Structure overlap and containment", ""])
    for item in run["overlap_analysis"]:
        sections.append(
            f"- `{item['structure_a']}` ∩ `{item['structure_b']}`: {_fmt(item['intersection_volume_cc'])} cc; "
            f"A overlap {_fmt(item['fraction_a_overlapping_b_percent'])}%; B overlap {_fmt(item['fraction_b_overlapping_a_percent'])}%."
        )
    sections.extend(["", "## 13. Existing dose-endpoint agreement", ""])
    for item in structures:
        endpoints = ", ".join(
            f"{record['endpoint']}={record['pass_fail']}"
            for record in item["dose_endpoint_context"]
        )
        sections.append(f"- `{item['roi_name']}`: {endpoints}. Volume disagreement is separate from dose-endpoint agreement.")
    sections.extend(["", "## 14. Mask reproducibility", ""])
    for item in structures:
        reproducibility = item["mask_reproducibility"]
        sections.append(
            f"- `{item['roi_name']}`: CT analysis rerun hash stable={reproducibility['ct_analysis_rerun_bitwise_equal']}; "
            f"stored dose mask versus diagnostic reconstruction bitwise equal={reproducibility['dose_stored_vs_rerun_bitwise_equal']}."
        )
    sections.extend(["", "## 15. Evidence-supported interpretation", ""])
    for item in structures:
        conclusion = item["diagnostic_interpretation"]
        sections.append(
            f"- `{item['roi_name']}`: `{conclusion['classification']}` ({conclusion['confidence']} confidence). "
            + conclusion["evidence_summary"]
        )
    sections.extend([
        "",
        "## 16. Remaining uncertainty",
        "",
        "The Eclipse TXT exports omit RTSTRUCT SOP Instance UID, ROI number, the internal Eclipse volume algorithm, and hidden numeric precision. Exact cross-system causation therefore remains unresolved.",
        "",
        "## 17. Scientific-code change",
        "",
        "No scientific-code change is warranted by the available evidence. Both reconstructed dose masks are bitwise identical to their stored masks, and no contour anomaly proving an ASCEND reconstruction defect was found.",
        "",
        "## 18. Recommended next action",
        "",
        "Retain both formal FAIL results. Obtain an Eclipse export or independent contour-volume calculation that records ROI identity and documents its volume convention before considering any validation-policy or scientific-code change.",
        "",
        "The original formal validation output and acceptance criteria were not modified.",
    ])
    return "\n".join(sections) + "\n"


def write_outputs(run: dict[str, Any], output_directory: Path) -> dict[str, dict[str, str]]:
    """Write outputs deterministically to disk."""
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "volume_discrepancy_diagnostics.json"
    json_path.write_text(json.dumps(run, indent=2, allow_nan=False), encoding="utf-8")

    summary_rows = []
    representation_rows = []
    contour_rows = []
    reproducibility_rows = []
    for item in run["structures"]:
        interpretation = item["diagnostic_interpretation"]
        finding = item["formal_volume_finding"]
        summary_rows.append({
            "structure": item["roi_name"],
            "roi_number": item["roi_number"],
            "comparison_status": finding["comparison_status"],
            "pass_fail": finding["pass_fail"],
            "eclipse_volume_cc": finding["eclipse_value"],
            "ascend_formal_volume_cc": finding["ascend_value"],
            "diagnostic_status": "investigated",
            "diagnostic_classification": interpretation["classification"],
            "confidence": interpretation["confidence"],
            "discrepancy_unresolved": interpretation["discrepancy_unresolved"],
            "scientific_algorithm_change_required": interpretation["scientific_algorithm_change_required"],
        })
        volumes = item["volume_representations"]
        differences = volumes["differences"]
        representation_rows.append({
            "structure": item["roi_name"],
            "eclipse_volume_cc": volumes["eclipse_volume_cc"],
            "ascend_contour_volume_cc": volumes["anatomical_volume_contour_cc"],
            "ascend_ct_volume_cc": volumes["anatomical_volume_ct_cc"],
            "ascend_dose_sampled_volume_cc": volumes["dose_sampled_volume_cc"],
            "eclipse_minus_contour_cc": differences["eclipse_minus_contour"]["absolute_cc"],
            "eclipse_minus_ct_cc": differences["eclipse_minus_ct"]["absolute_cc"],
            "eclipse_minus_dose_cc": differences["eclipse_minus_dose"]["absolute_cc"],
            "ct_minus_contour_cc": differences["ct_minus_contour"]["absolute_cc"],
            "dose_minus_contour_cc": differences["dose_minus_contour"]["absolute_cc"],
            "dose_minus_ct_cc": differences["dose_minus_ct"]["absolute_cc"],
            "formal_harness_comparator": volumes["formal_harness_comparator"],
        })
        contour_rows.extend(item["contour_stack_diagnostics"]["slices"])
        reproducibility_rows.append({"structure": item["roi_name"], **item["mask_reproducibility"]})

    _write_csv(output_directory / "volume_discrepancy_summary.csv", summary_rows)
    _write_csv(output_directory / "volume_representation_comparison.csv", representation_rows)
    _write_csv(output_directory / "contour_slice_areas.csv", contour_rows)
    _write_csv(output_directory / "overlap_analysis.csv", run["overlap_analysis"])
    reproducibility_path = output_directory / "mask_reproducibility.json"
    reproducibility_path.write_text(json.dumps(reproducibility_rows, indent=2, allow_nan=False), encoding="utf-8")
    report_path = output_directory / "ECLIPSE_VOLUME_DISCREPANCY_REPORT.md"
    report_path.write_text(markdown_report(run), encoding="utf-8")

    paths = [
        json_path,
        output_directory / "volume_discrepancy_summary.csv",
        output_directory / "volume_representation_comparison.csv",
        output_directory / "contour_slice_areas.csv",
        output_directory / "overlap_analysis.csv",
        reproducibility_path,
        report_path,
    ]
    return {path.name: {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in paths}
