"""Native-grid physical-coordinate primitives for Layer 2.2 extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


class SpatialValidationError(ValueError):
    """Reject an invalid dose grid before physical sampling."""


@dataclass(frozen=True)
class GridGeometry:
    """Immutable RTDOSE geometry in DICOM patient LPS coordinates."""

    origin_lps_mm: tuple[float, float, float]
    row_direction: tuple[float, float, float]
    column_direction: tuple[float, float, float]
    normal_direction: tuple[float, float, float]
    frame_offsets_mm: tuple[float, ...]
    row_spacing_mm: float
    column_spacing_mm: float
    shape_zyx: tuple[int, int, int]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GridGeometry":
        shape = tuple(int(item) for item in value["shape"])
        row = np.asarray(value.get("row_direction", value.get("row_dir")), dtype=float)
        column = np.asarray(value.get("column_direction", value.get("col_dir")), dtype=float)
        normal = np.asarray(value["normal"], dtype=float)
        origin = np.asarray(value["origin"], dtype=float)
        spacing = np.asarray(value["spacing"], dtype=float)
        offsets = np.asarray(value["offsets"], dtype=float)
        if shape.__len__() != 3 or any(item <= 0 for item in shape):
            raise SpatialValidationError("GEOMETRY_MISMATCH: dose shape must contain three positive dimensions.")
        if any(array.shape != (3,) for array in (origin, row, column, normal)):
            raise SpatialValidationError("GEOMETRY_MISMATCH: patient-coordinate vectors must contain three values.")
        if spacing.shape != (2,) or not np.isfinite(spacing).all() or np.any(spacing <= 0):
            raise SpatialValidationError("INVALID_PHYSICAL_SPACING: in-plane spacing must be finite and positive.")
        if offsets.shape != (shape[0],) or not np.isfinite(offsets).all():
            raise SpatialValidationError("GEOMETRY_MISMATCH: frame offsets do not match the dose frames.")
        if len(offsets) > 1 and (np.any(np.diff(offsets) == 0) or not (np.all(np.diff(offsets) > 0) or np.all(np.diff(offsets) < 0))):
            raise SpatialValidationError("GEOMETRY_MISMATCH: frame offsets must be strictly monotonic.")
        matrix = np.vstack((row, column, normal))
        if not np.isfinite(matrix).all() or not np.allclose(matrix @ matrix.T, np.eye(3), atol=1.0e-5):
            raise SpatialValidationError("GEOMETRY_MISMATCH: dose orientation vectors must be orthonormal.")
        return cls(
            tuple(map(float, origin)), tuple(map(float, row)), tuple(map(float, column)), tuple(map(float, normal)),
            tuple(map(float, offsets)), float(spacing[0]), float(spacing[1]), shape,
        )

    @property
    def origin(self) -> np.ndarray:
        return np.asarray(self.origin_lps_mm, dtype=float)

    @property
    def row(self) -> np.ndarray:
        return np.asarray(self.row_direction, dtype=float)

    @property
    def column(self) -> np.ndarray:
        return np.asarray(self.column_direction, dtype=float)

    @property
    def normal(self) -> np.ndarray:
        return np.asarray(self.normal_direction, dtype=float)

    @property
    def offsets(self) -> np.ndarray:
        return np.asarray(self.frame_offsets_mm, dtype=float)

    @property
    def frame_thicknesses_mm(self) -> np.ndarray:
        offsets = self.offsets
        if len(offsets) == 1:
            return np.asarray([min(self.row_spacing_mm, self.column_spacing_mm)], dtype=float)
        differences = np.abs(np.diff(offsets))
        thicknesses = np.empty(len(offsets), dtype=float)
        thicknesses[0], thicknesses[-1] = differences[0], differences[-1]
        if len(offsets) > 2:
            thicknesses[1:-1] = (differences[:-1] + differences[1:]) / 2.0
        return thicknesses

    @property
    def minimum_spacing_mm(self) -> float:
        return float(min(self.row_spacing_mm, self.column_spacing_mm, float(np.min(self.frame_thicknesses_mm))))

    @property
    def median_voxel_volume_cc(self) -> float:
        return float(np.median(self.frame_thicknesses_mm) * self.row_spacing_mm * self.column_spacing_mm / 1000.0)

    def voxel_volumes_cc(self, z_indices: np.ndarray) -> np.ndarray:
        return self.frame_thicknesses_mm[np.asarray(z_indices, dtype=int)] * self.row_spacing_mm * self.column_spacing_mm / 1000.0

    def points_lps_mm(self, indices_zyx: np.ndarray) -> np.ndarray:
        indices = np.asarray(indices_zyx, dtype=float)
        if indices.ndim != 2 or indices.shape[1] != 3:
            raise SpatialValidationError("GEOMETRY_MISMATCH: voxel indices must be an N×3 array.")
        z = indices[:, 0]
        offsets = np.interp(z, np.arange(len(self.frame_offsets_mm), dtype=float), self.offsets)
        return (
            self.origin[None, :]
            + offsets[:, None] * self.normal[None, :]
            + indices[:, 2, None] * self.column_spacing_mm * self.row[None, :]
            + indices[:, 1, None] * self.row_spacing_mm * self.column[None, :]
        )

    def local_indices(self, centre_lps_mm: np.ndarray, radius_mm: float) -> np.ndarray:
        """Return a conservative native-grid box surrounding a physical sphere."""
        if not np.isfinite(radius_mm) or radius_mm <= 0:
            return np.empty((0, 3), dtype=int)
        centre = np.asarray(centre_lps_mm, dtype=float)
        relative = centre - self.origin
        centre_x = float(relative @ self.row / self.column_spacing_mm)
        centre_y = float(relative @ self.column / self.row_spacing_mm)
        centre_offset = float(relative @ self.normal)
        x_radius = int(np.ceil(radius_mm / self.column_spacing_mm)) + 1
        y_radius = int(np.ceil(radius_mm / self.row_spacing_mm)) + 1
        x = np.arange(max(0, int(np.floor(centre_x)) - x_radius), min(self.shape_zyx[2], int(np.ceil(centre_x)) + x_radius + 1))
        y = np.arange(max(0, int(np.floor(centre_y)) - y_radius), min(self.shape_zyx[1], int(np.ceil(centre_y)) + y_radius + 1))
        z_margin = radius_mm + float(np.max(self.frame_thicknesses_mm))
        z = np.flatnonzero(np.abs(self.offsets - centre_offset) <= z_margin)
        if not len(x) or not len(y) or not len(z):
            return np.empty((0, 3), dtype=int)
        zz, yy, xx = np.meshgrid(z, y, x, indexing="ij")
        return np.column_stack((zz.ravel(), yy.ravel(), xx.ravel())).astype(int, copy=False)


def validate_native_inputs(
    dose_gy: np.ndarray,
    geometry: GridGeometry,
    masks: list[np.ndarray],
    *,
    dose_units: str = "Gy",
) -> None:
    dose = np.asarray(dose_gy)
    if str(dose_units).strip().upper() != "GY":
        raise SpatialValidationError("INVALID_DOSE_UNITS: Layer 2.2 extension calculations require Gy.")
    if dose.shape != geometry.shape_zyx or dose.ndim != 3:
        raise SpatialValidationError("GEOMETRY_MISMATCH: dose and geometry shapes differ.")
    if not np.isfinite(dose).all() or np.any(dose < 0):
        raise SpatialValidationError("INVALID_DOSE_VALUES: dose must be finite and non-negative.")
    if geometry.minimum_spacing_mm <= 0:
        raise SpatialValidationError("INVALID_PHYSICAL_SPACING: dose spacing is not positive.")
    if any(np.asarray(mask).shape != dose.shape for mask in masks):
        raise SpatialValidationError("GEOMETRY_MISMATCH: a structure mask differs from the dose grid.")


def physical_centroid(mask: np.ndarray, geometry: GridGeometry) -> np.ndarray:
    indices = np.argwhere(np.asarray(mask, dtype=bool))
    if not len(indices):
        raise SpatialValidationError("EMPTY_VERTEX_MASK")
    weights = geometry.voxel_volumes_cc(indices[:, 0])
    return np.average(geometry.points_lps_mm(indices), axis=0, weights=weights)


def spherical_selection(centre_lps_mm: np.ndarray, radius_mm: float, geometry: GridGeometry) -> tuple[np.ndarray, np.ndarray]:
    indices = geometry.local_indices(centre_lps_mm, radius_mm)
    if not len(indices):
        return indices, np.empty(0, dtype=bool)
    distances = np.linalg.norm(geometry.points_lps_mm(indices) - np.asarray(centre_lps_mm, dtype=float), axis=1)
    return indices, distances <= float(radius_mm) + 1.0e-9


def capsule_selection(
    start_lps_mm: np.ndarray,
    end_lps_mm: np.ndarray,
    radius_mm: float,
    geometry: GridGeometry,
) -> tuple[np.ndarray, np.ndarray]:
    start = np.asarray(start_lps_mm, dtype=float)
    end = np.asarray(end_lps_mm, dtype=float)
    centre = (start + end) / 2.0
    segment = end - start
    length = float(np.linalg.norm(segment))
    if length <= 0 or not np.isfinite(length):
        return np.empty((0, 3), dtype=int), np.empty(0, dtype=bool)
    indices = geometry.local_indices(centre, length / 2.0 + radius_mm)
    points = geometry.points_lps_mm(indices)
    denominator = float(segment @ segment)
    parameters = np.clip(((points - start) @ segment) / denominator, 0.0, 1.0)
    closest = start[None, :] + parameters[:, None] * segment[None, :]
    return indices, np.linalg.norm(points - closest, axis=1) <= float(radius_mm) + 1.0e-9
