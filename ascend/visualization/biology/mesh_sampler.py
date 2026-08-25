"""Volume-to-surface sampling; no biological quantity is calculated here."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyvista as pv

from .models import BiologicalVolume, BiologyRenderTolerance
from .validation import BiologicalRenderError, BiologyRenderGate, validate_mesh_overlap
from .volume_adapter import VALID_MASK_NAME, biological_volume_to_pyvista


@dataclass(frozen=True)
class SurfaceSamplingResult:
    surface: pv.PolyData
    values: np.ndarray
    valid: np.ndarray
    total_vertices: int
    valid_vertices: int
    invalid_vertices: int
    valid_fraction: float
    warnings: tuple[str, ...]


def sample_biological_volume_on_surface(
    volume: BiologicalVolume,
    surface: pv.PolyData,
    *,
    tolerance: BiologyRenderTolerance = BiologyRenderTolerance(),
) -> SurfaceSamplingResult:
    if not isinstance(surface, pv.PolyData) or surface.n_points == 0:
        raise BiologicalRenderError("BIOLOGICAL_MESH_GEOMETRY_INVALID")
    overlap = validate_mesh_overlap(volume, np.asarray(surface.points), tolerance=tolerance)
    overlap.require(BiologyRenderGate.SURFACE_OVERLAP_VALID)
    sampled = surface.sample(
        biological_volume_to_pyvista(volume),
        pass_point_data=False,
        pass_cell_data=False,
        categorical=False,
    )
    vtk_valid = np.asarray(sampled["vtkValidPointMask"], dtype=bool)
    source_valid = np.asarray(sampled[VALID_MASK_NAME], dtype=float) >= 0.999
    valid = vtk_valid & source_valid
    values = np.asarray(sampled[volume.scalar_name], dtype=np.float64)
    values[~valid] = np.nan
    sampled.point_data[volume.scalar_name] = values
    sampled.point_data["ascendBiologicalSampleValid"] = valid.astype(np.uint8)
    total = int(surface.n_points)
    valid_count = int(valid.sum())
    fraction = valid_count / total
    warnings = ("PARTIAL_SURFACE_SAMPLING",) if valid_count != total else ()
    if fraction < tolerance.surface_valid_fraction:
        raise BiologicalRenderError(
            "BIOLOGICAL_SURFACE_VALIDITY_TOO_LOW",
            {
                **overlap.diagnostics,
                "total_vertices": total,
                "valid_vertices": valid_count,
                "invalid_vertices": total - valid_count,
                "valid_fraction": fraction,
                "required_valid_fraction": tolerance.surface_valid_fraction,
            },
        )
    return SurfaceSamplingResult(sampled, values, valid, total, valid_count, total - valid_count, fraction, warnings)


def polydata_from_triangles(points_mm: np.ndarray, faces: np.ndarray) -> pv.PolyData:
    points = np.asarray(points_mm, dtype=float)
    triangles = np.asarray(faces, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 3 or triangles.ndim != 2 or triangles.shape[1] != 3:
        raise BiologicalRenderError("BIOLOGICAL_MESH_GEOMETRY_INVALID")
    vtk_faces = np.column_stack((np.full(len(triangles), 3, dtype=np.int64), triangles)).ravel()
    return pv.PolyData(points, vtk_faces)
