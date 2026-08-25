"""Fail-closed rendering gates and patient-space overlap diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

import numpy as np

from .models import BiologicalVolume, BiologyRenderTolerance


class BiologyRenderGate(Enum):
    VOLUME_AVAILABLE = "volume_available"
    GEOMETRY_VALID = "geometry_valid"
    FINITE_VALUES = "finite_values"
    MASK_VALID = "mask_valid"
    PATIENT_SPACE_VALID = "patient_space_valid"
    SURFACE_OVERLAP_VALID = "surface_overlap_valid"
    SURFACE_SAMPLING_VALID = "surface_sampling_valid"
    ENDPOINT_METADATA_VALID = "endpoint_metadata_valid"


class BiologicalRenderError(RuntimeError):
    def __init__(self, code: str, diagnostics: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostics = dict(diagnostics or {})


@dataclass(frozen=True)
class RenderValidationReport:
    passed: frozenset[BiologyRenderGate]
    warnings: tuple[str, ...]
    diagnostics: Mapping[str, Any]

    def require(self, *gates: BiologyRenderGate) -> None:
        missing = [gate.value for gate in gates if gate not in self.passed]
        if missing:
            raise BiologicalRenderError("BIOLOGICAL_RENDER_GATE_FAILED", {"missing_gates": missing, **self.diagnostics})


def validate_volume(volume: BiologicalVolume) -> RenderValidationReport:
    valid = np.asarray(volume.valid_mask, dtype=bool)
    values = np.asarray(volume.values)
    warnings: list[str] = []
    passed = {
        BiologyRenderGate.VOLUME_AVAILABLE,
        BiologyRenderGate.GEOMETRY_VALID,
        BiologyRenderGate.MASK_VALID,
        BiologyRenderGate.PATIENT_SPACE_VALID,
        BiologyRenderGate.ENDPOINT_METADATA_VALID,
    }
    if np.isfinite(values[valid]).all():
        passed.add(BiologyRenderGate.FINITE_VALUES)
    else:
        raise BiologicalRenderError("BIOLOGICAL_VOLUME_NONFINITE")
    if not valid.any():
        raise BiologicalRenderError("BIOLOGICAL_VOLUME_MISSING", {"valid_voxels": 0})
    finite_values = values[valid]
    true_min, true_max = float(np.min(finite_values)), float(np.max(finite_values))
    robust_min, robust_max = map(float, np.percentile(finite_values, (2.0, 98.0)))
    if robust_min > true_min or robust_max < true_max:
        warnings.append("DISPLAY_PERCENTILE_CLIPPING_ACTIVE")
    diagnostics = {
        "endpoint": volume.endpoint.value,
        "units": volume.units,
        "shape_zyx": volume.geometry.shape,
        "dimensions_xyz": volume.geometry.dimensions_xyz,
        "origin_mm": volume.geometry.origin_mm.tolist(),
        "spacing_xyz_mm": volume.geometry.spacing_mm.tolist(),
        "direction": volume.geometry.direction.tolist(),
        "volume_bounds_mm": volume.geometry.bounds_mm,
        "coordinate_system": volume.geometry.coordinate_system,
        "valid_voxels": int(valid.sum()),
        "true_minimum": true_min,
        "true_maximum": true_max,
        "robust_minimum": robust_min,
        "robust_maximum": robust_max,
    }
    return RenderValidationReport(frozenset(passed), tuple(warnings), diagnostics)


def _bounds_array(bounds: tuple[float, float, float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(bounds[::2], dtype=float), np.asarray(bounds[1::2], dtype=float)


def validate_mesh_overlap(
    volume: BiologicalVolume,
    mesh_points_mm: np.ndarray,
    *,
    tolerance: BiologyRenderTolerance = BiologyRenderTolerance(),
) -> RenderValidationReport:
    base = validate_volume(volume)
    points = np.asarray(mesh_points_mm, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise BiologicalRenderError("BIOLOGICAL_MESH_GEOMETRY_INVALID")
    mesh_low, mesh_high = np.min(points, axis=0), np.max(points, axis=0)
    volume_low, volume_high = _bounds_array(volume.geometry.bounds_mm)
    intersection = np.maximum(np.minimum(mesh_high, volume_high) - np.maximum(mesh_low, volume_low), 0.0)
    mesh_extent = np.maximum(mesh_high - mesh_low, tolerance.coordinate_mm)
    overlap_fraction = float(np.prod(intersection) / np.prod(mesh_extent))
    diagnostics = {
        **base.diagnostics,
        "mesh_bounds_mm": (mesh_low.tolist(), mesh_high.tolist()),
        "mesh_centroid_mm": np.mean(points, axis=0).tolist(),
        "bounds_overlap_fraction": overlap_fraction,
    }
    if not np.all(intersection > 0.0) or overlap_fraction < tolerance.minimum_bounds_overlap_fraction:
        raise BiologicalRenderError("BIOLOGICAL_RENDER_GEOMETRY_MISMATCH", diagnostics)
    return RenderValidationReport(
        base.passed | {BiologyRenderGate.SURFACE_OVERLAP_VALID},
        base.warnings,
        diagnostics,
    )
