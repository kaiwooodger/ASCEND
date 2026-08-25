"""Descriptive values queried only from authoritative voxel arrays."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from .models import BiologicalEndpoint, BiologicalVolume


@dataclass(frozen=True)
class RegionStatistics:
    valid_voxels: int
    minimum: float
    maximum: float
    mean: float
    median: float
    standard_deviation: float
    p05: float
    p25: float
    p75: float
    p95: float
    vertex_mean: float | None = None
    vertex_median: float | None = None
    valley_mean: float | None = None
    valley_median: float | None = None
    vertex_valley_contrast: float | None = None


def _masked_values(volume: BiologicalVolume, mask: np.ndarray) -> np.ndarray:
    selected = np.asarray(mask, dtype=bool)
    if selected.shape != volume.values.shape:
        raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
    values = np.asarray(volume.values, dtype=float)[selected & volume.valid_mask]
    return values[np.isfinite(values)]


def region_statistics(volume: BiologicalVolume, mask: np.ndarray) -> RegionStatistics:
    values = _masked_values(volume, mask)
    if not values.size:
        raise ValueError("BIOLOGICAL_REGION_EMPTY")
    percentiles = np.percentile(values, (5, 25, 75, 95))
    vertex = _masked_values(volume, np.asarray(mask, bool) & volume.vertex_mask) if volume.vertex_mask is not None else np.asarray([])
    valley = _masked_values(volume, np.asarray(mask, bool) & volume.valley_mask) if volume.valley_mask is not None else np.asarray([])
    vertex_mean = float(np.mean(vertex)) if vertex.size else None
    valley_mean = float(np.mean(valley)) if valley.size else None
    return RegionStatistics(
        int(values.size), float(np.min(values)), float(np.max(values)), float(np.mean(values)),
        float(np.median(values)), float(np.std(values)), *map(float, percentiles),
        vertex_mean, float(np.median(vertex)) if vertex.size else None,
        valley_mean, float(np.median(valley)) if valley.size else None,
        (vertex_mean / valley_mean) if vertex_mean is not None and valley_mean not in {None, 0.0} else None,
    )


@dataclass(frozen=True)
class BiologicalProbe:
    patient_mm: tuple[float, float, float]
    voxel_zyx: tuple[int, int, int] | None
    values: Mapping[BiologicalEndpoint, float | None]
    region: str
    vertex: bool
    valley: bool


def probe_volumes(volumes: Mapping[BiologicalEndpoint, BiologicalVolume], patient_mm: np.ndarray) -> BiologicalProbe:
    if not volumes:
        raise ValueError("BIOLOGICAL_VOLUME_MISSING")
    reference = next(iter(volumes.values()))
    continuous = np.asarray(reference.geometry.patient_to_voxel(patient_mm), dtype=float)
    index = np.rint(continuous).astype(int)
    inside = np.all(index >= 0) and np.all(index < np.asarray(reference.geometry.shape))
    if not inside:
        return BiologicalProbe(tuple(map(float, patient_mm)), None, {key: None for key in volumes}, "outside", False, False)
    point = tuple(map(int, index))
    queried: dict[BiologicalEndpoint, float | None] = {}
    for endpoint, volume in volumes.items():
        if (
            volume.geometry.shape != reference.geometry.shape
            or volume.geometry.coordinate_system != reference.geometry.coordinate_system
            or not np.allclose(volume.geometry.affine, reference.geometry.affine, rtol=0.0, atol=1.0e-7)
        ):
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_MISMATCH")
        queried[endpoint] = float(volume.values[point]) if volume.valid_mask[point] else None
    vertex = bool(reference.vertex_mask is not None and reference.vertex_mask[point])
    valley = bool(reference.valley_mask is not None and reference.valley_mask[point])
    if vertex:
        region = "vertex"
    elif valley:
        region = "valley"
    elif reference.tissue_mask is not None and reference.tissue_mask[point]:
        region = "tumour"
    else:
        region = next((name for name, mask in reference.roi_masks.items() if mask[point]), "outside")
    return BiologicalProbe(tuple(map(float, patient_mm)), point, queried, region, vertex, valley)
