"""Application orchestration for case import, configuration, dependency invalidation, analysis, and export."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pydicom

from ascend.dicom.discovery import discover_case, write_inventory
from ascend.dicom.relationships import select_chain
from ascend.dicom.roi import resolve_name
from ascend.dicom.rtplan_config import apply_unambiguous_rtplan_prefill, extract_rtplan_configuration
from ascend.layer1.service import Layer1Service
from ascend.layer1.cache import Layer1Cache
from ascend.layer2.graph.service import Layer22Service, OutsideValidatedScope
from ascend.layer2.metrics.service import Layer21Service
from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.nonlocal_effect.service import Layer32Service
from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.config import CaseConfiguration
from ascend.models.status import CalculationStatus, InterpretationStatus, Layer1Status
from ascend.reporting.export import export_case
from ascend.validation.provenance import canonical_hash, run_id, software_identity
from ascend.workflow.preferences import eclipse_endpoint_suggestions, merge_endpoint_suggestions

from .state import ApplicationState


def _normalise(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _auto_roles(rtstruct_path: str | None) -> dict[str, str | list[str]]:
    """Propose conservative display-name mappings for initial configuration.

    These name heuristics are a convenience only.  ``_bindings_from_roles``
    immediately resolves accepted names to RTSTRUCT SOP UID plus ROI number,
    which becomes the authoritative persisted identity.
    """
    if not rtstruct_path:
        return {}
    dataset = pydicom.dcmread(rtstruct_path, stop_before_pixels=True)
    names = [str(item.ROIName) for item in getattr(dataset, "StructureSetROISequence", [])]
    indexed = {_normalise(name): name for name in names}

    def first(candidates: list[str]) -> str | None:
        return next((indexed[key] for key in candidates if key in indexed), None)

    roles: dict[str, str | list[str]] = {}
    for role, candidates in {
        "GTV": ["GTV", "GTVPRIMARY", "GTVP", "LRTGTV", "CTV"],
        "T_L": ["PTVLOW", "PTV2000", "PTV1", "PTV"],
        "VTV_H": ["VTVH", "ALLVERTICES", "HIGHDENSITYCTV"],
        "VTV_L": ["VTVL", "ALLVALLEYS"],
    }.items():
        selected = first(candidates)
        if selected:
            roles[role] = selected
    individuals = sorted(name for name in names if re.fullmatch(r"VTVH[_ -]?\d+", name, re.I))
    if individuals:
        roles["VTV_H_individual"] = individuals
    return roles


def _referenced_series_uids(rtstruct_path: str) -> set[str]:
    dataset = pydicom.dcmread(rtstruct_path, stop_before_pixels=True)
    found: set[str] = set()
    for frame in getattr(dataset, "ReferencedFrameOfReferenceSequence", []):
        for study in getattr(frame, "RTReferencedStudySequence", []):
            for series in getattr(study, "RTReferencedSeriesSequence", []):
                uid = str(getattr(series, "SeriesInstanceUID", ""))
                if uid:
                    found.add(uid)
    return found


def _select_image_series(inventory: dict[str, Any], rtstruct_path: str) -> list[str]:
    images = inventory.get("objects", {}).get("CT", [])
    if not images:
        return []
    referenced = _referenced_series_uids(rtstruct_path)
    matching = [item["path"] for item in images if item.get("series_instance_uid") in referenced]
    if matching:
        return matching
    groups: dict[str, list[str]] = {}
    for item in images:
        groups.setdefault(item.get("series_instance_uid") or "UNIDENTIFIED", []).append(item["path"])
    if len(groups) == 1:
        return next(iter(groups.values()))
    raise ValueError(
        f"Ambiguous planning images: found {len(groups)} CT series and none matched the RTSTRUCT reference. "
        "Select a directory containing one complete DICOM-RT case."
    )


def _bindings_from_roles(rtstruct_path: str, roles: dict[str, str | list[str]]) -> dict[str, Any]:
    dataset = pydicom.dcmread(rtstruct_path, stop_before_pixels=True)
    bindings: dict[str, Any] = {}
    for role, configured in roles.items():
        bindings[role] = (
            [resolve_name(dataset, name) for name in configured]
            if isinstance(configured, list) else resolve_name(dataset, configured)
        )
    return bindings


class ApplicationController:
    """Own case workflow state and invoke scientific services for every UI."""
    def __init__(self, case: ASCENDCase | None = None) -> None:
        self.case = case
        self.state = ApplicationState()
        self.layer1_service = Layer1Service()
        self.layer21_service = Layer21Service()
        self.layer22_service = Layer22Service()
        self.layer31_service = Layer31Service()
        self.layer32_service = Layer32Service()

    def import_case(self, source_directory: str | Path, case_root: str | Path | None = None) -> ASCENDCase:
        """Inventory DICOM headers and initialise a case without running science."""
        inventory = discover_case(source_directory)
        if not inventory.get("dicom_chains"):
            raise ValueError("No candidate DICOM-RT chain could be resolved from the selected directory.")
        root = Path(case_root).expanduser().resolve() if case_root else Path(source_directory).expanduser().resolve() / "ASCEND_CASE"
        case = ASCENDCase(case_root=str(root), case_id=inventory["patient_id"])
        case.initialise_directories()
        case.dicom_objects = inventory["objects"]
        case.dicom_chains = inventory["dicom_chains"]
        case.patient_metadata = {"patient_id": inventory["patient_id"]}
        case.study_metadata = {"study_instance_uid": inventory["study_instance_uid"]}
        case.selected_objects = {"source_directory": inventory["source_directory"]}
        selected = next((item for item in case.dicom_chains if item["selection_status"] == "selected"), None)
        if selected:
            self._apply_chain(case, selected)
            case.chain_selection = select_chain(case.dicom_chains, selected["chain_id"])
            case.selected_chain_id = selected["chain_id"]
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        case.provenance = {
            **case.provenance,
            **software_identity(),
            "source_inventory": str(case.root / "raw" / "inventory.json"),
        }
        write_inventory(inventory, case.root / "raw" / "inventory.json")
        case.save()
        self.case = case
        self._log("case_imported", "INFO", "import", f"Imported {inventory['counts']}")
        self.state.update(stage="VALIDATION", message="DICOM objects imported; configure the case and run Layer 1")
        return case

    @staticmethod
    def _apply_chain(case: ASCENDCase, chain: dict[str, Any]) -> None:
        """Apply one resolved UID chain and safe RTPLAN configuration prefills."""
        source = case.selected_objects.get("source_directory")
        case.selected_objects = {"source_directory": source, **chain["objects"]}
        case.configuration.structure_roles = _auto_roles(chain["objects"].get("rtstruct"))
        case.configuration.structure_bindings = _bindings_from_roles(
            chain["objects"]["rtstruct"], case.configuration.structure_roles
        ) if chain["objects"].get("rtstruct") else {}
        evidence = extract_rtplan_configuration(
            chain["objects"].get("rtplan"), chain["objects"].get("rtdose"),
        )
        apply_unambiguous_rtplan_prefill(case.configuration, evidence)
        case.provenance["dicom_configuration_prefill"] = evidence

    def select_dicom_chain(
        self,
        chain_id: str,
        allow_incomplete: bool = False,
        override_reason: str | None = None,
    ) -> None:
        """Select a resolved chain and invalidate identity-dependent results."""
        case = self.require_case()
        evidence = select_chain(case.dicom_chains, chain_id, allow_incomplete, override_reason)
        chain = next(item for item in case.dicom_chains if item["chain_id"] == chain_id)
        changed_struct = case.selected_objects.get("rtstruct") != chain["objects"].get("rtstruct")
        self._apply_chain(case, chain)
        if changed_struct:
            case.configuration.validation_structures = []
            case.configuration.oar_structures = []
            self.invalidate(["layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"], "DICOM chain changed")
        case.selected_chain_id = chain_id
        case.chain_selection = evidence
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        case.save()
        self._log("dicom_chain_selected", "WARN" if evidence["override_confirmed"] else "INFO", "import", chain_id)

    def configure(self, configuration: CaseConfiguration) -> None:
        """Validate configuration and invalidate only affected downstream layers."""
        case = self.require_case()
        rtstruct = case.selected_objects.get("rtstruct")
        if configuration.structure_roles and not configuration.structure_bindings and rtstruct:
            configuration.structure_bindings = _bindings_from_roles(str(rtstruct), configuration.structure_roles)
        if rtstruct:
            dataset = pydicom.dcmread(str(rtstruct), stop_before_pixels=True)
            configuration.validation_structures = [
                resolve_name(dataset, item) if isinstance(item, str) else item
                for item in configuration.validation_structures
            ]
            for item in configuration.oar_structures:
                if item.get("roi_identity") is None:
                    item["roi_identity"] = resolve_name(dataset, str(item.get("name") or item.get("display_name")))
        configuration.validate()
        old = case.configuration.to_dict()
        new = configuration.to_dict()
        # Dependency-specific invalidation preserves unaffected evidence while
        # preventing any result from surviving a changed scientific input.
        if any(old.get(key) != new.get(key) for key in ("structure_bindings", "validation_structures")):
            self.invalidate(["layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"], "canonical structure mapping changed")
        if old.get("tps_metrics_csv") != new.get("tps_metrics_csv"):
            self.invalidate(["layer1"], "TPS DVH validation reference changed")
        if old.get("oar_structures") != new.get("oar_structures"):
            self.invalidate(
                ["layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"],
                "OAR rasterisation configuration changed",
            )
        if old.get("layer32_enabled") != new.get("layer32_enabled"):
            self.invalidate(["layer3_2"], "Layer 3.2 enable state changed")
        elif old.get("layer32_parameters") != new.get("layer32_parameters"):
            self.invalidate(["layer3_2"], "Layer 3.2 model parameters changed")
        if any(old.get(key) != new.get(key) for key in (
            "treatment_approach", "treatment_delivery_mode", "dose_context", "prescription_context",
            "treatment_components", "selected_treatment_component_id", "valley_includes_cert_background",
        )):
            self.invalidate(["layer2_1", "layer2_2", "layer3_1", "layer3_2"], "treatment context changed")
        if old.get("prescriptions") != new.get("prescriptions"):
            self.invalidate(["layer2_1", "layer3_1", "layer3_2"], "prescription changed")
        if old.get("fractionation") != new.get("fractionation"):
            self.invalidate(["layer3_1", "layer3_2"], "fractionation changed")
        if any(old.get(key) != new.get(key) for key in (
            "layer31_roi_parameters", "layer31_component_sources",
            "layer31_lq_high_dose_warning_gy_per_fraction",
            "layer31_mlq_tumour_parameters", "layer31_mlq_normal_parameters",
            "layer31_tumour_scenario", "layer31_normal_scenario",
            "layer31_tr_reference_schedule",
            "layer31_paired_course_reference_result_path",
        )):
            self.invalidate(["layer3_1", "layer3_2"], "Layer 3.1 biological configuration changed")
        if old.get("layer31_tcp_parameters") != new.get("layer31_tcp_parameters"):
            branch = (case.layer3_1.result or {}).get("layer3_1d_tumour_control_probability")
            if isinstance(branch, dict):
                branch.update({
                    "status": "STALE", "calculation_status": "stale",
                    "applicability_status": "NOT_ASSESSED", "interpretation_status": "not_interpretable",
                    "reason": "Layer 3.1D TCP parameters changed",
                })
            self.invalidate(["layer3_2"], "Layer 3.1D TCP configuration changed")
        if any(old.get(key) != new.get(key) for key in ("protocol_context", "protocol_native_endpoints", "valley_definition_source", "valley_overlap_tolerance_pct")):
            self.invalidate(["layer2_1", "layer3_2"], "protocol or Layer 2.1 configuration changed")
        case.configuration = configuration
        case.configuration_hash = canonical_hash(new)
        self._log("configuration_saved", "INFO", "configuration", f"Configuration hash {case.configuration_hash}")
        case.save()

    def prefill_eclipse_endpoints(self) -> list[dict[str, Any]]:
        """Import supported Eclipse endpoints into configuration without running science."""
        case = self.require_case()
        source = case.configuration.tps_metrics_csv
        if not source:
            raise ValueError(
                "No Eclipse DVH reference is configured. Select an Eclipse CSV/TXT file or export folder on Import."
            )
        reference_path = Path(source).expanduser()
        if not reference_path.exists():
            raise ValueError(f"The configured Eclipse DVH reference does not exist: {reference_path}")
        plan_path = case.selected_objects.get("rtplan")
        plan_label = None
        if isinstance(plan_path, str) and plan_path:
            plan = pydicom.dcmread(plan_path, stop_before_pixels=True)
            plan_label = str(getattr(plan, "RTPlanLabel", "")) or None
        suggestions, summary = eclipse_endpoint_suggestions(
            reference_path,
            case.configuration.structure_roles,
            expected_patient_id=case.case_id,
            expected_plan=plan_label,
        )
        merged = merge_endpoint_suggestions(case.configuration.protocol_native_endpoints, suggestions)
        added = len(merged) - len(case.configuration.protocol_native_endpoints)
        case.configuration.protocol_native_endpoints = merged
        case.configuration.eclipse_endpoint_prefill = {**(summary or {}), "added_endpoint_count": added}
        case.configuration.validate()
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        if added:
            self.invalidate(["layer2_1", "layer3_2"], "Eclipse protocol endpoints auto-filled")
        case.save()
        self._log("eclipse_endpoints_prefilled", "INFO", "configuration", f"Added {added} supported endpoint(s)")
        return suggestions

    def select_dicom_object(self, key: str, value: str | list[str] | None) -> None:
        """Select dicom object using explicit deterministic criteria."""
        case = self.require_case()
        if case.selected_objects.get(key) == value:
            return
        case.selected_objects[key] = value
        if key in {"rtstruct", "rtdose", "rtplan", "image_series"}:
            self.invalidate(["layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"], f"{key} selection changed")
        case.save()

    def invalidate(self, layers: list[str], reason: str) -> None:
        """Handle invalidate for the enclosing ASCEND workflow."""
        case = self.require_case()
        for name in layers:
            record: LayerRun = getattr(case, name)
            record.mark_stale(reason)
            if name == "layer1" and record.calculation_status == CalculationStatus.STALE.value:
                case.layer1_status = Layer1Status.STALE.value

    def run_layer1(self) -> LayerRun:
        """Execute layer1 and return its explicit calculation state and evidence."""
        case = self.require_case()
        self.state.update(busy=True, message="Layer 1: validating DICOM, geometry, dose, contours, masks, and volumes")
        try:
            record = self.layer1_service.run(case)
            case.layer1 = record
            layer_status = record.result.get("eligibility", {}).get("layer_1_status", "BLOCK") if record.result else "BLOCK"
            case.layer1_status = {"PASS": "PASS", "WARN": "WARN", "BLOCK": "FAIL"}.get(layer_status, "FAIL")
            self.invalidate(["layer2_1", "layer2_2", "layer3_1", "layer3_2"], "new Layer 1 run")
            self.state.update(stage="PHYSICAL_ANALYSIS", message=f"Layer 1 {case.layer1_status}")
            self._log("layer1_complete", "WARN" if case.layer1_status == "WARN" else "INFO", "layer1", f"Status {case.layer1_status}")
            if case.configuration.tps_metrics_csv:
                try:
                    self.prefill_eclipse_endpoints()
                except ValueError as exc:
                    case.warnings.append(f"Eclipse endpoint auto-fill not applied: {exc}")
                    self._log("eclipse_endpoint_prefill_skipped", "WARN", "configuration", str(exc))
        except Exception as exc:
            record = LayerRun("layer1", CalculationStatus.FAILED.value, InterpretationStatus.NOT_INTERPRETABLE.value, run_id=run_id("L1"), error=str(exc))
            case.layer1 = record
            case.layer1_status = Layer1Status.FAIL.value
            case.errors.append(str(exc))
            self.state.update(message=f"Layer 1 failed: {exc}")
            self._log("layer1_failed", "ERROR", "layer1", str(exc))
        finally:
            self.state.update(busy=False)
            case.save()
        return record

    def run_layer21(self) -> LayerRun:
        """Execute layer21 and return its explicit calculation state and evidence."""
        return self._run_downstream("layer2_1", self.layer21_service.run, "Layer 2.1")

    def run_layer22(self) -> LayerRun:
        """Execute layer22 and return its explicit calculation state and evidence."""
        return self._run_downstream("layer2_2", self.layer22_service.run, "Layer 2.2")

    def build_layer31_basis(self) -> Any:
        """Build the hash-verified fraction-resolved Layer 3.1 basis."""
        case = self.require_case()
        return self.layer31_service.build_basis(case)

    def run_layer31(self) -> LayerRun:
        """Run the gated Layer 3.1 fraction-history radiobiology workflow."""
        return self._run_downstream("layer3_1", self.layer31_service.run, "Layer 3.1")

    def run_layer32(self) -> LayerRun:
        """Run Layer 3.2 against the current authoritative Layer 3.1 basis."""
        if not self.require_case().configuration.layer32_enabled:
            raise ValueError("Layer 3.2 is disabled. Enable the Layer 3.2 research model before running it.")
        return self._run_downstream("layer3_2", self.layer32_service.run, "Layer 3.2")

    def run_layer31_parameter_sweep(self, roi_identity: dict[str, Any], alpha_beta_values: Any) -> dict[str, Any]:
        """Run an identity-bound alpha/beta sensitivity sweep on the stored P/Q basis."""
        return self.layer31_service.parameter_sweep(self.require_case(), roi_identity, alpha_beta_values).to_dict()

    def export_layer31(
        self,
        destination: str | Path,
        full_maps: list[tuple[dict[str, Any], float]] | None = None,
    ) -> list[Path]:
        """Export the current Layer 3.1 result and optional authoritative field maps."""
        return self.layer31_service.export(self.require_case(), destination, full_maps)

    def run_physical_analysis(self) -> tuple[LayerRun, LayerRun]:
        """Execute physical analysis and return its explicit calculation state and evidence."""
        return self.run_layer21(), self.run_layer22()

    def _run_downstream(self, attribute: str, function: Any, label: str) -> LayerRun:
        case = self.require_case()
        current_layer1_states = {
            CalculationStatus.COMPLETED.value,
            CalculationStatus.COMPLETED_WITH_WARNINGS.value,
        }
        if (
            case.layer1.calculation_status not in current_layer1_states
            or case.layer1_status not in {Layer1Status.PASS.value, Layer1Status.WARN.value}
        ):
            record = LayerRun(
                attribute,
                CalculationStatus.BLOCKED.value,
                InterpretationStatus.NOT_INTERPRETABLE.value,
                error=(
                    "A current validated Layer 1 result is required. "
                    f"Layer 1 is {case.layer1.calculation_status!r}; rerun Layer 1 after configuration changes."
                ),
            )
            setattr(case, attribute, record)
            case.save()
            return record
        if not case.layer1.result or not case.layer1.result.get("eligibility", {}).get("layer_2_eligible"):
            record = LayerRun(attribute, CalculationStatus.BLOCKED.value, InterpretationStatus.NOT_INTERPRETABLE.value, error="Layer 1 eligibility is closed.")
            setattr(case, attribute, record)
            case.save()
            return record
        self.state.update(busy=True, message=f"{label}: running")
        try:
            record = function(case)
            setattr(case, attribute, record)
            self.state.update(message=f"{label}: {record.calculation_status}")
            self._log(f"{attribute}_complete", "WARN" if record.warnings else "INFO", attribute, record.calculation_status)
        except OutsideValidatedScope as exc:
            record = LayerRun(
                attribute, CalculationStatus.OUTSIDE_VALIDATED_SCOPE.value,
                InterpretationStatus.NOT_INTERPRETABLE.value, run_id=run_id(attribute),
                parent_layer1_run_id=case.layer1.run_id, warnings=[str(exc)],
            )
            setattr(case, attribute, record)
            self.state.update(message=f"{label}: outside validated scope")
            self._log(f"{attribute}_outside_scope", "INFO", attribute, str(exc))
        except (ValueError, RuntimeError) as exc:
            record = LayerRun(attribute, CalculationStatus.BLOCKED.value, InterpretationStatus.NOT_INTERPRETABLE.value, run_id=run_id(attribute), parent_layer1_run_id=case.layer1.run_id, error=str(exc))
            setattr(case, attribute, record)
            case.warnings.append(f"{label} blocked: {exc}")
            self.state.update(message=f"{label} blocked: {exc}")
            self._log(f"{attribute}_blocked", "WARN", attribute, str(exc))
        except Exception as exc:
            record = LayerRun(attribute, CalculationStatus.FAILED.value, InterpretationStatus.NOT_INTERPRETABLE.value, run_id=run_id(attribute), parent_layer1_run_id=case.layer1.run_id, error=str(exc))
            setattr(case, attribute, record)
            case.errors.append(f"{label}: {exc}")
            self.state.update(message=f"{label} failed: {exc}")
            self._log(f"{attribute}_failed", "ERROR", attribute, str(exc))
        finally:
            self.state.update(busy=False)
            case.save()
        return record

    def export(self, destination: str | Path | None = None) -> list[Path]:
        """Export export from stored results without recalculation."""
        case = self.require_case()
        return export_case(case, destination or case.root / "exports")

    def inspect_layer1_cache(self) -> list[dict[str, Any]]:
        """Inspect layer1 cache without mutating stored state."""
        return Layer1Cache(self.require_case().root).inspect()

    def clear_layer1_cache(self, confirmed: bool = False) -> int:
        """Clear layer1 cache only after the caller's authorization requirements are met."""
        count = Layer1Cache(self.require_case().root).clear(confirmed=confirmed)
        self._log("layer1_cache_cleared", "INFO", "cache", f"Removed {count} cache entries")
        return count

    def validate_eclipse_dvh(
        self,
        reference_source: str | Path,
        output_directory: str | Path | None = None,
        criteria: Any = None,
    ) -> dict[str, Any]:
        """Validate eclipse dvh and raise a controlled error when requirements are not met."""
        from ascend.validation.eclipse_harness import AcceptanceCriteria, EclipseDvhValidationService

        case = self.require_case()
        configured = criteria if isinstance(criteria, AcceptanceCriteria) else (
            AcceptanceCriteria.from_dict(criteria) if isinstance(criteria, dict) else AcceptanceCriteria()
        )
        result = EclipseDvhValidationService().run(case, reference_source, output_directory, configured)
        counts = result["summary"]["overall_counts"]
        self._log(
            "eclipse_dvh_validation_complete",
            "WARN" if counts["n_failing"] or counts["n_excluded_or_not_comparable"] else "INFO",
            "validation",
            f"Valid={counts['n_valid_comparisons']} pass={counts['n_passing']} fail={counts['n_failing']} excluded={counts['n_excluded_or_not_comparable']}",
        )
        return result

    def diagnose_eclipse_volume_discrepancies(
        self,
        comparison_path: str | Path | None = None,
        output_directory: str | Path | None = None,
    ) -> dict[str, Any]:
        """Handle diagnose eclipse volume discrepancies for the enclosing ASCEND workflow."""
        from ascend.validation.volume_diagnostics import EclipseVolumeDiagnosticService

        case = self.require_case()
        result = EclipseVolumeDiagnosticService().run(case, comparison_path, output_directory)
        self._log(
            "eclipse_volume_diagnostics_complete",
            "WARN",
            "validation",
            "Investigated preserved all_vertices and all_valleys formal volume failures.",
        )
        return result

    def require_case(self) -> ASCENDCase:
        """Handle require case for the enclosing ASCEND workflow."""
        if self.case is None:
            raise RuntimeError("No ASCEND case is open.")
        return self.case

    def _log(self, code: str, level: str, layer: str, message: str) -> None:
        if self.case is None:
            return
        self.case.initialise_directories()
        timestamp = datetime.now(timezone.utc).isoformat()
        record = {"timestamp_utc": timestamp, "level": level, "code": code, "layer": layer, "message": message}
        with (self.case.root / "logs" / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        with (self.case.root / "logs" / "ascend.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{timestamp} {level} {layer} {code}: {message}\n")
