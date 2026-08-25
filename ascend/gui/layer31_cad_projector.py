"""Display-only CAD projection for validated Layer 3.1 scalar fields.

This module may construct and smooth render meshes.  It never recalculates a
biological endpoint or mutates the authoritative scalar arrays.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ascend.gui.layer31_viewer_models import CADProjectionOptions, CADSceneBundle, Layer31ViewerData
from ascend.layer3.visualization import BiologicalMeshResult, build_biological_mesh


def build_cad_scene_bundle(
    data: Layer31ViewerData,
    anatomy_names: tuple[str, ...],
    overlay_field_id: str | None,
    options: CADProjectionOptions,
) -> CADSceneBundle:
    """Build CAD surfaces outside the GUI thread without scientific recalculation."""
    smoothing = options.smoothing
    scalar_range = options.scalar_range
    display_mode = options.display_mode
    cut_axis = options.cut_axis
    cut_fraction = options.cut_fraction
    cut_inverted = options.cut_inverted
    cut_azimuth_degrees = options.cut_azimuth_degrees
    cut_elevation_degrees = options.cut_elevation_degrees
    isosurface_thresholds = options.isosurface_thresholds
    show_contours = options.show_contours
    gtv_opacity = options.gtv_opacity
    oar_opacity = options.oar_opacity
    isosurface_opacity = options.isosurface_opacity
    show_vertex_centres = options.show_vertex_centres
    show_graph = options.show_graph
    selected_region_name = options.selected_region_name
    volume_opacity_preset = options.volume_opacity_preset
    anatomy: dict[str, BiologicalMeshResult] = {}
    failures: dict[str, str] = {}
    physical = data.fields["physical_course_dose_gy"]
    for name in anatomy_names:
        mask = np.asarray(data.masks[name], dtype=bool)
        if not mask.any():
            failures[name] = "empty_validated_mask"
            continue
        result = build_biological_mesh(
            data.case_root, f"anatomy::{name}", mask, physical, data.geometry,
            "physical_course_dose_gy", "Gy", smoothing, None,
            export_formats=("stl",), expected_tissue_mask=mask,
        )
        if result.status == "PASS":
            anatomy[name] = result
        else:
            failures[name] = str(result.reason or "surface_unavailable")
    overlay_target = "Region: Whole GTV" if (
        "Region: Whole GTV" in data.masks and np.asarray(data.masks["Region: Whole GTV"]).any()
    ) else next((name for name in anatomy_names if not name.startswith("OAR:")), None)
    overlay: BiologicalMeshResult | None = None
    special: dict[str, BiologicalMeshResult] = {}
    if overlay_field_id and overlay_target and overlay_target in data.masks:
        meta = data.field_metadata[overlay_field_id]
        gtv_mask = np.asarray(data.masks[overlay_target], dtype=bool)
        mode = display_mode.upper()
        if mode == "CUTAWAY":
            axis = {"axial": 0, "coronal": 1, "sagittal": 2}.get(cut_axis, 0)
            normal = np.zeros(3, dtype=float); normal[axis] = 1.0
            azimuth = np.deg2rad(float(cut_azimuth_degrees)); elevation = np.deg2rad(float(cut_elevation_degrees))
            rotate_z = np.asarray([[np.cos(azimuth), -np.sin(azimuth), 0.0], [np.sin(azimuth), np.cos(azimuth), 0.0], [0.0, 0.0, 1.0]])
            rotate_y = np.asarray([[np.cos(elevation), 0.0, np.sin(elevation)], [0.0, 1.0, 0.0], [-np.sin(elevation), 0.0, np.cos(elevation)]])
            normal = rotate_z @ rotate_y @ normal
            coordinates = np.indices(gtv_mask.shape, dtype=float)
            projection = np.tensordot(normal, coordinates, axes=(0, 0))
            inside_projection = projection[gtv_mask]
            threshold = float(np.quantile(inside_projection, np.clip(float(cut_fraction), 0.0, 1.0)))
            retained = projection >= threshold if cut_inverted else projection <= threshold
            display_mask = gtv_mask & retained
            roi_id = (
                f"biological-cutaway::{overlay_target}::{cut_axis}::{threshold:.6g}::"
                f"{cut_azimuth_degrees:.3g}::{cut_elevation_degrees:.3g}::{int(cut_inverted)}"
            )
        else:
            display_mask = gtv_mask
            roi_id = f"biological-overlay::{overlay_target}"
        if mode in {"SURFACE", "CUTAWAY"}:
            overlay = build_biological_mesh(
                data.case_root, roi_id, display_mask,
                data.fields[overlay_field_id], data.geometry, overlay_field_id,
                str(meta["units"]), smoothing, scalar_range, export_formats=("vtp", "stl"),
                expected_tissue_mask=gtv_mask,
            )
            if overlay.status != "PASS":
                failures[f"overlay::{overlay_target}"] = str(overlay.reason or "surface_unavailable")
                overlay = None
        if mode in {"ISOSURFACE", "COMBINED"}:
            for threshold in isosurface_thresholds[:4]:
                threshold_mask = gtv_mask & np.isfinite(data.fields[overlay_field_id]) & (data.fields[overlay_field_id] >= float(threshold))
                if not threshold_mask.any() or np.array_equal(threshold_mask, gtv_mask):
                    failures[f"isosurface::{threshold:g}"] = "threshold_region_empty_or_whole_roi"
                    continue
                item = build_biological_mesh(
                    data.case_root, f"biological-isosurface::{overlay_target}::{threshold:.8g}", threshold_mask,
                    data.fields[overlay_field_id], data.geometry, overlay_field_id,
                    str(meta["units"]), smoothing, scalar_range, export_formats=("vtp", "stl"),
                    expected_tissue_mask=gtv_mask,
                )
                if item.status == "PASS": special[f"≥ {threshold:g} {meta['units']}"] = item
                else: failures[f"isosurface::{threshold:g}"] = str(item.reason or "surface_unavailable")
    meta = data.field_metadata.get(overlay_field_id or "", {})
    return CADSceneBundle(
        anatomy_meshes=anatomy, overlay_mesh=overlay, overlay_target=overlay_target,
        overlay_field_id=overlay_field_id,
        overlay_label=str(meta.get("label")) if meta else None,
        overlay_units=str(meta.get("units")) if meta else None,
        smoothing_enabled=bool((smoothing or {}).get("method", "taubin_non_shrinking") != "none"),
        failures=failures, mode=display_mode.upper(), special_meshes=special,
        show_contours=bool(show_contours), gtv_opacity=float(np.clip(gtv_opacity, 0.0, 1.0)),
        oar_opacity=float(np.clip(oar_opacity, 0.0, 1.0)),
        isosurface_opacity=float(np.clip(isosurface_opacity, 0.0, 1.0)),
        vertex_centres_lps_mm=tuple(data.vertex_centres_lps_mm.values()) if show_vertex_centres else (),
        graph_edges_lps_mm=data.graph_edges_lps_mm if show_graph else (),
        biological_volume=data.biological_volumes.get(overlay_field_id or ""),
        selected_region_name=selected_region_name,
        scalar_range=scalar_range, isosurface_thresholds=isosurface_thresholds,
        volume_opacity=float(np.clip(isosurface_opacity, 0.0, 1.0)),
        volume_opacity_preset=volume_opacity_preset,
        cut_axis=cut_axis, cut_fraction=float(np.clip(cut_fraction, 0.0, 1.0)),
        cut_inverted=cut_inverted, cut_azimuth_degrees=cut_azimuth_degrees,
        cut_elevation_degrees=cut_elevation_degrees,
    )


def _build_cad_scene_bundle(
    data: Layer31ViewerData,
    anatomy_names: tuple[str, ...],
    overlay_field_id: str | None,
    smoothing: dict[str, Any] | None,
    scalar_range: tuple[float, float] | None,
    *,
    display_mode: str = "SURFACE",
    cut_axis: str = "axial",
    cut_fraction: float = 0.5,
    cut_inverted: bool = False,
    cut_azimuth_degrees: float = 0.0,
    cut_elevation_degrees: float = 0.0,
    isosurface_thresholds: tuple[float, ...] = (),
    show_contours: bool = False,
    gtv_opacity: float = 0.96,
    oar_opacity: float = 0.25,
    isosurface_opacity: float = 0.45,
    show_vertex_centres: bool = False,
    show_graph: bool = False,
    selected_region_name: str = "Region: Whole GTV",
    volume_opacity_preset: str = "biological_effect",
) -> CADSceneBundle:
    """Compatibility wrapper for the pre-refactor public helper signature."""
    return build_cad_scene_bundle(
        data,
        anatomy_names,
        overlay_field_id,
        CADProjectionOptions(
            smoothing=smoothing,
            scalar_range=scalar_range,
            display_mode=display_mode,
            cut_axis=cut_axis,
            cut_fraction=cut_fraction,
            cut_inverted=cut_inverted,
            cut_azimuth_degrees=cut_azimuth_degrees,
            cut_elevation_degrees=cut_elevation_degrees,
            isosurface_thresholds=isosurface_thresholds,
            show_contours=show_contours,
            gtv_opacity=gtv_opacity,
            oar_opacity=oar_opacity,
            isosurface_opacity=isosurface_opacity,
            show_vertex_centres=show_vertex_centres,
            show_graph=show_graph,
            selected_region_name=selected_region_name,
            volume_opacity_preset=volume_opacity_preset,
        ),
    )
