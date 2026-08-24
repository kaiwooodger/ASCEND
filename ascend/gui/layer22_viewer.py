"""Qt 3D and orthogonal-slice presentation of stored Layer 2.2 geometry and dose evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
from typing import Any

import numpy as np
from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DExtras import Qt3DExtras
from PySide6.Qt3DRender import Qt3DRender
from PySide6.QtCore import QByteArray, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QQuaternion, QVector3D
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QSlider, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)
from skimage.measure import marching_cubes

from ascend.layer2.graph.service import _geometry
from ascend.models.case import ASCENDCase
from ascend.scientific.legacy import layer22_validated as validated
from ascend.validation.provenance import file_hash


@dataclass
class SurfaceMesh:
    """Triangle surface expressed in DICOM patient LPS millimetres."""
    vertices_lps_mm: np.ndarray
    faces: np.ndarray


@dataclass
class Layer22ViewerData:
    """Verified stored evidence required by the read-only Layer 2.2 viewer."""
    dose_gy: np.ndarray
    dose_ceiling_gy: float
    gtv_mask: np.ndarray
    vertex_union: np.ndarray
    geometry: dict[str, Any]
    gtv_mesh: SurfaceMesh
    vertex_meshes: dict[str, SurfaceMesh]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    vertex_source: str


def _artifact_path(case: ASCENDCase, configured: str | Path, fallback_name: str) -> Path:
    path = Path(str(configured))
    if path.is_file():
        return path
    result_path = Path(case.layer1.result_path or "")
    local = result_path.parent / fallback_name
    if local.is_file():
        return local
    if result_path.parent.name:
        relocated = case.root / "validated" / result_path.parent.name / fallback_name
        if relocated.is_file():
            return relocated
    return path


def _verified_mask_archive(case: ASCENDCase) -> tuple[Any, dict[str, Any]]:
    layer1 = case.layer1.result or {}
    export = layer1.get("manifest", {}).get("mask_export") or {}
    path = _artifact_path(case, export.get("path", ""), "layer1_native_dose_masks.npz")
    if not path.is_file():
        raise ValueError("Layer 1 validated mask archive is missing.")
    if export.get("sha256") and file_hash(path) != export["sha256"]:
        raise ValueError("Layer 1 mask archive hash differs; 3D rendering was blocked.")
    return np.load(path, allow_pickle=False), export


def _load_verified_mask(archive: Any, export: dict[str, Any], name: str) -> np.ndarray:
    if name not in archive.files:
        raise ValueError(f"Validated mask is missing: {name}")
    mask = np.asarray(archive[name], dtype=bool)
    expected = export.get("structures", {}).get(name, {})
    if expected and int(mask.sum()) != int(expected.get("voxel_count", -1)):
        raise ValueError(f"Validated mask voxel count differs: {name}")
    if expected.get("mask_sha256") and validated.sha256_array(mask) != expected["mask_sha256"]:
        raise ValueError(f"Validated mask hash differs: {name}")
    return mask


def _indices_to_lps(points_zyx: np.ndarray, geometry: dict[str, Any]) -> np.ndarray:
    points = np.asarray(points_zyx, dtype=float)
    offsets = np.interp(points[:, 0], np.arange(len(geometry["offsets"])), geometry["offsets"])
    return (
        geometry["origin"]
        + offsets[:, None] * geometry["normal"]
        + points[:, 2, None] * geometry["spacing"][1] * geometry["row_direction"]
        + points[:, 1, None] * geometry["spacing"][0] * geometry["column_direction"]
    )


def mask_surface(mask: np.ndarray, geometry: dict[str, Any], step_size: int = 1) -> SurfaceMesh:
    """Extract a mask surface and transform voxel indices to patient LPS mm."""
    occupied = np.argwhere(mask)
    if not len(occupied):
        raise ValueError("Cannot render an empty validated mask.")
    lower = np.maximum(occupied.min(axis=0) - 1, 0)
    upper = np.minimum(occupied.max(axis=0) + 2, np.asarray(mask.shape))
    slices = tuple(slice(int(a), int(b)) for a, b in zip(lower, upper))
    cropped = np.asarray(mask[slices], dtype=np.uint8)
    vertices, faces, _normals, _values = marching_cubes(
        np.pad(cropped, 1), level=0.5, step_size=max(int(step_size), 1), allow_degenerate=False,
    )
    vertices += lower - 1
    return SurfaceMesh(
        np.ascontiguousarray(_indices_to_lps(vertices, geometry), dtype=np.float32),
        np.ascontiguousarray(faces, dtype=np.uint32),
    )


def prepare_layer22_viewer_data(case: ASCENDCase) -> Layer22ViewerData:
    """Verify Layer 1 artifacts and build meshes for stored Layer 2.2 evidence.

    Rendering is presentation-only: masks, dose, node order, and provenance are
    hash-checked against completed runs before any surface is constructed.
    """
    result = case.layer2_2.result or {}
    if not result.get("nodes") or not result.get("edges"):
        raise ValueError("A completed Layer 2.2 result is required before building the viewer.")
    manifest = (case.layer1.result or {}).get("manifest", {})
    geometry_value = manifest.get("validated_geometry")
    native_dose = manifest.get("validated_native_dose") or {}
    dose_path = _artifact_path(case, native_dose.get("path", ""), "validated_native_rtdose_float64.npy")
    if not geometry_value or not dose_path.is_file():
        raise ValueError("Validated Layer 1 geometry or native dose is unavailable.")
    if native_dose.get("sha256") and file_hash(dose_path) != native_dose["sha256"]:
        raise ValueError("Validated native-dose hash differs; heatmap rendering was blocked.")
    dose = np.load(dose_path, mmap_mode="r", allow_pickle=False)
    geometry = _geometry(geometry_value)
    archive, export = _verified_mask_archive(case)
    try:
        roles = case.effective_structure_roles
        gtv_name = roles.get("GTV")
        if not isinstance(gtv_name, str):
            raise ValueError("A validated GTV role is required for the 3D envelope.")
        gtv = _load_verified_mask(archive, export, gtv_name)
        selected: dict[str, np.ndarray] = {"GTV": gtv}
        aggregate_name = roles.get("VTV_H")
        if isinstance(aggregate_name, str):
            selected["VTVH"] = _load_verified_mask(archive, export, aggregate_name)
        individual_names = roles.get("VTV_H_individual", [])
        if isinstance(individual_names, list):
            for index, name in enumerate(individual_names, 1):
                selected[f"VTVH_{index:02d}"] = _load_verified_mask(archive, export, name)
    finally:
        archive.close()
    # Reuse the locked vertex preparation solely to reproduce the stored node
    # ordering.  A mismatch blocks display instead of drawing misleading edges.
    names, masks, node_source = validated.prepare_vertices(selected)
    if names != [str(item.get("node")) for item in result["nodes"]]:
        raise ValueError("Viewer vertex ordering differs from the stored Layer 2.2 result.")
    vertex_source = "explicit_rtstruct_vertices" if node_source == "INDIVIDUAL_VTVH_STRUCTURES" else "connected_components_derived"
    if result.get("vertex_source") and result["vertex_source"] != vertex_source:
        raise ValueError("Viewer vertex provenance differs from the stored Layer 2.2 result.")
    vertex_masks = dict(zip(names, masks))
    vertex_union = np.logical_or.reduce(masks)
    vertex_meshes = {name: mask_surface(mask, geometry) for name, mask in vertex_masks.items()}
    gtv_extent = np.ptp(np.argwhere(gtv), axis=0) + 3
    gtv_step = 2 if int(np.prod(gtv_extent)) > 4_000_000 else 1
    return Layer22ViewerData(
        dose, float(np.nanmax(dose)), gtv, vertex_union, geometry, mask_surface(gtv, geometry, gtv_step),
        vertex_meshes,
        result["nodes"], result["edges"], vertex_source,
    )


def _write_binary_stl(mesh: SurfaceMesh, path: Path) -> None:
    header = b"ASCEND Layer 2.2 validated mask surface, DICOM patient LPS mm"[:80].ljust(80, b" ")
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(struct.pack("<I", len(mesh.faces)))
        for start in range(0, len(mesh.faces), 100_000):
            faces = mesh.faces[start:start + 100_000]
            triangles = np.asarray(mesh.vertices_lps_mm[faces], dtype="<f4")
            normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            lengths = np.linalg.norm(normals, axis=1)
            valid = lengths > 0
            normals[valid] /= lengths[valid, None]
            records = np.zeros(
                len(faces),
                dtype=[("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")],
            )
            records["normal"] = normals
            records["vertices"] = triangles
            handle.write(records.tobytes())


def _capped_connection_tube(
    first_lps_mm: np.ndarray,
    second_lps_mm: np.ndarray,
    radius_mm: float = 0.85,
    sides: int = 20,
) -> SurfaceMesh:
    """Create a closed physical-scale tube between two stored graph nodes."""
    first = np.asarray(first_lps_mm, dtype=float)
    second = np.asarray(second_lps_mm, dtype=float)
    axis = second - first
    length = float(np.linalg.norm(axis))
    if not np.isfinite(length) or length <= 0:
        raise ValueError("Cannot export a zero-length Layer 2.2 graph connection.")
    direction = axis / length
    helper = np.asarray([1.0, 0.0, 0.0])
    if abs(float(direction @ helper)) > 0.9:
        helper = np.asarray([0.0, 1.0, 0.0])
    radial_a = np.cross(direction, helper)
    radial_a /= np.linalg.norm(radial_a)
    radial_b = np.cross(direction, radial_a)
    angles = np.linspace(0.0, 2.0 * np.pi, max(int(sides), 3), endpoint=False)
    radial = radius_mm * (
        np.cos(angles)[:, None] * radial_a[None, :]
        + np.sin(angles)[:, None] * radial_b[None, :]
    )
    ring_size = len(radial)
    vertices = np.vstack((first + radial, second + radial, first, second))
    start_center, end_center = 2 * ring_size, 2 * ring_size + 1
    faces: list[tuple[int, int, int]] = []
    for index in range(ring_size):
        following = (index + 1) % ring_size
        faces.extend((
            (index, following, ring_size + following),
            (index, ring_size + following, ring_size + index),
            (start_center, following, index),
            (end_center, ring_size + index, ring_size + following),
        ))
    return SurfaceMesh(
        np.ascontiguousarray(vertices, dtype=np.float32),
        np.ascontiguousarray(faces, dtype=np.uint32),
    )


def _combine_surface_meshes(meshes: list[SurfaceMesh]) -> SurfaceMesh:
    if not meshes:
        raise ValueError("Cannot export an empty combined graph mesh.")
    vertices: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    vertex_offset = 0
    for mesh in meshes:
        vertices.append(np.asarray(mesh.vertices_lps_mm, dtype=np.float32))
        faces.append(np.asarray(mesh.faces, dtype=np.uint32) + vertex_offset)
        vertex_offset += len(mesh.vertices_lps_mm)
    return SurfaceMesh(
        np.ascontiguousarray(np.vstack(vertices), dtype=np.float32),
        np.ascontiguousarray(np.vstack(faces), dtype=np.uint32),
    )


def full_graph_surface(data: Layer22ViewerData, connection_radius_mm: float = 0.85) -> SurfaceMesh:
    """Combine validated vertex surfaces with all stored nearest-neighbour edges."""
    node_points = {
        str(item["node"]): np.asarray(item["centroid_lps_mm"], dtype=float)
        for item in data.nodes
    }
    parts = list(data.vertex_meshes.values())
    for edge in data.edges:
        node_names = list(edge.get("nodes") or [])
        if len(node_names) != 2 or any(str(name) not in node_points for name in node_names):
            raise ValueError("Layer 2.2 graph edge references an unavailable node.")
        parts.append(_capped_connection_tube(
            node_points[str(node_names[0])],
            node_points[str(node_names[1])],
            radius_mm=connection_radius_mm,
        ))
    return _combine_surface_meshes(parts)


def export_viewer_meshes(data: Layer22ViewerData, folder: str | Path) -> list[Path]:
    """Export viewer meshes from stored results without recalculation."""
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    gtv_path = target / "GTV_envelope_LPS_mm.stl"
    _write_binary_stl(data.gtv_mesh, gtv_path)
    outputs.append(gtv_path)
    vertex_files: dict[str, str] = {}
    for name, mesh in data.vertex_meshes.items():
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        path = target / f"{safe_name}_mask_LPS_mm.stl"
        _write_binary_stl(mesh, path)
        outputs.append(path)
        vertex_files[name] = path.name
    connection_radius_mm = 0.85
    graph_path = target / "Full_vertex_graph_connections_LPS_mm.stl"
    graph_mesh = full_graph_surface(data, connection_radius_mm)
    _write_binary_stl(graph_mesh, graph_path)
    outputs.append(graph_path)
    manifest_path = target / "layer22_mesh_manifest.json"
    manifest_path.write_text(json.dumps({
        "coordinate_system": "DICOM patient LPS",
        "units": "mm",
        "surface_definition": "marching cubes at validated native-mask 0.5 boundary",
        "gtv_envelope": gtv_path.name,
        "vertex_masks": vertex_files,
        "full_vertex_graph": graph_path.name,
        "full_vertex_graph_contents": "All validated vertex-mask surfaces plus every stored nearest-neighbour graph edge as a capped connection tube.",
        "connection_tube_radius_mm": connection_radius_mm,
        "combined_mesh_topology": "Intersecting closed shells; no Boolean union is applied. Validate/repair in the target slicer before fabrication.",
        "vertex_source": data.vertex_source,
        "nodes": data.nodes,
        "connections": data.edges,
    }, indent=2), encoding="utf-8")
    outputs.append(manifest_path)
    return outputs


def _vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(vertices, dtype=np.float32)
    triangles = vertices[faces]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(face_normals, axis=1)
    valid_faces = lengths > 0
    face_normals[valid_faces] /= lengths[valid_faces, None]
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 0
    normals[valid] /= lengths[valid, None]
    return np.ascontiguousarray(normals, dtype=np.float32)


def _mesh_renderer(mesh: SurfaceMesh, parent: Any) -> Any:
    vertices = np.ascontiguousarray(mesh.vertices_lps_mm, dtype=np.float32)
    packed = np.ascontiguousarray(np.column_stack((vertices, _vertex_normals(vertices, mesh.faces))), dtype=np.float32)
    indices = np.ascontiguousarray(mesh.faces.reshape(-1), dtype=np.uint32)
    geometry = Qt3DCore.QGeometry(parent)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    position = Qt3DCore.QAttribute(geometry)
    position.setName(Qt3DCore.QAttribute.defaultPositionAttributeName())
    position.setVertexBaseType(Qt3DCore.QAttribute.Float); position.setVertexSize(3)
    position.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); position.setBuffer(vertex_buffer)
    position.setByteOffset(0); position.setByteStride(24); position.setCount(len(vertices))
    normal = Qt3DCore.QAttribute(geometry)
    normal.setName(Qt3DCore.QAttribute.defaultNormalAttributeName())
    normal.setVertexBaseType(Qt3DCore.QAttribute.Float); normal.setVertexSize(3)
    normal.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); normal.setBuffer(vertex_buffer)
    normal.setByteOffset(12); normal.setByteStride(24); normal.setCount(len(vertices))
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry)
    index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt); index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute)
    index.setBuffer(index_buffer); index.setCount(len(indices))
    geometry.addAttribute(position); geometry.addAttribute(normal); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent)
    renderer.setGeometry(geometry); renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Triangles)
    renderer.setVertexCount(len(indices))
    return renderer


def _material(parent: Any, color: QColor, alpha: float = 1.0) -> Any:
    material = Qt3DExtras.QPhongAlphaMaterial(parent) if alpha < 1.0 else Qt3DExtras.QPhongMaterial(parent)
    if alpha < 1.0:
        material.setAlpha(alpha)
    material.setDiffuse(color); material.setAmbient(color.darker(145))
    material.setSpecular(QColor("#dbeafe")); material.setShininess(25.0)
    return material


class Scene3D(QWidget):
    """Represent scene3 d state and behavior."""
    def __init__(self) -> None:
        super().__init__()
        self.window = Qt3DExtras.Qt3DWindow()
        self.window.defaultFrameGraph().setClearColor(QColor("#f8fafc"))
        self.container = QWidget.createWindowContainer(self.window, self)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.container)
        self.root = Qt3DCore.QEntity(); self.window.setRootEntity(self.root)
        self.camera = self.window.camera()
        self.camera.lens().setPerspectiveProjection(35.0, 16.0 / 9.0, 0.1, 10000.0)
        self.controller = Qt3DExtras.QOrbitCameraController(self.root)
        self.controller.setCamera(self.camera); self.controller.setLinearSpeed(80.0); self.controller.setLookSpeed(140.0)
        self.gtv_entity: Any = None
        self.vertex_entities: list[Any] = []
        self.edge_entities: list[Any] = []
        self.edge_materials: list[Any] = []
        self.midpoint_entities: list[Any] = []
        self.centroid_entities: list[Any] = []
        self.data: Layer22ViewerData | None = None
        self.center = np.zeros(3); self.distance = 100.0

    def clear_scene(self) -> None:
        """Clear scene only after the caller's authorization requirements are met."""
        entities = ([self.gtv_entity] if self.gtv_entity else []) + self.vertex_entities + self.edge_entities + self.midpoint_entities + self.centroid_entities
        for entity in entities:
            entity.setParent(None); entity.deleteLater()
        self.gtv_entity = None; self.vertex_entities = []; self.edge_entities = []
        self.edge_materials = []; self.midpoint_entities = []; self.centroid_entities = []

    def set_data(self, data: Layer22ViewerData) -> None:
        """Update data presentation state."""
        self.clear_scene(); self.data = data
        self.gtv_entity = self._surface_entity(data.gtv_mesh, QColor("#e5c96d"), 0.28)
        for mesh in data.vertex_meshes.values():
            self.vertex_entities.append(self._surface_entity(mesh, QColor("#1689ad"), 0.66))
        values = [float(item.get("ipvdr") or 0.0) for item in data.edges]
        low, high = min(values), max(values)
        for edge, value in zip(data.edges, values):
            first, second = [np.asarray(item, dtype=float) for item in self._edge_points(edge)]
            entity, material = self._cylinder(first, second, 0.85, self._edge_color(value, low, high))
            self.edge_entities.append(entity); self.edge_materials.append(material)
            midpoint = np.asarray(edge["midpoint_lps_mm"], dtype=float)
            self.midpoint_entities.append(self._sphere(midpoint, 3.0, QColor("#7c3aed"), 0.96))
        for node in data.nodes:
            point = np.asarray(node["centroid_lps_mm"], dtype=float)
            self.centroid_entities.append(self._sphere(point, 1.25, QColor("#ffd400"), 1.0))
        all_vertices = np.vstack([mesh.vertices_lps_mm for mesh in data.vertex_meshes.values()])
        low_bound, high_bound = np.min(all_vertices, axis=0), np.max(all_vertices, axis=0)
        self.center = (low_bound + high_bound) / 2.0
        self.distance = max(float(np.linalg.norm(high_bound - low_bound)) * 1.55, 40.0)
        self._add_light(self.center + np.asarray([0.4, -0.6, 1.4]) * self.distance)
        self.set_view("perspective")

    def _add_light(self, position: np.ndarray) -> None:
        entity = Qt3DCore.QEntity(self.root)
        light = Qt3DRender.QPointLight(entity); light.setColor(QColor("#ffffff")); light.setIntensity(1.25)
        transform = Qt3DCore.QTransform(); transform.setTranslation(QVector3D(*map(float, position)))
        entity.addComponent(light); entity.addComponent(transform)

    def _edge_points(self, edge: dict[str, Any]) -> list[list[float]]:
        by_name = {str(item["node"]): item["centroid_lps_mm"] for item in self.data.nodes}
        return [by_name[name] for name in edge["nodes"]]

    @staticmethod
    def _edge_color(value: float, low: float, high: float) -> QColor:
        fraction = (value - low) / (high - low or 1.0)
        return QColor.fromHsvF(0.56 - 0.53 * fraction, 0.82, 0.72)

    def _surface_entity(self, mesh: SurfaceMesh, color: QColor, alpha: float) -> Any:
        entity = Qt3DCore.QEntity(self.root)
        entity.addComponent(_mesh_renderer(mesh, entity)); entity.addComponent(_material(entity, color, alpha))
        return entity

    def _sphere(self, position: np.ndarray, radius: float, color: QColor, alpha: float) -> Any:
        entity = Qt3DCore.QEntity(self.root)
        mesh = Qt3DExtras.QSphereMesh(entity); mesh.setRadius(radius); mesh.setRings(18); mesh.setSlices(24)
        transform = Qt3DCore.QTransform(entity); transform.setTranslation(QVector3D(*map(float, position)))
        entity.addComponent(mesh); entity.addComponent(transform); entity.addComponent(_material(entity, color, alpha))
        return entity

    def _cylinder(self, first: np.ndarray, second: np.ndarray, radius: float, color: QColor) -> tuple[Any, Any]:
        delta = second - first; length = float(np.linalg.norm(delta))
        entity = Qt3DCore.QEntity(self.root)
        mesh = Qt3DExtras.QCylinderMesh(entity); mesh.setRadius(radius); mesh.setLength(length); mesh.setRings(4); mesh.setSlices(20)
        transform = Qt3DCore.QTransform(entity)
        transform.setTranslation(QVector3D(*map(float, (first + second) / 2.0)))
        transform.setRotation(QQuaternion.rotationTo(QVector3D(0, 1, 0), QVector3D(*map(float, delta / length))))
        material = _material(entity, color)
        entity.addComponent(mesh); entity.addComponent(transform); entity.addComponent(material)
        return entity, material

    def set_view(self, name: str) -> None:
        """Update view presentation state."""
        if not self.data:
            return
        row = np.asarray(self.data.geometry["row_direction"], dtype=float)
        column = np.asarray(self.data.geometry["column_direction"], dtype=float)
        normal = np.asarray(self.data.geometry["normal"], dtype=float)
        if name == "axial": direction, up = normal, -column
        elif name == "sagittal": direction, up = row, normal
        elif name == "coronal": direction, up = column, normal
        else:
            direction = row - column + normal; direction /= np.linalg.norm(direction); up = normal
        self.camera.setViewCenter(QVector3D(*map(float, self.center)))
        self.camera.setPosition(QVector3D(*map(float, self.center + direction * self.distance)))
        self.camera.setUpVector(QVector3D(*map(float, up)))

    def zoom_by(self, factor: float) -> None:
        position = np.asarray([self.camera.position().x(), self.camera.position().y(), self.camera.position().z()])
        vector = position - self.center; length = float(np.linalg.norm(vector))
        if length <= 0: return
        target = float(np.clip(length * factor, max(self.distance * 0.08, 1.0), self.distance * 12.0))
        self.camera.setPosition(QVector3D(*map(float, self.center + vector / length * target)))

    def rotate_by(self, degrees: float) -> None:
        position = np.asarray([self.camera.position().x(), self.camera.position().y(), self.camera.position().z()])
        vector = position - self.center; angle = np.deg2rad(float(degrees))
        rotated = np.asarray([np.cos(angle)*vector[0]-np.sin(angle)*vector[1], np.sin(angle)*vector[0]+np.cos(angle)*vector[1], vector[2]])
        self.camera.setPosition(QVector3D(*map(float, self.center + rotated))); self.camera.setViewCenter(QVector3D(*map(float, self.center)))

    def set_visibility(self, group: str, visible: bool) -> None:
        """Update visibility presentation state."""
        groups = {
            "gtv": [self.gtv_entity] if self.gtv_entity else [],
            "vertices": self.vertex_entities + self.centroid_entities,
            "edges": self.edge_entities, "midpoints": self.midpoint_entities,
        }
        for entity in groups.get(group, []): entity.setEnabled(visible)

    def select_edge(self, index: int) -> None:
        """Select edge using explicit deterministic criteria."""
        if not self.data: return
        values = [float(item.get("ipvdr") or 0.0) for item in self.data.edges]
        low, high = min(values), max(values)
        for edge_index, (material, value) in enumerate(zip(self.edge_materials, values)):
            color = QColor("#ef7c22") if edge_index == index else self._edge_color(value, low, high)
            material.setDiffuse(color); material.setAmbient(color.darker(145))


class DoseSliceCanvas(QWidget):
    """Represent dose slice canvas state and behavior."""
    ORIENTATIONS = {"axial": 0, "coronal": 1, "sagittal": 2}
    MIDPOINT_SPHERE_RADIUS_MM = 3.0

    def __init__(self, orientation: str) -> None:
        super().__init__(); self.orientation = orientation
        self.data: Layer22ViewerData | None = None; self.index = 0
        self.show_dose = True; self.show_gtv = True; self.show_vertices = True
        self.midpoint: np.ndarray | None = None
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self._drag_position: QPointF | None = None
        self.setMinimumSize(250, 250)
        self.setCursor(Qt.OpenHandCursor)

    def zoom_by(self, factor: float) -> None:
        self.zoom = float(np.clip(self.zoom * factor, 0.5, 8.0)); self.update()

    def rotate_by(self, degrees: float) -> None:
        self.rotation_degrees = (self.rotation_degrees + float(degrees)) % 360.0; self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1/1.15); event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton: self._drag_position = event.position(); self.setCursor(Qt.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is not None:
            current = event.position(); self.pan += current - self._drag_position; self._drag_position = current; self.update(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton: self._drag_position = None; self.setCursor(Qt.OpenHandCursor); event.accept()

    def set_data(self, data: Layer22ViewerData) -> None:
        """Update data presentation state."""
        self.data = data; self.index = data.dose_gy.shape[self.ORIENTATIONS[self.orientation]] // 2; self.update()

    def set_index(self, index: int) -> None:
        """Update index presentation state."""
        self.index = int(index); self.update()

    def _plane(self, array: np.ndarray) -> np.ndarray:
        if self.orientation == "axial": return np.asarray(array[self.index, :, :])
        if self.orientation == "coronal": return np.asarray(array[:, self.index, :])
        return np.asarray(array[:, :, self.index])

    @staticmethod
    def _colourmap(values: np.ndarray, ceiling: float) -> np.ndarray:
        anchors = np.asarray([
            [5, 20, 65], [25, 83, 150], [33, 160, 181], [61, 187, 125],
            [245, 211, 51], [247, 139, 37], [191, 23, 45],
        ], dtype=float)
        scaled = np.clip(values / max(ceiling, 1.0e-9), 0, 1) * (len(anchors) - 1)
        lower = np.floor(scaled).astype(int); upper = np.minimum(lower + 1, len(anchors) - 1)
        fraction = (scaled - lower)[..., None]
        rgb = anchors[lower] * (1 - fraction) + anchors[upper] * fraction
        return np.asarray(np.concatenate((rgb, np.full(values.shape + (1,), 255.0)), axis=2), dtype=np.uint8)

    @staticmethod
    def _boundary(mask: np.ndarray) -> np.ndarray:
        if not mask.any(): return mask
        interior = mask.copy()
        interior[1:, :] &= mask[:-1, :]; interior[:-1, :] &= mask[1:, :]
        interior[:, 1:] &= mask[:, :-1]; interior[:, :-1] &= mask[:, 1:]
        return mask & ~interior

    def _image(self) -> QImage | None:
        if not self.data: return None
        dose = self._plane(self.data.dose_gy); ceiling = self.data.dose_ceiling_gy
        if self.show_dose: rgba = self._colourmap(dose, ceiling)
        else: rgba = np.full(dose.shape + (4,), [244, 246, 248, 255], dtype=np.uint8)
        if self.show_gtv:
            rgba[self._boundary(self._plane(self.data.gtv_mask))] = [235, 203, 98, 255]
        if self.show_vertices:
            rgba[self._boundary(self._plane(self.data.vertex_union))] = [0, 59, 92, 255]
        rgba = np.ascontiguousarray(np.flipud(rgba))
        return QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format_RGBA8888).copy()

    def paintEvent(self, _event: Any) -> None:
        """Handle paint event for the enclosing ASCEND workflow."""
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#ffffff")); image = self._image()
        if image is None:
            painter.setPen(QColor("#66737f")); painter.drawText(self.rect(), Qt.AlignCenter, "Build the Layer 2.2 viewer"); return
        top, bottom = 26, 32; available_w = max(self.width() - 12, 1); available_h = max(self.height() - top - bottom, 1)
        scale = min(available_w / image.width(), available_h / image.height())
        width, height = int(image.width() * scale), int(image.height() * scale)
        left = (self.width() - width) // 2; target_top = top + (available_h - height) // 2
        scaled_image = image.scaled(width, height, Qt.KeepAspectRatio, Qt.FastTransformation)
        painter.save(); painter.setClipRect(0, top, self.width(), available_h)
        center = QPointF(left + width / 2.0, target_top + height / 2.0) + self.pan
        painter.translate(center); painter.rotate(self.rotation_degrees); painter.scale(self.zoom, self.zoom)
        painter.drawImage(QPointF(-width / 2.0, -height / 2.0), scaled_image)
        if self.midpoint is not None:
            overlay = self._midpoint_overlay_geometry(image.height())
            if overlay is not None:
                horizontal, vertical, horizontal_radius, vertical_radius = overlay
                x, y = -width / 2.0 + horizontal * scale, -height / 2.0 + vertical * scale
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(QPen(QColor("#6d28d9"), 2))
                painter.drawEllipse(QPointF(x, y), horizontal_radius * scale, vertical_radius * scale)
        painter.restore()
        painter.setPen(QColor("#243b53")); painter.drawText(8, 18, f"{self.orientation.upper()} — native slice {self.index} · {self.zoom:.2g}× · {self.rotation_degrees:.0f}°")
        if self.show_dose:
            gradient_w = min(180, self.width() - 80)
            for offset in range(max(gradient_w, 1)):
                color = self._colourmap(np.asarray([[offset / max(gradient_w - 1, 1)]]), 1.0)[0, 0]
                painter.setPen(QColor(*map(int, color[:3]))); painter.drawLine(30 + offset, self.height() - 18, 30 + offset, self.height() - 8)
            painter.setPen(QColor("#243b53")); painter.drawText(8, self.height() - 8, "0")
            painter.drawText(35 + gradient_w, self.height() - 8, f"{self.data.dose_ceiling_gy:.2f} Gy")

    def _lps_to_voxel(self, point: np.ndarray) -> np.ndarray:
        relative = point - self.data.geometry["origin"]
        x = float(relative @ self.data.geometry["row_direction"] / self.data.geometry["spacing"][1])
        y = float(relative @ self.data.geometry["column_direction"] / self.data.geometry["spacing"][0])
        offset = float(relative @ self.data.geometry["normal"])
        offsets = np.asarray(self.data.geometry["offsets"], dtype=float)
        if len(offsets) < 2:
            z = 0.0
        elif offsets[0] <= offsets[-1]:
            z = float(np.interp(offset, offsets, np.arange(len(offsets))))
        else:
            z = float(np.interp(offset, offsets[::-1], np.arange(len(offsets))[::-1]))
        return np.asarray([z, y, x])

    def _midpoint_overlay_geometry(self, image_height: int) -> tuple[float, float, float, float] | None:
        if self.data is None or self.midpoint is None:
            return None
        geometry = self.data.geometry
        relative = self.midpoint - geometry["origin"]
        voxel = self._lps_to_voxel(self.midpoint)
        row_spacing, column_spacing = map(float, geometry["spacing"])
        offsets = np.asarray(geometry["offsets"], dtype=float)
        differences = np.abs(np.diff(offsets))
        positive = differences[differences > 0]
        slice_spacing = float(np.median(positive)) if len(positive) else 1.0
        if self.orientation == "axial":
            sphere_plane_position = float(relative @ geometry["normal"])
            selected_plane_position = float(offsets[self.index])
            plane_distance = abs(sphere_plane_position - selected_plane_position)
            horizontal, vertical = voxel[2], image_height - 1 - voxel[1]
            horizontal_spacing, vertical_spacing = column_spacing, row_spacing
        elif self.orientation == "coronal":
            sphere_plane_position = float(relative @ geometry["column_direction"])
            selected_plane_position = self.index * row_spacing
            plane_distance = abs(sphere_plane_position - selected_plane_position)
            horizontal, vertical = voxel[2], image_height - 1 - voxel[0]
            horizontal_spacing, vertical_spacing = column_spacing, slice_spacing
        else:
            sphere_plane_position = float(relative @ geometry["row_direction"])
            selected_plane_position = self.index * column_spacing
            plane_distance = abs(sphere_plane_position - selected_plane_position)
            horizontal, vertical = voxel[1], image_height - 1 - voxel[0]
            horizontal_spacing, vertical_spacing = row_spacing, slice_spacing
        radius = self.MIDPOINT_SPHERE_RADIUS_MM
        if plane_distance > radius:
            return None
        cross_section_radius = float(np.sqrt(max(radius * radius - plane_distance * plane_distance, 0.0)))
        return (
            float(horizontal), float(vertical),
            cross_section_radius / horizontal_spacing,
            cross_section_radius / vertical_spacing,
        )

    def set_midpoint(self, point: np.ndarray) -> int:
        """Update midpoint presentation state."""
        self.midpoint = np.asarray(point, dtype=float); voxel = self._lps_to_voxel(self.midpoint)
        self.index = int(round(voxel[self.ORIENTATIONS[self.orientation]])); self.update(); return self.index


class OrthogonalPanel(QWidget):
    """Represent orthogonal panel state and behavior."""
    def __init__(self) -> None:
        super().__init__(); layout = QVBoxLayout(self); controls = QHBoxLayout()
        self.orientation_checks: dict[str, QCheckBox] = {}
        for name in ("axial", "sagittal", "coronal"):
            check = QCheckBox(name.title()); check.setChecked(True)
            check.toggled.connect(lambda visible, key=name: self._toggle_orientation(key, visible))
            controls.addWidget(check); self.orientation_checks[name] = check
        controls.addStretch(); layout.addLayout(controls)
        self.splitter = QSplitter(Qt.Horizontal); self.canvases: dict[str, DoseSliceCanvas] = {}; self.sliders: dict[str, QSlider] = {}
        for name in ("axial", "sagittal", "coronal"):
            panel = QWidget(); panel_layout = QVBoxLayout(panel); panel_layout.setContentsMargins(2, 2, 2, 2)
            canvas = DoseSliceCanvas(name); slider = QSlider(Qt.Horizontal); slider.valueChanged.connect(canvas.set_index)
            tools = QHBoxLayout()
            for label, operation in (("−", lambda target=canvas: target.zoom_by(1/1.2)), ("+", lambda target=canvas: target.zoom_by(1.2)),
                                     ("↺", lambda target=canvas: target.rotate_by(-90)), ("↻", lambda target=canvas: target.rotate_by(90)), ("Fit", canvas.reset_view)):
                button = QPushButton(label); button.clicked.connect(operation); tools.addWidget(button)
            panel_layout.addWidget(canvas, 1); panel_layout.addLayout(tools); panel_layout.addWidget(slider); self.splitter.addWidget(panel)
            self.canvases[name] = canvas; self.sliders[name] = slider
        layout.addWidget(self.splitter, 1); self.data: Layer22ViewerData | None = None

    def set_data(self, data: Layer22ViewerData) -> None:
        """Update data presentation state."""
        self.data = data; axis = {"axial": 0, "coronal": 1, "sagittal": 2}
        for name, canvas in self.canvases.items():
            canvas.set_data(data); slider = self.sliders[name]
            slider.setRange(0, data.dose_gy.shape[axis[name]] - 1); slider.setValue(canvas.index)

    def _toggle_orientation(self, name: str, visible: bool) -> None:
        self.canvases[name].parentWidget().setVisible(visible)

    def set_overlays(self, dose: bool, gtv: bool, vertices: bool) -> None:
        """Update overlays presentation state."""
        for canvas in self.canvases.values():
            canvas.show_dose = dose; canvas.show_gtv = gtv; canvas.show_vertices = vertices; canvas.update()

    def select_edge(self, edge: dict[str, Any]) -> None:
        """Select edge using explicit deterministic criteria."""
        midpoint = np.asarray(edge["midpoint_lps_mm"], dtype=float)
        for name, canvas in self.canvases.items(): self.sliders[name].setValue(canvas.set_midpoint(midpoint))


class Layer22Viewer(QWidget):
    """Represent layer22 viewer state and behavior."""
    def __init__(self) -> None:
        super().__init__(); self.data: Layer22ViewerData | None = None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.gtv_toggle = QCheckBox("GTV envelope"); self.gtv_toggle.setChecked(True)
        self.vertex_toggle = QCheckBox("Vertex masks"); self.vertex_toggle.setChecked(True)
        self.edge_toggle = QCheckBox("Connections"); self.edge_toggle.setChecked(True)
        self.midpoint_toggle = QCheckBox("3 mm midpoint spheres (physical scale)"); self.midpoint_toggle.setChecked(True)
        self.dose_toggle = QCheckBox("Dose heatmap"); self.dose_toggle.setChecked(True)
        for widget in (self.gtv_toggle, self.vertex_toggle, self.edge_toggle, self.midpoint_toggle, self.dose_toggle): controls.addWidget(widget)
        controls.addStretch(); layout.addLayout(controls)
        camera_row = QHBoxLayout(); camera_row.addWidget(QLabel("3D view"))
        for name in ("Perspective", "Axial", "Sagittal", "Coronal"):
            button = QPushButton(name); button.clicked.connect(lambda _checked=False, view=name.lower(): self.scene.set_view(view))
            camera_row.addWidget(button)
        for label, operation in (("Zoom in", lambda: self.scene.zoom_by(0.82)), ("Zoom out", lambda: self.scene.zoom_by(1.22)),
                                 ("Rotate left", lambda: self.scene.rotate_by(-15)), ("Rotate right", lambda: self.scene.rotate_by(15))):
            button = QPushButton(label); button.clicked.connect(operation); camera_row.addWidget(button)
        export_button = QPushButton("Export STL meshes")
        export_button.clicked.connect(self._export_meshes)
        camera_row.addWidget(export_button)
        camera_row.addSpacing(18); camera_row.addWidget(QLabel("Selected connection"))
        self.edge_selector = QComboBox(); self.edge_selector.currentIndexChanged.connect(self.select_edge)
        camera_row.addWidget(self.edge_selector, 1); layout.addLayout(camera_row)
        self.tabs = QTabWidget(); cad = QWidget(); cad_layout = QHBoxLayout(cad); cad_layout.setContentsMargins(0, 0, 0, 0)
        self.scene = Scene3D(); cad_layout.addWidget(self.scene, 3)
        evidence_panel = QWidget(); evidence_layout = QVBoxLayout(evidence_panel)
        self.edge_table = QTableWidget(0, 4)
        self.edge_table.setHorizontalHeaderLabels(["Connection", "iPVDR", "Valley D50", "Length"])
        self.edge_table.setEditTriggers(QTableWidget.NoEditTriggers); self.edge_table.verticalHeader().setVisible(False)
        self.edge_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.edge_table.horizontalHeader().setStretchLastSection(True)
        self.edge_table.cellClicked.connect(lambda row, _column: self.edge_selector.setCurrentIndex(row))
        evidence_layout.addWidget(self.edge_table, 2)
        self.evidence = QTextEdit(); self.evidence.setReadOnly(True); evidence_layout.addWidget(self.evidence, 1)
        self.legend = QLabel("GTV envelope · vertex-mask surfaces · iPVDR-coloured connections · 3 mm midpoint spheres at physical scale")
        self.legend.setWordWrap(True); evidence_layout.addWidget(self.legend); cad_layout.addWidget(evidence_panel, 2)
        self.render_qa = QLabel("3D render QA has not run.")
        self.render_qa.setWordWrap(True); evidence_layout.addWidget(self.render_qa)
        self.orthogonal = OrthogonalPanel()
        self.tabs.addTab(cad, "3D CAD geometry"); self.tabs.addTab(self.orthogonal, "Axial / sagittal / coronal")
        layout.addWidget(self.tabs, 1)
        self.gtv_toggle.toggled.connect(lambda value: self._visibility("gtv", value))
        self.vertex_toggle.toggled.connect(lambda value: self._visibility("vertices", value))
        self.edge_toggle.toggled.connect(lambda value: self._visibility("edges", value))
        self.midpoint_toggle.toggled.connect(lambda value: self._visibility("midpoints", value))
        self.dose_toggle.toggled.connect(lambda _value: self._update_orthogonal())
        self.gtv_toggle.toggled.connect(lambda _value: self._update_orthogonal())
        self.vertex_toggle.toggled.connect(lambda _value: self._update_orthogonal())

    def set_data(self, data: Layer22ViewerData) -> None:
        """Update data presentation state."""
        self.data = data; self.scene.set_data(data); self.orthogonal.set_data(data)
        self.edge_selector.blockSignals(True); self.edge_selector.clear()
        for edge in data.edges:
            self.edge_selector.addItem(f"{' — '.join(edge['nodes'])}   iPVDR {float(edge['ipvdr']):.3f}")
        self.edge_selector.blockSignals(False); self.edge_table.setRowCount(len(data.edges))
        for row, edge in enumerate(data.edges):
            values = [" — ".join(edge["nodes"]), f"{float(edge['ipvdr']):.3f}", f"{float(edge['edge_local_valley_d50_gy']):.3f} Gy", f"{float(edge['length_mm']):.2f} mm"]
            for column, value in enumerate(values): self.edge_table.setItem(row, column, QTableWidgetItem(value))
        self._visibility("gtv", self.gtv_toggle.isChecked())
        self._visibility("vertices", self.vertex_toggle.isChecked())
        self._visibility("edges", self.edge_toggle.isChecked())
        self._visibility("midpoints", self.midpoint_toggle.isChecked())
        self.render_qa.setText(
            f"Render QA: GTV mesh {len(data.gtv_mesh.vertices_lps_mm)} vertices / {len(data.gtv_mesh.faces)} faces; "
            f"{len(data.vertex_meshes)} vertex mesh(es); {len(data.edges)} connection(s); "
            f"{len(data.edges)} midpoint sphere(s), radius 3.0 mm in DICOM patient coordinates."
        )
        self._update_orthogonal(); self.select_edge(0)

    def _visibility(self, group: str, visible: bool) -> None:
        self.scene.set_visibility(group, visible)

    def _update_orthogonal(self) -> None:
        self.orthogonal.set_overlays(self.dose_toggle.isChecked(), self.gtv_toggle.isChecked(), self.vertex_toggle.isChecked())

    def _export_meshes(self) -> None:
        if not self.data:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export Layer 2.2 STL meshes")
        if not folder:
            return
        outputs = export_viewer_meshes(self.data, folder)
        QMessageBox.information(
            self, "ASCEND", f"Exported {len(outputs) - 1} STL meshes and one manifest to:\n{folder}",
        )

    def select_edge(self, index: int) -> None:
        """Select edge using explicit deterministic criteria."""
        if not self.data or index < 0 or index >= len(self.data.edges): return
        edge = self.data.edges[index]; self.scene.select_edge(index); self.edge_table.selectRow(index); self.orthogonal.select_edge(edge)
        peak = float(edge["edge_peak_d50_gy"]); valley = float(edge["edge_local_valley_d50_gy"]); ratio = float(edge["ipvdr"])
        self.evidence.setPlainText(
            f"{' — '.join(edge['nodes'])}\n\nEdge-local iPVDR: {ratio:.4f}\n"
            f"Endpoint peak D50: {peak:.4f} Gy\n3 mm midpoint-sphere valley D50: {valley:.4f} Gy\n"
            f"Valid native voxels: {edge['valley_support_voxels']}\nEdge length: {float(edge['length_mm']):.3f} mm\n"
            f"Status: {edge['edge_status']}\nVertex source: {self.data.vertex_source}"
        )
