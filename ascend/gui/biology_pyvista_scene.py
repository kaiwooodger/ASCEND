"""Qt viewport backed by off-screen PyVista/VTK volume rendering."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyvista as pv
import vtk
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from ascend.visualization.biology.controller import BiologicalRenderController
from ascend.visualization.biology.anatomy_colours import anatomy_colour_map
from ascend.visualization.biology.mesh_sampler import polydata_from_triangles
from ascend.visualization.biology.models import BiologicalRegion, BiologicalRenderMode


class PyVistaBiologicalScene3D(QWidget):
    """Cross-platform Qt view of a true VTK biological scene.

    VTK renders off-screen and the resulting image is painted by Qt. This
    avoids the macOS Qt3D/Metal crash while retaining VTK volume mapping,
    isosurfaces, mesh probing, camera persistence and point picking.
    """

    pointPicked = Signal(float, float, float)
    linkedZoomRequested = Signal(float)
    linkedPanRequested = Signal(float, float)
    linkedRotationRequested = Signal(float)

    # Start the Layer 3.1 CAD pane closer than VTK's conservative fit while
    # retaining the complete anatomy in frame. This is local to the CAD scene;
    # the three linked spatial slice canvases keep their existing 1.0x view.
    INITIAL_CAMERA_ZOOM_FACTOR = 1.25

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(180, 120)
        self.setMouseTracking(True)
        self._plotter: pv.Plotter | None = None
        self._controller = BiologicalRenderController()
        self._image: QImage | None = None
        self._bundle: Any | None = None
        self._focused_name: str | None = None
        self._drag_position: QPoint | None = None
        self._drag_button: Qt.MouseButton | None = None
        self._loaded_volume: Any | None = None
        self.selected_world_position: np.ndarray | None = None
        self._interaction_timer = QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.setInterval(33)
        self._interaction_timer.timeout.connect(lambda: self._capture(interactive=True))
        self._quality_timer = QTimer(self)
        self._quality_timer.setSingleShot(True)
        self._quality_timer.setInterval(120)
        self._quality_timer.timeout.connect(self._capture)

    def _ensure_plotter(self) -> pv.Plotter:
        if self._plotter is None:
            try:
                self._plotter = pv.Plotter(off_screen=True, window_size=(max(self.width(), 320), max(self.height(), 240)))
                self._plotter.set_background("#071a38")
                self._plotter.theme.font.color = "#f4f8fc"
            except Exception as exc:
                raise RuntimeError("BIOLOGICAL_RENDERER_INITIALISATION_FAILED") from exc
        return self._plotter

    @staticmethod
    def _mesh(result: Any, *, raw: bool = False) -> pv.PolyData | None:
        source = result.raw_surface if raw else result.display_surface
        if source is None:
            return None
        return polydata_from_triangles(source.vertices_lps_mm, source.faces)

    @staticmethod
    def _slice_plane(bundle: Any, volume: Any) -> tuple[np.ndarray, np.ndarray]:
        axis_name = str(getattr(bundle, "cut_axis", "axial")).lower()
        array_axis = {"axial": 0, "coronal": 1, "sagittal": 2}.get(axis_name, 0)
        direction_column = {"sagittal": 0, "coronal": 1, "axial": 2}.get(axis_name, 2)
        voxel = (np.asarray(volume.geometry.shape, dtype=float) - 1.0) / 2.0
        voxel[array_axis] = float(getattr(bundle, "cut_fraction", 0.5)) * (volume.geometry.shape[array_axis] - 1.0)
        origin = np.asarray(volume.geometry.voxel_to_patient(voxel), dtype=float)
        normal = np.asarray(volume.geometry.direction[:, direction_column], dtype=float)
        azimuth = np.deg2rad(float(getattr(bundle, "cut_azimuth_degrees", 0.0)))
        elevation = np.deg2rad(float(getattr(bundle, "cut_elevation_degrees", 0.0)))
        rotate_z = np.asarray([[np.cos(azimuth), -np.sin(azimuth), 0.0], [np.sin(azimuth), np.cos(azimuth), 0.0], [0.0, 0.0, 1.0]])
        rotate_y = np.asarray([[np.cos(elevation), 0.0, np.sin(elevation)], [0.0, 1.0, 0.0], [-np.sin(elevation), 0.0, np.cos(elevation)]])
        normal = rotate_z @ rotate_y @ normal
        if bool(getattr(bundle, "cut_inverted", False)): normal *= -1.0
        return origin, normal / max(float(np.linalg.norm(normal)), 1.0e-12)

    def set_bundle(self, bundle: Any, focused_name: str | None = None) -> None:
        self._bundle = bundle; self._focused_name = focused_name
        volume = getattr(bundle, "biological_volume", None)
        if volume is None:
            self._render_anatomy_only(bundle)
            return
        plotter = self._ensure_plotter()
        initialise_camera = not bool(plotter.renderer.actors)
        if self._loaded_volume is not volume:
            self._controller.load_volume(volume)
            self._loaded_volume = volume
        region_name = str(getattr(bundle, "selected_region_name", None) or focused_name or "Region: Whole GTV")
        self._controller.set_region(BiologicalRegion.CUSTOM_ROI, region_name)
        raw_mode = str(getattr(bundle, "mode", "SURFACE")).upper()
        mode = {
            "SURFACE": BiologicalRenderMode.SURFACE,
            "CUTAWAY": BiologicalRenderMode.SLICE,
            "SLICE": BiologicalRenderMode.SLICE,
            "VOLUME": BiologicalRenderMode.VOLUME,
            "ISOSURFACE": BiologicalRenderMode.ISOSURFACE,
            "COMBINED": BiologicalRenderMode.COMBINED,
        }.get(raw_mode, BiologicalRenderMode.COMBINED)
        self._controller.set_render_mode(mode)
        scalar_range = getattr(bundle, "scalar_range", None)
        if scalar_range is not None:
            self._controller.set_clim(*scalar_range, lock=True)
        else:
            self._controller.rescale()
        self._controller.set_opacity(
            float(getattr(bundle, "volume_opacity", getattr(bundle, "isosurface_opacity", 0.6))),
            str(getattr(bundle, "volume_opacity_preset", "biological_effect")),
        )
        self._controller.set_isosurfaces(tuple(getattr(bundle, "isosurface_thresholds", ())))
        self._controller.set_vertex_centres_visible(bool(getattr(bundle, "vertex_centres_lps_mm", ())))
        anatomy: dict[str, pv.PolyData] = {}
        for name, result in getattr(bundle, "anatomy_meshes", {}).items():
            mesh = self._mesh(result)
            if mesh is not None:
                anatomy[name] = mesh
        surface_result = getattr(bundle, "overlay_mesh", None)
        biological_surface = self._mesh(surface_result, raw=True) if surface_result is not None else None
        centres = np.asarray(getattr(bundle, "vertex_centres_lps_mm", ()), dtype=float)
        slice_origin, slice_normal = self._slice_plane(bundle, volume)
        if mode is BiologicalRenderMode.SLICE:
            # The dedicated slice mode is an orthogonal biological tri-planar
            # view. A user-defined cut plane remains available through CUTAWAY.
            slice_origin = None
            slice_normal = None
        self._controller.render(
            plotter, anatomical_surfaces=anatomy,
            biological_surface=biological_surface,
            vertex_centres_mm=centres if centres.size else None,
            slice_origin_mm=slice_origin, slice_normal=slice_normal,
        )
        if initialise_camera:
            plotter.camera.Zoom(self.INITIAL_CAMERA_ZOOM_FACTOR)
        # The compact four-pane workspace uses the shared Qt legend below all
        # panes; retaining VTK's second legend would obscure the 3D viewport.
        plotter.remove_scalar_bar()
        self._add_selection_marker()
        self._capture()

    def _render_anatomy_only(self, bundle: Any) -> None:
        plotter = self._ensure_plotter(); camera = plotter.camera_position if plotter.renderer.actors else None; plotter.clear()
        colours = anatomy_colour_map(getattr(bundle, "anatomy_meshes", {}).keys())
        for name, result in getattr(bundle, "anatomy_meshes", {}).items():
            mesh = self._mesh(result)
            if mesh is not None:
                opacity = float(getattr(bundle, "oar_opacity", 0.25)) if name.startswith("OAR:") else 0.3
                plotter.add_mesh(mesh, color=colours[name], opacity=opacity, smooth_shading=True)
        if camera is not None:
            plotter.camera_position = camera
        else:
            plotter.reset_camera()
            plotter.camera.Zoom(self.INITIAL_CAMERA_ZOOM_FACTOR)
        self._add_selection_marker(); self._capture()

    def _add_selection_marker(self) -> None:
        if self._plotter is None or self.selected_world_position is None or not np.isfinite(self.selected_world_position).all():
            return
        marker = pv.Sphere(radius=1.0, center=tuple(map(float, self.selected_world_position)))
        self._plotter.add_mesh(marker, color="white", name="selected_voxel_marker")

    def _capture(self, *, interactive: bool = False) -> None:
        if self._plotter is None: return
        scale = 0.55 if interactive else 1.0
        width = max(int(self.width() * scale), 240 if interactive else 300)
        height = max(int(self.height() * scale), 180 if interactive else 240)
        if interactive:
            width, height = min(width, 640), min(height, 480)
        self._plotter.window_size = (width, height)
        pixels = np.ascontiguousarray(self._plotter.screenshot(return_img=True), dtype=np.uint8)
        height, width = pixels.shape[:2]
        self._image = QImage(pixels.data, width, height, int(pixels.strides[0]), QImage.Format_RGB888).copy()
        self.update()

    def clear(self) -> None:
        if self._plotter is not None:
            self._plotter.clear()
        self._image = None; self.update()

    def _schedule_interactive_capture(self) -> None:
        if not self._interaction_timer.isActive():
            self._interaction_timer.start()
        self._quality_timer.start()

    def set_view(self, orientation: str) -> None:
        if self._plotter is None: return
        if orientation == "axial": self._plotter.view_xy()
        elif orientation == "sagittal": self._plotter.view_yz()
        elif orientation == "coronal": self._plotter.view_xz()
        else: self._plotter.view_isometric()
        self._capture()

    def zoom_by(self, factor: float) -> None:
        if self._plotter is None or factor <= 0: return
        self._plotter.camera.Zoom(1.0 / float(factor)); self._capture()

    def rotate_by(self, degrees: float) -> None:
        if self._plotter is None: return
        self._plotter.camera.Azimuth(float(degrees)); self._capture()

    def pan_by(self, x_pixels: float, y_pixels: float) -> None:
        if self._plotter is None:
            return
        position = np.asarray(self._plotter.camera.position, dtype=float)
        focal = np.asarray(self._plotter.camera.focal_point, dtype=float)
        up = np.asarray(self._plotter.camera.up, dtype=float)
        up /= max(float(np.linalg.norm(up)), 1.0e-12)
        view = focal - position
        distance = max(float(np.linalg.norm(view)), 1.0e-6)
        view /= distance
        right = np.cross(view, up)
        right /= max(float(np.linalg.norm(right)), 1.0e-12)
        shift = (-float(x_pixels) * right + float(y_pixels) * up) * distance * 0.0015
        self._plotter.camera.position = tuple(position + shift)
        self._plotter.camera.focal_point = tuple(focal + shift)
        self._capture()

    def reset_view(self) -> None:
        if self._plotter is None: return
        self._plotter.reset_camera(); self._capture()

    def set_selected_world_position(self, point_lps_mm: tuple[float, float, float] | None) -> None:
        self.selected_world_position = None if point_lps_mm is None else np.asarray(point_lps_mm, dtype=float)
        if self._plotter is not None:
            self._plotter.remove_actor("selected_voxel_marker", render=False)
            self._add_selection_marker(); self._capture()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#071a38"))
        if self._image is None:
            painter.setPen(QColor("#d6e2ef")); painter.drawText(self.rect(), Qt.AlignCenter, "No validated biological volume")
        else:
            painter.drawImage(self.rect(), self._image)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self._image is not None:
            self._quality_timer.start()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() in {Qt.LeftButton, Qt.MiddleButton}:
            self._drag_position = event.position().toPoint(); self._drag_button = event.button(); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is None or self._plotter is None: return
        current = event.position().toPoint(); delta = current - self._drag_position; self._drag_position = current
        if self._drag_button == Qt.LeftButton:
            self._plotter.camera.Azimuth(float(-delta.x()) * 0.5); self._plotter.camera.Elevation(float(delta.y()) * 0.5)
            self._plotter.camera.OrthogonalizeViewUp()
            self.linkedRotationRequested.emit(float(-delta.x()) * 0.5)
        else:
            position = np.asarray(self._plotter.camera.position, dtype=float)
            focal = np.asarray(self._plotter.camera.focal_point, dtype=float)
            up = np.asarray(self._plotter.camera.up, dtype=float); up /= max(float(np.linalg.norm(up)), 1.0e-12)
            view = focal - position; distance = max(float(np.linalg.norm(view)), 1.0e-6); view /= distance
            right = np.cross(view, up); right /= max(float(np.linalg.norm(right)), 1.0e-12)
            shift = (-float(delta.x()) * right + float(delta.y()) * up) * distance * 0.0015
            self._plotter.camera.position = tuple(position + shift); self._plotter.camera.focal_point = tuple(focal + shift)
            self.linkedPanRequested.emit(float(delta.x()), float(delta.y()))
        self._schedule_interactive_capture(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() in {Qt.LeftButton, Qt.MiddleButton}:
            self._drag_position = None; self._drag_button = None
            self._interaction_timer.stop(); self._quality_timer.stop(); self._capture(); event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if self._plotter is None: return
        picker = vtk.vtkCellPicker(); picker.SetTolerance(0.005)
        picked = picker.Pick(float(event.position().x()), float(self.height() - event.position().y()), 0.0, self._plotter.renderer)
        if picked:
            self.pointPicked.emit(*map(float, picker.GetPickPosition()))
        event.accept()

    def wheelEvent(self, event: Any) -> None:
        if self._plotter is not None:
            factor = 0.85 if event.angleDelta().y() > 0 else 1.18
            self._plotter.camera.Zoom(1.0 / factor)
            self._schedule_interactive_capture()
            self.linkedZoomRequested.emit(1.0 / factor)
        event.accept()

    def closeEvent(self, event: Any) -> None:
        self._interaction_timer.stop(); self._quality_timer.stop()
        if self._plotter is not None:
            self._plotter.close(); self._plotter = None
        super().closeEvent(event)
