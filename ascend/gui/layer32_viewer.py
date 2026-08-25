"""Read-only Qt visualisation of stored Layer 3.2 fields and edge profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DExtras import Qt3DExtras
from PySide6.Qt3DRender import Qt3DRender
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QQuaternion, QVector3D
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSlider, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)
from scipy import ndimage

from ascend.models.case import ASCENDCase
from ascend.layer3.nonlocal_effect.spatial import (
    CONSEQUENCE_SURFACE_COLOURS, ScalarSurface, consequence_threshold_surfaces, crop_corners_lps,
    equivalent_exposure_h, export_spatial_package, indices_to_lps, mask_surface,
    scalar_level, scalar_range, scalar_surface, voxel_volume_cc,
)
from ascend.validation.provenance import file_hash
from ascend.gui.layer22_viewer import _material, _mesh_renderer
from ascend.gui.layer32_pyvista_scene import Layer32PyVistaScene3D
from ascend.visualization.biology.anatomy_colours import anatomy_colour_map


FIELD_CATALOG = [
    {
        "key": "physical_absorbed_dose_gy", "group": "Physical", "label": "Physical dose",
        "equation": "P(x) = sum_f d_f(x)", "units": "Gy",
        "meaning": "Delivered accumulated physical dose.",
        "exclusions": "Not a biological-effect field.",
    },
    {
        "key": "baseline_lq_survival_fraction", "group": "Baseline", "label": "LQ survival S_LQ",
        "equation": "S_LQ(x) = exp[-alpha P(x)-beta Q(x)]", "units": "fraction",
        "meaning": "Configured local-dose-only LQ survival model.",
        "exclusions": "Not measured survival or a clinical outcome probability.",
    },
    {
        "key": "cumulative_nonlocal_hazard", "group": "Mechanism", "label": "Cumulative mediator exposure H",
        "equation": "H(x) = integral [w_ROS C_ROS + w_cytokine C_cytokine] dt", "units": "dimensionless",
        "meaning": "Time-integrated weighted exposure to the modelled ROS-like and cytokine-like fields. Higher values indicate stronger accumulated modelled signalling.",
        "exclusions": "Not physical dose, measured concentration, toxicity probability or clinical risk. Advanced technical synonym: hazard field.",
    },
    {
        "key": "scaled_nonlocal_exposure", "group": "Mechanism", "label": "Scaled exposure sH",
        "equation": "sH(x)", "units": "log-survival decrement",
        "meaning": "Configured non-local decrement applied to log survival.",
        "exclusions": "Not toxicity, risk or absorbed dose.",
    },
    {
        "key": "nonlocal_survival_multiplier", "group": "Consequence", "label": "Non-local multiplier exp(-sH)",
        "equation": "M_NL(x) = exp[-sH(x)]", "units": "fraction of LQ baseline retained",
        "meaning": "Fraction of baseline LQ survival retained after the configured non-local model.",
        "exclusions": "Not a measured survival fraction or clinical probability.",
    },
    {
        "key": "additional_modelled_survival_reduction_percent", "group": "Consequence",
        "label": "Additional modelled survival reduction relative to LQ",
        "equation": "B_NL(x) = 100[1-exp(-sH(x))]", "units": "% relative to LQ baseline",
        "meaning": "Relative consequence of non-local signalling in the configured model.",
        "exclusions": "Never interpret as toxicity or cell-killing probability.",
    },
    {
        "key": "final_survival_fraction", "group": "Consequence", "label": "Final survival S_final",
        "equation": "S_final(x) = S_LQ(x) exp[-sH(x)]", "units": "fraction",
        "meaning": "Configured LQ baseline plus the no-sink non-local model.",
        "exclusions": "Not measured survival or patient-outcome probability.",
    },
    {
        "key": "additional_model_derived_effect_equivalent_dose_gy", "group": "Consequence",
        "label": "Additional effect-equivalent dose", "equation": "D_eq,final - D_eq,LQ", "units": "Gy-equivalent",
        "meaning": "Dose-equivalent representation of the configured model consequence.",
        "exclusions": "Not delivered or additional physical dose.",
    },
    {
        "key": "baseline_lq_effect_equivalent_dose_gy", "group": "Advanced", "label": "Baseline LQ effect-equivalent dose",
        "equation": "LQ inversion of S_LQ", "units": "Gy-equivalent", "meaning": "Baseline effect-equivalent field.",
        "exclusions": "Not absorbed dose.",
    },
    {
        "key": "biological_effect_equivalent_dose_gy", "group": "Advanced", "label": "Final biological effect-equivalent dose",
        "equation": "LQ inversion of S_final", "units": "Gy-equivalent", "meaning": "Final configured effect-equivalent field.",
        "exclusions": "Not absorbed dose or clinical outcome.",
    },
    {
        "key": "ros_like_concentration", "group": "Advanced", "label": "ROS-like model concentration",
        "equation": "C_ROS(x,t_final)", "units": "model concentration", "meaning": "Final numerical ROS-like mediator state.",
        "exclusions": "Not a measured biomarker concentration.",
    },
    {
        "key": "cytokine_like_concentration", "group": "Advanced", "label": "Cytokine-like model concentration",
        "equation": "C_cytokine(x,t_final)", "units": "model concentration", "meaning": "Final numerical cytokine-like mediator state.",
        "exclusions": "Not a measured biomarker concentration.",
    },
]
FIELD_METADATA = {item["key"]: item for item in FIELD_CATALOG}
FIELD_LABELS = {item["key"]: item["label"] for item in FIELD_CATALOG}
DEFAULT_FIELD = "additional_modelled_survival_reduction_percent"


@dataclass
class Layer32ViewerData:
    """Verified field archive plus stored presentation records."""
    fields: dict[str, np.ndarray]
    oar_masks: list[tuple[str, np.ndarray]]
    edge_profiles: list[dict[str, Any]]
    edge_metrics: list[dict[str, Any]]
    geometry: dict[str, Any]
    gtv_surface: ScalarSurface
    vertex_surface: ScalarSurface
    oar_surfaces: list[tuple[str, ScalarSurface]]
    parameters: dict[str, Any]
    comparison_scenarios: list[dict[str, Any]]
    regional_records: list[dict[str, Any]]


def prepare_layer32_viewer_data(case: ASCENDCase) -> Layer32ViewerData:
    """Load only the hash-verified artifact referenced by the current run."""
    result = case.layer3_2.result or {}
    if case.layer3_2.calculation_status not in {"completed", "completed_with_warnings"}:
        raise ValueError("A current completed Layer 3.2 run is required.")
    artifact = result.get("artifacts", {})
    path = Path(str(artifact.get("fields_path") or ""))
    if not path.is_file() and case.layer3_2.result_path:
        path = Path(case.layer3_2.result_path).parent / "layer3_2_fields.npz"
    if not path.is_file() or file_hash(path) != artifact.get("fields_sha256"):
        raise ValueError("Layer 3.2 field archive is missing or its hash differs.")
    with np.load(path, allow_pickle=False) as archive:
        stored_keys = {
            "physical_absorbed_dose_gy", "baseline_lq_effect_equivalent_dose_gy",
            "biological_effect_equivalent_dose_gy", "additional_model_derived_effect_equivalent_dose_gy",
            "cumulative_nonlocal_hazard", "final_survival_fraction",
            "ros_like_concentration", "cytokine_like_concentration",
        }
        fields = {key: np.asarray(archive[key]) for key in stored_keys}
        for key in FIELD_LABELS:
            if key in archive.files:
                fields[key] = np.asarray(archive[key])
        oar_masks = [
            (str(item.get("oar_name")), np.asarray(archive[str(item["array_key"])], dtype=bool))
            for item in artifact.get("oar_mask_arrays", [])
            if str(item.get("array_key")) in archive.files
        ]
        fields["gtv_mask"] = np.asarray(archive["gtv_mask"], dtype=bool)
        fields["vertex_union_mask"] = np.asarray(archive["vertex_union_mask"], dtype=bool)
    parameters = dict((result.get("model") or {}).get("parameters") or {})
    scaling = float(parameters.get("nonlocal_scaling", 0.0))
    exposure = np.asarray(fields["cumulative_nonlocal_hazard"], dtype=np.float32)
    fields.setdefault("scaled_nonlocal_exposure", (scaling * exposure).astype(np.float32))
    fields.setdefault("nonlocal_survival_multiplier", np.exp(-fields["scaled_nonlocal_exposure"]).astype(np.float32))
    fields.setdefault(
        "additional_modelled_survival_reduction_percent",
        (100.0 * (1.0 - fields["nonlocal_survival_multiplier"])).astype(np.float32),
    )
    fields.setdefault(
        "baseline_lq_survival_fraction",
        np.clip(
            np.divide(
                fields["final_survival_fraction"], fields["nonlocal_survival_multiplier"],
                out=np.ones_like(fields["final_survival_fraction"], dtype=np.float32),
                where=fields["nonlocal_survival_multiplier"] > 0,
            ), 1.0e-10, 1.0,
        ).astype(np.float32),
    )
    shape = fields["physical_absorbed_dose_gy"].shape
    if any(array.shape != shape for array in fields.values()) or any(mask.shape != shape for _name, mask in oar_masks):
        raise ValueError("Layer 3.2 viewer arrays do not share one stored crop geometry.")
    geometry = dict((result.get("geometry") or {}).get("model_crop_geometry") or {})
    if not geometry:
        raise ValueError("Layer 3.2 result does not contain its stored model-crop geometry.")
    return Layer32ViewerData(
        fields, oar_masks, result.get("edge_profiles", []), result.get("edge_metrics", []), geometry,
        mask_surface(fields["gtv_mask"], geometry),
        mask_surface(fields["vertex_union_mask"], geometry),
        [(name, mask_surface(mask, geometry)) for name, mask in oar_masks if mask.any()],
        parameters, list(result.get("comparison_scenarios", [])),
        list(result.get("modelled_regional_exposure_and_consequence", [])),
    )


def _slice(array: np.ndarray, orientation: str, index: int) -> np.ndarray:
    if orientation == "axial":
        return np.asarray(array[index, :, :])
    if orientation == "sagittal":
        return np.asarray(array[:, :, index])
    return np.asarray(array[:, index, :])


def _colour_map(values: np.ndarray, scale: tuple[float, float]) -> tuple[np.ndarray, float, float]:
    """Map one plane using a fixed complete-field viridis scale."""
    finite = np.asarray(values, dtype=np.float32)
    minimum, maximum = map(float, scale)
    if maximum <= minimum:
        scaled = np.zeros_like(finite)
    else:
        scaled = np.clip((finite - minimum) / (maximum - minimum), 0.0, 1.0)
    anchors = np.asarray([
        [68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37],
    ], dtype=float)
    position = scaled * (len(anchors) - 1)
    lower = np.floor(position).astype(int); upper = np.minimum(lower + 1, len(anchors) - 1)
    fraction = (position - lower)[..., None]
    rgb = anchors[lower] * (1.0 - fraction) + anchors[upper] * fraction
    return np.asarray(np.round(rgb), dtype=np.uint8), minimum, maximum


class Layer32FieldCanvas(QWidget):
    """Render one stored plane with target and OAR boundaries."""

    def __init__(self) -> None:
        super().__init__()
        self.data: Layer32ViewerData | None = None
        self.field = DEFAULT_FIELD
        self.orientation = "axial"
        self.index = 0
        self.scale_mode = "absolute"
        self.selected_zyx = (0, 0, 0)
        self.zoom = 1.0
        self.rotation_degrees = 0.0; self.pan = QPointF(); self._drag_position: QPointF | None = None
        self.show_gtv = True; self.show_vertices = True; self.show_oars = True
        self.setMinimumSize(300, 300)
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

    def set_view(
        self, data: Layer32ViewerData, field: str, orientation: str, index: int,
        *, scale_mode: str = "absolute", selected_zyx: tuple[int, int, int] = (0, 0, 0), zoom: float = 1.0,
        overlays: tuple[bool, bool, bool] = (True, True, True),
    ) -> None:
        self.data, self.field, self.orientation, self.index = data, field, orientation, index
        self.scale_mode, self.selected_zyx, self.zoom = scale_mode, selected_zyx, max(float(zoom), 1.0)
        self.show_gtv, self.show_vertices, self.show_oars = overlays
        self.update()

    def _scale(self) -> tuple[float, float]:
        values = np.asarray(self.data.fields[self.field], dtype=float)
        finite = values[np.isfinite(values)]
        if self.scale_mode == "case_relative" and len(finite):
            low, high = map(float, np.percentile(finite, [1.0, 99.0]))
            if low < high: return low, high
        return scalar_range(values)

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#071a38"))
        if self.data is None:
            painter.setPen(QColor("#d6e2ef")); painter.drawText(self.rect(), Qt.AlignCenter, "No stored Layer 3.2 field")
            return
        plane = _slice(self.data.fields[self.field], self.orientation, self.index)
        scale = self._scale(); rgb, minimum, maximum = _colour_map(plane, scale)
        overlays = []
        if self.show_gtv: overlays.append((_slice(self.data.fields["gtv_mask"], self.orientation, self.index), np.asarray([255, 220, 50], dtype=np.uint8)))
        if self.show_vertices: overlays.append((_slice(self.data.fields["vertex_union_mask"], self.orientation, self.index), np.asarray([20, 225, 245], dtype=np.uint8)))
        if self.show_oars:
            colours = anatomy_colour_map(f"OAR: {name}" for name, _mask in self.data.oar_masks)
            overlays.extend(
                (
                    _slice(mask, self.orientation, self.index),
                    np.asarray(QColor(colours[f"OAR: {name}"]).getRgb()[:3], dtype=np.uint8),
                )
                for name, mask in self.data.oar_masks
            )
        for mask, colour in overlays:
            boundary = np.asarray(mask, dtype=bool) & ~ndimage.binary_erosion(np.asarray(mask, dtype=bool))
            rgb[boundary] = colour
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
        target = self.rect().adjusted(12, 38, -12, -58)
        scaled = image.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if self.zoom > 1.0:
            scaled = image.scaled(int(scaled.width() * self.zoom), int(scaled.height() * self.zoom), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = target.x() + (target.width() - scaled.width()) // 2; y = target.y() + (target.height() - scaled.height()) // 2
        painter.save(); painter.setClipRect(target)
        center = QPointF(x + scaled.width()/2.0, y + scaled.height()/2.0) + self.pan
        painter.translate(center); painter.rotate(self.rotation_degrees)
        painter.drawImage(QPointF(-scaled.width()/2.0, -scaled.height()/2.0), scaled)
        selected_axis = {"axial": 0, "coronal": 1, "sagittal": 2}[self.orientation]
        if self.selected_zyx[selected_axis] == self.index:
            if self.orientation == "axial": horizontal, vertical = self.selected_zyx[2], self.selected_zyx[1]
            elif self.orientation == "coronal": horizontal, vertical = self.selected_zyx[2], self.selected_zyx[0]
            else: horizontal, vertical = self.selected_zyx[1], self.selected_zyx[0]
            cross_x = -scaled.width()/2.0 + horizontal * scaled.width() / max(plane.shape[1] - 1, 1)
            cross_y = -scaled.height()/2.0 + vertical * scaled.height() / max(plane.shape[0] - 1, 1)
            cross_x, cross_y = int(round(cross_x)), int(round(cross_y))
            painter.setPen(QPen(QColor("#ffffff"), 1)); painter.drawLine(cross_x - 8, cross_y, cross_x + 8, cross_y); painter.drawLine(cross_x, cross_y - 8, cross_x, cross_y + 8)
        painter.restore()
        painter.setPen(QColor("#eef5fb"))
        painter.drawText(12, 22, f"{FIELD_LABELS[self.field]} · {self.orientation} slice {self.index}")
        bar_left, bar_right, bar_y = 55, max(self.width() - 55, 56), self.height() - 27
        for offset in range(max(bar_right - bar_left, 1)):
            colour = _colour_map(np.asarray([[minimum + (maximum-minimum)*offset/max(bar_right-bar_left-1, 1)]]), (minimum, maximum))[0][0, 0]
            painter.setPen(QColor(*map(int, colour))); painter.drawLine(bar_left + offset, bar_y, bar_left + offset, bar_y + 10)
        selected_value = float(self.data.fields[self.field][self.selected_zyx])
        marker = bar_left + np.clip((selected_value - minimum) / max(maximum - minimum, 1.0e-12), 0, 1) * (bar_right - bar_left)
        marker = int(round(marker)); painter.setPen(QPen(QColor("#ffffff"), 2)); painter.drawLine(marker, bar_y - 4, marker, bar_y + 14)
        painter.setPen(QColor("#eef5fb")); painter.drawText(8, bar_y + 10, f"{minimum:.3g}"); painter.drawText(bar_right + 5, bar_y + 10, f"{maximum:.3g}")
        painter.drawText(12, self.height() - 42, f"Fixed complete-field scale · selected {selected_value:.5g} · {FIELD_METADATA[self.field]['units']} · {self.zoom:.2g}× · {self.rotation_degrees:.0f}°")


class Layer32ProfileCanvas(QWidget):
    """Plot stored physical and biological edge profiles without recalculation."""

    def __init__(self) -> None:
        super().__init__()
        self.profile: dict[str, Any] | None = None
        # Do not name this attribute ``metric``. QWidget inherits the virtual
        # QPaintDevice.metric() method and QPainter calls it while initialising
        # against this widget. Shadowing it with a Python dictionary causes
        # PySide/Shiboken to treat the dictionary as a Python override and can
        # terminate the process with EXC_BAD_ACCESS on macOS.
        self.metric_record: dict[str, Any] | None = None
        self.setMinimumSize(420, 300)

    def set_profile(self, profile: dict[str, Any] | None, metric: dict[str, Any] | None) -> None:
        self.profile, self.metric_record = profile, metric
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#ffffff"))
        if not self.profile:
            painter.setPen(QColor("#4b5e70")); painter.drawText(self.rect(), Qt.AlignCenter, "No stored edge profile")
            return
        distance = np.asarray(self.profile.get("distance_mm", []), dtype=float)
        physical = np.asarray(self.profile.get("physical_absorbed_dose_gy", []), dtype=float)
        effect = np.asarray(self.profile.get("biological_effect_equivalent_dose_gy", []), dtype=float)
        if not len(distance):
            return
        left, top, right, bottom = 54, 38, self.width() - 22, self.height() - 48
        maximum = max(float(np.max(physical)), float(np.max(effect)), 1.0)
        def points(values: np.ndarray) -> QPolygonF:
            from PySide6.QtCore import QPointF
            return QPolygonF([QPointF(left + (right-left)*d/max(distance[-1], 1e-9), bottom-(bottom-top)*v/maximum) for d, v in zip(distance, values)])
        painter.setPen(QPen(QColor("#5c6f82"), 1)); painter.drawLine(left, bottom, right, bottom); painter.drawLine(left, top, left, bottom)
        painter.setPen(QPen(QColor("#2463a0"), 2)); painter.drawPolyline(points(physical))
        painter.setPen(QPen(QColor("#b33b32"), 2)); painter.drawPolyline(points(effect))
        metric = self.metric_record or {}
        painter.setPen(QColor("#1d2d3d"))
        painter.drawText(12, 20, f"Edge {self.profile.get('edge_id')} · physical blue · effect-equivalent red")
        painter.drawText(12, self.height()-16, f"Physical iPVDR {metric.get('physical_ipvdr', '—')} · Biological iPVDR {metric.get('biological_effect_equivalent_ipvdr', '—')} · Shift {metric.get('biological_ipvdr_shift', '—')}")


class LegacyLayer32Scene3D(QWidget):
    """Qt3D scene for scalar shells and stored anatomical boundaries."""

    CROP_EDGES = (
        (0, 1), (0, 2), (1, 3), (2, 3), (4, 5), (4, 6),
        (5, 7), (6, 7), (0, 4), (1, 5), (2, 6), (3, 7),
    )

    def __init__(self) -> None:
        super().__init__()
        self.window = Qt3DExtras.Qt3DWindow()
        self.window.defaultFrameGraph().setClearColor(QColor("#071a38"))
        self.container = QWidget.createWindowContainer(self.window, self)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.container)
        self.root = Qt3DCore.QEntity(); self.window.setRootEntity(self.root)
        self.camera = self.window.camera(); self.camera.lens().setPerspectiveProjection(35.0, 16.0 / 9.0, 0.1, 10000.0)
        self.camera_controller = Qt3DExtras.QOrbitCameraController(self.root)
        self.camera_controller.setCamera(self.camera); self.camera_controller.setLinearSpeed(80.0); self.camera_controller.setLookSpeed(140.0)
        self.data: Layer32ViewerData | None = None
        self.entities: list[Any] = []
        self.groups: dict[str, list[Any]] = {name: [] for name in ("scalar", "gtv", "vertices", "oars", "crop")}
        self.center = np.zeros(3, dtype=float); self.distance = 100.0
        self.setMinimumSize(620, 500)

    def clear_scene(self) -> None:
        for entity in self.entities:
            entity.setParent(None); entity.deleteLater()
        self.entities = []; self.groups = {name: [] for name in self.groups}

    def _surface_entity(self, surface: ScalarSurface, colour: QColor, alpha: float, group: str) -> Any:
        entity = Qt3DCore.QEntity(self.root)
        entity.addComponent(_mesh_renderer(surface, entity)); entity.addComponent(_material(entity, colour, alpha))
        self.entities.append(entity); self.groups[group].append(entity)
        return entity

    def _cylinder(self, first: np.ndarray, second: np.ndarray, radius: float, colour: QColor, group: str) -> Any:
        delta = second - first; length = float(np.linalg.norm(delta))
        entity = Qt3DCore.QEntity(self.root)
        mesh = Qt3DExtras.QCylinderMesh(entity); mesh.setRadius(radius); mesh.setLength(length); mesh.setRings(3); mesh.setSlices(10)
        transform = Qt3DCore.QTransform(entity); transform.setTranslation(QVector3D(*map(float, (first + second) / 2.0)))
        transform.setRotation(QQuaternion.rotationTo(QVector3D(0, 1, 0), QVector3D(*map(float, delta / length))))
        entity.addComponent(mesh); entity.addComponent(transform); entity.addComponent(_material(entity, colour, 0.75))
        self.entities.append(entity); self.groups[group].append(entity)
        return entity

    def set_data(self, data: Layer32ViewerData, scalar_surfaces: list[ScalarSurface], opacity: float) -> None:
        self.clear_scene(); self.data = data
        count = max(len(scalar_surfaces), 1)
        for index, surface in enumerate(scalar_surfaces):
            colour = QColor(*map(int, surface.rgb[0]))
            shell_alpha = max(0.08, min(float(opacity) * (0.55 + 0.45 * (index + 1) / count), 0.96))
            self._surface_entity(surface, colour, shell_alpha, "scalar")
        self._surface_entity(data.gtv_surface, QColor("#f3d15f"), 0.20, "gtv")
        self._surface_entity(data.vertex_surface, QColor("#17cce0"), 0.38, "vertices")
        for _name, surface in data.oar_surfaces:
            self._surface_entity(surface, QColor("#e95ad1"), 0.22, "oars")
        corners = crop_corners_lps(data.geometry)
        for first, second in self.CROP_EDGES:
            self._cylinder(corners[first], corners[second], 0.28, QColor("#9fb4c8"), "crop")
        low, high = np.min(corners, axis=0), np.max(corners, axis=0)
        self.center = (low + high) / 2.0; self.distance = max(float(np.linalg.norm(high - low)) * 1.35, 40.0)
        light_entity = Qt3DCore.QEntity(self.root)
        light = Qt3DRender.QPointLight(light_entity); light.setColor(QColor("#ffffff")); light.setIntensity(1.3)
        transform = Qt3DCore.QTransform(light_entity)
        transform.setTranslation(QVector3D(*map(float, self.center + np.asarray([0.4, -0.6, 1.4]) * self.distance)))
        light_entity.addComponent(light); light_entity.addComponent(transform); self.entities.append(light_entity)
        self.set_view("perspective")

    def set_view(self, name: str) -> None:
        if self.data is None:
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
        for entity in self.groups.get(group, []):
            entity.setEnabled(bool(visible))


Layer32Scene3D = Layer32PyVistaScene3D


class Layer32Viewer(QWidget):
    """3D scalar/CAD workstation plus orthogonal field and graph evidence views."""

    def __init__(self) -> None:
        super().__init__()
        self.data: Layer32ViewerData | None = None
        layout = QVBoxLayout(self)
        common = QHBoxLayout(); self.field = QComboBox()
        for item in FIELD_CATALOG:
            self.field.addItem(f"{item['group']} — {item['label']}", item["key"])
        self.field.setCurrentIndex(max(self.field.findData(DEFAULT_FIELD), 0))
        self.orientation = QComboBox()
        for label, key in (("Axial", "axial"), ("Sagittal", "sagittal"), ("Coronal", "coronal")): self.orientation.addItem(label, key)
        self.scale_mode = QComboBox()
        self.scale_mode.addItem("Absolute complete-field scale", "absolute")
        self.scale_mode.addItem("Case-relative exploratory scale", "case_relative")
        self.comparison_mode = QComboBox()
        for label, key in (
            ("Three-panel biological consequence", "three_panel"),
            ("Baseline versus final survival", "baseline_final"),
            ("No-sink versus anatomical-sink", "sink_comparison"),
            ("Physical versus effect-equivalent dose", "physical_effect"),
            ("Absolute difference map", "difference"),
        ):
            self.comparison_mode.addItem(label, key)
        self.zoom_slider = QSlider(Qt.Horizontal); self.zoom_slider.setRange(100, 300); self.zoom_slider.setValue(100)
        self.edge = QComboBox()
        common.addWidget(QLabel("Stored field")); common.addWidget(self.field, 2)
        common.addWidget(QLabel("2D plane")); common.addWidget(self.orientation)
        common.addWidget(QLabel("Scale")); common.addWidget(self.scale_mode)
        common.addWidget(QLabel("Comparison")); common.addWidget(self.comparison_mode)
        common.addWidget(QLabel("Zoom")); common.addWidget(self.zoom_slider, 1)
        for label, operation in (("↺", lambda: self._transform_all_2d("rotate", -90)),
                                 ("↻", lambda: self._transform_all_2d("rotate", 90)),
                                 ("Fit", lambda: self._transform_all_2d("reset", 0))):
            button = QPushButton(label); button.setToolTip("Rotate or reset all synchronized biological panels"); button.clicked.connect(operation); common.addWidget(button)
        layout.addLayout(common)
        slice_row = QHBoxLayout(); self.slider = QSlider(Qt.Horizontal)
        slice_row.addWidget(QLabel("Synchronized slice")); slice_row.addWidget(self.slider, 1)
        layout.addLayout(slice_row)

        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)

        comparison_page = QWidget(); comparison_layout = QHBoxLayout(comparison_page)
        comparison_split = QSplitter(Qt.Horizontal)
        comparison_canvases = QWidget(); canvas_layout = QGridLayout(comparison_canvases)
        anatomy_notice = QLabel(
            "Stored GTV, vertex, and OAR boundaries are shown. CT pixels are not present in the "
            "Layer 3.2 artifact, so a CT heat-map overlay is unavailable; no synthetic anatomy is displayed."
        )
        anatomy_notice.setWordWrap(True); canvas_layout.addWidget(anatomy_notice, 2, 0, 1, 3)
        self.comparison_canvases: list[tuple[str, Layer32FieldCanvas]] = []
        self.comparison_labels: list[QLabel] = []
        for column, (title, key) in enumerate((
            ("Baseline LQ survival", "baseline_lq_survival_fraction"),
            ("Final survival", "final_survival_fraction"),
            ("Additional modelled reduction", "additional_modelled_survival_reduction_percent"),
        )):
            label = QLabel(title); label.setAlignment(Qt.AlignCenter)
            canvas = Layer32FieldCanvas(); canvas.setMinimumSize(250, 280)
            canvas_layout.addWidget(label, 0, column); canvas_layout.addWidget(canvas, 1, column)
            self.comparison_canvases.append((key, canvas))
            self.comparison_labels.append(label)
        comparison_split.addWidget(comparison_canvases)
        interpretation = QWidget(); interpretation_layout = QVBoxLayout(interpretation)
        interpretation_layout.addWidget(QLabel("Field interpretation"))
        self.interpretation_text = QLabel(); self.interpretation_text.setWordWrap(True)
        self.interpretation_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        interpretation_layout.addWidget(self.interpretation_text)
        self.calculation_chain = QLabel("Select a stored voxel to inspect the calculation chain.")
        self.calculation_chain.setWordWrap(True); self.calculation_chain.setTextInteractionFlags(Qt.TextSelectableByMouse)
        interpretation_layout.addWidget(QLabel("Selected voxel calculation chain"))
        interpretation_layout.addWidget(self.calculation_chain)
        self.scenario_status = QLabel(); self.scenario_status.setWordWrap(True)
        interpretation_layout.addWidget(QLabel("Comparison states")); interpretation_layout.addWidget(self.scenario_status)
        interpretation_layout.addStretch()
        comparison_split.addWidget(interpretation); comparison_split.setSizes([980, 340])
        comparison_layout.addWidget(comparison_split)
        self.tabs.addTab(comparison_page, "Synchronized biological comparison")

        spatial_page = QWidget(); spatial_layout = QVBoxLayout(spatial_page)
        controls = QGridLayout()
        self.render_mode = QComboBox()
        self.render_mode.addItem("Full biological volume", "VOLUME")
        self.render_mode.addItem("Biological isosurfaces", "ISOSURFACE")
        self.render_mode.addItem("Orthogonal biological slices", "SLICE")
        self.render_mode.addItem("Combined volume + isosurfaces", "COMBINED")
        self.region_focus = QComboBox()
        self.region_focus.addItem("Complete model domain", "Model domain")
        self.region_focus.addItem("GTV", "GTV")
        self.region_focus.addItem("Vertices", "Vertices")
        self.volume_opacity_preset = QComboBox()
        self.volume_opacity_preset.addItem("Biological effect", "biological_effect")
        self.volume_opacity_preset.addItem("High effect", "high_effect")
        self.volume_opacity_preset.addItem("Linear", "linear")
        self.iso_slider = QSlider(Qt.Horizontal); self.iso_slider.setRange(1, 99); self.iso_slider.setValue(50)
        self.iso_label = QLabel("Detailed-field isovalue 50%")
        self.effect_surfaces = QCheckBox("Absolute consequence surfaces"); self.effect_surfaces.setChecked(True)
        self.opacity = QSlider(Qt.Horizontal); self.opacity.setRange(5, 95); self.opacity.setValue(38)
        self.opacity_label = QLabel("Opacity 38%")
        self.clip_orientation = QComboBox()
        for label, value in (("No clipping", None), ("Axial clipping", 0), ("Coronal clipping", 1), ("Sagittal clipping", 2)):
            self.clip_orientation.addItem(label, value)
        self.clip_slider = QSlider(Qt.Horizontal); self.clip_slider.setRange(0, 100); self.clip_slider.setValue(50); self.clip_slider.setEnabled(False)
        controls.addWidget(QLabel("3D mode"), 0, 0); controls.addWidget(self.render_mode, 0, 1)
        controls.addWidget(QLabel("Region focus"), 0, 2); controls.addWidget(self.region_focus, 0, 3)
        controls.addWidget(self.volume_opacity_preset, 0, 4)
        controls.addWidget(self.iso_label, 1, 0); controls.addWidget(self.iso_slider, 1, 1, 1, 3)
        controls.addWidget(self.effect_surfaces, 1, 4); controls.addWidget(self.opacity_label, 2, 0); controls.addWidget(self.opacity, 2, 1, 1, 2)
        controls.addWidget(self.clip_orientation, 2, 3); controls.addWidget(self.clip_slider, 2, 4)
        self.show_gtv = QCheckBox("GTV"); self.show_gtv.setChecked(True)
        self.show_vertices = QCheckBox("Vertices"); self.show_vertices.setChecked(True)
        self.show_oars = QCheckBox("OARs"); self.show_oars.setChecked(True)
        self.show_crop = QCheckBox("Model-crop boundary"); self.show_crop.setChecked(True)
        views = QWidget(); view_layout = QHBoxLayout(views); view_layout.setContentsMargins(0, 0, 0, 0)
        for label, name in (("Perspective", "perspective"), ("Axial", "axial"), ("Sagittal", "sagittal"), ("Coronal", "coronal")):
            button = QPushButton(label); button.clicked.connect(lambda _checked=False, value=name: self.scene.set_view(value)); view_layout.addWidget(button)
        for label, operation in (("Zoom in", lambda: self.scene.zoom_by(0.82)), ("Zoom out", lambda: self.scene.zoom_by(1.22)),
                                 ("Rotate left", lambda: self.scene.rotate_by(-15)), ("Rotate right", lambda: self.scene.rotate_by(15))):
            button = QPushButton(label); button.clicked.connect(operation); view_layout.addWidget(button)
        self.export_button = QPushButton("Export VTI / VTP / PLY / GLB / 3MF / STL")
        self.export_button.clicked.connect(self._export_spatial)
        self.screenshot_button = QPushButton("Export view PNG")
        self.screenshot_button.clicked.connect(self._export_screenshot)
        view_layout.addWidget(self.screenshot_button)
        controls.addWidget(self.show_gtv, 3, 0); controls.addWidget(self.show_vertices, 3, 1)
        controls.addWidget(self.show_oars, 3, 2); controls.addWidget(self.show_crop, 3, 3); controls.addWidget(self.export_button, 3, 4)
        controls.addWidget(views, 4, 0, 1, 5)
        self.surface_checks: dict[float, QCheckBox] = {}
        self.surface_labels: dict[float, QLabel] = {}
        for column, threshold in enumerate((2.5, 5.0, 10.0, 20.0)):
            check = QCheckBox(f"{threshold:g}% reduction")
            check.setChecked(threshold in (5.0, 10.0))
            label = QLabel(""); label.setWordWrap(True)
            controls.addWidget(check, 5, column); controls.addWidget(label, 6, column)
            self.surface_checks[threshold] = check; self.surface_labels[threshold] = label
        spatial_layout.addLayout(controls)
        split3d = QSplitter(Qt.Horizontal); self.scene = Layer32PyVistaScene3D(); split3d.addWidget(self.scene)
        probe = QWidget(); probe_layout = QVBoxLayout(probe)
        probe_layout.addWidget(QLabel("Voxel probe — stored crop indices"))
        self.probe_spins: list[QSpinBox] = []
        probe_grid = QGridLayout()
        for row, label in enumerate(("Z", "Y", "X")):
            spin = QSpinBox(); spin.valueChanged.connect(self._update_probe); self.probe_spins.append(spin)
            probe_grid.addWidget(QLabel(label), row, 0); probe_grid.addWidget(spin, row, 1)
        probe_layout.addLayout(probe_grid)
        self.probe_result = QLabel("Build the viewer to inspect stored voxel values."); self.probe_result.setWordWrap(True)
        probe_layout.addWidget(self.probe_result)
        self.spatial_status = QLabel("The wireframe box is the stored GTV-plus-margin model crop, not a whole-patient calculation.")
        self.spatial_status.setWordWrap(True); probe_layout.addWidget(self.spatial_status); probe_layout.addStretch()
        split3d.addWidget(probe); split3d.setSizes([900, 260]); spatial_layout.addWidget(split3d, 1)
        self.tabs.addTab(spatial_page, "3D biological volume / structures")

        plane_page = QWidget(); plane_layout = QVBoxLayout(plane_page)
        split = QSplitter(Qt.Horizontal); self.field_canvas = Layer32FieldCanvas(); self.profile_canvas = Layer32ProfileCanvas()
        split.addWidget(self.field_canvas); split.addWidget(self.profile_canvas); split.setSizes([700, 520]); plane_layout.addWidget(split, 1)
        edge_row = QHBoxLayout(); edge_row.addWidget(QLabel("Edge profile")); edge_row.addWidget(self.edge, 1)
        plane_layout.insertLayout(0, edge_row)
        self.tabs.addTab(plane_page, "Single field / graph profile")

        self.field.currentIndexChanged.connect(self._field_changed)
        self.orientation.currentIndexChanged.connect(self._orientation_changed)
        self.edge.currentIndexChanged.connect(self._profile_changed)
        self.slider.valueChanged.connect(self._refresh_2d)
        self.scale_mode.currentIndexChanged.connect(self._refresh_2d)
        self.comparison_mode.currentIndexChanged.connect(self._comparison_changed)
        self.zoom_slider.valueChanged.connect(self._refresh_2d)
        self.iso_slider.valueChanged.connect(lambda value: self.iso_label.setText(f"Relative isovalue {value}%"))
        self.iso_slider.sliderReleased.connect(self._rebuild_3d)
        self.opacity.valueChanged.connect(lambda value: self.opacity_label.setText(f"Opacity {value}%"))
        self.opacity.sliderReleased.connect(self._rebuild_3d)
        self.effect_surfaces.toggled.connect(self._render_mode_changed)
        self.render_mode.currentIndexChanged.connect(self._render_mode_changed)
        self.region_focus.currentIndexChanged.connect(self._rebuild_3d)
        self.volume_opacity_preset.currentIndexChanged.connect(self._rebuild_3d)
        for check in self.surface_checks.values():
            check.toggled.connect(self._rebuild_3d)
        self.clip_orientation.currentIndexChanged.connect(self._clip_changed)
        self.clip_slider.sliderReleased.connect(self._rebuild_3d)
        for checkbox, group in ((self.show_gtv, "gtv"), (self.show_vertices, "vertices"), (self.show_oars, "oars"), (self.show_crop, "crop")):
            checkbox.toggled.connect(lambda visible, name=group: self.scene.set_visibility(name, visible))
            if group != "crop": checkbox.toggled.connect(self._refresh_2d)
        self._render_mode_changed()

    def set_data(self, data: Layer32ViewerData) -> None:
        self.data = data; self.edge.clear()
        while self.region_focus.count() > 3:
            self.region_focus.removeItem(3)
        for name, mask in sorted(data.oar_masks, key=lambda item: item[0]):
            if np.asarray(mask, dtype=bool).any():
                self.region_focus.addItem(name, f"OAR: {name}")
        for profile in data.edge_profiles:
            self.edge.addItem(f"Edge {profile.get('edge_id')} · {' — '.join(profile.get('nodes', []))}", profile.get("edge_id"))
        shape = data.fields["physical_absorbed_dose_gy"].shape
        for spin, maximum in zip(self.probe_spins, shape):
            spin.blockSignals(True); spin.setRange(0, maximum - 1); spin.setValue(maximum // 2); spin.blockSignals(False)
        scaling = float(data.parameters.get("nonlocal_scaling", 0.0))
        for threshold, label in self.surface_labels.items():
            h_value = equivalent_exposure_h(threshold, scaling) if scaling > 0 else None
            label.setText(f"H = {h_value:.1f}" if h_value is not None else "H unavailable")
        scenario_lines = []
        for item in data.comparison_scenarios:
            scenario_lines.append(f"{item.get('label', item.get('scenario', '—'))}: {item.get('status', '—')}")
        scenario_lines.append(
            "Cross-case comparison: no second case loaded. Direct comparison is blocked whenever parameter configurations differ."
        )
        self.scenario_status.setText("\n".join(scenario_lines) or "Only the stored no-sink result is available.")
        self._comparison_changed(); self._orientation_changed(); self._rebuild_3d(); self._update_probe()

    def _render_mode_changed(self, _index: int = -1) -> None:
        surfaces_enabled = str(self.render_mode.currentData()) in {"ISOSURFACE", "COMBINED"}
        self.effect_surfaces.setEnabled(surfaces_enabled)
        self.iso_slider.setEnabled(surfaces_enabled and not self.effect_surfaces.isChecked())
        for check in self.surface_checks.values():
            check.setEnabled(surfaces_enabled and self.effect_surfaces.isChecked())
        self._rebuild_3d()

    def _comparison_changed(self, _index: int = -1) -> None:
        mode = str(self.comparison_mode.currentData())
        layouts = {
            "three_panel": (
                ("Baseline LQ survival", "baseline_lq_survival_fraction"),
                ("Final survival", "final_survival_fraction"),
                ("Additional modelled reduction", "additional_modelled_survival_reduction_percent"),
            ),
            "baseline_final": (
                ("Baseline LQ survival", "baseline_lq_survival_fraction"),
                ("Final survival", "final_survival_fraction"),
                ("Absolute relative difference", "additional_modelled_survival_reduction_percent"),
            ),
            "physical_effect": (
                ("Physical dose", "physical_absorbed_dose_gy"),
                ("Biological effect-equivalent dose", "biological_effect_equivalent_dose_gy"),
                ("Additional effect-equivalent dose", "additional_model_derived_effect_equivalent_dose_gy"),
            ),
            "difference": (
                ("Additional modelled survival reduction", "additional_modelled_survival_reduction_percent"),
            ),
            "sink_comparison": (
                ("No vascular sink — final survival", "final_survival_fraction"),
            ),
        }
        selected = layouts.get(mode, layouts["three_panel"])
        revised: list[tuple[str, Layer32FieldCanvas]] = []
        for index, (old_key, canvas) in enumerate(self.comparison_canvases):
            visible = index < len(selected)
            self.comparison_labels[index].setVisible(visible); canvas.setVisible(visible)
            if visible:
                title, key = selected[index]
                self.comparison_labels[index].setText(title); revised.append((key, canvas))
            else:
                revised.append((old_key, canvas))
        self.comparison_canvases = revised
        if mode == "sink_comparison":
            self.scenario_status.setText(
                "Non-local model — no vascular sink: calculated.\n"
                "Non-local model — anatomical vascular sink: NOT AVAILABLE. No validated vessel mask or uptake model is accepted."
            )
        elif self.data is not None:
            lines = [f"{item.get('label')}: {item.get('status')}" for item in self.data.comparison_scenarios]
            lines.append("Direct cross-case comparison is blocked when model configurations differ.")
            self.scenario_status.setText("\n".join(lines))
        self._refresh_2d()

    def _field_changed(self, _index: int = -1) -> None:
        self._refresh_2d(); self._rebuild_3d(); self._update_probe()

    def _orientation_changed(self, _index: int = -1) -> None:
        if self.data is None: return
        shape = self.data.fields["physical_absorbed_dose_gy"].shape
        axis = {"axial": 0, "sagittal": 2, "coronal": 1}[str(self.orientation.currentData())]
        self.slider.setRange(0, shape[axis]-1); self.slider.setValue(shape[axis]//2); self._refresh_2d()

    def _transform_all_2d(self, operation: str, value: float) -> None:
        canvases = [self.field_canvas, *(canvas for _field, canvas in self.comparison_canvases)]
        seen: set[int] = set()
        for canvas in canvases:
            if id(canvas) in seen: continue
            seen.add(id(canvas))
            if operation == "rotate": canvas.rotate_by(value)
            else: canvas.reset_view()
        if operation == "reset": self.zoom_slider.setValue(100)

    def _refresh_2d(self, _value: int = -1) -> None:
        if self.data is None: return
        selected = tuple(spin.value() for spin in self.probe_spins)
        overlays = (self.show_gtv.isChecked(), self.show_vertices.isChecked(), self.show_oars.isChecked())
        kwargs = {
            "scale_mode": str(self.scale_mode.currentData()), "selected_zyx": selected,
            "zoom": self.zoom_slider.value() / 100.0, "overlays": overlays,
        }
        orientation = str(self.orientation.currentData()); index = self.slider.value()
        self.field_canvas.set_view(self.data, str(self.field.currentData()), orientation, index, **kwargs)
        for field_name, canvas in self.comparison_canvases:
            canvas.set_view(self.data, field_name, orientation, index, **kwargs)
        self._profile_changed()

    def _profile_changed(self, _index: int = -1) -> None:
        if self.data is None: return
        edge_id = self.edge.currentData()
        profile = next((item for item in self.data.edge_profiles if item.get("edge_id") == edge_id), None)
        metric = next((item for item in self.data.edge_metrics if item.get("edge_id") == edge_id), None)
        self.profile_canvas.set_profile(profile, metric)

    def _clip_changed(self, _index: int = -1) -> None:
        self.clip_slider.setEnabled(self.clip_orientation.currentData() is not None)
        self._rebuild_3d()

    def _rebuild_3d(self, _value: Any = None) -> None:
        if self.data is None:
            return
        field_name = str(self.field.currentData()); field = self.data.fields[field_name]
        clip_axis = self.clip_orientation.currentData(); clip_index = None
        if clip_axis is not None:
            axis = int(clip_axis); clip_index = round((field.shape[axis] - 1) * self.clip_slider.value() / 100.0)
        try:
            mode = str(self.render_mode.currentData() or "VOLUME")
            needs_surfaces = mode in {"ISOSURFACE", "COMBINED"}
            surfaces: list[ScalarSurface] = []
            if needs_surfaces and self.effect_surfaces.isChecked():
                consequence = self.data.fields["additional_modelled_survival_reduction_percent"]
                thresholds = [threshold for threshold, check in self.surface_checks.items() if check.isChecked()]
                surfaces = consequence_threshold_surfaces(
                    consequence, self.data.geometry, thresholds,
                    clip_axis=clip_axis, clip_index=clip_index,
                )
                scaling = float(self.data.parameters.get("nonlocal_scaling", 0.0))
                voxel_cc = voxel_volume_cc(self.data.geometry)
                for threshold, label in self.surface_labels.items():
                    h_value = equivalent_exposure_h(threshold, scaling) if scaling > 0 else None
                    volume = float(np.count_nonzero(consequence >= threshold) * voxel_cc)
                    colour = CONSEQUENCE_SURFACE_COLOURS[threshold]
                    colour_hex = "#" + "".join(f"{channel:02X}" for channel in colour)
                    label.setText(
                        (f"H = {h_value:.1f}" if h_value is not None else "H unavailable")
                        + f" · {colour_hex} · {self.opacity.value()}% opacity · {volume:.3f} cc"
                    )
            elif needs_surfaces:
                fraction = self.iso_slider.value() / 100.0
                surfaces = [scalar_surface(field, self.data.geometry, scalar_level(field, fraction),
                                           display_fraction=fraction, reverse_palette=field_name == "final_survival_fraction",
                                           clip_axis=clip_axis, clip_index=clip_index)]
            if needs_surfaces and not surfaces:
                raise ValueError("No surface crosses the selected stored-field levels.")
            self.scene.set_data(
                self.data, surfaces, self.opacity.value() / 100.0,
                field_name=field_name, mode=mode,
                region_name=str(self.region_focus.currentData() or "Model domain"),
                opacity_preset=str(self.volume_opacity_preset.currentData() or "biological_effect"),
                clip_axis=clip_axis, clip_index=clip_index,
            )
            for checkbox, group in ((self.show_gtv, "gtv"), (self.show_vertices, "vertices"), (self.show_oars, "oars"), (self.show_crop, "crop")):
                self.scene.set_visibility(group, checkbox.isChecked())
            low, high = scalar_range(field)
            self.spatial_status.setText(
                f"{mode.title()} · {FIELD_LABELS[field_name]} · region {self.region_focus.currentData()} · "
                f"stored range {low:.5g}–{high:.5g} · {len(surfaces)} optional isosurface(s). "
                "Full voxel values remain unchanged in DICOM patient LPS. Invalid or out-of-region voxels are transparent, never zero. "
                "Outside-crop mediator exposure was not modelled. OAR overlays are model consequences, not toxicity or compliance."
            )
        except (RuntimeError, ValueError) as exc:
            self.scene.clear_scene(); self.spatial_status.setText(f"3D biological map blocked: {exc}")

    def _update_probe(self, _value: int = -1) -> None:
        if self.data is None:
            return
        z, y, x = [spin.value() for spin in self.probe_spins]
        point = indices_to_lps(np.asarray([[z, y, x]], dtype=float), self.data.geometry)[0]
        field_name = str(self.field.currentData()); value = float(self.data.fields[field_name][z, y, x])
        memberships = ["GTV"] if self.data.fields["gtv_mask"][z, y, x] else []
        if self.data.fields["vertex_union_mask"][z, y, x]: memberships.append("vertex")
        memberships.extend(name for name, mask in self.data.oar_masks if mask[z, y, x])
        dose = float(self.data.fields["physical_absorbed_dose_gy"][z, y, x])
        baseline = float(self.data.fields["baseline_lq_survival_fraction"][z, y, x])
        exposure = float(self.data.fields["cumulative_nonlocal_hazard"][z, y, x])
        scaling = float(self.data.parameters.get("nonlocal_scaling", 0.0))
        scaled = float(self.data.fields["scaled_nonlocal_exposure"][z, y, x])
        multiplier = float(self.data.fields["nonlocal_survival_multiplier"][z, y, x])
        reduction = float(self.data.fields["additional_modelled_survival_reduction_percent"][z, y, x])
        final = float(self.data.fields["final_survival_fraction"][z, y, x])
        self.probe_result.setText(
            f"LPS: {point[0]:.2f}, {point[1]:.2f}, {point[2]:.2f} mm\n"
            f"{FIELD_LABELS[field_name]}: {value:.7g}\n"
            f"Masks: {', '.join(memberships) if memberships else 'none'}"
        )
        self.calculation_chain.setText(
            f"Physical dose: {dose:.5g} Gy\n"
            f"Baseline LQ survival: {baseline:.6g}\n"
            f"Cumulative mediator exposure H: {exposure:.6g}\n"
            f"Scaling parameter s: {scaling:.8g}\n"
            f"Scaled exposure sH: {scaled:.6g}\n"
            f"Non-local multiplier: {multiplier:.6g}\n"
            f"Relative additional reduction: {reduction:.4g}%\n"
            f"Final survival: {final:.6g}"
        )
        metadata = FIELD_METADATA[field_name]
        low, high = scalar_range(self.data.fields[field_name])
        self.interpretation_text.setText(
            f"{metadata['label']}\n\nEquation: {metadata['equation']}\nUnits: {metadata['units']}\n"
            f"Whole stored-volume range: {low:.6g} to {high:.6g}\n\n"
            f"Interpretation: {metadata['meaning']}\n\nExplicit exclusions: {metadata['exclusions']}"
        )
        selected_axis = {"axial": 0, "coronal": 1, "sagittal": 2}[str(self.orientation.currentData())]
        self.slider.blockSignals(True); self.slider.setValue((z, y, x)[selected_axis]); self.slider.blockSignals(False)
        self._refresh_2d()

    def _export_spatial(self) -> None:
        if self.data is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export Layer 3.2 spatial package")
        if not folder:
            return
        masks = {
            "GTV": self.data.fields["gtv_mask"], "VTVH_vertices": self.data.fields["vertex_union_mask"],
            **{name: mask for name, mask in self.data.oar_masks},
        }
        try:
            outputs = export_spatial_package(
                {name: self.data.fields[name] for name in FIELD_LABELS}, masks,
                self.data.geometry, str(self.field.currentData()), folder,
                nonlocal_scaling=float(self.data.parameters.get("nonlocal_scaling", 0.0)),
            )
        except Exception as exc:
            QMessageBox.critical(self, "ASCEND Layer 3.2 spatial export", str(exc)); return
        self.spatial_status.setText(f"Exported {len(outputs)} spatial files to {folder}.")
        QMessageBox.information(self, "ASCEND Layer 3.2 spatial export", f"Exported {len(outputs)} files.")

    def _export_screenshot(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Export Layer 3.2 rendered view", "layer3_2_biological_volume.png", "PNG image (*.png)",
        )
        if not path:
            return
        try:
            self.scene.save_screenshot(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "ASCEND Layer 3.2 screenshot export", str(exc))
            return
        self.spatial_status.setText(f"Exported rendered Layer 3.2 view to {path}.")
