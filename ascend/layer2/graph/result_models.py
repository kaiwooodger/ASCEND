"""Immutable result contracts for additive Layer 2.2 analyses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ASCEND_LAYER22_VERTEX_PROFILE_SCHEMA = "1.0"
ASCEND_LAYER22_SADDLE_GRAPH_SCHEMA = "1.0"
VERTEX_PROFILE_ALGORITHM_VERSION = "ASCEND-L2.2-vertex-profile-v1.0"
SADDLE_GRAPH_ALGORITHM_VERSION = "ASCEND-L2.2-saddle-graph-v1.0"


@dataclass(frozen=True)
class RadialShell:
    shell_index: int
    radius_mm: float
    inner_radius_mm: float
    outer_radius_mm: float
    mean_dose_gy: float
    median_dose_gy: float
    q25_dose_gy: float
    q75_dose_gy: float
    standard_deviation_gy: float
    interquartile_range_gy: float
    minimum_dose_gy: float
    maximum_dose_gy: float
    d10_gy: float
    d90_gy: float
    voxel_count: int
    sampled_volume_cc: float
    gtv_fraction: float
    other_vertex_fraction: float
    dose_domain_fraction_estimate: float
    corrected_profile: float | None = None
    radial_gradient_gy_per_mm: float | None = None
    anisotropy_index: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VertexProfileResult:
    case_id: str
    vertex_id: str
    vertex_roi_number: int | None
    geometric_centroid_xyz_mm: tuple[float, float, float] | None
    dose_weighted_centroid_xyz_mm: tuple[float, float, float] | None
    maximum_dose_xyz_mm: tuple[float, float, float] | None
    centroid_to_dose_centroid_mm: float | None
    centroid_to_maximum_mm: float | None
    geometric_volume_cc: float | None
    geometric_equivalent_diameter_mm: float | None
    core_d50_gy: float | None
    background_d50_gy: float | None
    background_iqr_gy: float | None
    background_voxel_count: int
    modulation_amplitude_gy: float | None
    r80_mm: float | None
    r50_mm: float | None
    r20_mm: float | None
    dosimetric_diameter_mm: float | None
    diameter_difference_mm: float | None
    diameter_ratio: float | None
    penumbra_80_20_mm: float | None
    mean_gradient_80_20_gy_per_mm: float | None
    maximum_gradient_gy_per_mm: float | None
    maximum_gradient_radius_mm: float | None
    normalised_gradient_per_mm: float | None
    profile_anisotropy: dict[str, Any]
    nearest_neighbour_distance_mm: float | None
    maximum_profile_radius_mm: float | None
    shell_width_mm: float
    profile_status: str
    warnings: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

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
