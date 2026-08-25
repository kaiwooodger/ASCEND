"""The sole NumPy-to-PyVista adapter for authoritative biological volumes."""

from __future__ import annotations

import numpy as np
import pyvista as pv

from .models import BiologicalVolume
from .validation import BiologyRenderGate, validate_volume


VALID_MASK_NAME = "ascend_valid_mask"


def biological_volume_to_pyvista(volume: BiologicalVolume) -> pv.ImageData:
    """Create point-centred ``ImageData`` without transposing the source.

    VTK point ids increase x-fastest. A C-order flatten of an ASCEND ``z,y,x``
    array has exactly that ordering. Dimensions are therefore reversed while
    the scalar buffer is flattened in C order.
    """
    report = validate_volume(volume)
    report.require(
        BiologyRenderGate.VOLUME_AVAILABLE,
        BiologyRenderGate.GEOMETRY_VALID,
        BiologyRenderGate.FINITE_VALUES,
        BiologyRenderGate.MASK_VALID,
        BiologyRenderGate.PATIENT_SPACE_VALID,
        BiologyRenderGate.ENDPOINT_METADATA_VALID,
    )
    geometry = volume.geometry
    grid = pv.ImageData(
        dimensions=geometry.dimensions_xyz,
        spacing=tuple(map(float, geometry.spacing_mm)),
        origin=tuple(map(float, geometry.origin_mm)),
        direction_matrix=np.asarray(geometry.direction),
    )
    grid.point_data[volume.scalar_name] = np.asarray(volume.values).ravel(order="C")
    grid.point_data[VALID_MASK_NAME] = np.asarray(volume.valid_mask, dtype=np.uint8).ravel(order="C")
    grid.field_data["ascend_endpoint"] = np.asarray([volume.endpoint.value])
    grid.field_data["ascend_units"] = np.asarray([volume.units])
    grid.field_data["ascend_coordinate_system"] = np.asarray([geometry.coordinate_system])
    grid.set_active_scalars(volume.scalar_name)
    return grid


def masked_pyvista_volume(volume: BiologicalVolume, mask: np.ndarray) -> pv.ImageData:
    selected = np.asarray(mask, dtype=bool)
    if selected.shape != volume.values.shape:
        raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
    grid = biological_volume_to_pyvista(volume).copy(deep=True)
    display = np.asarray(volume.values, dtype=np.float64).copy()
    display[~(selected & volume.valid_mask)] = np.nan
    grid.point_data[volume.scalar_name] = display.ravel(order="C")
    grid.point_data[VALID_MASK_NAME] = (selected & volume.valid_mask).astype(np.uint8).ravel(order="C")
    return grid


def sample_patient_points(volume: BiologicalVolume, points_mm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    points = np.asarray(points_mm, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("BIOLOGICAL_PATIENT_COORDINATE_INVALID")
    poly = pv.PolyData(points)
    sampled = poly.sample(biological_volume_to_pyvista(volume), pass_point_data=False, pass_cell_data=False)
    valid = np.asarray(sampled["vtkValidPointMask"], dtype=bool) & (np.asarray(sampled[VALID_MASK_NAME], dtype=float) >= 0.999)
    values = np.asarray(sampled[volume.scalar_name], dtype=float)
    values[~valid] = np.nan
    return values, valid
