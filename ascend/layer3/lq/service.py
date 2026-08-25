"""Service-layer orchestration for the enclosing ASCEND package."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from ascend import __version__
from ascend.dicom.roi import identity_key
from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.status import CalculationStatus, InterpretationStatus
from ascend.scientific.legacy import layer21_validated as handoff
from ascend.treatment.models import TreatmentContext
from ascend.validation.provenance import base_provenance, canonical_hash, file_hash, run_id
from ascend.layer3.history import GateResult, reconstruct_fraction_history

from .basis import _deterministic_npz, build_basis
from .biological_metrics import build_biological_six_metrics
from .metrics import (
    cumulative_volume_histogram, full_bed_map, full_eqd2_map,
    roi_summary_metrics, roi_summary_values,
)
from .models import LQ_ALGORITHM_VERSION, LQ_RESULT_SCHEMA_VERSION, LQBiologicalBasis, Layer31ROIResult, Layer31SweepResult, ROIParameterAssignment
from .parameters import parse_sweep, validate_alpha_beta, validate_parameter_assignment
from .spatial import build_spatial_lq_result
from ascend.layer3.response.course import (
    run_fraction_resolved_therapeutic_ratio, run_fraction_resolved_tumour_response,
    run_sensitivity_scenario_matrix,
)
from ascend.layer3.response.mlq import MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, TR_FORMALISM_ID, TR_FORMALISM_VERSION
from ascend.layer3.response.associations import research_association_record
from ascend.layer3.response.comparison import load_and_compare
from ascend.layer3.response.course import LAYER31B_SCOPE_EXCLUSIONS
from ascend.layer3.tcp.service import run_layer31d_tcp


LQ_REFERENCE_FORMALISM_ID = "CONVENTIONAL_LQ_REFERENCE"
LQ_REFERENCE_FORMALISM_VERSION = "ASCEND-L3.1A-LQ-PQ-v1.0"
LAYER31_COURSE_ALGORITHM_VERSION = "ASCEND-L3.1-fraction-event-course-v2.0"


class Layer31Service:
    """Coordinate the layer31 workflow without GUI-side calculation."""
    algorithm_version = LAYER31_COURSE_ALGORITHM_VERSION
    schema_version = LQ_RESULT_SCHEMA_VERSION

    def __init__(self) -> None:
        self._basis_memory_cache: dict[str, Any] = {}

    @staticmethod
    def _require_current_layer1(case: ASCENDCase) -> None:
        """Reject stale masks before any biological calculation or sweep."""
        current_states = {
            CalculationStatus.COMPLETED.value,
            CalculationStatus.COMPLETED_WITH_WARNINGS.value,
        }
        if case.layer1.calculation_status not in current_states:
            raise ValueError(
                "A current validated Layer 1 result is required for Layer 3.1; "
                f"Layer 1 is {case.layer1.calculation_status!r}."
            )

    @staticmethod
    def _components(case: ASCENDCase) -> list[dict[str, Any]]:
        if case.configuration.layer31_component_sources:
            configured_components = {str(item.get("component_id")): item for item in case.configuration.treatment_components}
            return [
                {**configured_components.get(str(source.get("component_id")), {}), **source}
                for source in case.configuration.layer31_component_sources
            ]
        if not case.layer1.result_path:
            return []
        manifest = (case.layer1.result or {}).get("manifest", {})
        selected = next(
            (item for item in case.configuration.treatment_components if item.get("component_id") == case.configuration.selected_treatment_component_id),
            None,
        )
        fractionation = manifest.get("fractionation", {})
        configured_fraction = selected.get("fraction_count") if selected else None
        if configured_fraction is None:
            configured_fraction = case.configuration.fractionation.get("fractions")
        return [{
            "component_id": (selected or {}).get("component_id") or manifest.get("treatment_component") or "primary",
            "component_type": (selected or {}).get("component_type") or manifest.get("treatment_component") or "unknown",
            "fraction_count": configured_fraction if configured_fraction is not None else fractionation.get("number_of_fractions"),
            "prescription_gy": (selected or {}).get("prescription_gy", fractionation.get("prescription_dose_gy")),
            "source": (selected or {}).get("source", fractionation.get("fractionation_source", "unknown")),
            "layer1_result_path": case.layer1.result_path,
            "fraction_dose_model": "identical_fractions",
            "timepoint": "current_validated_plan",
        }]

    @staticmethod
    def _assignments(case: ASCENDCase, layer1: dict[str, Any]) -> list[ROIParameterAssignment]:
        mappings = layer1.get("structure_mapping", [])
        inventory = layer1.get("manifest", {}).get("roi_inventory", [])
        role_by_standard = {
            standard: role
            for role, standard in case.effective_structure_roles.items()
            if isinstance(standard, str)
        }
        by_identity = {
            identity_key(item["roi_identity"]): item
            for item in inventory if item.get("roi_identity") and item.get("rasterisation_status") == "rasterised"
        }
        assignments: list[ROIParameterAssignment] = []
        for raw in case.configuration.layer31_roi_parameters:
            validate_parameter_assignment(raw)
            key = identity_key(raw["roi_identity"])
            item = by_identity.get(key)
            if item is None:
                raise ValueError("Layer 3.1 ROI assignment is not a rasterised Layer 1 ROI.")
            standard = item.get("canonical_mapping")
            source_type = str(raw["parameter_source_type"])
            warnings = ("manual_radiobiological_parameter",) if source_type == "user_selected" else ()
            assignments.append(ROIParameterAssignment(
                roi_identity=raw["roi_identity"], roi_name=str(item.get("original_name") or raw.get("roi_name") or standard),
                canonical_role=role_by_standard.get(standard), alpha_beta_gy=validate_alpha_beta(raw["alpha_beta_gy"]),
                parameter_source=str(raw["parameter_source"]), parameter_source_type=source_type,
                parameter_set_version=str(raw["parameter_set_version"]),
                assignment_method=str(raw.get("assignment_method") or "identity_bound"),
                assignment_origin="user_entered" if source_type == "user_selected" else "configured",
                warnings=warnings,
            ))
        return assignments

    @staticmethod
    def _viewer_geometry(layer1: dict[str, Any]) -> dict[str, Any]:
        """Expose validated Layer 1 geometry under explicit display names."""
        geometry = dict(layer1.get("manifest", {}).get("validated_geometry") or {})
        return {
            "origin": geometry.get("origin"),
            "row_direction": geometry.get("row_direction", geometry.get("row_dir")),
            "column_direction": geometry.get("column_direction", geometry.get("col_dir")),
            "normal": geometry.get("normal"),
            "offsets": geometry.get("offsets"),
            "in_plane_spacing_mm": geometry.get("spacing"),
            "spacing": geometry.get("spacing"),
            "shape": geometry.get("shape"),
            "dose_grid_spacing_mm": layer1.get("manifest", {}).get("dose_grid", {}).get("voxel_spacing_mm"),
            "coordinate_system": "DICOM patient LPS",
        }

    @staticmethod
    def _blocked_fraction_formalism(
        formalism_id: str,
        formalism_version: str,
        reason: str,
        gates: tuple[GateResult, ...],
    ) -> dict[str, Any]:
        return {
            "formalism_id": formalism_id,
            "formalism_version": formalism_version,
            "status": "BLOCKED",
            "calculation_status": "blocked",
            "applicability_status": "BLOCKED",
            "interpretation_status": "not_interpretable",
            "reason": reason,
            "blocking_reasons": [reason],
            "gate_results": [item.to_dict() for item in gates],
            "warnings": [],
        }

    @classmethod
    def _lq_formalism(
        cls,
        case: ASCENDCase,
        basis: LQBiologicalBasis,
        assignments: list[ROIParameterAssignment],
        roi_results: list[dict[str, Any]],
        fraction_history: Any | None,
        treatment_context: TreatmentContext,
        calculation_status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        # FractionHistory is the authoritative reconstruction of physical
        # fraction events.  Re-reading component totals here duplicated I/O
        # and could disagree with integrated same-fraction grouping.
        maximum = (
            float(np.max(fraction_history.maximum_fraction_dose_field))
            if fraction_history is not None else None
        )
        threshold = case.configuration.layer31_lq_high_dose_warning_gy_per_fraction
        triggered = bool(threshold is not None and maximum is not None and maximum >= float(threshold))
        warning = (
            "Conventional LQ reference — high-dose model sensitivity should be considered"
            if maximum is not None else "Maximum component dose per fraction is unavailable."
        )
        return {
            "formalism_id": LQ_REFERENCE_FORMALISM_ID,
            "formalism_version": LQ_REFERENCE_FORMALISM_VERSION,
            "calculation_status": calculation_status,
            "applicability_status": "APPLICABLE" if roi_results else "NOT_ASSESSED",
            "interpretation_status": "provisional" if roi_results else "not_interpretable",
            "reason": reason,
            "alpha_beta_parameter_sets": [{
                "roi_identity": item.roi_identity,
                "alpha_beta_gy": item.alpha_beta_gy,
                "parameter_source": item.parameter_source,
                "parameter_set_id": item.parameter_set_version,
                "parameter_hash": canonical_hash(item.to_dict()),
            } for item in assignments],
            "max_dose_per_fraction_gy": maximum,
            "high_dose_warning": {
                "message": warning,
                "configured_sensitivity_threshold_gy_per_fraction": threshold,
                "threshold_triggered": triggered,
                "validity_cutoff": False,
            },
            "time_effects_modelled": False,
            "repopulation_modelled": False,
            "roi_results": roi_results,
            "basis_hash": basis.basis_hash,
            "dose_hashes": basis.source_hashes,
            "treatment_context_hash": treatment_context.context_hash,
            "software_version": __version__,
            "warnings": sorted(
                set(basis.warnings)
                | {warning for assignment in assignments for warning in assignment.warnings}
                | ({"configured_high_dose_sensitivity_flag"} if triggered else set())
            ),
            "limitations": ["conventional_lq_reference_at_high_dose", "no_time_or_repopulation_model"],
            "formalism_source": {
                "model": "Voxelwise P/Q conventional LQ",
                "equations": basis.metadata().get("fundamental_model"),
                "implementation": "Existing ASCEND validated Layer 3.1 P/Q implementation retained unchanged",
            },
        }

    @staticmethod
    def _merge_lq_spatial_result(
        formalism: dict[str, Any], spatial: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge spatial evidence without erasing branch-level warnings/state."""
        merged = {**formalism, **spatial}
        warnings = sorted(set(formalism.get("warnings", [])) | set(spatial.get("warnings", [])))
        status_rank = {"blocked": 4, "completed_with_warnings": 3, "not_run": 2, "completed": 1}
        states = [str(formalism.get("calculation_status") or "not_run"), str(spatial.get("calculation_status") or "not_run")]
        calculation_status = max(states, key=lambda item: status_rank.get(item, 4))
        merged["warnings"] = warnings
        merged["calculation_status"] = calculation_status
        merged["status"] = {
            "blocked": "BLOCKED", "completed_with_warnings": "WARN",
            "not_run": "NOT_ASSESSED", "completed": "PASS",
        }.get(calculation_status, "BLOCKED")
        if calculation_status == "blocked":
            merged["interpretation_status"] = "not_interpretable"
        elif warnings or formalism.get("interpretation_status") == "provisional":
            merged["interpretation_status"] = "provisional"
        else:
            merged["interpretation_status"] = spatial.get("interpretation_status", "protocol_interpretable")
        return merged

    @classmethod
    def _overlap_audit(
        cls,
        layer1: dict[str, Any],
        masks: dict[str, np.ndarray],
        assignments: list[ROIParameterAssignment],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, first in enumerate(assignments):
            first_mask = cls._mask_for_assignment(layer1, masks, first)
            for second in assignments[index + 1:]:
                if first.alpha_beta_gy == second.alpha_beta_gy:
                    continue
                second_mask = cls._mask_for_assignment(layer1, masks, second)
                overlap = int(np.count_nonzero(first_mask & second_mask))
                if overlap:
                    records.append({
                        "first_roi_identity": first.roi_identity,
                        "second_roi_identity": second.roi_identity,
                        "first_alpha_beta_gy": first.alpha_beta_gy,
                        "second_alpha_beta_gy": second.alpha_beta_gy,
                        "overlap_voxel_count": overlap,
                        "policy": "independent_roi_analysis_only",
                        "combined_parameter_map_status": "blocked",
                        "combined_parameter_map_reason": "overlapping_roi_parameter_conflict",
                    })
        return records

    @staticmethod
    def _mask_for_assignment(layer1: dict[str, Any], masks: dict[str, np.ndarray], assignment: ROIParameterAssignment) -> np.ndarray:
        key = identity_key(assignment.roi_identity)
        inventory = layer1.get("manifest", {}).get("roi_inventory", [])
        match = next((item for item in inventory if item.get("roi_identity") and identity_key(item["roi_identity"]) == key), None)
        standard = match.get("canonical_mapping") if match else None
        if not standard or standard not in masks:
            raise ValueError("Validated ROI mask is unavailable for Layer 3.1 assignment.")
        return masks[standard]

    def build_basis_with_history(self, case: ASCENDCase) -> tuple[Any, list[dict[str, Any]], Any]:
        """Reconstruct fraction events once, then build/cache authoritative P/Q."""
        components = self._components(case)
        treatment_context = TreatmentContext.from_case(
            case.configuration, (case.layer1.result or {}).get("manifest", {}),
        )
        history_build = reconstruct_fraction_history(components, treatment_context)
        configuration = case.configuration.to_dict()
        configuration.pop("layer31_roi_parameters", None)
        basis_configuration_hash = canonical_hash({
            "treatment_approach": configuration.get("treatment_approach"),
            "treatment_delivery_mode": configuration.get("treatment_delivery_mode"),
            "dose_context": configuration.get("dose_context"),
            "prescription_context": configuration.get("prescription_context"),
            "treatment_components": configuration.get("treatment_components"),
            "selected_treatment_component_id": configuration.get("selected_treatment_component_id"),
            "fractionation": configuration.get("fractionation"),
            "layer31_component_sources": configuration.get("layer31_component_sources"),
        })
        request_key = canonical_hash({
            "case_root": str(case.root),
            "components": components,
            "basis_configuration_hash": basis_configuration_hash,
            "algorithm_version": LQ_ALGORITHM_VERSION,
            "fraction_history_hash": (
                history_build.history.history_hash if history_build.history is not None else None
            ),
        })
        cached = self._basis_memory_cache.get(request_key)
        if cached is not None:
            cached.basis.cache_hit = True
            cached.basis.provenance["last_reuse"] = "in_memory_verified_basis"
            return cached, components, history_build
        if history_build.history is None:
            from .models import BasisBuildResult
            result = BasisBuildResult(
                "blocked", "not_interpretable", history_build.reason, (), None,
            )
            return result, components, history_build
        result = build_basis(
            case.root, components, basis_configuration_hash,
            fraction_history=history_build.history,
        )
        if result.basis is not None:
            self._basis_memory_cache = {request_key: result}
        return result, components, history_build

    def build_basis(self, case: ASCENDCase) -> tuple[Any, list[dict[str, Any]]]:
        """Compatibility wrapper for the authoritative fraction-resolved basis."""
        result, components, _history = self.build_basis_with_history(case)
        return result, components

    def run(self, case: ASCENDCase) -> LayerRun:
        """Execute run and return its explicit calculation state and evidence."""
        try:
            self._require_current_layer1(case)
        except ValueError as exc:
            return LayerRun(
                "layer3_1", CalculationStatus.BLOCKED.value,
                InterpretationStatus.NOT_INTERPRETABLE.value, error=str(exc),
            )
        if not case.layer1.result_path or not case.layer1.run_id:
            return LayerRun("layer3_1", CalculationStatus.BLOCKED.value, InterpretationStatus.NOT_INTERPRETABLE.value, error="Layer 1 validated dose is required.")
        basis_result, configured_components, history_build = self.build_basis_with_history(case)
        identifier = run_id("L3_1")
        if basis_result.basis is None:
            payload = {
                "schema_version": LQ_RESULT_SCHEMA_VERSION, "algorithm_version": LAYER31_COURSE_ALGORITHM_VERSION,
                "run_id": identifier, "status": "BLOCKED", "calculation_status": "blocked", "interpretation_status": "not_interpretable",
                "reason": basis_result.reason, "warnings": list(basis_result.warnings),
                "gate_results": [{"gate_id": "GATE_0_UPSTREAM_DATA", "status": "BLOCKED", "reason_code": basis_result.reason}],
                "blocking_reasons": [basis_result.reason] if basis_result.reason else [],
                "component_configuration": configured_components,
                "provenance": base_provenance(case.configuration_hash or "", case.layer1.run_id),
            }
            output = case.root / "derived" / "layer3_1" / f"{identifier}.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return LayerRun("layer3_1", "blocked", "not_interpretable", identifier, case.layer1.run_id, str(output), payload, list(basis_result.warnings), basis_result.reason)
        basis: LQBiologicalBasis = basis_result.basis
        layer1_dir = Path(case.layer1.result_path).parent
        layer1, _dose, masks = handoff.load_handoff(layer1_dir)
        del _dose
        treatment_context = TreatmentContext.from_case(case.configuration, layer1.get("manifest", {}))
        fraction_history = history_build.history
        if fraction_history is not None:
            layer31b, tumour_state = run_fraction_resolved_tumour_response(
                case, basis, layer1, masks, fraction_history, identifier,
                materialise_fields=case.configuration.layer31_materialise_full_maps_on_run,
            )
            layer31c = run_fraction_resolved_therapeutic_ratio(case, tumour_state)
            layer31d = run_layer31d_tcp(case, basis, layer1, masks, tumour_state, identifier)
            if case.configuration.layer31_sensitivity_sweep_enabled:
                scenario_matrix = run_sensitivity_scenario_matrix(case, masks, fraction_history)
                scenario_matrix["enabled"] = True
            else:
                scenario_matrix = {
                    "status": "NOT_ASSESSED", "calculation_status": "not_run",
                    "applicability_status": "NOT_ASSESSED",
                    "reason": "SENSITIVITY_SWEEP_DISABLED", "records": [], "enabled": False,
                }
        else:
            reason = history_build.reason or "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED"
            layer31b = self._blocked_fraction_formalism(
                MLQ_FORMALISM_ID, MLQ_FORMALISM_VERSION, reason,
                history_build.gate_results,
            )
            layer31c = self._blocked_fraction_formalism(
                TR_FORMALISM_ID, TR_FORMALISM_VERSION,
                "TUMOUR_MLQ_RESULT_UNAVAILABLE", history_build.gate_results,
            )
            layer31d = {
                "status": "BLOCKED", "calculation_status": "blocked", "applicability_status": "BLOCKED",
                "interpretation_status": "not_interpretable", "reason": "VALID_LAYER_3_1B_SURVIVAL_FAILED",
                "blocking_reasons": ["VALID_LAYER_3_1B_SURVIVAL_FAILED"], "gate_results": [], "warnings": [],
            }
            scenario_matrix = {
                "status": "BLOCKED", "applicability_status": "BLOCKED",
                "reason": reason, "records": [],
            }
        research_associations = research_association_record(
            case.layer2_1.result, layer31b, layer31c, case.layer2_2.result,
        )
        assignment_error: str | None = None
        try:
            assignments = self._assignments(case, layer1)
        except ValueError as exc:
            assignments = []
            assignment_error = str(exc)
        if not assignments:
            biological_six_metrics = build_biological_six_metrics(
                basis, layer1, masks, case.effective_structure_roles, assignments,
                case.configuration.treatment_components, case.configuration.prescriptions,
                case.layer2_1.result,
            )
            lq_reason = assignment_error or "no_roi_parameter_assignments"
            layer31a = self._lq_formalism(
                case, basis, assignments, [], fraction_history, treatment_context,
                "blocked" if assignment_error else "not_run", lq_reason,
            )
            if fraction_history is not None:
                spatial_a = build_spatial_lq_result(
                    case.root, identifier, basis, fraction_history, [], {}, [],
                    self._viewer_geometry(layer1),
                    case.configuration.layer31_lq_high_dose_warning_gy_per_fraction,
                    dict(case.configuration.layer31_visualisation_settings.get("lq_high_dose_warning_criterion") or {}),
                    materialise_fields=case.configuration.layer31_materialise_full_maps_on_run,
                )
                layer31a = self._merge_lq_spatial_result(layer31a, spatial_a)
            independent_completed = layer31b.get("applicability_status") == "APPLICABLE"
            payload = {
                "schema_version": LQ_RESULT_SCHEMA_VERSION, "algorithm_version": LAYER31_COURSE_ALGORITHM_VERSION,
                "software_version": __version__,
                "scientific_position": "Radiobiological Response Modelling",
                "run_id": identifier,
                "calculation_status": "completed_with_warnings" if independent_completed else ("blocked" if assignment_error else "not_run"),
                "interpretation_status": "provisional" if independent_completed else "not_interpretable",
                "reason": lq_reason, "warnings": sorted(set(basis.warnings) | set(layer31b.get("warnings", [])) | set(layer31c.get("warnings", []))),
                "basis": basis.metadata(), "components": [item.to_dict() for item in basis.components],
                "fraction_history": fraction_history.metadata() if fraction_history is not None else {
                    "status": history_build.status,
                    "reason": history_build.reason,
                    "gate_results": [item.to_dict() for item in history_build.gate_results],
                },
                "roi_history": [item.to_dict() for item in basis.roi_history], "roi_results": [],
                "visualisation": {
                    "smoothing": dict(case.configuration.layer31_visualisation_settings),
                    "scope": "display_only_no_scientific_recalculation",
                },
                "biological_six_metrics": biological_six_metrics,
                "treatment_context": treatment_context.to_dict(),
                "layer3_1a_conventional_lq": layer31a,
                "layer3_1b_high_dose_sfrt_response": layer31b,
                "layer3_1c_modelled_therapeutic_ratio": layer31c,
                "layer3_1d_tumour_control_probability": layer31d,
                "layer3_1c_sensitivity_scenario_matrix": scenario_matrix,
                "research_associations": research_associations,
            }
        else:
            voxel_cc = float(np.prod(basis.dose_grid_spacing_mm) / 1000.0)
            results = []
            role_values: dict[str, tuple[np.ndarray, np.ndarray, ROIParameterAssignment]] = {}
            role_counts: dict[str, int] = {}
            warnings = set(basis.warnings)
            overlap_audit = self._overlap_audit(layer1, masks, assignments)
            if overlap_audit:
                warnings.add("overlapping_roi_parameters_independent_analysis")
            for assignment in assignments:
                mask = self._mask_for_assignment(layer1, masks, assignment)
                bed_values_for_roi, eqd2_values_for_roi, metrics = roi_summary_values(
                    basis.p_map, basis.q_map, mask, assignment.alpha_beta_gy,
                )
                bed_histogram = cumulative_volume_histogram(bed_values_for_roi)
                eqd2_histogram = cumulative_volume_histogram(eqd2_values_for_roi)
                if assignment.canonical_role:
                    role_counts[assignment.canonical_role] = role_counts.get(assignment.canonical_role, 0) + 1
                    role_values[assignment.canonical_role] = (
                        bed_values_for_roi, eqd2_values_for_roi, assignment,
                    )
                result_warnings = tuple(sorted(set(basis.warnings) | set(assignment.warnings)))
                warnings.update(result_warnings)
                result = Layer31ROIResult(
                    calculation_status="completed_with_warnings" if result_warnings else "completed",
                    interpretation_status="provisional" if assignment.warnings or basis.warnings else "protocol_interpretable",
                    assignment=assignment, metrics=metrics,
                    bed_volume_histogram={**bed_histogram, "histogram_type": "BED-volume histogram", "units": "Gy BED"},
                    eqd2_volume_histogram={**eqd2_histogram, "histogram_type": "EQD2-volume histogram", "units": "Gy EQD2"},
                    voxel_count=int(mask.sum()), dose_sampled_volume_cc=float(mask.sum() * voxel_cc),
                    warnings=result_warnings,
                    provenance={
                        "basis_hash": basis.basis_hash,
                        "alpha_beta_gy": assignment.alpha_beta_gy,
                        "roi_identity": assignment.roi_identity,
                        "parameter_source": assignment.parameter_source,
                        "parameter_set_version": assignment.parameter_set_version,
                        "roi_history_instances": [
                            item.to_dict() for item in basis.roi_history
                            if identity_key(item.roi_identity) == identity_key(assignment.roi_identity)
                        ],
                    },
                )
                results.append(result.to_dict())
            biological_six_metrics = build_biological_six_metrics(
                basis, layer1, masks, case.effective_structure_roles, assignments,
                case.configuration.treatment_components, case.configuration.prescriptions,
                case.layer2_1.result,
                {role: values for role, values in role_values.items() if role_counts.get(role) == 1},
            )
            for metric in biological_six_metrics["records"]:
                warnings.update(metric.get("warnings", []))
            warnings.update(layer31b.get("warnings", []))
            warnings.update(layer31c.get("warnings", []))
            layer31a = self._lq_formalism(
                case, basis, assignments, results, fraction_history, treatment_context,
                "completed_with_warnings" if warnings else "completed",
            )
            if fraction_history is not None:
                assignment_masks = {
                    (
                        str(item.roi_identity["rtstruct_sop_instance_uid"]),
                        int(item.roi_identity["roi_number"]),
                    ): self._mask_for_assignment(layer1, masks, item)
                    for item in assignments
                }
                spatial_a = build_spatial_lq_result(
                    case.root, identifier, basis, fraction_history, assignments,
                    assignment_masks, results, self._viewer_geometry(layer1),
                    case.configuration.layer31_lq_high_dose_warning_gy_per_fraction,
                    dict(case.configuration.layer31_visualisation_settings.get("lq_high_dose_warning_criterion") or {}),
                    materialise_fields=case.configuration.layer31_materialise_full_maps_on_run,
                )
                layer31a = self._merge_lq_spatial_result(layer31a, spatial_a)
                warnings.update(spatial_a.get("warnings", []))
            payload = {
                "schema_version": LQ_RESULT_SCHEMA_VERSION, "algorithm_version": LAYER31_COURSE_ALGORITHM_VERSION,
                "software_version": __version__, "scientific_position": "Radiobiological Response Modelling",
                "scope_exclusions": LAYER31B_SCOPE_EXCLUSIONS,
                "run_id": identifier, "parent_layer1_run_id": case.layer1.run_id,
                "calculation_status": "completed_with_warnings" if warnings else "completed",
                "interpretation_status": "provisional" if warnings else "protocol_interpretable",
                "warnings": sorted(warnings), "basis": basis.metadata(),
                "fraction_history": fraction_history.metadata() if fraction_history is not None else {
                    "status": history_build.status,
                    "reason": history_build.reason,
                    "gate_results": [item.to_dict() for item in history_build.gate_results],
                },
                "components": [item.to_dict() for item in basis.components], "roi_results": results,
                "visualisation": {
                    "smoothing": dict(case.configuration.layer31_visualisation_settings),
                    "scope": "display_only_no_scientific_recalculation",
                },
                "biological_six_metrics": biological_six_metrics,
                "roi_history": [item.to_dict() for item in basis.roi_history],
                "roi_overlap_audit": overlap_audit,
                "treatment_context": treatment_context.to_dict(),
                "layer3_1a_conventional_lq": layer31a,
                "layer3_1b_high_dose_sfrt_response": layer31b,
                "layer3_1c_modelled_therapeutic_ratio": layer31c,
                "layer3_1d_tumour_control_probability": layer31d,
                "layer3_1c_sensitivity_scenario_matrix": scenario_matrix,
                "research_associations": research_associations,
                "provenance": base_provenance(case.configuration_hash or "", case.layer1.run_id),
            }
        fraction_record = payload.get("fraction_history") or {}
        payload.setdefault("status", {
            "completed": "PASS", "completed_with_warnings": "WARN", "blocked": "BLOCKED",
            "not_run": "NOT_ASSESSED",
        }.get(str(payload.get("calculation_status")), "BLOCKED"))
        payload.setdefault("gate_results", list(fraction_record.get("gate_results", [])))
        payload.setdefault("blocking_reasons", [str(payload["reason"])] if payload.get("reason") and payload.get("calculation_status") == "blocked" else [])
        payload.setdefault("scope_exclusions", LAYER31B_SCOPE_EXCLUSIONS)
        payload["layer3_1b_paired_course_comparison"] = load_and_compare(
            payload, case.configuration.layer31_paired_course_reference_result_path,
        )
        payload.setdefault("provenance", base_provenance(case.configuration_hash or "", case.layer1.run_id))
        for branch in (layer31a, layer31b, layer31c, layer31d):
            branch.setdefault("status", {
                "completed": "PASS", "completed_with_warnings": "WARN", "blocked": "BLOCKED",
                "not_run": "NOT_ASSESSED",
            }.get(str(branch.get("calculation_status")), "NOT_ASSESSED"))
            branch.setdefault("gate_results", list(fraction_record.get("gate_results", [])))
            branch.setdefault("blocking_reasons", [str(branch["reason"])] if branch.get("reason") and str(branch.get("status", "")).upper() == "BLOCKED" else [])
        output = case.root / "derived" / "layer3_1" / f"{identifier}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return LayerRun(
            "layer3_1", payload["calculation_status"], payload["interpretation_status"], identifier,
            case.layer1.run_id, str(output), payload, payload.get("warnings", []),
            assignment_error if payload["calculation_status"] == "blocked" else None,
        )

    def materialise_visualisation_fields(self, case: ASCENDCase) -> dict[str, Any]:
        """Publish optional full-volume fields for a completed stored run.

        Summary calculations deliberately avoid these large artifacts.  This
        method is called by the viewer and retains the stored scalar endpoints;
        it only adds hash-verified presentation arrays to the same run record.
        """
        self._require_current_layer1(case)
        if not case.layer3_1.result or not case.layer3_1.run_id:
            raise ValueError("A completed Layer 3.1 run is required.")
        result = case.layer3_1.result
        branch_a = result.get("layer3_1a_conventional_lq") or {}
        branch_b = result.get("layer3_1b_high_dose_sfrt_response") or {}
        branch_d = result.get("layer3_1d_tumour_control_probability") or {}
        spatial_artifact = branch_a.get("artifacts") or {}
        survival_artifact = branch_b.get("artifacts") or {}
        tcp_artifact = branch_d.get("artifacts") or {}
        spatial_path = Path(str(spatial_artifact.get("spatial_fields_path") or ""))
        survival_path = Path(str(survival_artifact.get("survival_fields_path") or ""))
        tcp_path = Path(str(tcp_artifact.get("tcp_fields_path") or ""))
        spatial_archive_current = (
            spatial_path.is_file()
            and file_hash(spatial_path) == spatial_artifact.get("spatial_fields_sha256")
        )
        physical_reference = spatial_artifact.get("physical_course_dose_reference") or {}
        physical_reference_path = Path(str(physical_reference.get("basis_cache_path") or "")) / "pq_basis.npz"
        spatial_current = spatial_archive_current and physical_reference_path.is_file()
        if spatial_artifact.get("spatial_fields_path") and not spatial_archive_current:
            raise ValueError("Layer 3.1 spatial field archive is missing or its hash differs.")
        survival_required = branch_b.get("applicability_status") == "APPLICABLE"
        normal_mlq_required = (
            (result.get("layer3_1c_modelled_therapeutic_ratio") or {}).get("applicability_status") == "APPLICABLE"
        )
        normal_mlq_stored = False
        if survival_path.is_file():
            try:
                with np.load(survival_path, allow_pickle=False) as survival_archive:
                    normal_mlq_stored = "voxel_survival_MLQ_normal_tissue" in survival_archive.files
            except (OSError, ValueError):
                normal_mlq_stored = False
        survival_archive_current = (
            survival_path.is_file()
            and file_hash(survival_path) == survival_artifact.get("survival_fields_sha256")
        )
        survival_current = (
            not survival_required
            or (
                survival_archive_current
                and (not normal_mlq_required or normal_mlq_stored)
            )
        )
        if survival_artifact.get("survival_fields_path") and not survival_archive_current:
            raise ValueError("Layer 3.1 survival field archive is missing or its hash differs.")
        tcp_required = branch_d.get("applicability_status") == "APPLICABLE"
        tcp_current = (
            not tcp_required
            or (tcp_path.is_file() and file_hash(tcp_path) == tcp_artifact.get("tcp_fields_sha256"))
        )
        if tcp_artifact.get("tcp_fields_path") and not tcp_current:
            raise ValueError("Layer 3.1D TCP field archive is missing or its hash differs.")
        if spatial_current and survival_current and tcp_current:
            return result

        basis_result, configured_components, history_build = self.build_basis_with_history(case)
        if basis_result.basis is None:
            raise ValueError(basis_result.reason or "P/Q basis is unavailable.")
        layer1, _dose, masks = handoff.load_handoff(Path(case.layer1.result_path or "").parent)
        del _dose
        treatment_context = TreatmentContext.from_case(case.configuration, layer1.get("manifest", {}))
        if history_build.history is None:
            raise ValueError(history_build.reason or "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
        history = history_build.history
        basis = basis_result.basis
        assignments = self._assignments(case, layer1)
        if not assignments:
            raise ValueError("Layer 3.1 spatial fields require at least one rasterised ROI tissue assignment.")
        assignment_masks = {
            (str(item.roi_identity["rtstruct_sop_instance_uid"]), int(item.roi_identity["roi_number"])):
            self._mask_for_assignment(layer1, masks, item)
            for item in assignments
        }
        if not spatial_current:
            spatial = build_spatial_lq_result(
                case.root, case.layer3_1.run_id, basis, history, assignments,
                assignment_masks, list(result.get("roi_results", [])), self._viewer_geometry(layer1),
                case.configuration.layer31_lq_high_dose_warning_gy_per_fraction,
                dict(case.configuration.layer31_visualisation_settings.get("lq_high_dose_warning_criterion") or {}),
                materialise_fields=True,
            )
            if spatial.get("calculation_status") == "blocked":
                raise ValueError(spatial.get("blocking_reasons", ["Spatial field materialisation failed."])[0])
            branch_a = self._merge_lq_spatial_result(branch_a, spatial)
            result["layer3_1a_conventional_lq"] = branch_a

        tumour_state = None
        if survival_required and (not survival_current or not tcp_current):
            regenerated_b, tumour_state = run_fraction_resolved_tumour_response(
                case, basis, layer1, masks, history, case.layer3_1.run_id, materialise_fields=True,
            )
            for key in ("mean_tumour_survival_fraction", "tumour_eud_gy", "fraction_history_hash"):
                old, new = branch_b.get(key), regenerated_b.get(key)
                if old is not None and new is not None and old != new:
                    raise ValueError(f"Layer 3.1 viewer materialisation changed stored scientific endpoint {key}.")
            branch_b["artifacts"] = regenerated_b.get("artifacts", {})
            result["layer3_1b_high_dose_sfrt_response"] = branch_b
        if tcp_required and not tcp_current:
            if tumour_state is None:
                raise ValueError("Layer 3.1B authoritative survival state is unavailable for TCP field materialisation.")
            regenerated_d = run_layer31d_tcp(
                case, basis, layer1, masks, tumour_state, case.layer3_1.run_id, materialise_fields=True,
            )
            old_radiation = ((branch_d.get("endpoints") or {}).get("radiation_only") or {}).get("expected_surviving_clonogens")
            new_radiation = ((regenerated_d.get("endpoints") or {}).get("radiation_only") or {}).get("expected_surviving_clonogens")
            if old_radiation is not None and new_radiation is not None and old_radiation != new_radiation:
                raise ValueError("Layer 3.1D viewer materialisation changed the stored residual-clonogen endpoint.")
            branch_d["artifacts"] = regenerated_d.get("artifacts", {})
            result["layer3_1d_tumour_control_probability"] = branch_d

        if case.layer3_1.result_path:
            Path(case.layer3_1.result_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
        case.layer3_1.result = result
        return result

    def parameter_sweep(self, case: ASCENDCase, roi_identity: dict[str, Any], alpha_beta_values: Any) -> dict[str, Any]:
        """Handle parameter sweep for the enclosing ASCEND workflow."""
        self._require_current_layer1(case)
        basis_result, configured_components, history_build = self.build_basis_with_history(case)
        if basis_result.basis is None:
            raise ValueError(basis_result.reason or "Layer 3.1 basis is unavailable.")
        layer1, _dose, masks = handoff.load_handoff(Path(case.layer1.result_path).parent)
        del _dose
        treatment_context = TreatmentContext.from_case(case.configuration, layer1.get("manifest", {}))
        if history_build.history is None:
            raise ValueError(history_build.reason or "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
        basis = basis_result.basis
        assignment_raw = next((item for item in case.configuration.layer31_roi_parameters if identity_key(item["roi_identity"]) == identity_key(roi_identity)), None)
        if not assignment_raw:
            raise ValueError("The selected ROI has no Layer 3.1 parameter assignment.")
        assignment = self._assignments(case, layer1)[case.configuration.layer31_roi_parameters.index(assignment_raw)]
        mask = self._mask_for_assignment(layer1, masks, assignment)
        records = []
        for alpha_beta in parse_sweep(alpha_beta_values):
            metrics = roi_summary_metrics(basis.p_map, basis.q_map, mask, alpha_beta)
            records.append({"alpha_beta_gy": alpha_beta, **metrics})
        return Layer31SweepResult(
            calculation_status="completed_with_warnings" if basis_result.basis.warnings else "completed",
            interpretation_status="provisional" if assignment.warnings or basis_result.basis.warnings else "protocol_interpretable",
            basis_hash=basis.basis_hash,
            basis_cache_hit=basis.cache_hit,
            roi_identity=roi_identity,
            parameter_source=assignment.parameter_source,
            records=tuple(records),
            warnings=tuple(sorted(set(basis.warnings) | set(assignment.warnings))),
        ).to_dict()

    def export(self, case: ASCENDCase, destination: str | Path, full_maps: list[tuple[dict[str, Any], float]] | None = None) -> list[Path]:
        """Export export from stored results without recalculation."""
        if not case.layer3_1.result:
            raise ValueError("No stored Layer 3.1 result is available.")
        output = Path(destination); output.mkdir(parents=True, exist_ok=True)
        json_path = output / "layer3_1_lq_results.json"
        json_path.write_text(json.dumps(case.layer3_1.result, indent=2), encoding="utf-8")
        response_json_path = output / "layer3_1_radiobiological_response_results.json"
        response_json_path.write_text(json.dumps(case.layer3_1.result, indent=2), encoding="utf-8")
        csv_path = output / "layer3_1_roi_metrics.csv"
        rows = []
        for result in case.layer3_1.result.get("roi_results", []):
            assignment = result["assignment"]
            rows.append({
                "roi_name": assignment["roi_name"], "roi_number": assignment["roi_identity"]["roi_number"],
                "rtstruct_sop_instance_uid": assignment["roi_identity"]["rtstruct_sop_instance_uid"],
                "alpha_beta_gy": assignment["alpha_beta_gy"], "parameter_source": assignment["parameter_source"],
                **result["metrics"],
            })
        if rows:
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        created = [response_json_path, json_path] + ([csv_path] if csv_path.exists() else [])
        for key, filename in (
            ("layer3_1b_high_dose_sfrt_response", "layer3_1b_high_dose_sfrt_response.csv"),
            ("layer3_1c_modelled_therapeutic_ratio", "layer3_1c_modelled_therapeutic_ratio.csv"),
            ("layer3_1d_tumour_control_probability", "layer3_1d_tumour_control_probability.csv"),
        ):
            record = case.layer3_1.result.get(key) or {}
            if not record:
                continue
            row = {
                name: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
                for name, value in record.items()
            }
            path = output / filename
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            created.append(path)
        biological_path = output / "layer3_1_biological_six_metrics.csv"
        biological_rows = []
        for record in case.layer3_1.result.get("biological_six_metrics", {}).get("records", []):
            physical = record.get("physical_metric_reference") or {}
            biological_rows.append({
                "metric_id": record.get("metric_id"),
                "mapping_type": record.get("mapping_type"),
                "physical_value": physical.get("value"),
                "physical_units": physical.get("units"),
                "geometry_value": record.get("geometry", {}).get("value"),
                "geometry_units": record.get("geometry", {}).get("units"),
                "bed_value": record.get("bed", {}).get("value"),
                "bed_units": record.get("bed", {}).get("units"),
                "eqd2_value": record.get("eqd2", {}).get("value"),
                "eqd2_units": record.get("eqd2", {}).get("units"),
                "applicability": record.get("applicability"),
                "warnings": "|".join(record.get("warnings", [])),
                "interpretive_flags": "|".join(record.get("interpretive_flags", [])),
                "definition": record.get("definition"),
            })
        if biological_rows:
            with biological_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(biological_rows[0])); writer.writeheader(); writer.writerows(biological_rows)
            created.append(biological_path)
        gtv_context_path = output / "layer3_1_whole_gtv_biological_context.csv"
        gtv_context = case.layer3_1.result.get("biological_six_metrics", {}).get("whole_gtv_biological_context", {})
        gtv_rows = [{
            "endpoint_id": endpoint_id,
            "value": endpoint.get("value"),
            "units": endpoint.get("units"),
            "applicability": gtv_context.get("applicability"),
            "alpha_beta_gy": gtv_context.get("alpha_beta_gy"),
            "warnings": "|".join(gtv_context.get("warnings", [])),
        } for endpoint_id, endpoint in gtv_context.get("endpoints", {}).items()]
        if gtv_rows:
            with gtv_context_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(gtv_rows[0])); writer.writeheader(); writer.writerows(gtv_rows)
            created.append(gtv_context_path)
        if full_maps:
            basis_result, configured_components, history_build = self.build_basis_with_history(case)
            if basis_result.basis is None:
                raise ValueError(basis_result.reason or "P/Q basis is unavailable.")
            layer1, _dose, _masks = handoff.load_handoff(Path(case.layer1.result_path or "").parent)
            treatment_context = TreatmentContext.from_case(case.configuration, layer1.get("manifest", {}))
            if history_build.history is None:
                raise ValueError(history_build.reason or "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
            basis = basis_result.basis
            for identity, alpha_beta in full_maps:
                validate_alpha_beta(alpha_beta)
                suffix = f"roi{identity['roi_number']}_ab{alpha_beta:g}".replace(".", "p")
                path = output / f"layer3_1_{suffix}_maps.npz"
                _deterministic_npz(path, {
                    "BED_gy": full_bed_map(basis.p_map, basis.q_map, alpha_beta),
                    "EQD2_gy": full_eqd2_map(basis.p_map, basis.q_map, alpha_beta),
                })
                created.append(path)
        return created
