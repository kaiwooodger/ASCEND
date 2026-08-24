"""Command-line interface for ASCEND ingestion, analysis, validation, cache, and export workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ascend.app.controller import ApplicationController
from ascend.models.case import ASCENDCase
from ascend.models.config import CaseConfiguration
from ascend.models.status import CalculationStatus


def _configuration(path: Path | None, base: CaseConfiguration) -> CaseConfiguration:
    if path is None:
        return base
    return CaseConfiguration.from_dict(json.loads(path.read_text(encoding="utf-8")))


def build_parser() -> argparse.ArgumentParser:
    """Build parser from validated inputs."""
    parser = argparse.ArgumentParser(prog="ascend", description="ASCEND LRT analysis engine")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="Launch the PySide6/Qt desktop workstation")
    sub.add_parser("web-gui", help="Launch the optional localhost browser workstation")
    discover = sub.add_parser("discover", help="Inspect DICOM headers without analysis")
    discover.add_argument("case_directory", type=Path)
    run = sub.add_parser("run", help="Run Layer 1, Layer 2.1, Layer 2.2, and export")
    run.add_argument("case_directory", type=Path)
    run.add_argument("--case-root", type=Path, required=True)
    run.add_argument("--config", type=Path)
    run.add_argument("--chain-id")
    run.add_argument("--allow-incomplete-chain", action="store_true")
    run.add_argument("--override-reason")
    run.add_argument("--layer1-only", action="store_true")
    run.add_argument("--with-layer31", action="store_true", help="Run gated Layer 3.1 after Layers 1–2.2")
    resume = sub.add_parser("resume", help="Continue an existing ASCEND case")
    resume.add_argument("case_file", type=Path)
    resume.add_argument("--layer", choices=("layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2", "physical", "export"), default="physical")
    layer31 = sub.add_parser("layer31", help="Run the gated fraction-resolved Layer 3.1 workflow")
    layer31.add_argument("--case", type=Path, required=True)
    layer31.add_argument("--export", type=Path)
    layer32 = sub.add_parser("layer32", help="Run Layer 3.2 using current Layer 3.1 evidence")
    layer32.add_argument("--case", type=Path, required=True)
    layer32.add_argument("--export", type=Path)
    cache_inspect = sub.add_parser("cache-inspect", help="Inspect one case's Layer 1 cache")
    cache_inspect.add_argument("case_file", type=Path)
    cache_clear = sub.add_parser("cache-clear", help="Clear one case's Layer 1 cache")
    cache_clear.add_argument("case_file", type=Path)
    cache_clear.add_argument("--confirm", action="store_true", required=True)
    validate = sub.add_parser(
        "validate-eclipse-dvh",
        help="Compare stored ASCEND DVH endpoints with Eclipse reference endpoints",
    )
    validate.add_argument("--case", type=Path, required=True, help="ASCEND case root or ascend_case.json")
    validate.add_argument("--reference", type=Path, required=True, help="Canonical CSV, Eclipse TXT, or TXT directory")
    validate.add_argument("--output", type=Path, help="Validation output directory")
    validate.add_argument("--criteria", type=Path, help="Optional versioned acceptance-criteria JSON")
    diagnose = sub.add_parser(
        "diagnose-eclipse-volumes",
        help="Investigate preserved Eclipse all_vertices/all_valleys volume failures",
    )
    diagnose.add_argument("--case", type=Path, required=True, help="ASCEND case root or ascend_case.json")
    diagnose.add_argument("--comparison", type=Path, help="Formal eclipse_dvh_comparisons.json")
    diagnose.add_argument("--output", type=Path, help="Diagnostic output directory")
    anisotropic = sub.add_parser(
        "validate-anisotropic",
        help="Generate independent regular-anisotropic Layer 1/2.1 validation evidence",
    )
    anisotropic.add_argument("--output", type=Path, required=True)
    treatment = sub.add_parser(
        "validate-treatment-context",
        help="Generate treatment-component and prescription-context validation evidence",
    )
    treatment.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Handle main for the enclosing ASCEND workflow."""
    args = build_parser().parse_args(argv)
    if args.command in (None, "gui"):
        from ascend.gui import launch
        launch()
        return 0
    if args.command == "web-gui":
        from ascend.web import launch
        launch()
        return 0
    if args.command == "discover":
        from ascend.dicom.discovery import discover_case
        print(json.dumps(discover_case(args.case_directory), indent=2))
        return 0
    if args.command == "run":
        controller = ApplicationController()
        case = controller.import_case(args.case_directory, args.case_root)
        if args.chain_id:
            controller.select_dicom_chain(args.chain_id, args.allow_incomplete_chain, args.override_reason)
        elif not case.selected_chain_id:
            print(json.dumps({"error": "chain_selection_required", "dicom_chains": case.dicom_chains}, indent=2))
            return 2
        controller.configure(_configuration(args.config, case.configuration))
        l1 = controller.run_layer1()
        if l1.error:
            print(json.dumps({"layer1": l1.calculation_status, "error": l1.error}, indent=2))
            return 2
        if args.layer1_only:
            print(json.dumps({
                "case_file": str(case.root / "ascend_case.json"),
                "layer1": case.layer1_status,
                "cache": (l1.result or {}).get("manifest", {}).get("cache"),
            }, indent=2))
            return 0
        l21, l22 = controller.run_physical_analysis()
        l31 = controller.run_layer31() if args.with_layer31 else None
        files = controller.export()
        print(json.dumps({
            "case_file": str(case.root / "ascend_case.json"),
            "layer1": case.layer1_status,
            "layer2_1": l21.calculation_status,
            "layer2_2": l22.calculation_status,
            "layer3_1": l31.calculation_status if l31 else "not_requested",
            "exports": [str(path) for path in files],
        }, indent=2))
        if CalculationStatus.BLOCKED.value in {l21.calculation_status, l22.calculation_status}:
            return 2
        if CalculationStatus.OUTSIDE_VALIDATED_SCOPE.value in {l21.calculation_status, l22.calculation_status}:
            return 3
        return 0
    if args.command == "validate-eclipse-dvh":
        case_file = args.case / "ascend_case.json" if args.case.is_dir() else args.case
        try:
            case = ASCENDCase.load(case_file)
            criteria = json.loads(args.criteria.read_text(encoding="utf-8")) if args.criteria else None
            result = ApplicationController(case).validate_eclipse_dvh(args.reference, args.output, criteria)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "failed", "reason": str(exc)}, indent=2))
            return 2
        counts = result["summary"]["overall_counts"]
        print(json.dumps({
            "status": "completed_with_discrepancies" if counts["n_failing"] else "completed",
            "case_id": result["case_id"],
            "counts": counts,
            "artifacts": result["artifacts"],
        }, indent=2))
        return 4 if counts["n_failing"] else 0
    if args.command == "diagnose-eclipse-volumes":
        case_file = args.case / "ascend_case.json" if args.case.is_dir() else args.case
        try:
            case = ASCENDCase.load(case_file)
            result = ApplicationController(case).diagnose_eclipse_volume_discrepancies(args.comparison, args.output)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "failed", "reason": str(exc)}, indent=2))
            return 2
        print(json.dumps({
            "status": "investigated_with_preserved_failures",
            "case_id": result["case_id"],
            "classifications": {
                item["roi_name"]: item["diagnostic_interpretation"]
                for item in result["structures"]
            },
            "artifacts": result["artifacts"],
        }, indent=2))
        return 4
    if args.command == "validate-anisotropic":
        from ascend.validation.anisotropic import run_anisotropic_validation, write_anisotropic_report
        result = run_anisotropic_validation(include_complete_dicom_pipeline=True)
        artifacts = write_anisotropic_report(result, args.output)
        print(json.dumps({"status": result["status"], "artifacts": artifacts}, indent=2))
        return 0 if result["status"] == "PASS" else 4
    if args.command == "validate-treatment-context":
        from ascend.validation.treatment_context import run_treatment_context_validation, write_treatment_context_report
        result = run_treatment_context_validation()
        artifacts = write_treatment_context_report(result, args.output)
        print(json.dumps({"status": result["status"], "artifacts": artifacts}, indent=2))
        return 0 if result["status"] == "PASS" else 4
    if args.command in {"layer31", "layer32"}:
        case_file = args.case / "ascend_case.json" if args.case.is_dir() else args.case
        try:
            case = ASCENDCase.load(case_file)
            controller = ApplicationController(case)
            record = controller.run_layer31() if args.command == "layer31" else controller.run_layer32()
            exports = (
                controller.export_layer31(args.export) if args.command == "layer31" and args.export and record.result
                else controller.export(args.export) if args.export and record.result else []
            )
            print(json.dumps({
                "calculation_status": record.calculation_status,
                "interpretation_status": record.interpretation_status,
                "result_path": record.result_path,
                "warnings": record.warnings,
                "error": record.error,
                "exports": [str(path) for path in exports],
            }, indent=2))
            return 2 if record.calculation_status in {"blocked", "failed", "not_implemented"} else 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "failed", "reason": str(exc)}, indent=2))
            return 2
    case = ASCENDCase.load(args.case_file)
    controller = ApplicationController(case)
    if args.command == "cache-inspect":
        print(json.dumps(controller.inspect_layer1_cache(), indent=2))
        return 0
    if args.command == "cache-clear":
        print(json.dumps({"removed_entries": controller.clear_layer1_cache(confirmed=args.confirm)}, indent=2))
        return 0
    if args.layer == "layer1": controller.run_layer1()
    elif args.layer == "layer2_1": controller.run_layer21()
    elif args.layer == "layer2_2": controller.run_layer22()
    elif args.layer == "layer3_1": controller.run_layer31()
    elif args.layer == "layer3_2": controller.run_layer32()
    elif args.layer == "physical": controller.run_physical_analysis()
    else: controller.export()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
