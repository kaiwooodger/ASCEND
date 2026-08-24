"""Deterministic rendering of stored ASCEND results into human-readable artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_validation_artifacts(result: dict[str, Any], output_directory: str | Path) -> dict[str, str]:
    """Write validation artifacts deterministically to disk."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "layer31_validation.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    csv_path = output / "layer31_validation.csv"
    rows = result.get("validation_cases", [])
    if rows:
        fields = sorted({key for row in rows for key in row})
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    report_path = output / "LAYER31_VALIDATION_REPORT.md"
    report_path.write_text(
        "# ASCEND Layer 3.1 validation report\n\n"
        f"Overall status: **{result.get('status', 'NOT RUN')}**\n\n"
        "The validation covers analytic single-component LQ transformation, multi-component "
        "fractionation, explicit fraction-dose history, ROI-only equivalence, parameter sweeps, "
        "fail-safe parameter/fraction validation, and common-geometry enforcement.\n\n"
        "Layer 3.1 is a conventional LQ-derived reference representation. It is not TCP, NTCP, "
        "a complete LRT biological model, or a clinical outcome predictor.\n",
        encoding="utf-8",
    )
    return {"json": str(json_path), "csv": str(csv_path), "report": str(report_path)}

