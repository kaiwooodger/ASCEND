"""Immutable result contracts for additive Layer 2.2 analyses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ASCEND_LAYER22_DOSE_GRADIENT_SCHEMA = "1.0"
ASCEND_LAYER22_SADDLE_GRAPH_SCHEMA = "1.0"
DOSE_GRADIENT_ALGORITHM_VERSION = "ASCEND-L2.2-ICRU91-PADDICK-GI-v1.0"
SADDLE_GRAPH_ALGORITHM_VERSION = "ASCEND-L2.2-saddle-graph-v1.0"


@dataclass(frozen=True)
class IsodoseVolumePoint:
    relative_prescription_percent: float
    threshold_dose_gy: float
    isodose_volume_cc: float
    voxel_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SaddleEdgeResult:
    case_id: str
    edge_id: int
    vertex_i_id: str
    vertex_j_id: str
    vertex_i_d50_gy: float | None
    vertex_j_d50_gy: float | None
    edge_peak_d50_gy: float | None
    edge_length_mm: float
    midpoint_xyz_mm: tuple[float, float, float]
    midpoint_d50_gy: float | None
    midpoint_pvdr: float | None
    saddle_xyz_mm: tuple[float, float, float] | None
    raw_saddle_bottleneck_gy: float | None
    saddle_local_d50_gy: float | None
    saddle_pvdr: float | None
    saddle_to_midpoint_mm: float | None
    saddle_path_length_mm: float | None
    midpoint_minus_saddle_gy: float | None
    saddle_minus_midpoint_pvdr: float | None
    corridor_radius_mm: float
    local_sampling_radius_mm: float
    corridor_voxel_count: int
    saddle_roi_voxel_count: int
    edge_status: str
    exclusion_reason: str | None
    warnings: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    saddle_path_xyz_mm: tuple[tuple[float, float, float], ...] = ()
    sensitivity: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
