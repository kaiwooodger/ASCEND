"""Layer 2.1 orchestration and optional presentation-support evidence.

The six harmonised metrics are calculated only by the locked validated module.
This service prepares its inputs, applies treatment-context applicability, and
derives explicitly optional QA evidence without moving science into the GUI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.config import Prescription
from ascend.models.status import InterpretationStatus
from ascend.oar.geometry import OARClassification, OARGeometryService
from ascend.scientific.legacy import layer21_validated as validated
from ascend.treatment.applicability import apply_context_decisions, resolve_all_metric_applicability
from ascend.treatment.models import TreatmentContext
from ascend.validation.provenance import base_provenance, run_id


SUPPORTING_SCHEMA_VERSION = "ASCEND-Layer2.1-supporting-v3"


def _normalise_name(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _vertex_context(
    masks: dict[str, np.ndarray],
    effective_roles: dict[str, str | list[str]],
    voxel_volume_cc: float,
) -> tuple[np.ndarray | None, dict[str, np.ndarray], dict[str, Any]]:
    """Resolve explicit vertices or deterministic aggregate-mask components."""
    aggregate_name = effective_roles.get("VTV_H")
    aggregate = masks.get(aggregate_name) if isinstance(aggregate_name, str) else None
    explicit_names = effective_roles.get("VTV_H_individual")
    explicit = (
        {name: masks[name] for name in explicit_names if name in masks and masks[name].any()}
        if isinstance(explicit_names, list) else {}
    )
    if explicit:
        vertices = explicit
        high_mask = np.logical_or.reduce(list(vertices.values()))
        if aggregate is not None and aggregate.shape == high_mask.shape:
            xor_voxels = int(np.logical_xor(aggregate, high_mask).sum())
            consistency = {
                "status": "PASS" if xor_voxels == 0 else "WARN",
                "warning": None if xor_voxels == 0 else "aggregate_individual_vertex_mismatch",
                "symmetric_difference_volume_cc": float(xor_voxels * voxel_volume_cc),
            }
        else:
            consistency = {
                "status": "NOT_ASSESSED",
                "warning": "aggregate_vtvh_unavailable",
                "symmetric_difference_volume_cc": None,
            }
        source = "explicit_rtstruct_vertices"
    elif aggregate is not None and aggregate.any():
        # Connected components are a deterministic fallback for per-vertex QA.
        # They are identified as derived and never represented as source ROIs.
        components = validated.components(aggregate)
        vertices = {f"VTVH_CC_{index:02d}": mask for index, mask in enumerate(components, 1)}
        high_mask = aggregate
        consistency = {
            "status": "NOT_APPLICABLE",
            "warning": None,
            "symmetric_difference_volume_cc": 0.0,
        }
        source = "connected_components_derived_from_aggregate_vtv_h"
    else:
        vertices = {}
        high_mask = None
        consistency = {
            "status": "NOT_ASSESSED",
            "warning": "aggregate_vtvh_unavailable",
            "symmetric_difference_volume_cc": None,
        }
        source = "unavailable"
    return high_mask, vertices, {
        "source": source,
        "aggregate_individual_mask_consistency": consistency,
        "individual_vertex_mask_hashes": {
            vertex_id: validated.mask_hash(mask) for vertex_id, mask in vertices.items()
        },
    }


def _supporting_vertex_qa(
    dose_gy: np.ndarray,
    vertex_masks: dict[str, np.ndarray],
    voxel_volume_cc: float,
    rx_h_gy: float | None,
) -> list[dict[str, Any]]:
    """Calculate optional dose and volume QA for each resolved vertex mask."""
    records: list[dict[str, Any]] = []
    threshold = 0.95 * rx_h_gy if rx_h_gy is not None and rx_h_gy > 0 else None
    for vertex_id, mask in vertex_masks.items():
        if mask.shape != dose_gy.shape or not mask.any():
            records.append({
                "vertex_id": vertex_id,
                "applicability": "invalid",
                "warnings": ["invalid_vertex_mask"],
                "v95_rxh_pct": None,
                "v95_rxh_applicability": "invalid",
                "dmean_gy": None,
                "d95_gy": None,
                "dmax_gy": None,
                "volume_cc": None,
            })
            continue
        values = np.asarray(dose_gy[mask], dtype=float)
        records.append({
            "vertex_id": vertex_id,
            "applicability": "valid",
            "warnings": [] if threshold is not None else ["missing_prescription_for_v95_only"],
            "v95_rxh_pct": round(100.0 * float(np.count_nonzero(values >= threshold)) / len(values), 6) if threshold is not None else None,
            "v95_rxh_applicability": "valid" if threshold is not None else "not_assessed",
            "threshold_95pct_rxh_gy": threshold,
            "dmean_gy": round(float(values.mean()), 6),
            "d95_gy": round(float(np.percentile(values, 5)), 6),
            "dmax_gy": round(float(values.max()), 6),
            "volume_cc": round(float(mask.sum() * voxel_volume_cc), 6),
        })
    return records


def _resolve_oar_geometry(
    layer1: dict[str, Any],
    masks: dict[str, np.ndarray],
    configured_oars: list[dict[str, str]],
    high_mask: np.ndarray | None,
    vertex_masks: dict[str, np.ndarray],
    spacing_zyx_mm: tuple[float, float, float],
    voxel_volume_cc: float,
) -> dict[str, Any]:
    """Build descriptive OAR-to-vertex geometry without compliance inference."""
    scope = "Descriptive geometry only; no OAR dose compliance or clinical pass/fail interpretation."
    if not configured_oars:
        return {"status": "not_configured", "scope": scope, "records": []}
    if high_mask is None or not high_mask.any():
        return {"status": "not_assessed", "scope": scope, "reason": "Validated VTV_H is unavailable.", "records": []}
    mappings = layer1.get("structure_mapping", [])
    exact = {str(item.get("original_name")): str(item.get("standard_name")) for item in mappings}
    normalized: dict[str, list[str]] = {}
    for original, standard in exact.items():
        normalized.setdefault(_normalise_name(original), []).append(standard)
    volume_definitions = layer1.get("manifest", {}).get("rasterisation", {}).get("volume_definitions", {})
    service = OARGeometryService()
    records: list[dict[str, Any]] = []
    for item in configured_oars:
        original_name = str(item.get("name") or item.get("display_name") or item.get("roi_identity", {}).get("roi_number"))
        standard_name = None
        if item.get("roi_identity"):
            number = int(item["roi_identity"]["roi_number"])
            inventory = layer1.get("manifest", {}).get("roi_inventory", [])
            matched = [entry for entry in inventory if int(entry.get("roi_number", -1)) == number]
            if len(matched) == 1 and matched[0].get("rasterisation_status") == "rasterised":
                standard_name = matched[0].get("canonical_mapping")
        if not standard_name:
            standard_name = exact.get(original_name)
        if not standard_name:
            candidates = sorted(set(normalized.get(_normalise_name(original_name), [])))
            standard_name = candidates[0] if len(candidates) == 1 else None
        if not standard_name or standard_name not in masks or not masks[standard_name].any():
            records.append({
                "oar_name": original_name,
                "classification": item["classification"],
                "status": "not_assessed",
                "reason": "No unique non-empty Layer 1-validated mask matches the configured OAR name.",
                "compliance_interpretation": "not_performed",
            })
            continue
        volumes = volume_definitions.get(standard_name, {})
        anatomical_volume = volumes.get("anatomical_volume_contour_cc")
        record = service.analyse(
            original_name,
            masks[standard_name],
            OARClassification(item["classification"]),
            vertex_masks,
            spacing_zyx_mm,
            high_mask,
            voxel_volume_cc,
            float(anatomical_volume) if anatomical_volume is not None else None,
            "rtstruct_contour_stack" if anatomical_volume is not None else "dose_sampled_native_mask",
        )
        record.update({"status": "available", "validated_structure": standard_name})
        records.append(record)
    status = "available" if all(item.get("status") == "available" for item in records) else "completed_with_unassessed_oars"
    return {"status": status, "scope": scope, "records": records}


def build_supporting_outputs(
    payload: dict[str, Any],
    effective_roles: dict[str, str | list[str]],
    configured_endpoints: list[dict[str, Any]],
    vertex_context: dict[str, Any] | None = None,
    integrity_context: dict[str, Any] | None = None,
    oar_vertex_geometry: dict[str, Any] | None = None,
    supporting_vertex_qa: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Expose locked result descriptors without recalculating scientific values."""
    metrics = payload.get("harmonised_metrics", [])
    by_id = {item.get("metric_id"): item for item in metrics}
    coverage = by_id.get("high_dose_coverage_v95_rxh", {})
    volume_fraction = by_id.get("high_dose_volume_fraction", {})
    peak = by_id.get("mean_peak_dose", {})
    valley = by_id.get("mean_valley_dose", {})
    ratio = by_id.get("structure_based_dose_ratio", {})
    locked_vertex_records = payload.get("per_vertex_quality_control", [])
    vertex_records = supporting_vertex_qa if supporting_vertex_qa is not None else locked_vertex_records
    explicit = effective_roles.get("VTV_H_individual")
    if isinstance(explicit, list) and explicit:
        vertex_source = "explicit_rtstruct_vertices"
        configured_vertex_count = len(explicit)
    elif isinstance(effective_roles.get("VTV_H"), str):
        vertex_source = "connected_components_derived_from_aggregate_vtv_h"
        configured_vertex_count = int(volume_fraction.get("number_of_vertices") or 0)
    else:
        vertex_source = "unavailable"
        configured_vertex_count = 0
    if vertex_records and all(item.get("v95_rxh_applicability", "valid") == "valid" for item in vertex_records):
        vertex_status = "available"
        vertex_reason = None
    elif vertex_records:
        vertex_status = "available_with_unassessed_v95"
        vertex_reason = "Rx_H is unresolved; Dmean, D95, Dmax, and volume are available, while V95 relative to Rx_H is not assessed."
    else:
        coverage_warnings = list(coverage.get("warnings", []))
        vertex_status = "not_calculated"
        vertex_reason = (
            ", ".join(map(str, coverage_warnings))
            if coverage_warnings else
            "No valid vertex masks were available under the locked Layer 2.1 calculation context."
        )
    protocol_records = payload.get("protocol_native_metrics", [])
    if not configured_endpoints:
        protocol_status = "not_configured"
        protocol_reason = "No optional protocol-native endpoints are configured for this case."
    elif not protocol_records:
        protocol_status = "not_calculated"
        protocol_reason = "Configured protocol-native endpoints produced no stored records."
    elif any(item.get("applicability") != "valid" for item in protocol_records):
        protocol_status = "calculated_with_invalid_endpoints"
        protocol_reason = "One or more configured protocol-native endpoints lack required inputs."
    else:
        protocol_status = "available"
        protocol_reason = None
    primary_fields = {
        "metric_id", "value", "units", "applicability", "warnings",
        "dose_context", "prescription_context",
    }
    descriptors = []
    for metric in metrics:
        detail = {key: value for key, value in metric.items() if key not in primary_fields}
        descriptors.append({
            "metric_id": metric.get("metric_id"),
            "applicability": metric.get("applicability"),
            "descriptors": detail,
        })
    dose_context = next((item.get("dose_context") for item in metrics if item.get("dose_context")), {})
    prescription_context = next((item.get("prescription_context") for item in metrics if item.get("prescription_context")), {})
    provenance = payload.get("provenance", {})
    default_integrity = {
        "layer1_result_sha256": provenance.get("layer1_result_sha256"),
        "native_mask_archive_sha256": provenance.get("layer1_mask_export_sha256"),
        "individual_vertex_mask_hashes": (vertex_context or {}).get("individual_vertex_mask_hashes", {}),
        "rtdose_sha256": dose_context.get("dose_object_sha256"),
        "rtdose_sop_instance_uid": dose_context.get("dose_object_uid") or provenance.get("rtdose_sop_instance_uid"),
        "dose_grid_spacing_mm": provenance.get("dose_grid_spacing_mm"),
        "dose_grid_voxel_volume_cc": provenance.get("dose_grid_voxel_volume_cc"),
        "treatment_component": dose_context.get("treatment_component"),
        "dose_state": dose_context.get("dose_state"),
        "prescription_context": prescription_context,
        "layer1_inherited_warnings": payload.get("inherited_layer1_findings", []),
        "calculation_status": payload.get("calculation_status"),
        "interpretation_status": payload.get("interpretation_status"),
    }
    if integrity_context:
        default_integrity.update(integrity_context)
    vertex_consistency = (vertex_context or {}).get("aggregate_individual_mask_consistency", {
        "status": "NOT_ASSESSED", "warning": None, "symmetric_difference_volume_cc": None,
    })
    return {
        "schema_version": SUPPORTING_SCHEMA_VERSION,
        "derivation": "Supporting and integrity adapter over the validated Layer 1 handoff and stored locked Layer 2.1 records; locked six-metric formulas are unchanged.",
        "metric_descriptors": descriptors,
        "per_vertex_qa": vertex_records,
        "vertex_analysis": {
            "status": vertex_status,
            "source": (vertex_context or {}).get("source", vertex_source),
            "configured_or_derived_vertex_count": configured_vertex_count,
            "stored_record_count": len(vertex_records),
            "locked_record_count": len(locked_vertex_records),
            "reason": vertex_reason,
            "aggregate_individual_mask_consistency": vertex_consistency,
            "individual_vertex_mask_hashes": (vertex_context or {}).get("individual_vertex_mask_hashes", {}),
        },
        "high_dose_coverage_context": {
            "applicability": coverage.get("applicability"),
            "covered_vtvh_volume_cc": coverage.get("covered_volume_cc"),
            "threshold_95pct_rxh_gy": coverage.get("threshold_gy") or (
                0.95 * float(prescription_context["rx_h_gy"])
                if prescription_context.get("rx_h_gy") is not None else None
            ),
            "number_of_vertices": coverage.get("number_of_vertices", volume_fraction.get("number_of_vertices")),
            "source_structure_names": coverage.get("original_structure_names", peak.get("original_structure_names")),
            "warnings": coverage.get("warnings", []),
        },
        "high_dose_volume_fraction_context": {
            "applicability": volume_fraction.get("applicability"),
            "vtvh_volume_cc": volume_fraction.get("high_dose_volume_cc"),
            "gtv_volume_cc": volume_fraction.get("gtv_volume_cc"),
            "common_volume_basis": volume_fraction.get("volume_basis"),
            "dose_sampled_high_dose_volume_fraction_pct": volume_fraction.get("high_dose_volume_fraction_dose_sampled_pct"),
            "vtvh_volume_outside_gtv_cc": volume_fraction.get("high_dose_volume_outside_gtv_cc"),
            "warnings": volume_fraction.get("warnings", []),
        },
        "peak_valley_dose_context": {
            "peak": {
                "dose_sampled_volume_cc": peak.get("dose_sampled_volume_cc"),
                "voxel_count": peak.get("voxel_count"),
                "d50_gy": peak.get("d50_gy"),
                "mean_dose_gy": peak.get("value"),
                "mean_dose_normalised_to_rxh": peak.get("normalised_value"),
                "warnings": peak.get("warnings", []),
            },
            "valley": {
                "dose_sampled_volume_cc": valley.get("dose_sampled_volume_cc"),
                "voxel_count": valley.get("voxel_count"),
                "d50_gy": valley.get("d50_gy"),
                "mean_dose_gy": valley.get("value"),
                "mean_dose_normalised_to_rxl": valley.get("normalised_to_rxl"),
                "valley_definition_source": valley.get("valley_definition_source"),
                "warnings": valley.get("warnings", []),
            },
            "peak_valley_overlap_warning": "peak_valley_overlap" in valley.get("warnings", []),
            "valley_outside_gtv_warning": "valley_outside_gtv" in valley.get("warnings", []),
        },
        "ratio_context": {
            "applicability": ratio.get("applicability"),
            "numerator_dmean_vtvh_gy": ratio.get("numerator_gy"),
            "denominator_dmean_vtvl_gy": ratio.get("denominator_gy"),
            "formula": ratio.get("formula"),
            "display_expression": ratio.get("display_expression"),
            "warnings": ratio.get("warnings", []),
        },
        "integrity_and_interpretability_qa": default_integrity,
        "protocol_native_endpoint_status": {
            "status": protocol_status,
            "configured_endpoint_count": len(configured_endpoints),
            "stored_record_count": len(protocol_records),
            "reason": protocol_reason,
        },
        "protocol_native_metrics": protocol_records,
        "oar_vertex_geometry": oar_vertex_geometry or {
            "status": "not_configured",
            "scope": "Descriptive geometry only; no OAR dose compliance or clinical pass/fail interpretation.",
            "records": [],
        },
    }


class Layer21Service:
    """Coordinate locked Layer 2.1 analysis and selected supporting outputs."""
    algorithm_version = validated.VERSION
    schema_version = validated.SCHEMA_VERSION

    def run(self, case: ASCENDCase) -> LayerRun:
        """Calculate Layer 2.1 from the current validated Layer 1 handoff."""
        if not case.layer1.result_path or not case.layer1.run_id:
            raise ValueError("Current Layer 1 result is required.")
        layer1_dir = Path(case.layer1.result_path).parent
        layer1, dose, masks = validated.load_handoff(layer1_dir)
        rx_l = case.configuration.prescriptions["Rx_L"]
        rx_h = case.configuration.prescriptions["Rx_H"]
        treatment_context = TreatmentContext.from_case(case.configuration, layer1.get("manifest", {}))
        selected_component = treatment_context.selected_component
        if (
            treatment_context.treatment_approach == "LRT_SEQUENTIAL_CERT"
            and case.configuration.dose_context == "lrt_component"
            and selected_component is not None
        ):
            rx_l = Prescription(
                selected_component.rx_low_gy,
                selected_component.fraction_count,
                selected_component.prescription_source or selected_component.source,
            )
            if selected_component.rx_high_gy is not None:
                rx_h = Prescription(
                    selected_component.rx_high_gy,
                    selected_component.fraction_count,
                    selected_component.prescription_source or selected_component.source,
                )
        roles = dict(case.effective_structure_roles)
        spacing_zyx_mm = tuple(map(float, layer1.get("manifest", {}).get("dose_grid", {}).get("voxel_spacing_mm", [1.0, 1.0, 1.0])))
        voxel_volume_cc = float(np.prod(spacing_zyx_mm) / 1000.0)
        high_mask, vertex_masks, vertex_context = _vertex_context(masks, roles, voxel_volume_cc)
        supporting_enabled = bool(case.configuration.supporting_outputs_enabled)
        supporting_categories = set(case.configuration.supporting_output_categories) if supporting_enabled else set()
        # Optional categories are checked before their calculations.  Disabled
        # work is omitted, not emitted as failed or null-valued metric records.
        supporting_vertex_qa = (
            _supporting_vertex_qa(
                dose, vertex_masks, voxel_volume_cc,
                float(rx_h.gy) if rx_h.gy is not None else None,
            )
            if "per_vertex" in supporting_categories else []
        )
        oar_vertex_geometry = (
            _resolve_oar_geometry(
                layer1, masks, case.configuration.oar_structures, high_mask, vertex_masks,
                spacing_zyx_mm, voxel_volume_cc,
            )
            if "oar_geometry" in supporting_categories else {
                "status": "not_selected", "scope": "Optional geometry calculation was not selected before this run.",
                "records": [],
            }
        )
        config = {
            "_layer1_dir": layer1_dir,
            "dose_state": case.configuration.dose_context,
            "case_type": "test_data",
            "prescription_status": "configured",
            "equal_prescriptions_protocol_confirmed": case.configuration.equal_prescriptions_protocol_confirmed,
            "partial_volume_only": case.configuration.partial_volume_only,
            "dose_context": {
                "treatment_component": layer1.get("manifest", {}).get("treatment_component"),
                "dose_object_uid": layer1.get("manifest", {}).get("rtdose_uid"),
                "prescription_context_id": case.configuration.protocol_id or "unidentified",
                "protocol_confirmed": all(case.configuration.protocol_context.values()),
                "ascend_treatment_delivery_mode": case.configuration.treatment_delivery_mode,
                "ascend_dose_context": case.configuration.dose_context,
            },
            "protocol_context": case.configuration.protocol_context,
            "roles": roles,
            "prescriptions": {"Rx_L": vars(rx_l), "Rx_H": vars(rx_h)},
            "valley_definition_source": case.configuration.valley_definition_source,
            "valley_overlap_tolerance_pct": case.configuration.valley_overlap_tolerance_pct,
            "protocol_native_endpoints": (
                case.configuration.protocol_native_endpoints
                if "protocol_native" in supporting_categories else []
            ),
        }
        context_decisions = resolve_all_metric_applicability(
            treatment_context,
            roles,
            validated.PRIMARY_IDS,
            {
                "protocol_id": case.configuration.protocol_id,
                "endpoints": case.configuration.protocol_native_endpoints,
                "confirmation": case.configuration.protocol_context,
            },
        )
        blocking = next(
            (item for item in context_decisions if item.reason in {
                "dose_uid_not_associated_with_selected_treatment_component",
                "plan_uid_not_associated_with_selected_treatment_component",
            }),
            None,
        )
        if blocking is not None:
            raise ValueError(f"BLOCK_TREATMENT_CONTEXT: {blocking.reason}")
        # Scientific metric definitions remain in the hash-locked implementation.
        payload = validated.analyse(layer1, dose, masks, config)
        apply_context_decisions(payload["harmonised_metrics"], context_decisions)
        payload["treatment_context"] = treatment_context.to_dict()
        payload["metric_applicability"] = [item.to_dict() for item in context_decisions]
        interpretation_states = {item.interpretation_status for item in context_decisions}
        if "not_interpretable" in interpretation_states:
            payload["interpretation_status"] = "not_interpretable"
        elif "provisional" in interpretation_states:
            payload["interpretation_status"] = "provisional"
        context_warnings = sorted({warning for item in context_decisions for warning in item.warnings})
        payload["warnings"] = sorted(set(payload.get("warnings", [])) | set(context_warnings))
        identifier = run_id("L2_1")
        payload["run_id"] = identifier
        payload["parent_layer1_run_id"] = case.layer1.run_id
        payload["provenance"].update(base_provenance(case.configuration_hash or "", case.layer1.run_id))
        payload["provenance"].update({
            "rtdose_sop_instance_uid": layer1.get("manifest", {}).get("rtdose_uid"),
            "rtstruct_sop_instance_uid": layer1.get("manifest", {}).get("rtstruct_uid"),
            "rtplan_sop_instance_uid": layer1.get("manifest", {}).get("rtplan_uid"),
            "prescriptions": {"Rx_L": vars(rx_l), "Rx_H": vars(rx_h)},
            "dose_context": case.configuration.dose_context,
            "treatment_delivery_mode": case.configuration.treatment_delivery_mode,
            "prescription_context": case.configuration.prescription_context,
            "treatment_components": [item.to_dict() for item in treatment_context.components],
            "selected_treatment_component_id": (
                treatment_context.selected_component.component_id if treatment_context.selected_component else None
            ),
            "supporting_outputs_schema_version": SUPPORTING_SCHEMA_VERSION,
        })
        manifest = layer1.get("manifest", {})
        payload["oar_vertex_geometry"] = oar_vertex_geometry if "oar_geometry" in supporting_categories else {
            "status": "not_selected", "scope": "Optional geometry calculation was not selected before this run.",
            "records": [],
        }
        complete_supporting_outputs = build_supporting_outputs(
            payload,
            case.effective_structure_roles,
            case.configuration.protocol_native_endpoints if "protocol_native" in supporting_categories else [],
            vertex_context=vertex_context,
            integrity_context={
                "layer1_run_id": case.layer1.run_id,
                "parent_layer1_run_id": case.layer1.run_id,
                "rtdose_sha256": manifest.get("input_file_hashes", {}).get("rtdose"),
                "rtdose_sop_instance_uid": manifest.get("rtdose_uid"),
                "dose_grid_spacing_mm": list(spacing_zyx_mm),
                "dose_grid_voxel_volume_cc": voxel_volume_cc,
                "treatment_component": manifest.get("treatment_component"),
                "dose_state": case.configuration.dose_context,
                "prescriptions": {"Rx_L": vars(rx_l), "Rx_H": vars(rx_h)},
                "protocol_confirmation": case.configuration.protocol_context,
                "protocol_confirmed": all(case.configuration.protocol_context.values()),
            },
            oar_vertex_geometry=oar_vertex_geometry,
            supporting_vertex_qa=supporting_vertex_qa,
        )
        from ascend.workflow.preferences import selected_supporting_outputs
        payload["supporting_outputs"] = selected_supporting_outputs(
            complete_supporting_outputs, supporting_enabled, list(supporting_categories),
        )
        payload["supporting_output_selection"] = {
            "enabled": supporting_enabled,
            "selected_categories": sorted(supporting_categories),
            "selection_applied_before_calculation": True,
            "skipped_optional_calculations": sorted(
                {"per_vertex", "protocol_native", "oar_geometry"} - supporting_categories
            ),
        }
        output = case.root / "derived" / "layer2_1" / f"{identifier}.json"
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return LayerRun(
            layer="layer2_1", calculation_status=payload["calculation_status"],
            interpretation_status=payload["interpretation_status"], run_id=identifier,
            parent_layer1_run_id=case.layer1.run_id, result_path=str(output), result=payload,
            warnings=payload.get("warnings", []),
        )
