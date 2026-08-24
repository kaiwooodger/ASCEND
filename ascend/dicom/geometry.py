"""Geometry calculations used by DICOM ingestion or validation diagnostics."""

from __future__ import annotations

from typing import Any

import numpy as np


# Keep the algorithm identifier separate from the numerical tolerances.  A
# tolerance change can alter acceptance at a boundary even when the coordinate
# conversion itself is unchanged, so both are persisted in run provenance.
GEOMETRY_NORMALISATION_VERSION = "ASCEND-RTDOSE-geometry-v1"
GEOMETRY_TOLERANCES = {
    "orientation_vector_norm": 1.0e-4,
    "orientation_orthogonality_dot": 1.0e-4,
    "orientation_component_comparison": 1.0e-4,
    "position_and_offset_mm": 1.0e-3,
    "spacing_absolute_mm": 1.0e-3,
    "spacing_relative": 1.0e-4,
}


class DoseGeometryError(ValueError):
    """Signal a controlled dose geometry error condition."""
    pass


def _required(dataset: Any, name: str) -> Any:
    value = getattr(dataset, name, None)
    if value is None or value == "":
        raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: required attribute {name} is missing.")
    return value


def _finite_vector(dataset: Any, name: str, length: int) -> np.ndarray:
    try:
        value = np.asarray(_required(dataset, name), dtype=float)
    except (TypeError, ValueError) as exc:
        raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: {name} is not numeric.") from exc
    if value.shape != (length,) or not np.isfinite(value).all():
        raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: {name} must contain {length} finite values.")
    return value


def _positive_int(dataset: Any, name: str) -> int:
    try:
        value = int(_required(dataset, name))
    except (TypeError, ValueError) as exc:
        raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: {name} must be an integer.") from exc
    if value <= 0:
        raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: {name} must be positive.")
    return value


def _integer(dataset: Any, name: str) -> int:
    try:
        return int(_required(dataset, name))
    except (TypeError, ValueError) as exc:
        raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: {name} must be an integer.") from exc


def _spacing_tolerance(value: float) -> float:
    return max(
        GEOMETRY_TOLERANCES["spacing_absolute_mm"],
        GEOMETRY_TOLERANCES["spacing_relative"] * abs(value),
    )


def normalise_rtdose_geometry(dataset: Any, validate_pixels: bool = True) -> dict[str, Any]:
    """Validate RTDOSE geometry and express frame positions relative to the origin.

    The returned offsets retain source pixel-frame order.  This function must
    never sort ``GridFrameOffsetVector`` independently of ``pixel_array``;
    doing so would silently associate dose values with the wrong anatomy.
    """
    rows = _positive_int(dataset, "Rows")
    columns = _positive_int(dataset, "Columns")
    frames = _positive_int(dataset, "NumberOfFrames")
    origin = _finite_vector(dataset, "ImagePositionPatient", 3)
    orientation = _finite_vector(dataset, "ImageOrientationPatient", 6)
    column_direction = orientation[:3]
    row_direction = orientation[3:]
    norm_tolerance = GEOMETRY_TOLERANCES["orientation_vector_norm"]
    dot_tolerance = GEOMETRY_TOLERANCES["orientation_orthogonality_dot"]
    if abs(float(np.linalg.norm(column_direction)) - 1.0) > norm_tolerance:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: first orientation vector is not unit length.")
    if abs(float(np.linalg.norm(row_direction)) - 1.0) > norm_tolerance:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: second orientation vector is not unit length.")
    if abs(float(np.dot(column_direction, row_direction))) > dot_tolerance:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: orientation vectors are not orthogonal.")
    normal = np.cross(column_direction, row_direction)
    normal_norm = float(np.linalg.norm(normal))
    if abs(normal_norm - 1.0) > norm_tolerance:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: slice normal is degenerate.")
    normal /= normal_norm
    pixel_spacing = _finite_vector(dataset, "PixelSpacing", 2)
    if np.any(pixel_spacing <= 0):
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: PixelSpacing must be positive.")
    if str(_required(dataset, "DoseUnits")).upper() != "GY":
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: DoseUnits must be GY.")
    try:
        scaling = float(_required(dataset, "DoseGridScaling"))
    except (TypeError, ValueError) as exc:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: DoseGridScaling must be numeric.") from exc
    if not np.isfinite(scaling) or scaling <= 0:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: DoseGridScaling must be finite and positive.")
    samples = _positive_int(dataset, "SamplesPerPixel")
    photometric = str(_required(dataset, "PhotometricInterpretation")).upper()
    bits_allocated = _positive_int(dataset, "BitsAllocated")
    bits_stored = _positive_int(dataset, "BitsStored")
    high_bit = _integer(dataset, "HighBit")
    pixel_representation = _integer(dataset, "PixelRepresentation")
    if samples != 1:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: SamplesPerPixel must be 1 for RTDOSE.")
    if photometric not in {"MONOCHROME1", "MONOCHROME2"}:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: PhotometricInterpretation must be monochrome.")
    if bits_allocated not in {8, 16, 32, 64} or bits_stored > bits_allocated:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: BitsAllocated/BitsStored are inconsistent.")
    if high_bit != bits_stored - 1:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: HighBit must equal BitsStored minus one.")
    if pixel_representation not in {0, 1}:
        raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: PixelRepresentation must be 0 or 1.")
    if frames > 1:
        offsets_source = _finite_vector(dataset, "GridFrameOffsetVector", frames)
    else:
        offsets_source = np.asarray([0.0], dtype=float)
    position_tolerance = GEOMETRY_TOLERANCES["position_and_offset_mm"]
    # DICOM permits absolute patient-Z offsets only for the canonical axial
    # orientation.  Oblique grids must use offsets relative to Image Position.
    axial = np.max(np.abs(orientation - np.asarray([1, 0, 0, 0, 1, 0], dtype=float))) <= GEOMETRY_TOLERANCES["orientation_component_comparison"]
    if abs(float(offsets_source[0])) <= position_tolerance:
        convention = "relative_from_image_position_patient"
        offsets = offsets_source.copy()
    elif axial and abs(float(offsets_source[0] - origin[2])) <= position_tolerance:
        convention = "absolute_patient_z_axial"
        offsets = offsets_source - origin[2]
    else:
        raise DoseGeometryError(
            "BLOCK_RTDOSE_GEOMETRY: GridFrameOffsetVector matches neither the relative nor permitted absolute-Z convention."
        )
    differences = np.diff(offsets)
    if differences.size:
        if np.any(np.abs(differences) <= position_tolerance) or not (
            np.all(differences > 0) or np.all(differences < 0)
        ):
            raise DoseGeometryError("BLOCK_RTDOSE_GEOMETRY: frame offsets must be strictly monotonic.")
        median_spacing = float(np.median(np.abs(differences)))
        if np.max(np.abs(np.abs(differences) - median_spacing)) > _spacing_tolerance(median_spacing):
            raise DoseGeometryError(
                "BLOCK_NONUNIFORM_RTDOSE_GRID: non-uniform frame spacing is outside the validated scalar-volume contract."
            )
    else:
        median_spacing = 1.0
    if validate_pixels:
        try:
            pixels = dataset.pixel_array
        except Exception as exc:
            raise DoseGeometryError(f"BLOCK_RTDOSE_GEOMETRY: pixel data cannot be decoded: {exc}") from exc
        expected_shape = (frames, rows, columns) if frames > 1 else (rows, columns)
        if tuple(pixels.shape) != expected_shape:
            raise DoseGeometryError(
                f"BLOCK_RTDOSE_GEOMETRY: decoded pixel shape {tuple(pixels.shape)} differs from {expected_shape}."
            )
    # source_frame_index is the audit link between a physical position and the
    # corresponding source array frame, including descending-offset datasets.
    frame_records = [
        {
            "source_frame_index": index,
            "source_offset_value": float(offsets_source[index]),
            "relative_offset_mm": float(offsets[index]),
            "image_position_patient": (origin + offsets[index] * normal).tolist(),
        }
        for index in range(frames)
    ]
    spacing_zyx = np.asarray([median_spacing, pixel_spacing[0], pixel_spacing[1]], dtype=float)
    isotropic = bool(
        np.max(np.abs(spacing_zyx - spacing_zyx[0]))
        <= max(_spacing_tolerance(float(value)) for value in spacing_zyx)
    )
    return {
        "normalisation_version": GEOMETRY_NORMALISATION_VERSION,
        "tolerances": dict(GEOMETRY_TOLERANCES),
        "grid_frame_offset_vector_convention": convention,
        "source_frame_order_preserved": True,
        "canonical_frame_permutation": list(range(frames)),
        "frames": frame_records,
        "origin": origin,
        "row_dir": column_direction,
        "col_dir": row_direction,
        "normal": normal,
        "offsets": offsets,
        "source_offsets": offsets_source,
        "spacing": pixel_spacing,
        "spacing_zyx_mm": spacing_zyx,
        "uniform_frame_spacing": True,
        "isotropic": isotropic,
        "anisotropic": not isotropic,
        "shape": (frames, rows, columns),
        "dose_grid_scaling": scaling,
    }


def serialise_geometry(value: dict[str, Any]) -> dict[str, Any]:
    """Convert NumPy geometry fields into manifest-safe Python containers."""
    return {
        key: item.tolist() if isinstance(item, np.ndarray) else list(item) if isinstance(item, tuple) else item
        for key, item in value.items()
    }


def validate_classic_image_series(datasets: list[Any]) -> dict[str, Any]:
    """Validate classic image series and raise a controlled error when requirements are not met."""
    if len(datasets) < 2:
        raise DoseGeometryError("BLOCK_IMAGE_GEOMETRY: a complete classic planning-image series requires at least two images.")
    first_orientation = _finite_vector(datasets[0], "ImageOrientationPatient", 6)
    first_spacing = _finite_vector(datasets[0], "PixelSpacing", 2)
    first_rows = _positive_int(datasets[0], "Rows")
    first_columns = _positive_int(datasets[0], "Columns")
    first_column_direction = first_orientation[:3]
    first_row_direction = first_orientation[3:]
    if abs(float(np.linalg.norm(first_column_direction)) - 1.0) > GEOMETRY_TOLERANCES["orientation_vector_norm"]:
        raise DoseGeometryError("BLOCK_IMAGE_GEOMETRY: first orientation vector is not unit length.")
    if abs(float(np.linalg.norm(first_row_direction)) - 1.0) > GEOMETRY_TOLERANCES["orientation_vector_norm"]:
        raise DoseGeometryError("BLOCK_IMAGE_GEOMETRY: second orientation vector is not unit length.")
    if abs(float(np.dot(first_column_direction, first_row_direction))) > GEOMETRY_TOLERANCES["orientation_orthogonality_dot"]:
        raise DoseGeometryError("BLOCK_IMAGE_GEOMETRY: orientation vectors are not orthogonal.")
    orientation_tolerance = GEOMETRY_TOLERANCES["orientation_component_comparison"]
    positions: list[np.ndarray] = []
    for index, dataset in enumerate(datasets):
        orientation = _finite_vector(dataset, "ImageOrientationPatient", 6)
        spacing = _finite_vector(dataset, "PixelSpacing", 2)
        if np.max(np.abs(orientation - first_orientation)) > orientation_tolerance:
            raise DoseGeometryError(f"BLOCK_IMAGE_GEOMETRY: orientation differs at planning image {index}.")
        for current, expected in zip(spacing, first_spacing):
            if abs(float(current - expected)) > _spacing_tolerance(float(expected)):
                raise DoseGeometryError(f"BLOCK_IMAGE_GEOMETRY: pixel spacing differs at planning image {index}.")
        if _positive_int(dataset, "Rows") != first_rows or _positive_int(dataset, "Columns") != first_columns:
            raise DoseGeometryError(f"BLOCK_IMAGE_GEOMETRY: pixel dimensions differ at planning image {index}.")
        positions.append(_finite_vector(dataset, "ImagePositionPatient", 3))
    column_direction = first_orientation[:3]
    row_direction = first_orientation[3:]
    normal = np.cross(column_direction, row_direction)
    normal /= np.linalg.norm(normal)
    projected = np.asarray([float(position @ normal) for position in positions])
    ordered = np.sort(projected)
    differences = np.diff(ordered)
    position_tolerance = GEOMETRY_TOLERANCES["position_and_offset_mm"]
    if np.any(differences <= position_tolerance):
        raise DoseGeometryError("BLOCK_IMAGE_GEOMETRY: planning-image positions are duplicate or non-separable.")
    median = float(np.median(differences))
    if np.max(np.abs(differences - median)) > _spacing_tolerance(median):
        raise DoseGeometryError("BLOCK_IMAGE_GEOMETRY: planning-image slice spacing is non-uniform.")
    return {
        "orientation": first_orientation.tolist(),
        "pixel_spacing_mm": first_spacing.tolist(),
        "slice_spacing_mm": median,
        "dimensions": [len(datasets), first_rows, first_columns],
        "tolerances": dict(GEOMETRY_TOLERANCES),
    }
