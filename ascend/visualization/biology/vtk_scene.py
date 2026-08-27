"""PyVista/VTK actors for all five rendering modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pyvista as pv

from .colour_scale import BiologicalColourScale
from .anatomy_colours import anatomy_colour_map
from .mesh_sampler import SurfaceSamplingResult, sample_biological_volume_on_surface
from .models import BiologicalRenderMode, BiologicalRenderState, BiologicalVolume
from .volume_adapter import masked_pyvista_volume


@dataclass(frozen=True)
class BiologicalSceneResult:
    actors: Mapping[str, object]
    datasets: Mapping[str, pv.DataSet]
    warnings: tuple[str, ...]


def _display_grid(
    volume: BiologicalVolume,
    mask: np.ndarray,
    scale: BiologicalColourScale,
) -> tuple[pv.ImageData, float]:
    grid = masked_pyvista_volume(volume, mask)
    scalars = np.asarray(grid.point_data[volume.scalar_name], dtype=float).copy()
    # NaN remains authoritative in masked_pyvista_volume. The VTK-only buffer
    # uses a sentinel below the scientific range. This keeps the mask fully
    # transparent without sacrificing the lowest valid MLQ-SF values, whose
    # opacity is intentionally high because low survival means high effect.
    width = max(scale.maximum - scale.minimum, abs(scale.minimum) * 1.0e-6, 1.0e-6)
    sentinel = float(scale.minimum - width / 255.0)
    scalars[~np.isfinite(scalars)] = sentinel
    grid.point_data[volume.scalar_name] = scalars
    return grid, sentinel


def _contours(grid: pv.ImageData, volume: BiologicalVolume, thresholds: tuple[float, ...]) -> pv.PolyData:
    if not thresholds:
        return pv.PolyData()
    result = grid.contour(isosurfaces=list(thresholds), scalars=volume.scalar_name)
    result.field_data["ascend_thresholds_are_visualisation_only"] = np.asarray([1], dtype=np.uint8)
    return result


def render_biological_scene(
    plotter: pv.Plotter,
    volume: BiologicalVolume,
    state: BiologicalRenderState,
    scale: BiologicalColourScale,
    *,
    mask: np.ndarray,
    anatomical_surfaces: Mapping[str, pv.PolyData] | None = None,
    biological_surface: pv.PolyData | None = None,
    vertex_centres_mm: np.ndarray | None = None,
    slice_origin_mm: np.ndarray | None = None,
    slice_normal: np.ndarray | None = None,
    preserve_camera: bool = True,
) -> BiologicalSceneResult:
    """Replace scene actors without touching any biological calculation."""
    camera = plotter.camera_position if preserve_camera and plotter.renderer.actors else None
    plotter.clear()
    actors: dict[str, object] = {}
    datasets: dict[str, pv.DataSet] = {}
    warnings: list[str] = []
    grid, transparent_sentinel = _display_grid(volume, mask, scale)
    datasets["volume"] = grid
    clim = (transparent_sentinel, scale.maximum)
    mode = state.mode

    if state.tumour_visible:
        structure_colours = anatomy_colour_map((anatomical_surfaces or {}).keys())
        for name, surface in (anatomical_surfaces or {}).items():
            opacity = 0.18 if "tumour" in name.lower() or "gtv" in name.lower() else 0.10
            actors[f"anatomy::{name}"] = plotter.add_mesh(
                surface, color=structure_colours[name],
                opacity=opacity, name=f"anatomy::{name}", smooth_shading=True,
            )
            datasets[f"anatomy::{name}"] = surface

    if mode is BiologicalRenderMode.SURFACE:
        if biological_surface is None:
            raise ValueError("BIOLOGICAL_SURFACE_MISSING")
        sampled: SurfaceSamplingResult = sample_biological_volume_on_surface(volume, biological_surface)
        warnings.extend(sampled.warnings)
        datasets["biological_surface"] = sampled.surface
        actors["biological_surface"] = plotter.add_mesh(
            sampled.surface, scalars=volume.scalar_name, clim=clim, cmap=scale.colormap,
            opacity=state.opacity, name="biological_surface", nan_color="#777777", show_scalar_bar=False,
        )

    if mode in {BiologicalRenderMode.VOLUME, BiologicalRenderMode.COMBINED}:
        actors["biological_volume"] = plotter.add_volume(
            grid, scalars=volume.scalar_name, clim=clim, cmap=scale.colormap,
            opacity=list(scale.opacity_function), opacity_unit_distance=float(np.min(volume.geometry.spacing_mm)),
            shade=False, blending="composite", name="biological_volume", show_scalar_bar=False,
        )

    if mode in {BiologicalRenderMode.ISOSURFACE, BiologicalRenderMode.COMBINED}:
        contours = _contours(grid, volume, state.isosurfaces)
        datasets["isosurfaces"] = contours
        if contours.n_points:
            actors["isosurfaces"] = plotter.add_mesh(
                contours, scalars=volume.scalar_name, clim=clim, cmap=scale.colormap,
                opacity=state.opacity, smooth_shading=True, name="isosurfaces", show_scalar_bar=False,
            )

    if mode is BiologicalRenderMode.SLICE:
        centre = volume.geometry.voxel_to_patient((np.asarray(volume.geometry.shape) - 1.0) / 2.0)
        if slice_origin_mm is not None and slice_normal is not None:
            slices = grid.slice(normal=np.asarray(slice_normal, dtype=float), origin=np.asarray(slice_origin_mm, dtype=float))
        else:
            slices = grid.slice_orthogonal(x=float(centre[0]), y=float(centre[1]), z=float(centre[2]))
        datasets["orthogonal_slices"] = slices
        actors["orthogonal_slices"] = plotter.add_mesh(
            slices, scalars=volume.scalar_name, clim=clim, cmap=scale.colormap,
            opacity=state.opacity, name="orthogonal_slices", show_scalar_bar=False,
        )

    centres = np.asarray(vertex_centres_mm if vertex_centres_mm is not None else [], dtype=float)
    if state.vertices_visible and centres.size:
        points = pv.PolyData(centres.reshape(-1, 3))
        glyphs = points.glyph(geom=pv.Sphere(radius=max(float(np.min(volume.geometry.spacing_mm)), 0.5)), scale=False)
        datasets["vertex_centres"] = glyphs
        actors["vertex_centres"] = plotter.add_mesh(glyphs, color="#ffe13a", name="vertex_centres")

    plotter.add_scalar_bar(
        title=f"{volume.endpoint.value} [{volume.units}]" if volume.units else volume.endpoint.value,
        n_labels=5,
        color="#f4f8fc",
        title_font_size=14,
        label_font_size=12,
        fmt="%.3g",
    )
    if camera is not None:
        plotter.camera_position = camera
    else:
        plotter.reset_camera()
    return BiologicalSceneResult(actors, datasets, tuple(dict.fromkeys(warnings)))
