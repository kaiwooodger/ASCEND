"""RTSTRUCT ROI identity, lookup, validation, and inventory helpers."""

from __future__ import annotations

from typing import Any


def identity(rtstruct_uid: str, roi_number: int, display_name: str | None = None) -> dict[str, Any]:
    """Handle identity for the enclosing ASCEND workflow."""
    value: dict[str, Any] = {
        "rtstruct_sop_instance_uid": str(rtstruct_uid),
        "roi_number": int(roi_number),
    }
    if display_name is not None:
        value["display_name"] = str(display_name)
    return value


def identity_key(value: dict[str, Any]) -> tuple[str, int]:
    """Handle identity key for the enclosing ASCEND workflow."""
    return str(value["rtstruct_sop_instance_uid"]), int(value["roi_number"])


def validate_identity(value: Any, label: str = "ROI identity") -> None:
    """Validate identity and raise a controlled error when requirements are not met."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object containing RTSTRUCT UID and ROI number.")
    if not str(value.get("rtstruct_sop_instance_uid") or "").strip():
        raise ValueError(f"{label} requires rtstruct_sop_instance_uid.")
    try:
        number = int(value["roi_number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} requires an integer roi_number.") from exc
    if number < 0:
        raise ValueError(f"{label} roi_number must be non-negative.")


def rtstruct_roi_lookup(dataset: Any) -> tuple[dict[int, str], dict[str, list[int]]]:
    """Handle rtstruct roi lookup for the enclosing ASCEND workflow."""
    by_number: dict[int, str] = {}
    by_name: dict[str, list[int]] = {}
    for item in getattr(dataset, "StructureSetROISequence", []):
        number = int(item.ROINumber)
        name = str(item.ROIName)
        by_number[number] = name
        by_name.setdefault(name, []).append(number)
    return by_number, by_name


def resolve_name(dataset: Any, name: str) -> dict[str, Any]:
    """Resolve name without silently guessing ambiguous meaning."""
    uid = str(getattr(dataset, "SOPInstanceUID", ""))
    by_number, by_name = rtstruct_roi_lookup(dataset)
    numbers = by_name.get(str(name), [])
    if len(numbers) != 1:
        reason = "not found" if not numbers else "ambiguous"
        raise ValueError(f"RTSTRUCT ROI name {name!r} is {reason}; bind the ROI by number.")
    number = numbers[0]
    return identity(uid, number, by_number[number])


def inventory(dataset: Any) -> list[dict[str, Any]]:
    """Handle inventory for the enclosing ASCEND workflow."""
    uid = str(getattr(dataset, "SOPInstanceUID", ""))
    contours = {
        int(item.ReferencedROINumber): item
        for item in getattr(dataset, "ROIContourSequence", [])
        if getattr(item, "ReferencedROINumber", None) is not None
    }
    records: list[dict[str, Any]] = []
    for roi in getattr(dataset, "StructureSetROISequence", []):
        number = int(roi.ROINumber)
        contour_item = contours.get(number)
        contour_sequence = list(getattr(contour_item, "ContourSequence", [])) if contour_item else []
        referenced = sorted({
            str(image.ReferencedSOPInstanceUID)
            for contour in contour_sequence
            for image in getattr(contour, "ContourImageSequence", [])
            if getattr(image, "ReferencedSOPInstanceUID", None)
        })
        records.append({
            "roi_identity": identity(uid, number),
            "roi_number": number,
            "original_name": str(roi.ROIName),
            "generation_algorithm": str(getattr(roi, "ROIGenerationAlgorithm", "")) or None,
            "contour_available": bool(contour_sequence),
            "contour_count": len(contour_sequence),
            "contour_geometric_types": sorted({
                str(getattr(contour, "ContourGeometricType", "UNKNOWN")) for contour in contour_sequence
            }),
            "referenced_contour_image_sop_uids": referenced,
            "canonical_mapping": None,
            "mapping_status": "unresolved",
            "selection_reason": "not_selected",
            "rasterisation_status": "not_rasterised",
            "rasterisation_failure": None,
        })
    return records
