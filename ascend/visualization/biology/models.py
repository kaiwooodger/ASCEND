"""Immutable scientific handoff and explicit display state.

ASCEND arrays are stored as ``values[z, y, x]``. Geometry spacing and direction
are expressed in VTK order ``(x, y, z)``. The affine maps an ``(x, y, z)``
continuous voxel coordinate to DICOM patient LPS millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


class BiologicalEndpoint(Enum):
    PHYSICAL_DOSE = "physical_dose"
    SBED = "sbed"
    SEQD2 = "seqd2"
    MLQ_SF = "mlq_sf"
    MLQ_EFFECT = "mlq_effect"


ENDPOINT_METADATA: Mapping[BiologicalEndpoint, Mapping[str, str]] = MappingProxyType({
    BiologicalEndpoint.PHYSICAL_DOSE: MappingProxyType({"display_name": "Physical Dose", "units": "Gy", "colormap": "turbo"}),
    BiologicalEndpoint.SBED: MappingProxyType({"display_name": "Spatial BED", "units": "Gy", "colormap": "viridis"}),
    BiologicalEndpoint.SEQD2: MappingProxyType({"display_name": "Spatial EQD2", "units": "Gy", "colormap": "viridis"}),
    BiologicalEndpoint.MLQ_SF: MappingProxyType({"display_name": "MLQ Survival Fraction", "units": "", "colormap": "magma_r"}),
    BiologicalEndpoint.MLQ_EFFECT: MappingProxyType({"display_name": "MLQ Biological Effect", "units": "", "colormap": "magma"}),
})


class BiologicalRenderMode(Enum):
    SURFACE = "surface"
    VOLUME = "volume"
    ISOSURFACE = "isosurface"
    SLICE = "slice"
    COMBINED = "combined"


class BiologicalRegion(Enum):
    WHOLE_TUMOUR = "whole_tumour"
    VERTEX = "vertex"
    VALLEY = "valley"
    OAR = "oar"
    CUSTOM_ROI = "custom_roi"
    ALL_VALID_TISSUE = "all_valid_tissue"


class ColourMappingMode(Enum):
    ABSOLUTE = "absolute"
    PERCENTILE = "percentile"
    ROI_SPECIFIC = "roi_specific"
    LOCKED_COMPARISON = "locked_comparison"


def _immutable_array(value: np.ndarray, *, dtype: Any | None = None) -> np.ndarray:
    result = np.array(value, dtype=dtype, copy=True, order="C")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class VolumeGeometry:
    """One validated patient-space geometry for an authoritative voxel grid."""

    shape: tuple[int, int, int]
    origin_mm: np.ndarray
    spacing_mm: np.ndarray
    direction: np.ndarray
    affine: np.ndarray | None = None
    coordinate_system: str = "DICOM patient LPS"

    def __post_init__(self) -> None:
        shape = tuple(int(item) for item in self.shape)
        origin = np.asarray(self.origin_mm, dtype=np.float64)
        spacing = np.asarray(self.spacing_mm, dtype=np.float64)
        direction = np.asarray(self.direction, dtype=np.float64)
        if len(shape) != 3 or any(item <= 0 for item in shape):
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
        if origin.shape != (3,) or not np.isfinite(origin).all():
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
        if spacing.shape != (3,) or not np.isfinite(spacing).all() or np.any(spacing <= 0.0):
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
        if direction.shape != (3, 3) or not np.isfinite(direction).all():
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
        gram = direction.T @ direction
        determinant = float(np.linalg.det(direction))
        if not np.allclose(gram, np.eye(3), atol=1.0e-6) or not np.isclose(abs(determinant), 1.0, atol=1.0e-6):
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
        expected = np.eye(4, dtype=np.float64)
        expected[:3, :3] = direction @ np.diag(spacing)
        expected[:3, 3] = origin
        affine = expected if self.affine is None else np.asarray(self.affine, dtype=np.float64)
        if affine.shape != (4, 4) or not np.isfinite(affine).all() or abs(float(np.linalg.det(affine))) <= 1.0e-12:
            raise ValueError("BIOLOGICAL_VOLUME_AFFINE_INVALID")
        if not np.allclose(affine, expected, atol=1.0e-7):
            raise ValueError("BIOLOGICAL_VOLUME_AFFINE_INVALID")
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "origin_mm", _immutable_array(origin))
        object.__setattr__(self, "spacing_mm", _immutable_array(spacing))
        object.__setattr__(self, "direction", _immutable_array(direction))
        object.__setattr__(self, "affine", _immutable_array(affine))

    @property
    def dimensions_xyz(self) -> tuple[int, int, int]:
        return self.shape[2], self.shape[1], self.shape[0]

    def voxel_to_patient(self, points_zyx: np.ndarray) -> np.ndarray:
        points = np.asarray(points_zyx, dtype=np.float64)
        single = points.ndim == 1
        points = np.atleast_2d(points)
        if points.shape[1] != 3 or not np.isfinite(points).all():
            raise ValueError("BIOLOGICAL_VOXEL_COORDINATE_INVALID")
        xyz = points[:, ::-1]
        result = xyz @ self.affine[:3, :3].T + self.affine[:3, 3]
        return result[0] if single else result

    def patient_to_voxel(self, points_mm: np.ndarray) -> np.ndarray:
        points = np.asarray(points_mm, dtype=np.float64)
        single = points.ndim == 1
        points = np.atleast_2d(points)
        if points.shape[1] != 3 or not np.isfinite(points).all():
            raise ValueError("BIOLOGICAL_PATIENT_COORDINATE_INVALID")
        inverse = np.linalg.inv(self.affine)
        xyz = (points - self.affine[:3, 3]) @ inverse[:3, :3].T
        result = xyz[:, ::-1]
        return result[0] if single else result

    @property
    def bounds_mm(self) -> tuple[float, float, float, float, float, float]:
        last = np.asarray(self.shape, dtype=float) - 1.0
        corners = np.asarray([(z, y, x) for z in (0.0, last[0]) for y in (0.0, last[1]) for x in (0.0, last[2])])
        points = self.voxel_to_patient(corners)
        low, high = np.min(points, axis=0), np.max(points, axis=0)
        return float(low[0]), float(high[0]), float(low[1]), float(high[1]), float(low[2]), float(high[2])


@dataclass(frozen=True)
class BiologicalVolume:
    """Immutable authoritative biological result consumed by renderers."""

    values: np.ndarray
    endpoint: BiologicalEndpoint
    geometry: VolumeGeometry
    units: str
    valid_mask: np.ndarray | None = None
    tissue_mask: np.ndarray | None = None
    vertex_mask: np.ndarray | None = None
    valley_mask: np.ndarray | None = None
    roi_masks: Mapping[str, np.ndarray] = field(default_factory=dict)
    roi_name: str | None = None
    tissue_type: str | None = None
    treatment_components: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, BiologicalEndpoint):
            raise ValueError("BIOLOGICAL_ENDPOINT_UNSUPPORTED")
        values = np.asarray(self.values)
        if values.ndim != 3 or values.shape != self.geometry.shape:
            raise ValueError("BIOLOGICAL_VOLUME_SHAPE_MISMATCH")
        valid = np.isfinite(values) if self.valid_mask is None else np.asarray(self.valid_mask, dtype=bool)
        if valid.shape != values.shape:
            raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
        if not np.isfinite(values[valid]).all():
            raise ValueError("BIOLOGICAL_VOLUME_NONFINITE")
        expected_units = ENDPOINT_METADATA[self.endpoint]["units"]
        if self.endpoint in {BiologicalEndpoint.MLQ_SF, BiologicalEndpoint.MLQ_EFFECT}:
            if self.units not in {"", "fraction", "dimensionless", "dimensionless effect"}:
                raise ValueError("BIOLOGICAL_ENDPOINT_METADATA_INVALID")
        elif not self.units or not (self.units == expected_units or self.units.startswith("Gy")):
            raise ValueError("BIOLOGICAL_ENDPOINT_METADATA_INVALID")
        if self.endpoint is BiologicalEndpoint.MLQ_SF and (np.any(values[valid] < 0.0) or np.any(values[valid] > 1.0)):
            raise ValueError("BIOLOGICAL_VOLUME_VALUE_DOMAIN_INVALID")
        object.__setattr__(self, "values", _immutable_array(values))
        object.__setattr__(self, "valid_mask", _immutable_array(valid, dtype=bool))
        for name in ("tissue_mask", "vertex_mask", "valley_mask"):
            mask = getattr(self, name)
            if mask is not None:
                mask_array = np.asarray(mask, dtype=bool)
                if mask_array.shape != values.shape:
                    raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
                object.__setattr__(self, name, _immutable_array(mask_array, dtype=bool))
        immutable_rois: dict[str, np.ndarray] = {}
        for name, mask in self.roi_masks.items():
            mask_array = np.asarray(mask, dtype=bool)
            if mask_array.shape != values.shape:
                raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
            immutable_rois[str(name)] = _immutable_array(mask_array, dtype=bool)
        object.__setattr__(self, "roi_masks", MappingProxyType(immutable_rois))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "treatment_components", tuple(map(str, self.treatment_components)))

    @property
    def scalar_name(self) -> str:
        return self.endpoint.value

    def mask_for(self, region: BiologicalRegion, custom_roi: str | None = None) -> np.ndarray:
        if region is BiologicalRegion.ALL_VALID_TISSUE:
            return self.valid_mask
        if region is BiologicalRegion.WHOLE_TUMOUR:
            mask = self.tissue_mask
        elif region is BiologicalRegion.VERTEX:
            mask = self.vertex_mask
        elif region is BiologicalRegion.VALLEY:
            mask = self.valley_mask
        elif region in {BiologicalRegion.OAR, BiologicalRegion.CUSTOM_ROI}:
            mask = self.roi_masks.get(custom_roi or self.roi_name or "")
        else:
            mask = None
        if mask is None:
            raise ValueError("BIOLOGICAL_REGION_MASK_MISSING")
        return self.valid_mask & mask


@dataclass(frozen=True)
class BiologyRenderTolerance:
    coordinate_mm: float = 1.0e-5
    scalar_absolute: float = 1.0e-6
    scalar_relative: float = 1.0e-6
    surface_valid_fraction: float = 0.98
    minimum_bounds_overlap_fraction: float = 1.0e-6


@dataclass(frozen=True)
class BiologicalRenderState:
    endpoint: BiologicalEndpoint = BiologicalEndpoint.SBED
    mode: BiologicalRenderMode = BiologicalRenderMode.COMBINED
    region: BiologicalRegion = BiologicalRegion.WHOLE_TUMOUR
    custom_roi: str | None = None
    scalar_min: float | None = None
    scalar_max: float | None = None
    opacity: float = 0.75
    volume_opacity_preset: str = "biological_effect"
    tumour_visible: bool = True
    vertices_visible: bool = True
    valleys_visible: bool = False
    oars_visible: bool = False
    isosurfaces: tuple[float, ...] = ()
    scale_locked: bool = False

    def updated(self, **changes: Any) -> "BiologicalRenderState":
        result = replace(self, **changes)
        if not 0.0 <= result.opacity <= 1.0:
            raise ValueError("BIOLOGICAL_DISPLAY_OPACITY_INVALID")
        if result.scalar_min is not None and result.scalar_max is not None and result.scalar_max <= result.scalar_min:
            raise ValueError("BIOLOGICAL_DISPLAY_RANGE_INVALID")
        if any(not np.isfinite(value) for value in result.isosurfaces):
            raise ValueError("BIOLOGICAL_ISOSURFACE_THRESHOLD_INVALID")
        return result
