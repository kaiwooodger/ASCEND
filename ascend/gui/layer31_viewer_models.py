"""Display contracts for the Layer 3.1 viewer.

The records bind stored upstream fields and validated geometry to presentation.
They contain no radiobiological calculations.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

import numpy as np

from ascend.layer3.spatial_biology import SpatialBiologyField
from ascend.layer3.visualization import BiologicalMeshResult
from ascend.visualization.biology.models import BiologicalVolume


@dataclass
class Layer31ViewerData:
    fields: dict[str, np.ndarray]
    field_metadata: dict[str, dict[str, Any]]
    masks: dict[str, np.ndarray]
    geometry: dict[str, Any]
    result: dict[str, Any]
    case_root: Path
    spatial_fields: dict[str, SpatialBiologyField] = dataclass_field(default_factory=dict)
    vertex_centres_lps_mm: dict[str, tuple[float, float, float]] = dataclass_field(default_factory=dict)
    graph_edges_lps_mm: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = ()
    biological_volumes: dict[str, BiologicalVolume] = dataclass_field(default_factory=dict)


@dataclass
class CADSceneBundle:
    """Display-only collection of anatomical and biological CAD surfaces."""
    anatomy_meshes: dict[str, BiologicalMeshResult]
    overlay_mesh: BiologicalMeshResult | None
    overlay_target: str | None
    overlay_field_id: str | None
    overlay_label: str | None
    overlay_units: str | None
    smoothing_enabled: bool
    failures: dict[str, str]
    mode: str = "SURFACE"
    special_meshes: dict[str, BiologicalMeshResult] = dataclass_field(default_factory=dict)
    show_contours: bool = False
    gtv_opacity: float = 0.96
    oar_opacity: float = 0.25
    isosurface_opacity: float = 0.45
    vertex_centres_lps_mm: tuple[tuple[float, float, float], ...] = ()
    graph_edges_lps_mm: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = ()
    biological_volume: BiologicalVolume | None = None
    selected_region_name: str = "Region: Whole GTV"
    scalar_range: tuple[float, float] | None = None
    isosurface_thresholds: tuple[float, ...] = ()
    volume_opacity: float = 0.65
    volume_opacity_preset: str = "biological_effect"
    cut_axis: str = "axial"
    cut_fraction: float = 0.5
    cut_inverted: bool = False
    cut_azimuth_degrees: float = 0.0
    cut_elevation_degrees: float = 0.0


@dataclass(frozen=True)
class CADProjectionOptions:
    """Coherent, presentation-only settings for one CAD projection request."""

    smoothing: dict[str, Any] | None = None
    scalar_range: tuple[float, float] | None = None
    display_mode: str = "SURFACE"
    cut_axis: str = "axial"
    cut_fraction: float = 0.5
    cut_inverted: bool = False
    cut_azimuth_degrees: float = 0.0
    cut_elevation_degrees: float = 0.0
    isosurface_thresholds: tuple[float, ...] = ()
    show_contours: bool = False
    gtv_opacity: float = 0.96
    oar_opacity: float = 0.25
    isosurface_opacity: float = 0.45
    show_vertex_centres: bool = False
    show_graph: bool = False
    selected_region_name: str = "Region: Whole GTV"
    volume_opacity_preset: str = "biological_effect"
