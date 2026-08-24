"""Service-layer orchestration for the enclosing ASCEND package."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from ascend import __version__
from ascend.models.case import ASCENDCase

from .comparison import compare_references
from .reference_import import import_eclipse_reference, sha256_file
from .reporting import write_validation_outputs
from .schemas import COMPARISON_SCHEMA_VERSION, SUMMARY_SCHEMA_VERSION, AcceptanceCriteria
from .statistics import bland_altman, build_summary


class EclipseDvhValidationService:
    """Compare imported Eclipse endpoints with existing stored ASCEND results."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[3]

    def _locked_hashes(self) -> dict[str, str]:
        root = self.project_root / "ascend" / "scientific" / "legacy"
        return {
            "Layer 1": sha256_file(root / "layer1_validated.py"),
            "Layer 2.1": sha256_file(root / "layer21_validated.py"),
            "Layer 2.2": sha256_file(root / "layer22_validated.py"),
        }

    @staticmethod
    def _limitations(
        comparisons: list[dict[str, Any]],
        imported: dict[str, Any],
        summary: dict[str, Any],
    ) -> list[str]:
        limitations = []
        if any(item.get("matching_status") == "matched_unique_fallback" for item in comparisons):
            limitations.append(
                "At least one Eclipse reference lacked complete RTSTRUCT UID and ROI-number identity and used a uniquely proven fallback."
            )
        if any(not record.rtdose_uid for record in imported["records"]):
            limitations.append(
                "At least one Eclipse reference did not supply an RTDOSE SOP Instance UID; same-dose-object identity could not be proven from the reference and relied on the adapter's case/plan checks."
            )
        if any(item.get("comparison_status") == "missing_ascend_endpoint" for item in comparisons):
            limitations.append(
                "Some supplied Eclipse endpoints have no corresponding endpoint stored by the locked ASCEND result; the harness did not recalculate them."
            )
        unavailable = summary["planned_endpoint_availability"]["unavailable_in_reference"]
        if unavailable:
            limitations.append("The supplied Eclipse reference does not contain: " + ", ".join(unavailable) + ".")
        if any(not record.eclipse_version for record in imported["records"]):
            limitations.append("The Eclipse software version was not supplied for every reference row.")
        if imported.get("issues"):
            limitations.append("Reference-import issues and intentionally skipped out-of-scope endpoints are retained in provenance.")
        return limitations

    def run(
        self,
        case: ASCENDCase,
        reference_source: str | Path,
        output_directory: str | Path | None = None,
        criteria: AcceptanceCriteria | None = None,
    ) -> dict[str, Any]:
        """Execute run and return its explicit calculation state and evidence."""
        if not case.layer1.result:
            raise ValueError("ECLIPSE_DVH_VALIDATION: a completed stored Layer 1 result is required.")
        manifest = case.layer1.result.get("manifest", {})
        if not manifest.get("roi_inventory"):
            raise ValueError("ECLIPSE_DVH_VALIDATION: the Layer 1 result has no ROI-identity inventory; rerun Layer 1 with ASCEND 0.7 or later.")
        criteria = criteria or AcceptanceCriteria()
        imported = import_eclipse_reference(
            reference_source,
            structure_roles=case.configuration.structure_roles,
            expected_patient_id=manifest.get("case_id") or case.case_id,
            expected_plan=manifest.get("plan_label"),
        )
        comparisons = compare_references(case, imported["records"], criteria)
        summary = build_summary(comparisons)
        ba = bland_altman(comparisons)
        created = datetime.now(timezone.utc).isoformat()
        reference_summary = {
            key: value for key, value in imported.items() if key != "records"
        }
        reference_summary["record_count"] = len(imported["records"])
        run = {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            "created_utc": created,
            "ascend_version": __version__,
            "case_id": case.case_id,
            "locked_scientific_source_hashes": self._locked_hashes(),
            "acceptance_criteria": criteria.to_dict(),
            "reference_import": reference_summary,
            "comparisons": comparisons,
            "summary": summary,
            "bland_altman": ba,
            "limitations": self._limitations(comparisons, imported, summary),
            "provenance": {
                "layer1_run_id": case.layer1.run_id,
                "layer1_result_path": case.layer1.result_path,
                "layer1_result_schema_version": manifest.get("layer1_result_schema_version"),
                "layer1_algorithm_version": manifest.get("layer1_algorithm_version"),
                "rtdose_uid": manifest.get("rtdose_uid"),
                "rtstruct_uid": manifest.get("rtstruct_uid"),
                "rtplan_uid": manifest.get("rtplan_uid"),
                "calculation_policy": "Stored ASCEND endpoints only; no DVH endpoint recalculation.",
            },
        }
        destination = Path(output_directory) if output_directory else case.root / "validation" / "eclipse_dvh" / "results"
        run["artifacts"] = write_validation_outputs(run, destination)
        run_manifest = destination.expanduser().resolve() / "eclipse_dvh_validation_run.json"
        run_manifest.write_text(json.dumps(run, indent=2, allow_nan=False), encoding="utf-8")
        run["artifacts"]["run_manifest"] = {"path": str(run_manifest), "sha256": sha256_file(run_manifest)}
        return run
