"""Off-screen PyVista/VTK volume scene for stored Layer 3.2 fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyvista as pv
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from ascend.layer3.nonlocal_effect.spatial import ScalarSurface, crop_corners_lps
from ascend.visualization.biology.anatomy_colours import anatomy_colour_map


def layer32_image_data(values: np.ndarray, geometry: dict[str, Any], scalar_name: str) -> pv.ImageData:
    """Bind one stored z,y,x crop field to its DICOM patient-LPS geometry."""
    array = np.asarray(values)
    shape = tuple(map(int, geometry.get("shape") or array.shape))
    if array.ndim != 3 or tuple(array.shape) != shape:
        raise ValueError("LAYER32_VOLUME_GEOMETRY_MISMATCH")
    origin = np.asarray(geometry["origin"], dtype=float)
    row = np.asarray(geometry["row_direction"], dtype=float)
    column = np.asarray(geometry["column_direction"], dtype=float)
    normal = np.asarray(geometry["normal"], dtype=float)
    offsets = np.asarray(geometry["offsets"], dtype=float)
    spacing_yx = np.asarray(geometry["spacing"], dtype=float)[-2:]
    if offsets.size != shape[0]:
        raise ValueError("LAYER32_VOLUME_GEOMETRY_MISMATCH")
    origin = origin + normal * float(offsets[0])
    if offsets.size > 1:
        differences = np.diff(offsets)
        spacing_z = float(np.median(np.abs(differences)))
        if not np.allclose(np.abs(differences), spacing_z, rtol=1.0e-5, atol=1.0e-6):
            raise ValueError("LAYER32_NONUNIFORM_Z_VOLUME_UNSUPPORTED")
        normal = normal * (1.0 if differences[0] > 0 else -1.0)
    else:
        spacing_z = 1.0
    direction = np.column_stack((row, column, normal))
    if not np.allclose(direction.T @ direction, np.eye(3), atol=1.0e-6):
        raise ValueError("LAYER32_VOLUME_DIRECTION_INVALID")
    grid = pv.ImageData(
        dimensions=(shape[2], shape[1], shape[0]),
        spacing=(float(spacing_yx[1]), float(spacing_yx[0]), spacing_z),
        origin=tuple(map(float, origin)),
        direction_matrix=direction,
    )
    grid.point_data[scalar_name] = np.asarray(array, dtype=np.float32).ravel(order="C")
    grid.set_active_scalars(scalar_name)
    return grid


def _polydata(surface: ScalarSurface) -> pv.PolyData:
    faces = np.column_stack((np.full(len(surface.faces), 3, dtype=np.int64), surface.faces)).ravel()
    return pv.PolyData(np.asarray(surface.vertices_lps_mm, dtype=float), faces)


def _field_style(field_name: str) -> tuple[str, bool]:
    survival = "survival" in field_name or field_name == "nonlocal_survival_multiplier"
    if survival:
        return "magma_r", True
    if "physical" in field_name:
        return "turbo", False
    if "concentration" in field_name:
        return "plasma", False
    return "viridis", False


def _opacity_values(reverse: bool, preset: str, samples: int = 256) -> list[float]:
    x = np.linspace(0.0, 1.0, samples)
    if reverse:
        opacity = np.power(1.0 - x, 1.35)
    elif preset == "linear":
        opacity = x
    elif preset == "high_effect":
        opacity = np.power(x, 2.4)
    else:
        opacity = np.power(x, 1.45)
    # Index zero is reserved for the out-of-mask sentinel. The first valid
    # field value begins at index one, including low-survival high-effect data.
    opacity[0] = 0.0
    return list(map(float, opacity))


class Layer32PyVistaScene3D(QWidget):
    """Interactive Qt image of a true VTK Layer 3.2 scalar volume."""

    CROP_EDGES = (
        (0, 1), (0, 2), (1, 3), (2, 3), (4, 5), (4, 6),
        (5, 7), (6, 7), (0, 4), (1, 5), (2, 6), (3, 7),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(620, 500)
        self.setMouseTracking(True)
        self._plotter: pv.Plotter | None = None
        self._image: QImage | None = None
        self._drag_position: QPoint | None = None
        self._drag_button: Qt.MouseButton | None = None
        self._actors_by_group: dict[str, list[Any]] = {}
        self._visibility = {name: True for name in ("scalar", "gtv", "vertices", "oars", "crop")}

    def _ensure_plotter(self) -> pv.Plotter:
        if self._plotter is None:
            self._plotter = pv.Plotter(
                off_screen=True,
                window_size=(max(self.width(), 320), max(self.height(), 240)),
            )
            self._plotter.set_background("#071a38")
        return self._plotter

    def _add_actor(self, group: str, actor: Any) -> None:
        self._actors_by_group.setdefault(group, []).append(actor)
        actor.SetVisibility(bool(self._visibility.get(group, True)))

    @staticmethod
    def _region_mask(data: Any, region_name: str) -> np.ndarray:
        shape = data.fields["physical_absorbed_dose_gy"].shape
        if region_name == "Model domain":
            return np.ones(shape, dtype=bool)
        if region_name == "GTV":
            return np.asarray(data.fields["gtv_mask"], dtype=bool)
        if region_name == "Vertices":
            return np.asarray(data.fields["vertex_union_mask"], dtype=bool)
        if region_name.startswith("OAR:"):
            target = region_name.split(":", 1)[1].strip()
            return next((np.asarray(mask, dtype=bool) for name, mask in data.oar_masks if name == target), np.zeros(shape, dtype=bool))
        raise ValueError("LAYER32_DISPLAY_REGION_UNSUPPORTED")

    def set_data(
        self,
        data: Any,
        scalar_surfaces: list[ScalarSurface],
        opacity: float,
        *,
        field_name: str,
        mode: str = "VOLUME",
        region_name: str = "Model domain",
        opacity_preset: str = "biological_effect",
        clip_axis: int | None = None,
        clip_index: int | None = None,
    ) -> None:
        field = np.asarray(data.fields[field_name], dtype=np.float32)
        valid = np.isfinite(field)
        mask = self._region_mask(data, region_name) & valid
        if clip_axis is not None:
            axis = int(clip_axis)
            index = int(np.clip(clip_index if clip_index is not None else field.shape[axis] // 2, 0, field.shape[axis] - 1))
            retained = np.zeros(field.shape, dtype=bool)
            selection = [slice(None), slice(None), slice(None)]
            selection[axis] = slice(0, index + 1)
            retained[tuple(selection)] = True
            mask &= retained
        selected = field[mask]
        if not selected.size:
            raise ValueError("LAYER32_SELECTED_REGION_HAS_NO_VALID_VOXELS")
        low, high = float(np.min(selected)), float(np.max(selected))
        if high <= low:
            delta = max(abs(low) * 1.0e-6, 1.0e-6)
            low, high = low - delta, high + delta
        width = max(high - low, abs(low) * 1.0e-6, 1.0e-6)
        sentinel = low - width / 255.0
        display = field.copy()
        display[~mask] = sentinel
        grid = layer32_image_data(display, data.geometry, field_name)
        cmap, reverse_opacity = _field_style(field_name)
        plotter = self._ensure_plotter()
        camera = plotter.camera_position if plotter.renderer.actors else None
        plotter.clear()
        self._actors_by_group = {}
        render_mode = str(mode).upper()
        if render_mode in {"VOLUME", "COMBINED"}:
            actor = plotter.add_volume(
                grid, scalars=field_name, clim=(sentinel, high), cmap=cmap,
                opacity=_opacity_values(reverse_opacity, opacity_preset),
                opacity_unit_distance=float(min(grid.spacing)), shade=True,
                blending="composite", name="layer32_biological_volume",
            )
            self._add_actor("scalar", actor)
        if render_mode in {"ISOSURFACE", "COMBINED"}:
            for index, surface in enumerate(scalar_surfaces):
                mesh = _polydata(surface)
                colour = "#" + "".join(f"{int(channel):02x}" for channel in surface.rgb[0])
                actor = plotter.add_mesh(
                    mesh, color=colour, opacity=float(np.clip(opacity, 0.05, 0.95)),
                    smooth_shading=True, name=f"layer32_isosurface_{index}",
                )
                self._add_actor("scalar", actor)
        if render_mode == "SLICE":
            slices = grid.slice_orthogonal()
            actor = plotter.add_mesh(
                slices, scalars=field_name, clim=(low, high), cmap=cmap,
                opacity=float(np.clip(opacity, 0.05, 1.0)), name="layer32_orthogonal_slices",
            )
            self._add_actor("scalar", actor)

        names = ["Region: Whole GTV", "Region: Vertices", *(f"OAR: {name}" for name, _mask in data.oar_masks)]
        colours = anatomy_colour_map(names)
        self._add_actor("gtv", plotter.add_mesh(
            _polydata(data.gtv_surface), color=colours["Region: Whole GTV"], opacity=0.18, smooth_shading=True,
        ))
        self._add_actor("vertices", plotter.add_mesh(
            _polydata(data.vertex_surface), color=colours["Region: Vertices"], opacity=0.34, smooth_shading=True,
        ))
        for name, surface in data.oar_surfaces:
            actor = plotter.add_mesh(_polydata(surface), color=colours[f"OAR: {name}"], opacity=0.24, smooth_shading=True)
            self._add_actor("oars", actor)
        corners = crop_corners_lps(data.geometry)
        for first, second in self.CROP_EDGES:
            line = pv.Line(corners[first], corners[second])
            self._add_actor("crop", plotter.add_mesh(line, color="#9fb4c8", line_width=1.5))
        plotter.add_scalar_bar(title=f"{field_name} [{low:.4g}, {high:.4g}]", n_labels=5)
        if camera is not None:
            plotter.camera_position = camera
        else:
            plotter.reset_camera()
        self._capture()

    def clear_scene(self) -> None:
        if self._plotter is not None:
            self._plotter.clear()
        self._actors_by_group = {}
        self._image = None
        self.update()

    def set_view(self, name: str) -> None:
        if self._plotter is None:
            return
        if name == "axial":
            self._plotter.view_xy()
        elif name == "sagittal":
            self._plotter.view_yz()
        elif name == "coronal":
            self._plotter.view_xz()
        else:
            self._plotter.view_isometric()
        self._capture()

    def zoom_by(self, factor: float) -> None:
        if self._plotter is None or factor <= 0:
            return
        self._plotter.camera.Zoom(1.0 / float(factor))
        self._capture()

    def rotate_by(self, degrees: float) -> None:
        if self._plotter is None:
            return
        self._plotter.camera.Azimuth(float(degrees))
        self._capture()

    def set_visibility(self, group: str, visible: bool) -> None:
        self._visibility[group] = bool(visible)
        for actor in self._actors_by_group.get(group, []):
            actor.SetVisibility(bool(visible))
        self._capture()

    def _capture(self) -> None:
        if self._plotter is None:
            return
        self._plotter.window_size = (max(self.width(), 320), max(self.height(), 240))
        pixels = np.ascontiguousarray(self._plotter.screenshot(return_img=True), dtype=np.uint8)
        height, width = pixels.shape[:2]
        self._image = QImage(pixels.data, width, height, int(pixels.strides[0]), QImage.Format_RGB888).copy()
        self.update()

    def save_screenshot(self, path: str | Path) -> None:
        """Save the current rendered scene without recalculating scientific fields."""
        if self._image is None:
            raise ValueError("LAYER32_SCREENSHOT_HAS_NO_RENDERED_SCENE")
        if not self._image.save(str(path), "PNG"):
            raise OSError(f"Unable to write Layer 3.2 screenshot: {path}")

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#071a38"))
        if self._image is None:
            painter.setPen(QColor("#d6e2ef"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No validated Layer 3.2 volume")
        else:
            painter.drawImage(self.rect(), self._image)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() in {Qt.LeftButton, Qt.MiddleButton}:
            self._drag_position = event.position().toPoint()
            self._drag_button = event.button()
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is None or self._plotter is None:
            return
        current = event.position().toPoint()
        delta = current - self._drag_position
        self._drag_position = current
        if self._drag_button == Qt.LeftButton:
            self._plotter.camera.Azimuth(float(-delta.x()) * 0.5)
            self._plotter.camera.Elevation(float(delta.y()) * 0.5)
            self._plotter.camera.OrthogonalizeViewUp()
        else:
            position = np.asarray(self._plotter.camera.position, dtype=float)
            focal = np.asarray(self._plotter.camera.focal_point, dtype=float)
            up = np.asarray(self._plotter.camera.up, dtype=float)
            up /= max(float(np.linalg.norm(up)), 1.0e-12)
            view = focal - position
            distance = max(float(np.linalg.norm(view)), 1.0e-6)
            view /= distance
            right = np.cross(view, up)
            right /= max(float(np.linalg.norm(right)), 1.0e-12)
            shift = (-float(delta.x()) * right + float(delta.y()) * up) * distance * 0.0015
            self._plotter.camera.position = tuple(position + shift)
            self._plotter.camera.focal_point = tuple(focal + shift)
        self._capture()
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() in {Qt.LeftButton, Qt.MiddleButton}:
            self._drag_position = None
            self._drag_button = None
            event.accept()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(0.85 if event.angleDelta().y() > 0 else 1.18)
        event.accept()

    def closeEvent(self, event: Any) -> None:
        if self._plotter is not None:
            self._plotter.close()
            self._plotter = None
        super().closeEvent(event)
