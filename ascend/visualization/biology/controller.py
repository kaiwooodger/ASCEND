"""Scene-state controller; Qt issues commands here instead of touching VTK."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Mapping

import numpy as np
import pyvista as pv

from .colour_scale import BiologicalColourScale, BiologicalColourScaleManager, endpoint_opacity
from .models import (
    BiologicalEndpoint, BiologicalRegion, BiologicalRenderMode,
    BiologicalRenderState, BiologicalVolume, ColourMappingMode,
)
from .slice_renderer import BiologicalSlice, biological_slice
from .statistics import BiologicalProbe, RegionStatistics, probe_volumes, region_statistics
from .validation import RenderValidationReport, validate_volume
from .volume_adapter import biological_volume_to_pyvista
from .vtk_scene import BiologicalSceneResult, render_biological_scene


LOGGER = logging.getLogger(__name__)


def _same_geometry(first: BiologicalVolume, second: BiologicalVolume) -> bool:
    left, right = first.geometry, second.geometry
    return (
        left.shape == right.shape
        and left.coordinate_system == right.coordinate_system
        and np.allclose(left.affine, right.affine, rtol=0.0, atol=1.0e-7)
    )


class BiologicalRenderController:
    """Owns cached display representations of immutable endpoint volumes."""

    def __init__(self) -> None:
        self.state = BiologicalRenderState()
        self._volumes: dict[BiologicalEndpoint, BiologicalVolume] = {}
        self._reports: dict[BiologicalEndpoint, RenderValidationReport] = {}
        self._grids: dict[BiologicalEndpoint, pv.ImageData] = {}
        self._statistics: dict[tuple[BiologicalEndpoint, BiologicalRegion, str | None], RegionStatistics] = {}
        self._scales = BiologicalColourScaleManager()
        self._scene: BiologicalSceneResult | None = None

    @property
    def volume(self) -> BiologicalVolume:
        try:
            return self._volumes[self.state.endpoint]
        except KeyError as exc:
            raise ValueError("BIOLOGICAL_VOLUME_MISSING") from exc

    @property
    def validation_report(self) -> RenderValidationReport:
        return self._reports[self.state.endpoint]

    def load_volume(self, volume: BiologicalVolume) -> None:
        if self._volumes and not _same_geometry(next(iter(self._volumes.values())), volume):
            raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_MISMATCH")
        report = validate_volume(volume)
        self._volumes[volume.endpoint] = volume
        self._reports[volume.endpoint] = report
        self._grids[volume.endpoint] = biological_volume_to_pyvista(volume)
        self._statistics = {key: value for key, value in self._statistics.items() if key[0] is not volume.endpoint}
        if len(self._volumes) == 1 or self.state.endpoint not in self._volumes:
            self.state = self.state.updated(endpoint=volume.endpoint)
        LOGGER.info(
            "Biological endpoint loaded endpoint=%s shape=%s spacing=%s origin=%s range=(%s,%s) valid_voxels=%s",
            volume.endpoint.value, volume.geometry.shape, volume.geometry.spacing_mm.tolist(),
            volume.geometry.origin_mm.tolist(), report.diagnostics["true_minimum"],
            report.diagnostics["true_maximum"], report.diagnostics["valid_voxels"],
        )

    def load_volumes(self, volumes: Mapping[BiologicalEndpoint, BiologicalVolume]) -> None:
        for endpoint, volume in volumes.items():
            if endpoint is not volume.endpoint:
                raise ValueError("BIOLOGICAL_ENDPOINT_METADATA_INVALID")
            self.load_volume(volume)

    def set_endpoint(self, endpoint: BiologicalEndpoint) -> None:
        if endpoint not in self._volumes:
            raise ValueError("BIOLOGICAL_VOLUME_MISSING")
        self.state = self.state.updated(endpoint=endpoint)

    def set_region(self, region: BiologicalRegion, custom_roi: str | None = None) -> None:
        self.volume.mask_for(region, custom_roi)
        self.state = self.state.updated(region=region, custom_roi=custom_roi)

    def set_render_mode(self, mode: BiologicalRenderMode) -> None:
        self.state = self.state.updated(mode=mode)

    def set_clim(self, low: float, high: float, *, lock: bool = True) -> None:
        self.state = self.state.updated(scalar_min=float(low), scalar_max=float(high), scale_locked=lock)

    def rescale(self) -> None:
        self._scales.unlock(self.state.endpoint)
        self.state = self.state.updated(scalar_min=None, scalar_max=None, scale_locked=False)

    def set_opacity(self, opacity: float, preset: str | None = None) -> None:
        changes = {"opacity": float(opacity)}
        if preset is not None:
            changes["volume_opacity_preset"] = str(preset)
        self.state = self.state.updated(**changes)

    def set_structure_visible(self, name: str, visible: bool) -> None:
        attribute = {"tumour": "tumour_visible", "vertices": "vertices_visible", "valleys": "valleys_visible", "oars": "oars_visible"}.get(name.lower())
        if attribute is None:
            raise ValueError("BIOLOGICAL_STRUCTURE_UNSUPPORTED")
        self.state = self.state.updated(**{attribute: bool(visible)})

    def set_vertex_centres_visible(self, visible: bool) -> None:
        self.set_structure_visible("vertices", visible)

    def set_valley_visible(self, visible: bool) -> None:
        self.set_structure_visible("valleys", visible)

    def set_isosurfaces(self, values: tuple[float, ...]) -> None:
        self.state = self.state.updated(isosurfaces=tuple(sorted(set(map(float, values)))))

    def colour_scale(self) -> BiologicalColourScale:
        mask = self.volume.mask_for(self.state.region, self.state.custom_roi)
        absolute = None
        mode = ColourMappingMode.PERCENTILE
        if self.state.scalar_min is not None and self.state.scalar_max is not None:
            absolute = (self.state.scalar_min, self.state.scalar_max)
            mode = ColourMappingMode.LOCKED_COMPARISON if self.state.scale_locked else ColourMappingMode.ABSOLUTE
        scale = self._scales.resolve(self.volume, mask=mask, mode=mode, absolute=absolute, lock=self.state.scale_locked)
        return replace(scale, opacity_function=endpoint_opacity(self.volume.endpoint, preset=self.state.volume_opacity_preset))

    def render(
        self,
        plotter: pv.Plotter,
        *,
        anatomical_surfaces: Mapping[str, pv.PolyData] | None = None,
        biological_surface: pv.PolyData | None = None,
        vertex_centres_mm: np.ndarray | None = None,
        slice_origin_mm: np.ndarray | None = None,
        slice_normal: np.ndarray | None = None,
    ) -> BiologicalSceneResult:
        mask = self.volume.mask_for(self.state.region, self.state.custom_roi)
        self._scene = render_biological_scene(
            plotter, self.volume, self.state, self.colour_scale(), mask=mask,
            anatomical_surfaces=anatomical_surfaces, biological_surface=biological_surface,
            vertex_centres_mm=vertex_centres_mm, slice_origin_mm=slice_origin_mm,
            slice_normal=slice_normal,
        )
        LOGGER.info("Biological scene rendered endpoint=%s mode=%s warnings=%s", self.state.endpoint.value, self.state.mode.value, self._scene.warnings)
        return self._scene

    def slice(self, orientation: str, index: int) -> BiologicalSlice:
        return biological_slice(self.volume, orientation, index, self.volume.mask_for(self.state.region, self.state.custom_roi))

    def probe(self, patient_mm: np.ndarray) -> BiologicalProbe:
        return probe_volumes(self._volumes, patient_mm)

    def statistics(self) -> RegionStatistics:
        key = (self.state.endpoint, self.state.region, self.state.custom_roi)
        if key not in self._statistics:
            self._statistics[key] = region_statistics(self.volume, self.volume.mask_for(self.state.region, self.state.custom_roi))
        return self._statistics[key]

    def clear_scene(self, plotter: pv.Plotter | None = None) -> None:
        if plotter is not None:
            plotter.clear()
        self._scene = None

    @staticmethod
    def reset_camera(plotter: pv.Plotter) -> None:
        plotter.reset_camera()
