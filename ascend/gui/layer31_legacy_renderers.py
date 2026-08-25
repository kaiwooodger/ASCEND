"""Legacy Qt raster and Qt3D render backends for Layer 3.1 CAD bundles.

The active workstation uses the PyVista scene.  These retained compatibility
renderers consume display meshes only and contain no scientific calculations.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DExtras import Qt3DExtras
from PySide6.Qt3DRender import Qt3DRender
from PySide6.QtCore import QByteArray, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QVector3D
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ascend.gui.layer22_viewer import _material
from ascend.gui.layer31_viewer_models import CADSceneBundle
from ascend.layer3.visualization import BiologicalMeshResult


def _vertex_colour_renderer(mesh: Any, normals: np.ndarray, parent: Any) -> Any:
    vertices = np.asarray(mesh.vertices_lps_mm, dtype=np.float32)
    colours = np.column_stack((np.asarray(mesh.rgb, dtype=np.float32) / 255.0, np.ones(len(vertices), dtype=np.float32)))
    packed = np.ascontiguousarray(np.column_stack((vertices, normals, colours)), dtype=np.float32)
    indices = np.ascontiguousarray(mesh.faces.reshape(-1), dtype=np.uint32)
    geometry = Qt3DCore.QGeometry(parent)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    for name, offset, size in (
        (Qt3DCore.QAttribute.defaultPositionAttributeName(), 0, 3),
        (Qt3DCore.QAttribute.defaultNormalAttributeName(), 12, 3),
        (Qt3DCore.QAttribute.defaultColorAttributeName(), 24, 4),
    ):
        attribute = Qt3DCore.QAttribute(geometry); attribute.setName(name)
        attribute.setVertexBaseType(Qt3DCore.QAttribute.Float); attribute.setVertexSize(size)
        attribute.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); attribute.setBuffer(vertex_buffer)
        attribute.setByteOffset(offset); attribute.setByteStride(40); attribute.setCount(len(vertices)); geometry.addAttribute(attribute)
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry); index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt)
    index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute); index.setBuffer(index_buffer); index.setCount(len(indices)); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent); renderer.setGeometry(geometry)
    renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Triangles); renderer.setVertexCount(len(indices)); return renderer


def _solid_surface_renderer(mesh: Any, normals: np.ndarray, parent: Any, faces: np.ndarray | None = None) -> Any:
    """Create a reliable position/normal renderer for Phong CAD materials."""
    vertices = np.asarray(mesh.vertices_lps_mm, dtype=np.float32)
    normal_values = np.asarray(normals, dtype=np.float32)
    selected_faces = np.asarray(mesh.faces if faces is None else faces, dtype=np.uint32)
    packed = np.ascontiguousarray(np.column_stack((vertices, normal_values)), dtype=np.float32)
    indices = np.ascontiguousarray(selected_faces.reshape(-1), dtype=np.uint32)
    geometry = Qt3DCore.QGeometry(parent)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    for name, offset in (
        (Qt3DCore.QAttribute.defaultPositionAttributeName(), 0),
        (Qt3DCore.QAttribute.defaultNormalAttributeName(), 12),
    ):
        attribute = Qt3DCore.QAttribute(geometry); attribute.setName(name)
        attribute.setVertexBaseType(Qt3DCore.QAttribute.Float); attribute.setVertexSize(3)
        attribute.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); attribute.setBuffer(vertex_buffer)
        attribute.setByteOffset(offset); attribute.setByteStride(24); attribute.setCount(len(vertices)); geometry.addAttribute(attribute)
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry); index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt)
    index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute); index.setBuffer(index_buffer)
    index.setCount(len(indices)); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent); renderer.setGeometry(geometry)
    renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Triangles); renderer.setVertexCount(len(indices))
    return renderer


def _line_renderer(vertices: np.ndarray, edges: np.ndarray, parent: Any) -> Any:
    geometry = Qt3DCore.QGeometry(parent)
    positions = np.ascontiguousarray(vertices, dtype=np.float32)
    normals = np.tile(np.asarray([[0.0, 0.0, 1.0]], dtype=np.float32), (len(positions), 1))
    packed = np.ascontiguousarray(np.column_stack((positions, normals)), dtype=np.float32)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    for name, offset in ((Qt3DCore.QAttribute.defaultPositionAttributeName(), 0), (Qt3DCore.QAttribute.defaultNormalAttributeName(), 12)):
        attribute = Qt3DCore.QAttribute(geometry); attribute.setName(name)
        attribute.setVertexBaseType(Qt3DCore.QAttribute.Float); attribute.setVertexSize(3)
        attribute.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); attribute.setBuffer(vertex_buffer)
        attribute.setByteOffset(offset); attribute.setByteStride(24); attribute.setCount(len(positions)); geometry.addAttribute(attribute)
    indices = np.ascontiguousarray(edges.reshape(-1), dtype=np.uint32)
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry); index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt)
    index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute); index.setBuffer(index_buffer); index.setCount(len(indices)); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent); renderer.setGeometry(geometry)
    renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Lines); renderer.setVertexCount(len(indices)); return renderer


class SoftwareBiologicalScene3D(QWidget):
    """Crash-resistant macOS CAD projection using Qt's raster paint engine.

    Mesh generation, DICOM-LPS coordinates, scalar sampling, and exports remain
    authoritative and unchanged.  Only final interactive presentation is
    projected in software, avoiding the Qt3D/Metal pipeline that can terminate
    the Python process inside Apple's render driver.
    """

    pointPicked = Signal(float, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._bundle: CADSceneBundle | None = None
        self._focused_name: str | None = None
        self.center = np.zeros(3, dtype=float)
        self.distance = 100.0
        self.yaw = -35.0; self.pitch = 24.0; self.zoom = 1.0
        self.pan = QPointF(); self._drag: QPointF | None = None; self._pan_drag = False
        self.selected_world_position: np.ndarray | None = None
        self._projected_pick_points = np.empty((0, 2), dtype=float)
        self._projected_world_points = np.empty((0, 3), dtype=float)
        self.setMinimumSize(620, 500); self.setMouseTracking(True); self.setCursor(Qt.OpenHandCursor)

    def clear(self) -> None:
        self._bundle = None
        self._projected_pick_points = np.empty((0, 2), dtype=float)
        self._projected_world_points = np.empty((0, 3), dtype=float)
        self.update()

    @staticmethod
    def _anatomical_style(name: str, focused: bool) -> tuple[QColor, float]:
        return BiologicalScene3D._anatomical_style(name, focused)

    def set_bundle(self, bundle: CADSceneBundle, focused_name: str | None = None) -> None:
        self._bundle = bundle; self._focused_name = focused_name
        vertices = []
        for result in [*bundle.anatomy_meshes.values(), *bundle.special_meshes.values(), *([bundle.overlay_mesh] if bundle.overlay_mesh else [])]:
            if result and result.display_surface is not None:
                values = np.asarray(result.display_surface.vertices_lps_mm, dtype=float)
                if values.size: vertices.append(values)
        if vertices:
            combined = np.vstack(vertices); low, high = np.nanmin(combined, axis=0), np.nanmax(combined, axis=0)
            self.center = (low + high) / 2.0; self.distance = max(float(np.linalg.norm(high - low)), 1.0)
        self.update()

    def set_selected_world_position(self, point_lps_mm: tuple[float, float, float] | None) -> None:
        self.selected_world_position = None if point_lps_mm is None else np.asarray(point_lps_mm, dtype=float)
        self.update()

    def set_view(self, orientation: str) -> None:
        values = {"axial": (0.0, 0.0), "sagittal": (90.0, 0.0), "coronal": (0.0, 90.0), "perspective": (-35.0, 24.0)}
        self.yaw, self.pitch = values.get(orientation, values["perspective"]); self.pan = QPointF(); self.update()

    def zoom_by(self, factor: float) -> None:
        # Native scene used camera-distance factors: values below one zoom in.
        self.zoom = float(np.clip(self.zoom / max(float(factor), 1.0e-6), 0.2, 12.0)); self.update()

    def rotate_by(self, degrees: float) -> None:
        self.yaw = (self.yaw + float(degrees)) % 360.0; self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(0.86 if event.angleDelta().y() > 0 else 1.16); event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self._drag = event.position(); self._pan_drag = event.button() != Qt.LeftButton
            self.setCursor(Qt.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag is None: return
        delta = event.position() - self._drag; self._drag = event.position()
        if self._pan_drag: self.pan += delta
        else:
            self.yaw += delta.x() * 0.45; self.pitch = float(np.clip(self.pitch + delta.y() * 0.45, -89.0, 89.0))
        self.update(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._drag is not None:
            moved = event.position() - self._drag
            if event.button() == Qt.LeftButton and abs(moved.x()) + abs(moved.y()) < 3:
                self._pick(event.position())
        self._drag = None; self.setCursor(Qt.OpenHandCursor); event.accept()

    def _rotation(self) -> np.ndarray:
        yaw, pitch = np.deg2rad(self.yaw), np.deg2rad(self.pitch)
        rz = np.asarray([[np.cos(yaw), -np.sin(yaw), 0.0], [np.sin(yaw), np.cos(yaw), 0.0], [0.0, 0.0, 1.0]])
        rx = np.asarray([[1.0, 0.0, 0.0], [0.0, np.cos(pitch), -np.sin(pitch)], [0.0, np.sin(pitch), np.cos(pitch)]])
        return rx @ rz

    def _project(self, world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rotated = (np.asarray(world, dtype=float) - self.center) @ self._rotation().T
        scale = 0.82 * min(max(self.width(), 1), max(self.height(), 1)) / max(self.distance, 1.0) * self.zoom
        screen = np.column_stack((self.width() / 2.0 + self.pan.x() + rotated[:, 0] * scale,
                                  self.height() / 2.0 + self.pan.y() - rotated[:, 1] * scale))
        return screen, rotated[:, 2]

    def _mesh_triangles(self, result: BiologicalMeshResult, colour: QColor | None, alpha: float, overlay: bool) -> list[tuple[float, QPolygonF, QColor]]:
        surface = result.display_surface
        if surface is None: return []
        vertices = np.asarray(surface.vertices_lps_mm, dtype=float); faces = np.asarray(surface.faces, dtype=np.int64)
        if not len(vertices) or not len(faces): return []
        # Bound raster workload deterministically. Geometry and exported meshes
        # stay complete; only display triangles are decimated.
        stride = max(int(np.ceil(len(faces) / 12000.0)), 1); faces = faces[::stride]
        screen, depth = self._project(vertices); rgb = np.asarray(surface.rgb, dtype=np.uint8)
        records = []
        for face in faces:
            points = QPolygonF([QPointF(float(screen[index, 0]), float(screen[index, 1])) for index in face])
            if overlay and len(rgb) == len(vertices):
                mean = np.mean(rgb[face], axis=0).astype(int); face_colour = QColor(*map(int, mean))
            else: face_colour = QColor(colour or QColor("#8fb5ce"))
            face_colour.setAlphaF(float(np.clip(alpha, 0.0, 1.0)))
            records.append((float(np.mean(depth[face])), points, face_colour))
        self._projected_pick_points = screen[::max(len(screen) // 5000, 1)]
        self._projected_world_points = vertices[::max(len(vertices) // 5000, 1)]
        return records

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#071a38")); painter.setRenderHint(QPainter.Antialiasing, True)
        bundle = self._bundle
        if bundle is None:
            painter.setPen(QColor("#d6e2ef")); painter.drawText(self.rect(), Qt.AlignCenter, "No biological CAD scene")
            return
        triangles: list[tuple[float, QPolygonF, QColor]] = []
        for name, result in bundle.anatomy_meshes.items():
            if bundle.overlay_mesh is not None and name == bundle.overlay_target: continue
            colour, alpha = self._anatomical_style(name, name == self._focused_name)
            if name == "Region: Whole GTV": alpha = bundle.gtv_opacity * (1.0 if name == self._focused_name else 0.32)
            elif name.startswith("OAR:"): alpha = bundle.oar_opacity * (1.5 if name == self._focused_name else 1.0)
            triangles.extend(self._mesh_triangles(result, colour, alpha, False))
        if bundle.overlay_mesh is not None:
            triangles.extend(self._mesh_triangles(bundle.overlay_mesh, None, bundle.gtv_opacity, True))
        for result in bundle.special_meshes.values():
            triangles.extend(self._mesh_triangles(result, None, bundle.isosurface_opacity, True))
        painter.setPen(Qt.NoPen)
        for _depth, polygon, colour in sorted(triangles, key=lambda item: item[0]):
            painter.setBrush(colour); painter.drawPolygon(polygon)
        painter.setPen(QPen(QColor("#24d6a5"), 2.0))
        for start, end in bundle.graph_edges_lps_mm:
            projected, _ = self._project(np.asarray([start, end], dtype=float)); painter.drawLine(QPointF(*projected[0]), QPointF(*projected[1]))
        painter.setBrush(QColor("#ffe13a")); painter.setPen(Qt.NoPen)
        for point in bundle.vertex_centres_lps_mm:
            projected, _ = self._project(np.asarray([point], dtype=float)); painter.drawEllipse(QPointF(*projected[0]), 4.5, 4.5)
        if self.selected_world_position is not None and np.isfinite(self.selected_world_position).all():
            projected, _ = self._project(self.selected_world_position.reshape(1, 3)); painter.setBrush(QColor("#ffffff")); painter.drawEllipse(QPointF(*projected[0]), 6.0, 6.0)
        painter.setPen(QColor("#d6e2ef")); painter.drawText(12, 22, f"Software CAD · DICOM LPS · zoom {self.zoom:.2g}× · yaw {self.yaw:.0f}° · pitch {self.pitch:.0f}°")

    def _pick(self, point: QPointF) -> None:
        if not len(self._projected_pick_points): return
        delta = self._projected_pick_points - np.asarray([point.x(), point.y()])
        index = int(np.argmin(np.sum(delta * delta, axis=1)))
        if float(np.linalg.norm(delta[index])) <= 18.0:
            world = self._projected_world_points[index]; self.pointPicked.emit(*map(float, world))


class BiologicalScene3D(QWidget):
    pointPicked = Signal(float, float, float)

    def __init__(self) -> None:
        super().__init__(); self.window = Qt3DExtras.Qt3DWindow(); self.window.defaultFrameGraph().setClearColor(QColor("#071a38"))
        self.container = QWidget.createWindowContainer(self.window, self); layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.container)
        self.root = Qt3DCore.QEntity(); self.window.setRootEntity(self.root); self.camera = self.window.camera()
        self.camera.lens().setPerspectiveProjection(35.0, 16/9, 0.1, 10000.0)
        self.controls = Qt3DExtras.QOrbitCameraController(self.root); self.controls.setCamera(self.camera)
        self.controls.setLinearSpeed(80.0); self.controls.setLookSpeed(140.0)
        self.entities: list[Any] = []; self.center = np.zeros(3); self.distance = 100.0; self.setMinimumSize(620, 500)
        self._bundle: CADSceneBundle | None = None
        self.selected_world_position: np.ndarray | None = None

    def clear(self) -> None:
        # Qt3D finishes QNode construction through posted events. Detaching a
        # newly-created entity before those events run can leave Qt's private
        # post-constructor event holding a dead parent and cause a native
        # QNodePrivate::_q_postConstructorInit segmentation fault. Disable the
        # old actors immediately but retain their scene parent until Qt handles
        # deleteLater in event order.
        for entity in self.entities:
            entity.setEnabled(False)
            entity.deleteLater()
        self.entities = []

    @staticmethod
    def _anatomical_style(name: str, focused: bool) -> tuple[QColor, float]:
        if name == "Region: Whole GTV":
            return QColor("#f4d77a"), 0.20 if not focused else 0.34
        if name == "Region: Vertices":
            return QColor("#20a6c9"), 0.90 if not focused else 1.0
        if name == "Region: Valleys":
            return QColor("#6e5bd8"), 0.74 if not focused else 0.95
        if name.startswith("OAR:"):
            if name.split(":", 1)[-1].strip().upper() in {"BODY", "EXTERNAL", "BODY-PTV"}:
                return QColor("#a8b3bf"), 0.08 if not focused else 0.18
            return QColor("#ed78b5"), 0.34 if not focused else 0.62
        return QColor("#8fb5ce"), 0.42 if not focused else 0.72

    def _add_surface(self, result: BiologicalMeshResult, colour: QColor, alpha: float) -> None:
        if result.display_surface is None or result.vertex_normals is None:
            return
        entity = Qt3DCore.QEntity(self.root)
        entity.addComponent(_solid_surface_renderer(result.display_surface, result.vertex_normals, entity))
        entity.addComponent(_material(entity, colour, alpha))
        self._attach_picker(entity)
        self.entities.append(entity)

    def _attach_picker(self, entity: Any) -> None:
        picker = Qt3DRender.QObjectPicker(entity); picker.setHoverEnabled(False)
        def emit_point(event: Any) -> None:
            point = event.worldIntersection()
            self.pointPicked.emit(float(point.x()), float(point.y()), float(point.z()))
        picker.clicked.connect(emit_point); entity.addComponent(picker)

    def _add_scalar_overlay(self, result: BiologicalMeshResult, alpha: float = 0.72) -> None:
        """Render scalar bands with Phong materials instead of fragile per-vertex material."""
        surface = result.display_surface
        if surface is None or result.vertex_normals is None:
            return
        faces = np.asarray(surface.faces, dtype=np.uint32)
        values = np.asarray(surface.scalar_values, dtype=float)
        face_values = np.nanmean(values[faces], axis=1)
        finite = np.isfinite(face_values)
        if not finite.any():
            return
        configured_range = result.provenance.get("display_scalar_range")
        if isinstance(configured_range, list) and len(configured_range) == 2:
            low, high = map(float, configured_range)
        else:
            low, high = float(np.nanmin(values)), float(np.nanmax(values))
        if high <= low:
            self._add_surface(result, QColor("#33a884"), alpha)
            return
        band_count = 10
        band = np.zeros(len(face_values), dtype=int)
        band[finite] = np.clip(
            ((face_values[finite] - low) / (high - low) * band_count).astype(int),
            0, band_count - 1,
        )
        for index in range(band_count):
            selection = finite & (band == index)
            if not selection.any():
                continue
            selected_faces = faces[selection]
            colour_values = np.asarray(surface.rgb, dtype=np.uint8)[selected_faces].reshape(-1, 3)
            mean_colour = np.round(np.mean(colour_values, axis=0)).astype(int)
            entity = Qt3DCore.QEntity(self.root)
            entity.addComponent(_solid_surface_renderer(surface, result.vertex_normals, entity, selected_faces))
            entity.addComponent(_material(entity, QColor(*map(int, mean_colour)), alpha))
            self._attach_picker(entity)
            self.entities.append(entity)

    def _add_scalar_contours(self, result: BiologicalMeshResult) -> None:
        surface = result.display_surface
        if surface is None: return
        values = np.asarray(surface.scalar_values, dtype=float); finite = np.isfinite(values)
        if not finite.any(): return
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
        if high <= low: return
        bins = np.full(len(values), -1, dtype=int)
        bins[finite] = np.clip(((values[finite] - low) / (high - low) * 10).astype(int), 0, 9)
        faces = np.asarray(surface.faces, dtype=np.uint32)
        edges = np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
        crossing = finite[edges[:, 0]] & finite[edges[:, 1]] & (bins[edges[:, 0]] != bins[edges[:, 1]])
        edges = np.unique(np.sort(edges[crossing], axis=1), axis=0)
        if not len(edges): return
        entity = Qt3DCore.QEntity(self.root); entity.addComponent(_line_renderer(surface.vertices_lps_mm, edges, entity))
        entity.addComponent(_material(entity, QColor("#f4f8fc"), 0.82)); self.entities.append(entity)

    def _add_vertex_centres(self, centres: tuple[tuple[float, float, float], ...]) -> None:
        for point in centres:
            entity = Qt3DCore.QEntity(self.root); sphere = Qt3DExtras.QSphereMesh(entity)
            sphere.setRadius(max(self.distance * 0.009, 0.65)); sphere.setRings(12); sphere.setSlices(18)
            transform = Qt3DCore.QTransform(entity); transform.setTranslation(QVector3D(*map(float, point)))
            entity.addComponent(sphere); entity.addComponent(transform)
            entity.addComponent(_material(entity, QColor("#ffe13a"), 1.0)); self.entities.append(entity)

    def _add_graph(self, segments: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...]) -> None:
        if not segments: return
        vertices = np.asarray([point for segment in segments for point in segment], dtype=np.float32)
        edges = np.arange(len(vertices), dtype=np.uint32).reshape(-1, 2)
        entity = Qt3DCore.QEntity(self.root); entity.addComponent(_line_renderer(vertices, edges, entity))
        entity.addComponent(_material(entity, QColor("#24d6a5"), 0.96)); self.entities.append(entity)

    def set_bundle(self, bundle: CADSceneBundle, focused_name: str | None = None) -> None:
        self.clear()
        self._bundle = bundle
        all_vertices: list[np.ndarray] = []
        for name, result in bundle.anatomy_meshes.items():
            if result.display_surface is None:
                continue
            # The biological overlay replaces the solid GTV skin while all
            # other anatomical structures remain visible in the same LPS scene.
            if bundle.overlay_mesh is not None and name == bundle.overlay_target:
                continue
            colour, alpha = self._anatomical_style(name, name == focused_name)
            if name == "Region: Whole GTV":
                alpha = 0.08 if bundle.mode == "ISOSURFACE" else bundle.gtv_opacity * (1.0 if name == focused_name else 0.32)
            elif name.startswith("OAR:"): alpha = bundle.oar_opacity * (1.5 if name == focused_name else 1.0)
            self._add_surface(result, colour, alpha)
            all_vertices.append(np.asarray(result.display_surface.vertices_lps_mm))
        if bundle.overlay_mesh is not None and bundle.overlay_mesh.display_surface is not None:
            self._add_scalar_overlay(bundle.overlay_mesh, alpha=bundle.gtv_opacity)
            if bundle.show_contours: self._add_scalar_contours(bundle.overlay_mesh)
            all_vertices.append(np.asarray(bundle.overlay_mesh.display_surface.vertices_lps_mm))
        for _label, result in bundle.special_meshes.items():
            if result.display_surface is None:
                continue
            self._add_scalar_overlay(result, alpha=bundle.isosurface_opacity)
            if bundle.show_contours: self._add_scalar_contours(result)
            all_vertices.append(np.asarray(result.display_surface.vertices_lps_mm))
        if not all_vertices:
            return
        vertices = np.vstack(all_vertices); low, high = np.min(vertices, axis=0), np.max(vertices, axis=0)
        self.center = (low + high) / 2; self.distance = max(float(np.linalg.norm(high - low)) * 1.5, 20.0)
        self.camera.lens().setPerspectiveProjection(35.0, 16/9, max(self.distance / 1000.0, 0.01), self.distance * 30.0)
        self.set_view("perspective")
        for direction, intensity in ((np.ones(3), 1.05), (np.asarray([-1., 1., -0.5]), 0.65)):
            light_entity = Qt3DCore.QEntity(self.root); light = Qt3DRender.QPointLight(light_entity); light.setIntensity(intensity)
            transform = Qt3DCore.QTransform(light_entity)
            transform.setTranslation(QVector3D(*map(float, self.center + direction * self.distance)))
            light_entity.addComponent(light); light_entity.addComponent(transform); self.entities.append(light_entity)
        self._add_graph(bundle.graph_edges_lps_mm)
        self._add_vertex_centres(bundle.vertex_centres_lps_mm)
        self._add_selection_marker()

    def set_selected_world_position(self, point_lps_mm: tuple[float, float, float] | None) -> None:
        self.selected_world_position = None if point_lps_mm is None else np.asarray(point_lps_mm, dtype=float)
        if self._bundle is not None:
            self.set_bundle(self._bundle)

    def _add_selection_marker(self) -> None:
        if self.selected_world_position is None or not np.isfinite(self.selected_world_position).all():
            return
        entity = Qt3DCore.QEntity(self.root); sphere = Qt3DExtras.QSphereMesh(entity)
        sphere.setRadius(max(self.distance * 0.012, 0.8)); sphere.setRings(14); sphere.setSlices(20)
        transform = Qt3DCore.QTransform(entity); transform.setTranslation(QVector3D(*map(float, self.selected_world_position)))
        entity.addComponent(sphere); entity.addComponent(transform); entity.addComponent(_material(entity, QColor("#ffffff"), 1.0))
        self.entities.append(entity)

    def set_view(self, orientation: str) -> None:
        directions = {"axial": np.array([0., 0., 1.]), "sagittal": np.array([1., 0., 0.]), "coronal": np.array([0., 1., 0.])}
        direction = directions.get(orientation, np.array([1., -1., 1.]) / np.sqrt(3))
        self.camera.setViewCenter(QVector3D(*map(float, self.center))); self.camera.setPosition(QVector3D(*map(float, self.center + direction * self.distance)))
        self.camera.setUpVector(QVector3D(0, 0, 1) if orientation != "axial" else QVector3D(0, -1, 0))

    def zoom_by(self, factor: float) -> None:
        position = np.asarray([self.camera.position().x(), self.camera.position().y(), self.camera.position().z()], dtype=float)
        vector = position - self.center
        length = float(np.linalg.norm(vector))
        if length <= 0: return
        new_length = float(np.clip(length * factor, max(self.distance * 0.08, 1.0), self.distance * 12.0))
        self.camera.setPosition(QVector3D(*map(float, self.center + vector / length * new_length)))

    def rotate_by(self, degrees: float) -> None:
        position = np.asarray([self.camera.position().x(), self.camera.position().y(), self.camera.position().z()], dtype=float)
        vector = position - self.center; radians = np.deg2rad(float(degrees))
        rotated = np.asarray([np.cos(radians) * vector[0] - np.sin(radians) * vector[1],
                              np.sin(radians) * vector[0] + np.cos(radians) * vector[1], vector[2]])
        self.camera.setPosition(QVector3D(*map(float, self.center + rotated)))
        self.camera.setViewCenter(QVector3D(*map(float, self.center)))
