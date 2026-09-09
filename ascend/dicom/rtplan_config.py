"""Auditable RTPLAN/RTDOSE configuration extraction with ambiguity-preserving prefilling."""

from __future__ import annotations

import math
import re
from typing import Any

import pydicom


RTPLAN_DELIVERY_METADATA_VERSION = "ASCEND-RTPLAN-delivery-v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalise(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def _dose_reference_label(item: Any) -> str:
    return next((
        _text(getattr(item, field, ""))
        for field in ("DoseReferenceDescription", "DoseReferenceStructureType", "DoseReferenceType")
        if _text(getattr(item, field, ""))
    ), "Dose reference")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _single_value(values: list[float | None]) -> float | None:
    present = {value for value in values if value is not None}
    return next(iter(present)) if len(present) == 1 else None


def _effective_control_points(beam: Any) -> list[dict[str, Any]]:
    """Expand inherited RT control-point values needed for delivery reporting."""
    state: dict[str, Any] = {
        "dose_rate_mu_per_min": None,
        "nominal_energy_mv": None,
        "gantry_angle_deg": None,
        "gantry_rotation_direction": None,
        "collimator_angle_deg": None,
        "couch_angle_deg": None,
        "dose_rate_source_control_point_index": None,
    }
    output: list[dict[str, Any]] = []
    for sequence_index, control_point in enumerate(getattr(beam, "ControlPointSequence", [])):
        control_point_index = _int(getattr(control_point, "ControlPointIndex", None))
        cumulative_meterset_weight = _float(getattr(control_point, "CumulativeMetersetWeight", None))
        explicit_dose_rate = _float(getattr(control_point, "DoseRateSet", None))
        updates = {
            "dose_rate_mu_per_min": explicit_dose_rate,
            "nominal_energy_mv": _float(getattr(control_point, "NominalBeamEnergy", None)),
            "gantry_angle_deg": _float(getattr(control_point, "GantryAngle", None)),
            "gantry_rotation_direction": _text(getattr(control_point, "GantryRotationDirection", "")).upper() or None,
            "collimator_angle_deg": _float(getattr(control_point, "BeamLimitingDeviceAngle", None)),
            "couch_angle_deg": _float(getattr(control_point, "PatientSupportAngle", None)),
        }
        state.update({key: value for key, value in updates.items() if value is not None})
        if explicit_dose_rate is not None:
            state["dose_rate_source_control_point_index"] = control_point_index
        output.append({
            **state,
            "sequence_index": sequence_index,
            "control_point_index": control_point_index,
            # CumulativeMetersetWeight is required independently at each
            # control point; unlike treatment parameters, it is not inherited.
            "cumulative_meterset_weight": cumulative_meterset_weight,
            "dose_rate_explicit_at_control_point": explicit_dose_rate is not None,
        })
    return output


def _mlc_positions_vary(beam: Any) -> bool:
    states: set[tuple[float, ...]] = set()
    current: tuple[float, ...] | None = None
    for control_point in getattr(beam, "ControlPointSequence", []):
        for positions in getattr(control_point, "BeamLimitingDevicePositionSequence", []):
            if not _text(getattr(positions, "RTBeamLimitingDeviceType", "")).upper().startswith("MLC"):
                continue
            raw = getattr(positions, "LeafJawPositions", None)
            if raw is not None:
                current = tuple(round(float(value), 6) for value in raw)
        if current is not None:
            states.add(current)
    return len(states) > 1


def _rotation_degrees(points: list[dict[str, Any]]) -> float | None:
    angles = [item["gantry_angle_deg"] for item in points if item["gantry_angle_deg"] is not None]
    if len(angles) < 2:
        return None
    total = 0.0
    for start, end in zip(angles, angles[1:]):
        delta = (end - start) % 360.0
        if delta > 180.0:
            delta = 360.0 - delta
        total += delta
    return round(total, 3)


def _beam_on_time_from_control_points(beam: Any, meterset_mu: float | None) -> dict[str, Any]:
    """Calculate beam-on time by integrating the RTPLAN irradiation segments.

    DICOM defines Dose Rate Set at a control point for the segment beginning at
    that control point.  Therefore an end-point dose rate is never substituted
    for a missing start-point rate.
    """
    points = _effective_control_points(beam)
    base = {
        "status": "not_calculated",
        "method": "RTPLAN_CONTROL_POINT_METERSET_INTERVAL_INTEGRATION",
        "beam_meterset_mu": meterset_mu,
        "final_cumulative_meterset_weight": None,
        "beam_on_time_seconds": None,
        "irradiation_segment_count": 0,
        "control_points": [],
        "segments": [],
        "reason": None,
    }
    if meterset_mu is None or not math.isfinite(meterset_mu) or meterset_mu < 0:
        return {**base, "reason": "BEAM_METERSET_MISSING_OR_INVALID"}
    primary_unit = _text(getattr(beam, "PrimaryDosimeterUnit", "")).upper()
    if primary_unit and primary_unit not in {"MU", "MONITOR UNIT", "MONITOR_UNITS"}:
        return {**base, "reason": f"UNSUPPORTED_PRIMARY_DOSIMETER_UNIT:{primary_unit}"}
    final_weight = _float(getattr(beam, "FinalCumulativeMetersetWeight", None))
    if final_weight is None and points:
        final_weight = points[-1]["cumulative_meterset_weight"]
    base["final_cumulative_meterset_weight"] = final_weight
    if final_weight is None or not math.isfinite(final_weight) or final_weight <= 0:
        return {**base, "reason": "FINAL_CUMULATIVE_METERSET_WEIGHT_MISSING_OR_INVALID"}
    if len(points) < 2:
        return {**base, "reason": "CONTROL_POINT_SEQUENCE_REQUIRES_AT_LEAST_TWO_POINTS"}
    declared_count = _int(getattr(beam, "NumberOfControlPoints", None))
    if declared_count is not None and declared_count != len(points):
        return {**base, "reason": "CONTROL_POINT_COUNT_MISMATCH"}
    indices = [item["control_point_index"] for item in points]
    if any(index is None for index in indices) or any(
        int(end) <= int(start) for start, end in zip(indices, indices[1:])
    ):
        return {**base, "reason": "CONTROL_POINT_INDICES_NOT_STRICTLY_INCREASING"}
    weights = [item["cumulative_meterset_weight"] for item in points]
    if any(weight is None or not math.isfinite(weight) for weight in weights):
        return {**base, "reason": "CUMULATIVE_METERSET_WEIGHT_MISSING_OR_INVALID"}
    numeric_weights = [float(weight) for weight in weights]
    weight_tolerance = max(1.0, abs(final_weight)) * 1.0e-9
    if abs(numeric_weights[0]) > weight_tolerance:
        return {**base, "reason": "FIRST_CUMULATIVE_METERSET_WEIGHT_NOT_ZERO"}
    if abs(numeric_weights[-1] - final_weight) > weight_tolerance:
        return {**base, "reason": "FINAL_CONTROL_POINT_WEIGHT_MISMATCH"}
    if any(end + weight_tolerance < start for start, end in zip(numeric_weights, numeric_weights[1:])):
        return {**base, "reason": "CUMULATIVE_METERSET_WEIGHTS_NOT_MONOTONIC"}

    control_points = []
    for point, weight in zip(points, numeric_weights):
        control_points.append({
            "sequence_index": point["sequence_index"],
            "control_point_index": point["control_point_index"],
            "cumulative_meterset_weight": weight,
            "cumulative_meterset_mu": round(meterset_mu * weight / final_weight, 9),
            "dose_rate_mu_per_min": point["dose_rate_mu_per_min"],
            "dose_rate_explicit_at_control_point": point["dose_rate_explicit_at_control_point"],
            "dose_rate_source_control_point_index": point["dose_rate_source_control_point_index"],
        })
    base["control_points"] = control_points

    seconds = 0.0
    segments: list[dict[str, Any]] = []
    for start, end in zip(control_points, control_points[1:]):
        delta_weight = end["cumulative_meterset_weight"] - start["cumulative_meterset_weight"]
        delta_mu = delta_weight / final_weight * meterset_mu
        dose_rate = start["dose_rate_mu_per_min"]
        is_irradiation_segment = delta_weight > weight_tolerance
        if is_irradiation_segment and (
            dose_rate is None or not math.isfinite(dose_rate) or dose_rate <= 0
        ):
            return {
                **base,
                "segments": segments,
                "reason": f"SEGMENT_START_DOSE_RATE_MISSING_OR_INVALID:CP{start['control_point_index']}",
            }
        segment_seconds = delta_mu / float(dose_rate) * 60.0 if is_irradiation_segment else 0.0
        seconds += segment_seconds
        segments.append({
            "start_control_point_index": start["control_point_index"],
            "end_control_point_index": end["control_point_index"],
            "start_cumulative_meterset_weight": start["cumulative_meterset_weight"],
            "end_cumulative_meterset_weight": end["cumulative_meterset_weight"],
            "delta_meterset_weight": round(delta_weight, 12),
            "start_meterset_mu": start["cumulative_meterset_mu"],
            "end_meterset_mu": end["cumulative_meterset_mu"],
            "delta_meterset_mu": round(delta_mu, 9),
            "dose_rate_mu_per_min": dose_rate,
            "dose_rate_source_control_point_index": start["dose_rate_source_control_point_index"],
            "beam_on_time_seconds": round(segment_seconds, 9),
        })
    return {
        **base,
        "status": "calculated",
        "beam_on_time_seconds": round(seconds, 3),
        "irradiation_segment_count": sum(item["delta_meterset_weight"] > weight_tolerance for item in segments),
        "segments": segments,
        "reason": None,
    }


def extract_rtplan_delivery_metadata(plan: Any | None) -> dict[str, Any]:
    """Extract standard RTPLAN delivery metadata and explicitly labelled derivations."""
    if plan is None:
        return {
            "schema_version": RTPLAN_DELIVERY_METADATA_VERSION,
            "status": "not_available",
            "beam_count": 0,
            "treatment_beam_count": 0,
            "vmat_arc_count": 0,
            "fraction_groups": [],
            "beams": [],
            "notes": ["The selected DICOM chain has no RTPLAN."],
        }

    beams_by_number: dict[int, Any] = {}
    for index, beam in enumerate(getattr(plan, "BeamSequence", []), 1):
        beam_number = _int(getattr(beam, "BeamNumber", index))
        beams_by_number[beam_number if beam_number is not None else index] = beam
    references_by_beam: dict[int, list[dict[str, Any]]] = {}
    fraction_groups: list[dict[str, Any]] = []
    for index, group in enumerate(getattr(plan, "FractionGroupSequence", []), 1):
        group_number = _int(getattr(group, "FractionGroupNumber", index)) or index
        fractions = _int(getattr(group, "NumberOfFractionsPlanned", None))
        group_references: list[dict[str, Any]] = []
        for reference in getattr(group, "ReferencedBeamSequence", []):
            beam_number = _int(getattr(reference, "ReferencedBeamNumber", None))
            if beam_number is None:
                continue
            meterset = _float(getattr(reference, "BeamMeterset", None))
            beam_dose = _float(getattr(reference, "BeamDose", None))
            record = {
                "fraction_group_number": group_number,
                "beam_number": beam_number,
                "meterset_mu": meterset,
                "beam_dose_gy": beam_dose,
                "mu_per_gy": (
                    round(meterset / beam_dose, 6)
                    if meterset is not None and beam_dose is not None and beam_dose != 0.0
                    else None
                ),
            }
            group_references.append(record)
            references_by_beam.setdefault(beam_number, []).append(record)
        group_mu = sum(item["meterset_mu"] for item in group_references if item["meterset_mu"] is not None)
        complete_mu = bool(group_references) and all(item["meterset_mu"] is not None for item in group_references)
        fraction_groups.append({
            "fraction_group_number": group_number,
            "planned_fractions": fractions,
            "referenced_beam_count": len(group_references),
            "total_mu_per_fraction": round(group_mu, 6) if complete_mu else None,
            "total_planned_mu": round(group_mu * fractions, 6) if complete_mu and fractions is not None else None,
            "referenced_beams": group_references,
        })

    beams: list[dict[str, Any]] = []
    for beam_number, beam in beams_by_number.items():
        points = _effective_control_points(beam)
        directions = [item["gantry_rotation_direction"] for item in points if item["gantry_rotation_direction"]]
        rotation_direction = next((item for item in directions if item not in {"NONE", ""}), directions[0] if directions else None)
        rotation = _rotation_degrees(points)
        rotates = bool(rotation_direction and rotation_direction != "NONE") or bool(rotation and rotation > 0.01)
        beam_type = _text(getattr(beam, "BeamType", "")).upper()
        mlc_varies = _mlc_positions_vary(beam)
        is_vmat = beam_type == "DYNAMIC" and rotates and mlc_varies
        technique = "VMAT" if is_vmat else "DYNAMIC_ARC" if beam_type == "DYNAMIC" and rotates else beam_type or "UNKNOWN"
        references = references_by_beam.get(beam_number, [])
        meterset = _single_value([item["meterset_mu"] for item in references])
        beam_dose = _single_value([item["beam_dose_gy"] for item in references])
        mu_per_gy = _single_value([item["mu_per_gy"] for item in references])
        energies = sorted({item["nominal_energy_mv"] for item in points if item["nominal_energy_mv"] is not None})
        dose_rates = sorted({item["dose_rate_mu_per_min"] for item in points if item["dose_rate_mu_per_min"] is not None})
        gantry_angles = [item["gantry_angle_deg"] for item in points if item["gantry_angle_deg"] is not None]
        collimator_angles = [item["collimator_angle_deg"] for item in points if item["collimator_angle_deg"] is not None]
        couch_angles = [item["couch_angle_deg"] for item in points if item["couch_angle_deg"] is not None]
        delivery_type = _text(getattr(beam, "TreatmentDeliveryType", "")).upper() or None
        beam_on_time = _beam_on_time_from_control_points(beam, meterset)
        beams.append({
            "beam_number": beam_number,
            "beam_name": _text(getattr(beam, "BeamName", "")) or f"Beam {beam_number}",
            "beam_description": _text(getattr(beam, "BeamDescription", "")) or None,
            "delivery_technique": technique,
            "is_vmat_arc": is_vmat,
            "beam_type": beam_type or None,
            "treatment_delivery_type": delivery_type,
            "radiation_type": _text(getattr(beam, "RadiationType", "")).upper() or None,
            "treatment_machine_name": _text(getattr(beam, "TreatmentMachineName", "")) or None,
            "fraction_group_numbers": [item["fraction_group_number"] for item in references],
            "meterset_mu": meterset,
            "beam_dose_gy": beam_dose,
            "mu_per_gy": mu_per_gy,
            "nominal_energy_mv": energies[0] if len(energies) == 1 else energies,
            "dose_rate_mu_per_min": dose_rates[0] if len(dose_rates) == 1 else dose_rates,
            "control_point_count": _int(getattr(beam, "NumberOfControlPoints", None)) or len(points),
            "gantry_start_deg": gantry_angles[0] if gantry_angles else None,
            "gantry_end_deg": gantry_angles[-1] if gantry_angles else None,
            "gantry_rotation_direction": rotation_direction,
            "gantry_rotation_deg": rotation,
            "collimator_start_deg": collimator_angles[0] if collimator_angles else None,
            "collimator_end_deg": collimator_angles[-1] if collimator_angles else None,
            "couch_start_deg": couch_angles[0] if couch_angles else None,
            "couch_end_deg": couch_angles[-1] if couch_angles else None,
            "delivery_duration_limit_seconds": _float(getattr(beam, "BeamDeliveryDurationLimit", None)),
            "beam_on_time_seconds": beam_on_time["beam_on_time_seconds"],
            "beam_on_time_calculation": beam_on_time,
            # Compatibility alias for pre-1.6.6 exports.
            "estimated_beam_on_time_seconds": beam_on_time["beam_on_time_seconds"],
            "fraction_group_values": references,
        })

    for group in fraction_groups:
        calculated_times: list[float] = []
        control_point_calculations: list[dict[str, Any]] = []
        limits: list[float] = []
        for reference in group["referenced_beams"]:
            beam = beams_by_number.get(reference["beam_number"])
            calculation = (
                _beam_on_time_from_control_points(beam, reference["meterset_mu"])
                if beam is not None else {
                    "status": "not_calculated", "beam_on_time_seconds": None,
                    "reason": "REFERENCED_BEAM_NOT_FOUND", "segments": [], "control_points": [],
                }
            )
            control_point_calculations.append({
                "beam_number": reference["beam_number"],
                **calculation,
            })
            if calculation["beam_on_time_seconds"] is not None:
                calculated_times.append(calculation["beam_on_time_seconds"])
            limit = _float(getattr(beam, "BeamDeliveryDurationLimit", None)) if beam is not None else None
            if limit is not None:
                limits.append(limit)
        reference_count = len(group["referenced_beams"])
        group["beam_on_time_control_point_calculations"] = control_point_calculations
        group["beam_on_time_status"] = (
            "calculated"
            if reference_count and all(item.get("status") == "calculated" for item in control_point_calculations)
            else "not_calculated"
        )
        group["beam_on_time_seconds_per_fraction"] = (
            round(sum(calculated_times), 3)
            if reference_count and len(calculated_times) == reference_count else None
        )
        # Compatibility alias for pre-1.6.6 exports.
        group["estimated_beam_on_time_seconds_per_fraction"] = group["beam_on_time_seconds_per_fraction"]
        group["delivery_duration_limit_seconds_per_fraction"] = (
            round(sum(limits), 3) if reference_count and len(limits) == reference_count else None
        )

    treatment_beams = [
        item for item in beams
        if item["treatment_delivery_type"] not in {"SETUP", "OPEN_PORTFILM", "TRMT_PORTFILM"}
    ]
    total_planned_mu_values = [item["total_planned_mu"] for item in fraction_groups]
    total_time_values = [
        item["beam_on_time_seconds_per_fraction"] * item["planned_fractions"]
        for item in fraction_groups
        if item["beam_on_time_seconds_per_fraction"] is not None and item["planned_fractions"] is not None
    ]
    one_group = fraction_groups[0] if len(fraction_groups) == 1 else {}
    return {
        "schema_version": RTPLAN_DELIVERY_METADATA_VERSION,
        "status": "available",
        "plan_label": _text(getattr(plan, "RTPlanLabel", "")) or None,
        "plan_name": _text(getattr(plan, "RTPlanName", "")) or None,
        "plan_uid": _text(getattr(plan, "SOPInstanceUID", "")) or None,
        "approval_status": _text(getattr(plan, "ApprovalStatus", "")) or None,
        "beam_count": len(beams),
        "treatment_beam_count": len(treatment_beams),
        "vmat_arc_count": sum(bool(item["is_vmat_arc"]) for item in treatment_beams),
        "total_mu_per_fraction": one_group.get("total_mu_per_fraction"),
        "total_planned_mu": round(sum(total_planned_mu_values), 6) if fraction_groups and all(value is not None for value in total_planned_mu_values) else None,
        "beam_on_time_seconds_per_fraction": one_group.get("beam_on_time_seconds_per_fraction"),
        "total_beam_on_time_seconds": round(sum(total_time_values), 3) if fraction_groups and len(total_time_values) == len(fraction_groups) else None,
        # Compatibility aliases for pre-1.6.6 exports.
        "estimated_beam_on_time_seconds_per_fraction": one_group.get("beam_on_time_seconds_per_fraction"),
        "estimated_total_beam_on_time_seconds": round(sum(total_time_values), 3) if fraction_groups and len(total_time_values) == len(fraction_groups) else None,
        "fraction_groups": fraction_groups,
        "beams": beams,
        "notes": [
            "MU and Beam Dose are read from FractionGroupSequence.ReferencedBeamSequence.",
            "MU/Gy is derived as BeamMeterset divided by BeamDose for the same referenced beam.",
            "Beam-on time is calculated interval-by-interval from RTPLAN ControlPointSequence cumulative meterset weights and the DoseRateSet that applies to the segment beginning at each control point.",
            "The control-point beam-on time excludes imaging, setup, inter-beam, and mechanical-transition overhead and is not a treatment-record delivery time.",
            "A beam is counted as VMAT only when the RTPLAN records a dynamic rotating beam with changing MLC leaf positions.",
        ],
    }


def extract_rtplan_configuration(rtplan_path: str | None, rtdose_path: str | None = None) -> dict[str, Any]:
    """Extract auditable configuration candidates without guessing clinical roles."""
    if not rtplan_path:
        return {
            "status": "not_available", "fraction_candidates": [], "prescription_candidates": [],
            "treatment_components": [], "delivery_metadata": extract_rtplan_delivery_metadata(None),
            "warnings": ["selected_chain_has_no_rtplan"],
        }
    plan = pydicom.dcmread(rtplan_path, stop_before_pixels=True)
    dose = pydicom.dcmread(rtdose_path, stop_before_pixels=True) if rtdose_path else None
    fraction_candidates: list[dict[str, Any]] = []
    for index, group in enumerate(getattr(plan, "FractionGroupSequence", []), 1):
        count = getattr(group, "NumberOfFractionsPlanned", None)
        if count is None:
            continue
        referenced_beams = list(getattr(group, "ReferencedBeamSequence", []))
        fraction_candidates.append({
            "fraction_group_number": int(getattr(group, "FractionGroupNumber", index)),
            "fractions": int(count),
            "referenced_beam_count": len(referenced_beams),
            "source": "RTPLAN.FractionGroupSequence.NumberOfFractionsPlanned",
        })
    prescription_candidates: list[dict[str, Any]] = []
    for index, reference in enumerate(getattr(plan, "DoseReferenceSequence", []), 1):
        dose_gy = getattr(reference, "TargetPrescriptionDose", None)
        if dose_gy is None:
            continue
        referenced_roi = getattr(reference, "ReferencedROINumber", None)
        label = _dose_reference_label(reference)
        prescription_candidates.append({
            "dose_reference_number": int(getattr(reference, "DoseReferenceNumber", index)),
            "dose_gy": float(dose_gy),
            "label": label,
            "referenced_roi_number": int(referenced_roi) if referenced_roi is not None else None,
            "dose_reference_type": _text(getattr(reference, "DoseReferenceType", "")),
            "dose_reference_structure_type": _text(getattr(reference, "DoseReferenceStructureType", "")),
            "source": "RTPLAN.DoseReferenceSequence.TargetPrescriptionDose",
        })
    component_id = _text(getattr(plan, "RTPlanLabel", "")) or "RTPLAN_COMPONENT"
    unique_fractions = sorted({item["fractions"] for item in fraction_candidates})
    unique_prescriptions = sorted({item["dose_gy"] for item in prescription_candidates})
    component = {
        "component_id": component_id,
        "component_type": "unknown",
        "dose_object_uid": _text(getattr(dose, "SOPInstanceUID", "")) or None if dose is not None else None,
        "plan_uid": _text(getattr(plan, "SOPInstanceUID", "")) or None,
        "fraction_count": unique_fractions[0] if len(unique_fractions) == 1 else None,
        "prescription_gy": unique_prescriptions[0] if len(unique_prescriptions) == 1 else None,
        "source": "RTPLAN",
    }
    warnings: list[str] = []
    if len(unique_fractions) > 1:
        warnings.append("multiple_rtplan_fraction_groups_require_explicit_fraction_selection")
    if len(unique_prescriptions) > 1:
        warnings.append("multiple_rtplan_prescriptions_require_role_specific_selection")
    if not prescription_candidates:
        warnings.append("rtplan_has_no_target_prescription_dose")
    delivery_metadata = extract_rtplan_delivery_metadata(plan)
    return {
        "status": "available_with_choices" if warnings else "available",
        "plan_label": component_id,
        "plan_uid": component["plan_uid"],
        "dose_uid": component["dose_object_uid"],
        "dose_summation_type": _text(getattr(dose, "DoseSummationType", "")) if dose is not None else None,
        "beam_count": len(getattr(plan, "BeamSequence", [])),
        "delivery_metadata": delivery_metadata,
        "fraction_candidates": fraction_candidates,
        "prescription_candidates": prescription_candidates,
        "treatment_components": [component],
        "warnings": warnings,
    }


def apply_unambiguous_rtplan_prefill(configuration: Any, evidence: dict[str, Any]) -> None:
    """Populate only fields with one unambiguous DICOM meaning; preserve unresolved choices."""
    fractions = sorted({int(item["fractions"]) for item in evidence.get("fraction_candidates", [])})
    if len(fractions) == 1:
        configuration.fractionation = {"fractions": fractions[0], "source": "RTPLAN"}
        for prescription in configuration.prescriptions.values():
            prescription.fractions = fractions[0]
    roles_by_roi: dict[int, set[str]] = {}
    for role, raw in configuration.structure_bindings.items():
        bindings = raw if isinstance(raw, list) else [raw]
        for binding in bindings:
            if isinstance(binding, dict) and binding.get("roi_number") is not None:
                roles_by_roi.setdefault(int(binding["roi_number"]), set()).add(str(role))
    role_candidates: dict[str, list[float]] = {"Rx_L": [], "Rx_H": []}
    for item in evidence.get("prescription_candidates", []):
        roles = roles_by_roi.get(int(item["referenced_roi_number"]), set()) if item.get("referenced_roi_number") is not None else set()
        normalised_label = _normalise(str(item.get("label") or ""))
        if "T_L" in roles or any(token in normalised_label for token in ("PERIPHERAL", "LOWDOSE", "PTVLOW", "RXL")):
            role_candidates["Rx_L"].append(float(item["dose_gy"]))
        if "VTV_H" in roles or any(token in normalised_label for token in ("VERTEX", "HIGHDOSE", "VTVH", "RXH")):
            role_candidates["Rx_H"].append(float(item["dose_gy"]))
    for key, candidates in role_candidates.items():
        values = sorted(set(candidates))
        if len(values) == 1 and configuration.prescriptions[key].gy is None:
            configuration.prescriptions[key].gy = values[0]
            configuration.prescriptions[key].source = "RTPLAN"
    if evidence.get("treatment_components"):
        component = dict(evidence["treatment_components"][0])
        if configuration.prescriptions["Rx_L"].gy is not None:
            component["rx_low_gy"] = float(configuration.prescriptions["Rx_L"].gy)
        if configuration.prescriptions["Rx_H"].gy is not None:
            component["rx_high_gy"] = float(configuration.prescriptions["Rx_H"].gy)
        configuration.treatment_components = [component]
        configuration.selected_treatment_component_id = evidence["treatment_components"][0]["component_id"]
    summation = str(evidence.get("dose_summation_type") or "").upper()
    configuration.dose_context = {
        "PLAN": "complete_single_plan", "MULTI_PLAN": "composite_course",
        "FRACTION": "lrt_component", "BEAM": "lrt_component",
    }.get(summation, configuration.dose_context)
    if configuration.dose_context == "complete_single_plan":
        configuration.prescription_context = "complete_plan"
    elif configuration.dose_context == "composite_course":
        configuration.prescription_context = "composite_course"
