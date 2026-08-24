"""Deterministic rendering of stored ASCEND results into human-readable artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_anisotropic_report(result: dict[str, Any], destination: str | Path) -> dict[str, str]:
    """Write anisotropic report deterministically to disk."""
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "anisotropic_results.json"
    csv_path = output / "anisotropic_results.csv"
    sensitivity_path = output / "anisotropic_resolution_sensitivity.csv"
    report_path = output / "ANISOTROPIC_VALIDATION_REPORT.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    rows = []
    sensitivity = []
    for item in result["results"]:
        rows.append({
            "grid": item["grid_id"],
            "spacing_xyz_mm": "x".join(map(str, item["spacing_xyz_mm"])),
            "layer1_geometry": item["geometry"]["status"],
            "uniform_dvh": item["uniform_dose_validation"]["status"],
            "physical_gradient": item["physical_gradient_validation"]["status"],
            "layer2_1": item["layer2_1"]["status"],
            "layer2_2": item["layer2_2"]["calculation_status"],
            "layer2_2_reason": item["layer2_2"]["reason"],
            "complete_dicom_pipeline": item.get("complete_dicom_pipeline", {}).get("status", "not_run"),
        })
        for volume in item["volume_validation"]:
            sensitivity.append({"grid": item["grid_id"], **volume})
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    with sensitivity_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(sensitivity[0]))
        writer.writeheader(); writer.writerows(sensitivity)
    lines = [
        "# ASCEND anisotropic Layer 1 / Layer 2.1 validation",
        "",
        f"Overall status: **{result['status']}**",
        "",
        "This evidence package validates physical-coordinate reconstruction and locked Layer 2.1 metrics on regular anisotropic grids. It does not expand Layer 2.2's validated domain.",
        "",
        "| Grid | L1 geometry | Uniform DVH | Physical gradient | L2.1 | L2.2 | Complete DICOM chain |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['spacing_xyz_mm']} | {row['layer1_geometry']} | {row['uniform_dvh']} | {row['physical_gradient']} | {row['layer2_1']} | {row['layer2_2']} | {row['complete_dicom_pipeline']} |"
        for row in rows
    )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Sampled volumes are resolution-dependent and are assessed against analytic ground truth using signed relative error. Exact cross-grid voxel-volume equality is not required. Uniform and patient-coordinate dose fields must remain numerically exact at sampled voxel centres.",
        "",
        "Supported claim: Layer 1 and Layer 2.1 are validated on regular anisotropic dose grids across the tested resolution domain. Layer 2.2 returns `outside_validated_scope` for anisotropic grids.",
    ])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "report": str(report_path), "json": str(json_path), "csv": str(csv_path),
        "resolution_sensitivity_csv": str(sensitivity_path),
    }
