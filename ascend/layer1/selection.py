"""Identity-based ROI selection, filtering, inventory, and effective-role derivation."""

from __future__ import annotations

import copy
import re
from typing import Any

from ascend.dicom.roi import identity_key, inventory as roi_inventory
from ascend.models.config import CaseConfiguration
from ascend.scientific.legacy import layer1_validated as validated


def selected_roi_reasons(configuration: CaseConfiguration, rtstruct_uid: str) -> dict[int, list[str]]:
    """Handle selected roi reasons for the enclosing ASCEND workflow."""
    selected: dict[int, list[str]] = {}

    def add(value: dict[str, Any], reason: str) -> None:
        uid, number = identity_key(value)
        if uid != rtstruct_uid:
            raise ValueError(
                f"ROI binding {reason} belongs to RTSTRUCT {uid}, not selected RTSTRUCT {rtstruct_uid}."
            )
        selected.setdefault(number, []).append(reason)

    for role, binding in configuration.structure_bindings.items():
        values = binding if isinstance(binding, list) else [binding]
        for value in values:
            add(value, f"structure_role:{role}")
    for value in configuration.validation_structures:
        add(value, "explicit_validation_structure")
    for item in configuration.oar_structures:
        if item.get("roi_identity"):
            add(item["roi_identity"], "oar_geometry")
    return {number: sorted(set(reasons)) for number, reasons in selected.items()}


def filtered_rtstruct(dataset: Any, selected_numbers: set[int]) -> Any:
    """Handle filtered rtstruct for the enclosing ASCEND workflow."""
    filtered = copy.deepcopy(dataset)
    filtered.StructureSetROISequence = [
        item for item in getattr(filtered, "StructureSetROISequence", [])
        if int(item.ROINumber) in selected_numbers
    ]
    filtered.ROIContourSequence = [
        item for item in getattr(filtered, "ROIContourSequence", [])
        if int(item.ReferencedROINumber) in selected_numbers
    ]
    if hasattr(filtered, "RTROIObservationsSequence"):
        filtered.RTROIObservationsSequence = [
            item for item in filtered.RTROIObservationsSequence
            if int(getattr(item, "ReferencedROINumber", -1)) in selected_numbers
        ]
    return filtered


def _canonical_inventory_mapping(
    name: str,
    number: int,
    gtv_number: int | None,
) -> tuple[str, str]:
    normalized = validated.norm(name)
    aliases = {
        validated.norm(alias): canonical
        for canonical, values in validated.STRUCTURE_ALIASES.items()
        for alias in [canonical, *values]
    }
    if gtv_number == number:
        return "GTV", "MANUALLY_CONFIRMED"
    canonical = aliases.get(normalized)
    if canonical:
        return canonical, "EXACT" if normalized == validated.norm(canonical) else "CONFIGURED_ALIAS"
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "UNNAMED"
    return f"ROI_{number}_{safe}", "UNMAPPED_INVENTORY"


def build_roi_inventory(
    dataset: Any,
    configuration: CaseConfiguration,
    mappings: list[dict[str, Any]],
    exported_structures: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build roi inventory from validated inputs."""
    uid = str(getattr(dataset, "SOPInstanceUID", ""))
    reasons = selected_roi_reasons(configuration, uid)
    gtv = configuration.structure_bindings.get("GTV")
    gtv_number = identity_key(gtv)[1] if isinstance(gtv, dict) else None
    mapped = {int(item["roi_number"]): item for item in mappings if item.get("roi_number") is not None}
    output = roi_inventory(dataset)
    for record in output:
        number = record["roi_number"]
        calculated = mapped.get(number)
        canonical, mapping_status = _canonical_inventory_mapping(record["original_name"], number, gtv_number)
        if calculated:
            canonical = calculated.get("standard_name") or canonical
            mapping_status = calculated.get("mapping_status") or mapping_status
        record["canonical_mapping"] = canonical
        record["mapping_status"] = mapping_status
        record["selection_reason"] = reasons.get(number, ["not_selected"])
        if number not in reasons:
            record["rasterisation_status"] = "not_rasterised"
        elif canonical in exported_structures:
            record["rasterisation_status"] = "rasterised"
        else:
            record["rasterisation_status"] = "rasterisation_failed"
            record["rasterisation_failure"] = "Selected ROI did not produce a non-empty validated native-dose mask."
    return output


def effective_roles_from_bindings(
    configuration: CaseConfiguration,
    mappings: list[dict[str, Any]],
) -> dict[str, str | list[str]]:
    """Handle effective roles from bindings for the enclosing ASCEND workflow."""
    by_number = {int(item["roi_number"]): str(item["standard_name"]) for item in mappings}
    effective: dict[str, str | list[str]] = {}
    for role, binding in configuration.structure_bindings.items():
        if isinstance(binding, list):
            missing = [identity_key(item)[1] for item in binding if identity_key(item)[1] not in by_number]
            if missing:
                raise ValueError(f"Configured ROI identities were not rasterised for {role}: {missing}")
            effective[role] = [by_number[identity_key(item)[1]] for item in binding]
        else:
            number = identity_key(binding)[1]
            if number not in by_number:
                raise ValueError(f"Configured ROI identity was not rasterised for {role}: {number}")
            effective[role] = by_number[number]
    return effective
