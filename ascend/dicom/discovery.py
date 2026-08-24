"""Header-only DICOM discovery and inventory generation without scientific calculation."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pydicom

from .relationships import resolve_dicom_chains


SUPPORTED = {"RTPLAN", "RTSTRUCT", "RTDOSE", "CT", "MR", "PT"}
INVENTORY_SCHEMA_VERSION = "ASCEND-DICOM-inventory-v2"
SKIP_DIRECTORIES = {".Trash", ".git", ".venv", "__pycache__", "node_modules", "site-packages"}


def _record(path: Path, dataset: Any) -> dict[str, Any]:
    record = {
        "path": str(path.resolve()),
        "modality": str(getattr(dataset, "Modality", "")).upper(),
        "sop_instance_uid": str(getattr(dataset, "SOPInstanceUID", "")),
        "study_instance_uid": str(getattr(dataset, "StudyInstanceUID", "")),
        "series_instance_uid": str(getattr(dataset, "SeriesInstanceUID", "")),
        "frame_of_reference_uid": str(getattr(dataset, "FrameOfReferenceUID", "")),
        "patient_id": str(getattr(dataset, "PatientID", "")),
        "patient_name": str(getattr(dataset, "PatientName", "")),
        "plan_label": str(getattr(dataset, "RTPlanLabel", "")),
        "approval_status": str(getattr(dataset, "ApprovalStatus", "")),
        "manufacturer": str(getattr(dataset, "Manufacturer", "")),
        "manufacturer_model_name": str(getattr(dataset, "ManufacturerModelName", "")),
        "software_versions": str(getattr(dataset, "SoftwareVersions", "")),
        "dose_summation_type": str(getattr(dataset, "DoseSummationType", "")),
        "dose_type": str(getattr(dataset, "DoseType", "")),
    }
    if record["modality"] == "RTSTRUCT":
        record["roi_names"] = [str(item.ROIName) for item in getattr(dataset, "StructureSetROISequence", [])]
        record["frame_of_reference_uid"] = record["frame_of_reference_uid"] or next((
            str(getattr(item, "FrameOfReferenceUID", ""))
            for item in getattr(dataset, "ReferencedFrameOfReferenceSequence", [])
            if getattr(item, "FrameOfReferenceUID", None)
        ), "")
        record["referenced_image_series_uids"] = sorted({
            str(series.SeriesInstanceUID)
            for frame in getattr(dataset, "ReferencedFrameOfReferenceSequence", [])
            for study in getattr(frame, "RTReferencedStudySequence", [])
            for series in getattr(study, "RTReferencedSeriesSequence", [])
            if getattr(series, "SeriesInstanceUID", None)
        })
        record["referenced_contour_image_uids"] = sorted({
            str(image.ReferencedSOPInstanceUID)
            for roi in getattr(dataset, "ROIContourSequence", [])
            for contour in getattr(roi, "ContourSequence", [])
            for image in getattr(contour, "ContourImageSequence", [])
            if getattr(image, "ReferencedSOPInstanceUID", None)
        })
    elif record["modality"] == "RTDOSE":
        record["referenced_rtplan_uids"] = sorted({
            str(item.ReferencedSOPInstanceUID)
            for item in getattr(dataset, "ReferencedRTPlanSequence", [])
            if getattr(item, "ReferencedSOPInstanceUID", None)
        })
    elif record["modality"] == "RTPLAN":
        record["referenced_rtstruct_uids"] = sorted({
            str(item.ReferencedSOPInstanceUID)
            for item in getattr(dataset, "ReferencedStructureSetSequence", [])
            if getattr(item, "ReferencedSOPInstanceUID", None)
        })
    return record


def discover_case(folder: str | Path) -> dict[str, Any]:
    """Read headers only. No dose decoding, geometry construction, or rasterisation."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Case directory does not exist: {root}")
    objects: dict[str, list[dict[str, Any]]] = {key: [] for key in SUPPORTED}
    for directory, child_directories, filenames in os.walk(root):
        child_directories[:] = sorted(
            name for name in child_directories
            if name not in SKIP_DIRECTORIES and not name.startswith(".") and not name.endswith(".app")
        )
        base = Path(directory)
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = base / filename
            try:
                dataset = pydicom.dcmread(path, stop_before_pixels=True)
            except Exception:
                continue
            modality = str(getattr(dataset, "Modality", "")).upper()
            if modality in objects:
                objects[modality].append(_record(path, dataset))
    objects = {key: value for key, value in objects.items() if value}
    patient_ids = Counter(item["patient_id"] for values in objects.values() for item in values if item["patient_id"])
    studies = Counter(item["study_instance_uid"] for values in objects.values() for item in values if item["study_instance_uid"])
    inventory = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_directory": str(root),
        "objects": objects,
        "patient_id": patient_ids.most_common(1)[0][0] if patient_ids else "unidentified",
        "study_instance_uid": studies.most_common(1)[0][0] if studies else "",
        "counts": {key: len(value) for key, value in objects.items()},
    }
    inventory["dicom_chains"] = resolve_dicom_chains(objects)
    return inventory


def write_inventory(inventory: dict[str, Any], path: str | Path) -> Path:
    """Write inventory deterministically to disk."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return output
