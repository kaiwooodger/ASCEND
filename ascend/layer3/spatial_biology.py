"""Canonical downstream data and sampling contracts for Layer 3.1A displays.

This module never derives BED or EQD2.  It validates and samples an already
authoritative voxel field in DICOM patient LPS coordinates for presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

from ascend.layer3.nonlocal_effect.spatial import indices_to_lps


SUPPORTED_QUANTITIES = {"PHYSICAL_DOSE", "S_BED", "S_EQD2"}


def _geometry_arrays(geometry: Mapping[str, Any]) -> dict[str, np.ndarray]:
    spacing = geometry.get("spacing", geometry.get("in_plane_spacing_mm"))
    raw = {
        "origin": geometry.get("origin"),
        "row_direction": geometry.get("row_direction", geometry.get("row_dir")),
        "column_direction": geometry.get("column_direction", geometry.get("col_dir")),
        "normal": geometry.get("normal"),
        "offsets": geometry.get("offsets"),
        "spacing": spacing,
        "shape": geometry.get("shape"),
    }
    if any(value is None for value in raw.values()):
        raise ValueError("INVALID_BIOLOGY_GRID_GEOMETRY")
    values = {key: np.asarray(value, dtype=float) for key, value in raw.items()}
    if values["origin"].shape != (3,) or values["row_direction"].shape != (3,) or values["column_direction"].shape != (3,):
        raise ValueError("INVALID_BIOLOGY_GRID_GEOMETRY")
    if values["normal"].shape != (3,) or values["spacing"].reshape(-1).size not in (2, 3):
        raise ValueError("INVALID_BIOLOGY_GRID_GEOMETRY")
    if not all(np.isfinite(item).all() for item in values.values()):
        raise ValueError("INVALID_BIOLOGY_GRID_GEOMETRY")
    values["pixel_spacing"] = values["spacing"].reshape(-1)[-2:]
    if np.any(values["pixel_spacing"] <= 0) or values["offsets"].size != int(values["shape"][0]):
        raise ValueError("INVALID_BIOLOGY_GRID_GEOMETRY")
    return values


def world_to_voxel_lps(points_lps_mm: np.ndarray, geometry: Mapping[str, Any]) -> np.ndarray:
    """Convert DICOM-LPS points to continuous array coordinates ``z,y,x``."""
    values = _geometry_arrays(geometry)
    points = np.asarray(points_lps_mm, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("INVALID_WORLD_COORDINATES")
    relative = points - values["origin"]
    physical_offsets = relative @ values["normal"]
    offsets = values["offsets"].reshape(-1)
    source = np.arange(offsets.size, dtype=float)
    if offsets.size > 1 and offsets[0] > offsets[-1]:
        z = np.interp(physical_offsets, offsets[::-1], source[::-1], left=np.nan, right=np.nan)
    else:
        z = np.interp(physical_offsets, offsets, source, left=np.nan, right=np.nan)
    x = (relative @ values["row_direction"]) / values["pixel_spacing"][1]
    y = (relative @ values["column_direction"]) / values["pixel_spacing"][0]
    return np.column_stack((z, y, x))


def voxel_to_world_lps(points_zyx: np.ndarray, geometry: Mapping[str, Any]) -> np.ndarray:
    """Convert continuous array coordinates to DICOM patient LPS millimetres."""
    _geometry_arrays(geometry)
    return np.asarray(indices_to_lps(np.asarray(points_zyx, dtype=float), dict(geometry)), dtype=float)


def voxel_spacing_zyx_mm(geometry: Mapping[str, Any]) -> np.ndarray:
    values = _geometry_arrays(geometry)
    offsets = values["offsets"].reshape(-1)
    z_spacing = float(np.median(np.abs(np.diff(offsets)))) if len(offsets) > 1 else (
        float(values["spacing"].reshape(-1)[0]) if values["spacing"].size == 3 else 1.0
    )
    return np.asarray([z_spacing, *values["pixel_spacing"]], dtype=float)


@dataclass(frozen=True)
class SpatialBiologyField:
    """Self-contained authoritative spatial-field handoff for a viewer."""

    values_3d: np.ndarray
    quantity: str
    units: str
    origin_lps_mm: tuple[float, float, float]
    spacing_mm: tuple[float, float, float]
    direction_matrix: tuple[tuple[float, float, float], ...]
    shape: tuple[int, int, int]
    valid_mask: np.ndarray
    tissue_label_map: np.ndarray | None
    roi_masks: Mapping[str, np.ndarray]
    model_name: str
    alpha_beta_metadata: Mapping[str, Any] = field(default_factory=dict)
    fractionation_metadata: Mapping[str, Any] = field(default_factory=dict)
    treatment_components: tuple[Mapping[str, Any], ...] = ()
    source_dose_uids: tuple[str, ...] = ()
    field_id: str = ""

    def __post_init__(self) -> None:
        values = np.asarray(self.values_3d)
        valid = np.asarray(self.valid_mask, dtype=bool)
        if self.quantity not in SUPPORTED_QUANTITIES:
            raise ValueError("BIOLOGY_FIELD_QUANTITY_UNSUPPORTED")
        if values.ndim != 3 or tuple(values.shape) != tuple(self.shape) or valid.shape != values.shape:
            raise ValueError("INVALID_BIOLOGY_GRID_GEOMETRY")
        if not self.units:
            raise ValueError("BIOLOGY_FIELD_UNITS_MISSING")
        if not np.isfinite(values[valid]).all():
            raise ValueError("NONFINITE_BIOLOGY_FIELD")
        if self.tissue_label_map is not None and np.asarray(self.tissue_label_map).shape != values.shape:
            raise ValueError("MISSING_TISSUE_PARAMETER_MAP")
        for mask in self.roi_masks.values():
            if np.asarray(mask).shape != values.shape:
                raise ValueError("ROI_FIELD_MISMATCH")

    @property
    def geometry(self) -> dict[str, Any]:
        # Direction rows are array z/y/x axes represented in LPS.
        direction = np.asarray(self.direction_matrix, dtype=float)
        offsets = np.arange(self.shape[0], dtype=float) * float(self.spacing_mm[0])
        return {
            "origin": list(self.origin_lps_mm), "normal": direction[0].tolist(),
            "column_direction": direction[1].tolist(), "row_direction": direction[2].tolist(),
            "offsets": offsets.tolist(), "spacing": [self.spacing_mm[1], self.spacing_mm[2]],
            "shape": list(self.shape),
        }


@dataclass(frozen=True)
class SurfaceSamplingResult:
    values: np.ndarray
    valid: np.ndarray
    sampled_points_lps_mm: np.ndarray
    voxel_indices_zyx: np.ndarray
    sampling_distance_mm: np.ndarray
    method: str = "mask_aware_trilinear_inward_normal"


def _nearest_mask_label(mask: np.ndarray, indices: np.ndarray) -> np.ndarray:
    shape = np.asarray(mask.shape)
    valid = np.isfinite(indices).all(axis=1) & np.all(indices >= 0, axis=1) & np.all(indices <= shape - 1, axis=1)
    result = np.zeros(len(indices), dtype=bool)
    if valid.any():
        rounded = np.rint(indices[valid]).astype(int)
        result[valid] = mask[rounded[:, 0], rounded[:, 1], rounded[:, 2]]
    return result


def _masked_trilinear(values: np.ndarray, mask: np.ndarray, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trilinear interpolation using only corners in the expected tissue."""
    shape = np.asarray(values.shape)
    inside = np.isfinite(indices).all(axis=1) & np.all(indices >= 0, axis=1) & np.all(indices <= shape - 1, axis=1)
    sampled = np.full(len(indices), np.nan, dtype=np.float32)
    tissue_weight = np.zeros(len(indices), dtype=float)
    if not inside.any():
        return sampled, np.zeros(len(indices), dtype=bool)
    active = np.flatnonzero(inside); points = indices[active]
    lower = np.floor(points).astype(int); upper = np.minimum(lower + 1, shape - 1)
    fraction = points - lower
    numerator = np.zeros(len(active), dtype=float); denominator = np.zeros(len(active), dtype=float)
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                corner = np.column_stack((
                    np.where(dz, upper[:, 0], lower[:, 0]),
                    np.where(dy, upper[:, 1], lower[:, 1]),
                    np.where(dx, upper[:, 2], lower[:, 2]),
                ))
                weight = (
                    (fraction[:, 0] if dz else 1.0 - fraction[:, 0])
                    * (fraction[:, 1] if dy else 1.0 - fraction[:, 1])
                    * (fraction[:, 2] if dx else 1.0 - fraction[:, 2])
                )
                allowed = mask[corner[:, 0], corner[:, 1], corner[:, 2]]
                effective = weight * allowed
                numerator += effective * values[corner[:, 0], corner[:, 1], corner[:, 2]]
                denominator += effective
    valid = denominator > 1.0e-8
    sampled[active[valid]] = (numerator[valid] / denominator[valid]).astype(np.float32)
    tissue_weight[active] = denominator
    # Require the nearest label to match and nearly all interpolation support
    # to be in the expected tissue. This prevents boundary mixing.
    accepted = inside & _nearest_mask_label(mask, indices) & (tissue_weight >= 0.999)
    sampled[~accepted] = np.nan
    return sampled, accepted


def sample_surface_inward(
    values: np.ndarray,
    mask: np.ndarray,
    vertices_lps_mm: np.ndarray,
    normals_lps: np.ndarray,
    geometry: Mapping[str, Any],
    *,
    max_offset_voxels: float = 1.0,
) -> SurfaceSamplingResult:
    """Sample a closed ROI surface without interpolating across tissue labels."""
    field_values = np.asarray(values, dtype=np.float64)
    expected = np.asarray(mask, dtype=bool)
    vertices = np.asarray(vertices_lps_mm, dtype=float)
    normals = np.asarray(normals_lps, dtype=float)
    if field_values.shape != expected.shape or vertices.shape != normals.shape or vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError("ROI_FIELD_MISMATCH")
    if not np.isfinite(field_values).all():
        raise ValueError("NONFINITE_BIOLOGY_FIELD")
    count = len(vertices)
    result_values = np.full(count, np.nan, dtype=np.float32)
    result_points = np.full((count, 3), np.nan, dtype=float)
    result_indices = np.full((count, 3), np.nan, dtype=float)
    result_distance = np.full(count, np.nan, dtype=float)
    unresolved = np.ones(count, dtype=bool)
    minimum_spacing = float(np.min(voxel_spacing_zyx_mm(geometry)))
    steps = np.linspace(0.25, max_offset_voxels, max(int(round(max_offset_voxels / 0.25)), 1)) * minimum_spacing
    # Marching-cubes normal orientation is implementation-dependent. Try both
    # directions, accepting only positions that remain inside the ROI.
    for distance in steps:
        for sign in (-1.0, 1.0):
            active = np.flatnonzero(unresolved)
            if not active.size:
                break
            candidates = vertices[active] + sign * distance * normals[active]
            indices = world_to_voxel_lps(candidates, geometry)
            sampled, valid = _masked_trilinear(field_values, expected, indices)
            if valid.any():
                selected = active[valid]
                result_values[selected] = sampled[valid]
                result_points[selected] = candidates[valid]
                result_indices[selected] = indices[valid]
                result_distance[selected] = distance
                unresolved[selected] = False
    # Explicit nearest-valid-ROI fallback within one voxel. Zero is never used
    # as an invalid marker; unresolved samples remain NaN.
    active = np.flatnonzero(unresolved)
    if active.size:
        base_indices = world_to_voxel_lps(vertices[active], geometry)
        finite_rows = np.isfinite(base_indices).all(axis=1)
        active = active[finite_rows]; base_indices = base_indices[finite_rows]
    if active.size:
        rounded = np.rint(base_indices).astype(int)
        offsets = np.asarray([(z, y, x) for z in (-1, 0, 1) for y in (-1, 0, 1) for x in (-1, 0, 1)], dtype=int)
        candidates = rounded[:, None, :] + offsets[None, :, :]
        shape = np.asarray(expected.shape)
        in_grid = np.all(candidates >= 0, axis=2) & np.all(candidates < shape, axis=2)
        clipped = np.clip(candidates, 0, shape - 1)
        allowed = in_grid & expected[clipped[..., 0], clipped[..., 1], clipped[..., 2]]
        distances = np.linalg.norm((candidates - base_indices[:, None, :]) * voxel_spacing_zyx_mm(geometry), axis=2)
        distances[~allowed] = np.inf
        choice = np.argmin(distances, axis=1); has_choice = np.isfinite(distances[np.arange(len(active)), choice])
        if has_choice.any():
            selected = active[has_choice]; chosen = candidates[np.arange(len(active)), choice][has_choice]
            result_values[selected] = field_values[chosen[:, 0], chosen[:, 1], chosen[:, 2]].astype(np.float32)
            result_indices[selected] = chosen
            result_points[selected] = voxel_to_world_lps(chosen, geometry)
            result_distance[selected] = np.linalg.norm(result_points[selected] - vertices[selected], axis=1)
            unresolved[selected] = False
    return SurfaceSamplingResult(
        result_values, ~unresolved, result_points, result_indices, result_distance,
    )


@dataclass(frozen=True)
class MeshAlignmentReport:
    status: str
    coverage_percent: float
    valid_samples: int
    total_samples: int
    median_sampling_distance_mm: float | None
    maximum_sampling_distance_mm: float | None
    coordinate_frame: str
    mesh_bounding_box_lps_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    grid_bounding_box_lps_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    roi_bounding_box_lps_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    error_code: str | None


def _grid_box(geometry: Mapping[str, Any], shape: tuple[int, int, int]) -> np.ndarray:
    last = np.asarray(shape, dtype=float) - 1.0
    corners = np.asarray([(z, y, x) for z in (0.0, last[0]) for y in (0.0, last[1]) for x in (0.0, last[2])])
    points = voxel_to_world_lps(corners, geometry)
    return np.asarray([np.min(points, axis=0), np.max(points, axis=0)])


def _mask_box(mask: np.ndarray, geometry: Mapping[str, Any]) -> np.ndarray:
    coordinates = np.argwhere(mask)
    if not coordinates.size:
        raise ValueError("ROI_FIELD_MISMATCH")
    low, high = coordinates.min(axis=0), coordinates.max(axis=0)
    corners = np.asarray([(z, y, x) for z in (low[0], high[0]) for y in (low[1], high[1]) for x in (low[2], high[2])])
    points = voxel_to_world_lps(corners, geometry)
    return np.asarray([np.min(points, axis=0), np.max(points, axis=0)])


def validate_mesh_alignment(
    vertices_lps_mm: np.ndarray,
    geometry: Mapping[str, Any],
    roi_mask: np.ndarray,
    sampling: SurfaceSamplingResult,
    *,
    green_threshold_percent: float = 99.0,
    block_threshold_percent: float = 95.0,
) -> MeshAlignmentReport:
    vertices = np.asarray(vertices_lps_mm, dtype=float)
    if not len(vertices) or not np.isfinite(vertices).all():
        raise ValueError("CAD_FRAME_ALIGNMENT_FAILED")
    mesh_box = np.asarray([np.min(vertices, axis=0), np.max(vertices, axis=0)])
    grid_box = _grid_box(geometry, tuple(np.asarray(roi_mask).shape))
    roi_box = _mask_box(np.asarray(roi_mask, dtype=bool), geometry)
    overlap = np.all(np.minimum(mesh_box[1], grid_box[1]) >= np.maximum(mesh_box[0], grid_box[0]))
    total = len(vertices); valid = int(np.asarray(sampling.valid, dtype=bool).sum())
    coverage = 100.0 * valid / max(total, 1)
    finite_distances = np.asarray(sampling.sampling_distance_mm)[np.asarray(sampling.valid, dtype=bool)]
    if not overlap or coverage < block_threshold_percent:
        status, error = "BLOCK", "CAD_FRAME_ALIGNMENT_FAILED" if not overlap else "SURFACE_SAMPLING_COVERAGE_FAILED"
    elif coverage < green_threshold_percent:
        status, error = "AMBER", None
    else:
        status, error = "GREEN", None
    return MeshAlignmentReport(
        status, coverage, valid, total,
        float(np.median(finite_distances)) if finite_distances.size else None,
        float(np.max(finite_distances)) if finite_distances.size else None,
        "DICOM patient LPS", tuple(map(tuple, mesh_box)), tuple(map(tuple, grid_box)), tuple(map(tuple, roi_box)), error,
    )


@dataclass
class BiologyViewerState:
    active_quantity: str = "S_BED"
    active_roi: str = "Region: Whole GTV"
    active_component: str = "TOTAL"
    active_region: str = "WHOLE_GTV"
    display_mode: str = "SURFACE"
    display_min: float | None = None
    display_max: float | None = None
    range_mode: str = "ROBUST"
    color_map: str = "viridis"
    gtv_opacity: float = 1.0
    oar_opacity: float = 0.24
    isosurface_opacity: float = 0.42
    show_vertices: bool = True
    show_valleys: bool = True
    show_oars: bool = True
    show_contours: bool = False
    show_isosurfaces: bool = False
    clipping_axis: str = "axial"
    clipping_fraction: float = 0.5
    clipping_inverted: bool = False
    selected_world_position_lps: tuple[float, float, float] | None = None


class BiologyColorScaleController:
    """One colour-range policy shared by 2D, CAD, legend and inspector."""

    def __init__(self, mode: str = "ROBUST", percentiles: tuple[float, float] = (2.0, 98.0)) -> None:
        self.mode = mode; self.percentiles = percentiles

    def resolve(
        self,
        field: SpatialBiologyField,
        *,
        roi_mask: np.ndarray | None = None,
        manual: tuple[float, float] | None = None,
    ) -> tuple[float, float]:
        valid = np.asarray(field.valid_mask, dtype=bool)
        if roi_mask is not None:
            valid &= np.asarray(roi_mask, dtype=bool)
        values = np.asarray(field.values_3d, dtype=float)[valid]
        values = values[np.isfinite(values)]
        if not values.size:
            raise ValueError("BIOLOGY_FIELD_UNAVAILABLE")
        mode = self.mode.upper()
        if mode == "MANUAL":
            if manual is None or not np.isfinite(manual).all() or manual[1] <= manual[0]:
                raise ValueError("INVALID_DISPLAY_RANGE")
            return float(manual[0]), float(manual[1])
        if mode == "FULL RANGE":
            return float(np.min(values)), float(np.max(values))
        if mode in {"ROBUST", "PERCENTILE"}:
            low, high = np.percentile(values, self.percentiles)
            return float(low), float(high)
        raise ValueError("INVALID_DISPLAY_RANGE_MODE")
