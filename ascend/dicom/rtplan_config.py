"""Auditable RTPLAN/RTDOSE configuration extraction with ambiguity-preserving prefilling."""

from __future__ import annotations

import re
from typing import Any

import pydicom


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


def extract_rtplan_configuration(rtplan_path: str | None, rtdose_path: str | None = None) -> dict[str, Any]:
    """Extract auditable configuration candidates without guessing clinical roles."""
    if not rtplan_path:
        return {
            "status": "not_available", "fraction_candidates": [], "prescription_candidates": [],
            "treatment_components": [], "warnings": ["selected_chain_has_no_rtplan"],
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
    return {
        "status": "available_with_choices" if warnings else "available",
        "plan_label": component_id,
        "plan_uid": component["plan_uid"],
        "dose_uid": component["dose_object_uid"],
        "dose_summation_type": _text(getattr(dose, "DoseSummationType", "")) if dose is not None else None,
        "beam_count": len(getattr(plan, "BeamSequence", [])),
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
