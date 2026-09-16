"""Preflight the selected ROI geometry before the locked volume rasteriser runs."""

from __future__ import annotations

from typing import Any

import numpy as np

from ascend.dicom.geometry import DoseGeometryError, GEOMETRY_TOLERANCES


# RTSTRUCT contour coordinates and Image Position Patient are independently
# encoded DICOM DS values. Permit sub-voxel export rounding without weakening
# the stricter RTDOSE and planning-image geometry checks.
CONTOUR_REFERENCE_PLANE_TOLERANCE_MM = 0.1


def validate_selected_contours(
    structure: Any,
    selected_numbers: set[int],
    image_headers: list[Any],
    dose: Any,
) -> None:
    """Reject unsupported contour inputs instead of silently flattening or dropping them.

    Contour Image Sequence is optional in DICOM. When present, its references
    must resolve in the selected classic series. Referenced Frame of Reference
    UID is checked per selected ROI, since a structure set can contain several
    frames and its first referenced frame is not authority for every ROI.
    """
    dose_frame = str(getattr(dose, "FrameOfReferenceUID", ""))
    images_by_uid = {
        str(getattr(image, "SOPInstanceUID", "")): image for image in image_headers
    }
    if "" in images_by_uid or len(images_by_uid) != len(image_headers):
        raise DoseGeometryError("BLOCK_IMAGE_IDENTITY: selected planning images have missing or duplicate SOP Instance UIDs.")
    if any(str(getattr(image, "FrameOfReferenceUID", "")) != dose_frame for image in image_headers):
        raise DoseGeometryError(
            "BLOCK_IMAGE_IDENTITY: selected planning images and RTDOSE must share the same Frame of Reference UID."
        )
    if not dose_frame:
        raise DoseGeometryError("BLOCK_IMAGE_IDENTITY: the selected RTDOSE requires a Frame of Reference UID.")
    for roi in getattr(structure, "StructureSetROISequence", []):
        if int(roi.ROINumber) not in selected_numbers:
            continue
        if str(getattr(roi, "ReferencedFrameOfReferenceUID", "")) != dose_frame:
            raise DoseGeometryError(
                f"BLOCK_RTSTRUCT_IDENTITY: selected ROI {roi.ROINumber} does not reference the RTDOSE Frame of Reference; "
                "export structures in the dose frame. Registration transforms are not applied."
            )

    orientation = np.asarray(image_headers[0].ImageOrientationPatient, dtype=float)
    normal = np.cross(orientation[:3], orientation[3:])
    normal /= np.linalg.norm(normal)
    tolerance = GEOMETRY_TOLERANCES["position_and_offset_mm"]
    image_plane_positions = {
        uid: float(np.asarray(image.ImagePositionPatient, dtype=float) @ normal)
        for uid, image in images_by_uid.items()
    }
    seen: set[int] = set()
    for roi in getattr(structure, "ROIContourSequence", []):
        number = int(getattr(roi, "ReferencedROINumber", -1))
        if number not in selected_numbers:
            continue
        if number in seen:
            raise DoseGeometryError(f"BLOCK_RTSTRUCT_IDENTITY: multiple ROI Contour Sequence items reference ROI {number}.")
        seen.add(number)
        contours = list(getattr(roi, "ContourSequence", []))
        if not contours:
            raise DoseGeometryError(f"BLOCK_RTSTRUCT_CONTOUR: selected ROI {number} has no contours to rasterise.")
        types = {str(getattr(contour, "ContourGeometricType", "")).upper() for contour in contours}
        if not types <= {"CLOSED_PLANAR", "CLOSEDPLANAR_XOR"}:
            raise DoseGeometryError(
                f"BLOCK_RTSTRUCT_CONTOUR: selected ROI {number} contains unsupported contour types {sorted(types)}; "
                "volume analysis requires closed planar polygons."
            )
        if "CLOSEDPLANAR_XOR" in types and len(types) != 1:
            raise DoseGeometryError(f"BLOCK_RTSTRUCT_CONTOUR: ROI {number} mixes CLOSEDPLANAR_XOR with other contour types.")
        for index, contour in enumerate(contours, 1):
            label = f"ROI {number}, contour {index}"
            try:
                data = np.asarray(getattr(contour, "ContourData", []), dtype=float)
                count = int(getattr(contour, "NumberOfContourPoints", 0))
            except (TypeError, ValueError) as exc:
                raise DoseGeometryError(f"BLOCK_RTSTRUCT_CONTOUR: {label} contains invalid numeric coordinates/count.") from exc
            if data.ndim != 1 or count < 3 or data.size != 3 * count or not np.isfinite(data).all():
                raise DoseGeometryError(
                    f"BLOCK_RTSTRUCT_CONTOUR: {label} requires at least three finite xyz points matching NumberOfContourPoints."
                )
            points = data.reshape(-1, 3)
            positions = points @ normal
            if float(np.ptp(positions)) > tolerance:
                raise DoseGeometryError(
                    f"BLOCK_RTSTRUCT_CONTOUR: {label} is not planar and parallel to the selected planning-image slices; "
                    "export contours on the selected classic image planes."
                )
            for reference in getattr(contour, "ContourImageSequence", []):
                uid = str(getattr(reference, "ReferencedSOPInstanceUID", ""))
                if uid not in images_by_uid:
                    raise DoseGeometryError(
                        f"BLOCK_RTSTRUCT_REFERENCE: {label} references an image absent from the selected series; "
                        "export the complete referenced planning-image series."
                    )
                contour_position = float(np.mean(positions))
                reference_offset = abs(contour_position - image_plane_positions[uid])
                if reference_offset > CONTOUR_REFERENCE_PLANE_TOLERANCE_MM:
                    nearest_uid, nearest_position = min(
                        image_plane_positions.items(),
                        key=lambda item: abs(contour_position - item[1]),
                    )
                    nearest_offset = abs(contour_position - nearest_position)
                    raise DoseGeometryError(
                        f"BLOCK_RTSTRUCT_REFERENCE: {label} is {reference_offset:.6g} mm from its referenced "
                        f"image plane (tolerance {CONTOUR_REFERENCE_PLANE_TOLERANCE_MM:g} mm); nearest selected "
                        f"image SOP Instance UID {nearest_uid} is {nearest_offset:.6g} mm away."
                    )
    missing = sorted(selected_numbers - seen)
    if missing:
        raise DoseGeometryError(f"BLOCK_RTSTRUCT_CONTOUR: selected ROI numbers have no contour items: {missing}.")
