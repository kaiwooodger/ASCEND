"""Compare ASCEND CI reports with frozen values and with one another."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "ASCEND-cross-platform-report-v1":
        raise ValueError(f"{path}: unsupported or missing report schema")
    if payload.get("case_id") != "SYNTHETIC-CROSS-PLATFORM-V1":
        raise ValueError(f"{path}: unexpected synthetic case identifier")
    return payload


def _close(actual: float, expected: float, tolerance: dict[str, float]) -> bool:
    return math.isclose(
        actual,
        expected,
        rel_tol=float(tolerance.get("relative", 0.0)),
        abs_tol=float(tolerance.get("absolute", 0.0)),
    )


def _label(report: dict[str, Any]) -> str:
    environment = report["environment"]
    return f"{environment['runner_os']}/{environment['runner_arch']}/Python {environment['python_version']}"


def _classification(actual: Any, expected: Any, tolerance: dict[str, float]) -> tuple[str, float | None, float | None]:
    if actual is None or expected is None:
        return "MISSING", None, None
    try:
        actual_value = float(actual)
        expected_value = float(expected)
    except (TypeError, ValueError):
        return "NONFINITE", None, None
    if not math.isfinite(actual_value) or not math.isfinite(expected_value):
        return "NONFINITE", None, None
    absolute = abs(actual_value - expected_value)
    relative = absolute / abs(expected_value) if expected_value != 0.0 else (0.0 if absolute == 0.0 else math.inf)
    if actual_value == expected_value:
        return "EXACT", absolute, relative
    if _close(actual_value, expected_value, tolerance):
        return "WITHIN_TOLERANCE", absolute, relative
    return "OUTSIDE_TOLERANCE", absolute, relative


def compare(report_paths: list[Path], reference_path: Path, output_path: Path) -> dict[str, Any]:
    if not report_paths:
        raise ValueError("No cross-platform reports were provided.")
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    expected_metrics = reference["metrics"]
    reports = [_load(path) for path in report_paths]
    failures: list[str] = []
    required_environments = {
        ("Linux", "3.11"), ("Linux", "3.12"),
        ("Windows", "3.11"), ("Windows", "3.12"),
        ("macOS", "3.11"), ("macOS", "3.12"),
    }
    observed_environments = {
        (report["environment"]["runner_os"], ".".join(str(report["environment"]["python_version"]).split(".")[:2]))
        for report in reports
    }
    if observed_environments != required_environments:
        failures.append(
            f"Primary environment set mismatch: expected {sorted(required_environments)}, "
            f"observed {sorted(observed_environments)}."
        )

    baselines = [
        report for report in reports
        if report["environment"]["runner_os"] == "Linux"
        and str(report["environment"]["python_version"]).startswith("3.11.")
    ]
    if len(baselines) != 1:
        failures.append(f"Expected one Linux/Python 3.11 baseline; found {len(baselines)}.")
        baseline = reports[0]
    else:
        baseline = baselines[0]

    reference_checks: dict[str, dict[str, Any]] = {}
    for report in reports:
        label = _label(report)
        values = report.get("metrics", {})
        missing = sorted(set(expected_metrics) - set(values))
        extra = sorted(set(values) - set(expected_metrics))
        if missing:
            failures.append(f"{label}: missing metrics: {', '.join(missing)}")
        if extra:
            failures.append(f"{label}: unexpected metrics: {', '.join(extra)}")
        checks: dict[str, Any] = {}
        for name, definition in expected_metrics.items():
            actual = values.get(name)
            expected = definition["expected"]
            tolerance = definition["tolerance"]
            classification, absolute, relative = _classification(actual, expected, tolerance)
            checks[name] = {
                "classification": classification,
                "actual": actual,
                "expected": expected,
                "absolute_difference": absolute,
                "relative_difference": relative,
                "tolerance": tolerance,
            }
            if classification in {"MISSING", "NONFINITE", "OUTSIDE_TOLERANCE"}:
                failures.append(
                    f"{label}: {name} classified {classification}; actual={actual!r}, expected={expected!r}, "
                    f"rtol={tolerance.get('relative', 0)}, atol={tolerance.get('absolute', 0)}"
                )
        reference_checks[label] = checks

    baseline_values = baseline["metrics"]
    baseline_comparisons: dict[str, dict[str, Any]] = {}
    maximum_absolute = 0.0
    maximum_relative = 0.0
    for report in reports:
        if report is baseline:
            continue
        label = _label(report)
        checks = {}
        for name, definition in expected_metrics.items():
            actual = report["metrics"].get(name)
            expected = baseline_values.get(name)
            tolerance = definition["tolerance"]
            classification, absolute, relative = _classification(actual, expected, tolerance)
            checks[name] = {
                "classification": classification,
                "candidate": actual,
                "baseline": expected,
                "absolute_difference": absolute,
                "relative_difference": relative,
                "tolerance": tolerance,
            }
            if absolute is not None:
                maximum_absolute = max(maximum_absolute, absolute)
            if relative is not None and math.isfinite(relative):
                maximum_relative = max(maximum_relative, relative)
            if classification in {"MISSING", "NONFINITE", "OUTSIDE_TOLERANCE"}:
                failures.append(
                    f"{label}: {name} versus {_label(baseline)} classified {classification}: "
                    f"{actual!r} versus {expected!r}"
                )
        baseline_comparisons[label] = checks

    summary = {
        "schema_version": "ASCEND-cross-platform-comparison-v1",
        "reference": str(reference_path),
        "baseline": _label(baseline),
        "report_count": len(reports),
        "maximum_observed_absolute_difference": maximum_absolute,
        "maximum_observed_relative_difference": maximum_relative,
        "reference_checks": reference_checks,
        "baseline_comparisons": baseline_comparisons,
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Compared {len(reports)} reports against {reference_path} and baseline {_label(baseline)}.")
    for report in sorted(reports, key=_label):
        print(f"PASS candidate: {_label(report)}")
    if failures:
        raise SystemExit("Cross-platform scientific comparison failed:\n- " + "\n- ".join(failures))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, help="Directory containing downloaded report JSON files")
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path("validation/synthetic_reference_cases/cross_platform_expected.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("cross-platform-comparison.json"))
    args = parser.parse_args()
    compare(sorted(args.reports.glob("scientific-*.json")), args.reference, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
