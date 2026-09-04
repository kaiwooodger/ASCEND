"""Reusable workstation presentation widgets.

This module owns generic Qt rendering and table adaptation.  It consumes
stored result records and must not calculate scientific quantities.
"""

from __future__ import annotations

import html
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolTip,
    QToolBox,
    QWidget,
)


class WorkstationToolBox(QToolBox):
    """QToolBox with stable, unclipped section headers on macOS Qt styles."""

    TAB_HEIGHT = 38

    def addItem(self, widget: QWidget, text: str) -> int:  # type: ignore[override]
        """Add an item and normalise the style-specific tab button."""
        index = super().addItem(widget, text)
        self.normalise_tab_buttons()
        return index

    def normalise_tab_buttons(self) -> None:
        """Normalise tab buttons without changing result state."""
        for button in self.findChildren(QAbstractButton):
            if button.metaObject().className() != "QToolBoxButton":
                continue
            button.setMinimumHeight(self.TAB_HEIGHT)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class GraphCanvas(QWidget):
    """Read-only 2-D projection of a stored Layer 2.2 graph result."""

    nodeSelected = Signal(str)
    edgeSelected = Signal(int)

    PALETTE = ("#2463a0", "#b35c1e", "#21835b", "#7a4ea3", "#a33b56")

    def __init__(self) -> None:
        super().__init__()
        self.result: dict[str, Any] | None = None
        self.projection = "auto"
        self.show_edge_labels = True
        self.show_invalid_edges = True
        self.edge_metric_mode = "midpoint_pvdr"
        self.show_saddle_markers = True
        self.show_saddle_paths = False
        self.show_diagnostic_corridors = False
        self.selected_edge_index: int | None = None
        self.selected_node_name: str | None = None
        self.zoom = 1.0
        self.rotation_degrees = 0.0
        self.pan = QPointF()
        self._drag_position: QPointF | None = None
        self._node_hover_targets: list[tuple[dict[str, Any], QPointF, float]] = []
        self._edge_hover_targets: list[tuple[dict[str, Any], int, QPointF, QPointF]] = []
        self.setMinimumSize(520, 400)
        self.setCursor(Qt.OpenHandCursor)
        self.setMouseTracking(True)

    def zoom_by(self, factor: float) -> None:
        self.zoom = float(min(max(self.zoom * factor, 0.5), 6.0))
        self.update()

    def rotate_by(self, degrees: float) -> None:
        self.rotation_degrees = (self.rotation_degrees + float(degrees)) % 360.0
        self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.rotation_degrees = 0.0
        self.pan = QPointF()
        self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            target_type, payload = self._hit_test(event.position())
            if target_type == "node":
                node_name = str(payload.get("node", ""))
                self.select_node(node_name)
                self.nodeSelected.emit(node_name)
                event.accept()
                return
            if target_type == "edge":
                edge_index = int(payload)
                self.select_edge(edge_index)
                self.edgeSelected.emit(edge_index)
                event.accept()
                return
            self._drag_position = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is not None:
            current = event.position()
            self.pan += current - self._drag_position
            self._drag_position = current
            self.update()
            event.accept()
            return
        target_type, payload = self._hit_test(event.position())
        if target_type == "node":
            QToolTip.showText(event.globalPosition().toPoint(), self.node_hover_text(payload), self)
        elif target_type == "edge":
            edge = (self.result or {}).get("edges", [])[int(payload)]
            QToolTip.showText(event.globalPosition().toPoint(), self.edge_hover_text(edge), self)
        else:
            QToolTip.hideText()
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()

    def leaveEvent(self, event: Any) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def set_result(self, result: dict[str, Any] | None) -> None:
        self.result = result
        self.selected_edge_index = None
        self.selected_node_name = None
        self._node_hover_targets = []
        self._edge_hover_targets = []
        self.update()

    def set_projection(self, projection: str) -> None:
        self.projection = projection
        self.update()

    def set_edge_labels_visible(self, visible: bool) -> None:
        self.show_edge_labels = visible
        self.update()

    def set_invalid_edges_visible(self, visible: bool) -> None:
        self.show_invalid_edges = visible
        self.update()

    def set_edge_metric_mode(self, mode: str) -> None:
        self.edge_metric_mode = str(mode)
        self.update()

    def set_saddle_markers_visible(self, visible: bool) -> None:
        self.show_saddle_markers = bool(visible); self.update()

    def set_saddle_paths_visible(self, visible: bool) -> None:
        self.show_saddle_paths = bool(visible); self.update()

    def set_diagnostic_corridors_visible(self, visible: bool) -> None:
        self.show_diagnostic_corridors = bool(visible); self.update()

    def select_edge(self, index: int) -> None:
        self.selected_edge_index = int(index) if index >= 0 else None
        self.update()

    def select_node(self, node_name: str | None) -> None:
        self.selected_node_name = str(node_name) if node_name else None
        self.update()

    @staticmethod
    def _point_to_segment_distance(point: QPointF, start: QPointF, end: QPointF) -> float:
        dx, dy = end.x() - start.x(), end.y() - start.y()
        denominator = dx * dx + dy * dy
        if denominator <= 0.0:
            return math.hypot(point.x() - start.x(), point.y() - start.y())
        fraction = min(max(((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / denominator, 0.0), 1.0)
        closest = QPointF(start.x() + fraction * dx, start.y() + fraction * dy)
        return math.hypot(point.x() - closest.x(), point.y() - closest.y())

    def _hit_test(self, position: QPointF) -> tuple[str | None, Any]:
        for node, point, radius in reversed(self._node_hover_targets):
            if math.hypot(position.x() - point.x(), position.y() - point.y()) <= radius + 6.0:
                return "node", node
        for _edge, index, start, end in reversed(self._edge_hover_targets):
            if self._point_to_segment_distance(position, start, end) <= 7.0:
                return "edge", index
        return None, None

    @staticmethod
    def node_hover_text(node: dict[str, Any]) -> str:
        centroid = node.get("centroid_lps_mm") or []
        position = " / ".join(f"{float(value):.2f}" for value in centroid) if len(centroid) == 3 else "Not available"
        d50 = node.get("peak_d50_gy", node.get("vertex_d50_gy"))
        d95 = node.get("peak_d95_gy", node.get("vertex_d95_gy"))
        dmean = node.get("peak_dmean_gy", node.get("vertex_dmean_gy"))
        display = lambda value, suffix="": f"{float(value):.3f}{suffix}" if isinstance(value, (int, float)) else "Not available"
        return (
            f"<b>{html.escape(str(node.get('node', 'Vertex')))}</b><br>"
            f"Centroid LPS X / Y / Z: {position} mm<br>"
            f"D50: {display(d50, ' Gy')}<br>D95: {display(d95, ' Gy')}<br>"
            f"Mean dose: {display(dmean, ' Gy')}<br>Status: {html.escape(str(node.get('vertex_status') or node.get('status') or 'recorded'))}"
        )

    @staticmethod
    def edge_hover_text(edge: dict[str, Any]) -> str:
        nodes = edge.get("nodes") or []
        node_text = " — ".join(map(str, nodes)) if len(nodes) == 2 else "Not available"
        display = lambda value, suffix="": f"{float(value):.3f}{suffix}" if isinstance(value, (int, float)) else "Not available"
        return (
            f"<b>Edge {html.escape(str(edge.get('edge_id', '—')))}</b><br>"
            f"Vertices: {html.escape(node_text)}<br>Length: {display(edge.get('length_mm'), ' mm')}<br>"
            f"Valley D50: {display(edge.get('edge_local_valley_d50_gy'), ' Gy')}<br>"
            f"iPVDR: {display(edge.get('ipvdr'))}<br>Status: {html.escape(str(edge.get('edge_status') or 'recorded'))}"
        )

    @staticmethod
    def _edge_label(edge: dict[str, Any]) -> str:
        edge_id = edge.get("edge_id", "?")
        value = edge.get("ipvdr")
        formatted = f"{float(value):.3f}" if isinstance(value, (int, float)) and math.isfinite(float(value)) else "—"
        return f"E{edge_id}  iPVDR {formatted}"

    def _saddle_by_edge(self) -> dict[int, dict[str, Any]]:
        extension = ((self.result or {}).get("layer2_2_extensions") or {}).get("saddle_graph") or {}
        return {int(item.get("edge_id", 0)): item for item in extension.get("edges", [])}

    def _metric_value(self, edge: dict[str, Any], saddle: dict[str, Any]) -> float | None:
        if self.edge_metric_mode == "saddle_pvdr": value = saddle.get("saddle_pvdr")
        elif self.edge_metric_mode == "midpoint_minus_saddle_gy": value = saddle.get("midpoint_minus_saddle_gy")
        elif self.edge_metric_mode == "edge_length_mm": value = edge.get("length_mm")
        elif self.edge_metric_mode == "validation_status": value = 1.0 if saddle.get("edge_status") == "VALID" else 0.0
        else: value = edge.get("ipvdr")
        return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None

    def _metric_label(self, edge: dict[str, Any], saddle: dict[str, Any]) -> str:
        value = self._metric_value(edge, saddle)
        formatted = f"{value:.3f}" if value is not None else "—"
        units = {"midpoint_minus_saddle_gy": " Gy", "edge_length_mm": " mm"}.get(self.edge_metric_mode, "")
        name = {
            "saddle_pvdr": "sPVDR", "midpoint_minus_saddle_gy": "ΔD", "edge_length_mm": "length",
            "validation_status": "status",
        }.get(self.edge_metric_mode, "iPVDR")
        if self.edge_metric_mode == "validation_status":
            formatted = str(saddle.get("edge_status") or "NOT ASSESSED")
        return f"E{edge.get('edge_id', '?')}  {name} {formatted}{units}"

    @staticmethod
    def _components(names: list[str], edges: list[dict[str, Any]]) -> dict[str, int]:
        neighbours = {name: set() for name in names}
        for edge in edges:
            a, b = edge.get("nodes", [None, None])
            if a in neighbours and b in neighbours:
                neighbours[a].add(b)
                neighbours[b].add(a)
        output: dict[str, int] = {}
        component = 0
        for start in names:
            if start in output:
                continue
            pending = [start]
            output[start] = component
            while pending:
                current = pending.pop()
                for neighbour in neighbours[current]:
                    if neighbour not in output:
                        output[neighbour] = component
                        pending.append(neighbour)
            component += 1
        return output

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        background = self.palette().color(QPalette.Base)
        foreground = self.palette().color(QPalette.Text)
        muted = self.palette().color(QPalette.PlaceholderText)
        painter.fillRect(self.rect(), background)
        nodes = (self.result or {}).get("nodes", [])
        edges = (self.result or {}).get("edges", [])
        if not nodes:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignCenter, "Run Layer 2.2 to inspect centroids and edges")
            return
        painter.save()
        center = QPointF(self.rect().center())
        painter.translate(center + self.pan)
        painter.rotate(self.rotation_degrees)
        painter.scale(self.zoom, self.zoom)
        painter.translate(-center.x(), -center.y())
        coords = [list(map(float, item["centroid_lps_mm"])) for item in nodes]
        spreads = [max(row[axis] for row in coords) - min(row[axis] for row in coords) for axis in range(3)]
        axes = {
            "axial": [0, 1], "sagittal": [1, 2], "coronal": [0, 2],
        }.get(self.projection, sorted(range(3), key=lambda axis: spreads[axis], reverse=True)[:2])
        axis_labels = ("LPS X", "LPS Y", "LPS Z")
        xs = [row[axes[0]] for row in coords]
        ys = [row[axes[1]] for row in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(self.width() - 100, 1)
        height = max(self.height() - 100, 1)

        def point(index: int) -> QPointF:
            x = 50 + (xs[index] - min_x) / (max_x - min_x or 1.0) * width
            y = 50 + (max_y - ys[index]) / (max_y - min_y or 1.0) * height
            return QPointF(x, y)

        names = [str(item["node"]) for item in nodes]
        by_name = {name: index for index, name in enumerate(names)}
        components = self._components(names, edges)
        saddles = self._saddle_by_edge()
        edge_values = [self._metric_value(edge, saddles.get(int(edge.get("edge_id", 0)), {})) for edge in edges]
        finite_edge_values = [value for value in edge_values if value is not None]
        metric_low, metric_high = (min(finite_edge_values), max(finite_edge_values)) if finite_edge_values else (0.0, 1.0)
        transform = painter.worldTransform()
        edge_hover_targets: list[tuple[dict[str, Any], int, QPointF, QPointF]] = []

        def projected_lps(coordinate: list[float] | tuple[float, ...]) -> QPointF:
            row = list(map(float, coordinate))
            x = 50 + (row[axes[0]] - min_x) / (max_x - min_x or 1.0) * width
            y = 50 + (max_y - row[axes[1]]) / (max_y - min_y or 1.0) * height
            return QPointF(x, y)

        edge_labels: list[tuple[QPointF, str, QColor]] = []
        for edge_index, edge in enumerate(edges):
            if not edge.get("valid", False) and not self.show_invalid_edges:
                continue
            a, b = edge.get("nodes", [None, None])
            if a not in by_name or b not in by_name:
                continue
            saddle = saddles.get(int(edge.get("edge_id", 0)), {})
            value = edge_values[edge_index]
            if self.edge_metric_mode == "validation_status":
                color = QColor("#15803d" if saddle.get("edge_status") == "VALID" else "#b42318")
            elif value is None:
                color = QColor("#8b98a5")
            else:
                fraction = (value - metric_low) / (metric_high - metric_low or 1.0)
                color = QColor.fromHsvF(0.56 - 0.53 * fraction, 0.82, 0.72)
            if self.selected_edge_index == edge_index:
                color = QColor("#ef7c22")
            painter.setPen(QPen(color, 3.0 if self.selected_edge_index == edge_index else 2.0))
            first, second = point(by_name[a]), point(by_name[b])
            painter.drawLine(first, second)
            edge_hover_targets.append((edge, edge_index, transform.map(first), transform.map(second)))
            if self.show_diagnostic_corridors:
                painter.setPen(QPen(QColor("#94a3b8"), 8.0, Qt.DotLine)); painter.drawLine(first, second)
            midpoint_coordinate = edge.get("midpoint_lps_mm")
            if midpoint_coordinate is not None:
                painter.setPen(QPen(QColor("#7c3aed"), 1.2)); painter.setBrush(QColor("#7c3aed")); painter.drawEllipse(projected_lps(midpoint_coordinate), 3.5, 3.5)
            if self.show_saddle_markers and saddle.get("saddle_xyz_mm") is not None:
                saddle_point = projected_lps(saddle["saddle_xyz_mm"])
                painter.setPen(QPen(QColor("#9a3412"), 1.0)); painter.setBrush(QColor("#f97316")); painter.drawRect(QRectF(saddle_point.x()-4, saddle_point.y()-4, 8, 8))
            if self.show_saddle_paths:
                path = [projected_lps(item) for item in saddle.get("saddle_path_xyz_mm") or []]
                painter.setPen(QPen(QColor("#f97316"), 1.2, Qt.DashLine))
                for path_start, path_end in zip(path, path[1:]): painter.drawLine(path_start, path_end)
            delta_x, delta_y = second.x() - first.x(), second.y() - first.y()
            length = math.hypot(delta_x, delta_y) or 1.0
            anchor = QPointF(
                (first.x() + second.x()) / 2.0 - 12.0 * delta_y / length,
                (first.y() + second.y()) / 2.0 + 12.0 * delta_x / length,
            )
            edge_labels.append((anchor, self._metric_label(edge, saddle), color))
        if self.show_edge_labels:
            edge_font = painter.font()
            edge_font.setPointSize(8)
            edge_font.setBold(True)
            painter.setFont(edge_font)
            for anchor, label, color in edge_labels:
                bounds = painter.fontMetrics().boundingRect(label)
                rect = QRectF(
                    anchor.x() - bounds.width() / 2.0 - 4.0,
                    anchor.y() - bounds.height() / 2.0 - 2.0,
                    bounds.width() + 8.0,
                    bounds.height() + 4.0,
                )
                painter.setPen(QPen(color, 1.0))
                painter.setBrush(background)
                painter.drawRoundedRect(rect, 3.0, 3.0)
                painter.drawText(rect, Qt.AlignCenter, label)
        font = painter.font()
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        node_hover_targets: list[tuple[dict[str, Any], QPointF, float]] = []
        for index, name in enumerate(names):
            point_value = point(index)
            color = QColor(self.PALETTE[components[name] % len(self.PALETTE)])
            selected = name == self.selected_node_name
            painter.setPen(QPen(QColor("#ef7c22") if selected else foreground, 3.0 if selected else 1.0))
            painter.setBrush(color)
            radius = 9.0 if selected else 7.0
            painter.drawEllipse(point_value, radius, radius)
            label_x = 10 if point_value.x() < self.width() - 105 else -72
            label_y = -8 if point_value.y() > 32 else 19
            painter.drawText(point_value + QPointF(label_x, label_y), name)
            node_hover_targets.append(((self.result or {}).get("nodes", [])[index], transform.map(point_value), radius * self.zoom))
        painter.restore()
        self._edge_hover_targets = edge_hover_targets
        self._node_hover_targets = node_hover_targets
        painter.setPen(muted)
        painter.drawText(
            12, self.height() - 14,
            f"Projection: {axis_labels[axes[0]]} × {axis_labels[axes[1]]}; {self.edge_metric_mode}; "
            f"zoom {self.zoom:.2g}×; rotation {self.rotation_degrees:.0f}°",
        )


class VerticesQACanvas(GraphCanvas):
    """Interactive Layer 2.1 vertex layout over stored QA evidence."""

    vertexSelected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []
        self.connections: list[dict[str, Any]] = []
        self.show_vertex_labels = True
        self.show_distance_labels = True
        self.selected_vertex_id: str | None = None
        self._hover_targets: list[tuple[dict[str, Any], QPointF, float]] = []
        self.setMouseTracking(True)
        self.setAccessibleName("Layer 2.1 interactive vertices QA layout")

    def set_vertex_qa(
        self,
        records: list[dict[str, Any]] | None,
        connections: list[dict[str, Any]] | None,
    ) -> None:
        self.records = [dict(item) for item in (records or []) if item.get("centroid_lps_mm") is not None]
        self.connections = [dict(item) for item in (connections or [])]
        self._hover_targets = []
        self.update()

    def select_vertex(self, vertex_id: str | None) -> None:
        self.selected_vertex_id = str(vertex_id) if vertex_id else None
        self.update()

    def set_vertex_labels_visible(self, visible: bool) -> None:
        self.show_vertex_labels = bool(visible)
        self.update()

    def set_distance_labels_visible(self, visible: bool) -> None:
        self.show_distance_labels = bool(visible)
        self.update()

    @staticmethod
    def _display(value: Any, suffix: str = "", decimals: int = 2) -> str:
        if value is None:
            return "Not available"
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return f"{float(value):.{decimals}f}{suffix}"
        return html.escape(str(value))

    @classmethod
    def hover_text(cls, record: dict[str, Any]) -> str:
        """Return the concise, auditable rich-text hover card for a vertex."""
        axes = record.get("fwhm_axes_mm") or {}
        nearest = record.get("nearest_vertex_id")
        nearest_text = (
            f"{html.escape(str(nearest))} · {cls._display(record.get('nearest_vertex_distance_mm'), ' mm')}"
            if nearest else "Not available"
        )
        warnings = record.get("warnings") or []
        warning_text = ", ".join(html.escape(str(item)) for item in warnings) if warnings else "None"
        return (
            f"<b>{html.escape(str(record.get('vertex_id', 'Vertex')))}</b><br>"
            f"D95: {cls._display(record.get('d95_gy'), ' Gy')}<br>"
            f"V95 RxH: {cls._display(record.get('v95_rxh_pct'), '%')}<br>"
            f"Mean / maximum dose: {cls._display(record.get('dmean_gy'), ' Gy')} / "
            f"{cls._display(record.get('dmax_gy'), ' Gy')}<br>"
            f"Volume: {cls._display(record.get('volume_cc'), ' cc', 3)}<br>"
            f"Nearest vertex: {nearest_text}<br>"
            f"Local FWHM: <b>{cls._display(record.get('local_fwhm_mm'), ' mm')}</b><br>"
            f"FWHM native X / Y / Z: {cls._display(axes.get('grid_x'), ' mm')} / "
            f"{cls._display(axes.get('grid_y'), ' mm')} / {cls._display(axes.get('grid_z'), ' mm')}<br>"
            f"QA warnings: {warning_text}"
        )

    @staticmethod
    def fwhm_colour(value: Any, minimum: float, maximum: float) -> QColor:
        """Map local FWHM to a colour-blind-safe blue-to-purple gradient."""
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return QColor("#8b98a5")
        fraction = 0.5 if maximum <= minimum else min(max((float(value) - minimum) / (maximum - minimum), 0.0), 1.0)
        low = QColor("#78c7f2")
        high = QColor("#5a2a9e")
        return QColor(
            round(low.red() + fraction * (high.red() - low.red())),
            round(low.green() + fraction * (high.green() - low.green())),
            round(low.blue() + fraction * (high.blue() - low.blue())),
        )

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is not None:
            super().mouseMoveEvent(event)
            return
        position = event.position()
        hit = next(
            (
                record for record, point, radius in reversed(self._hover_targets)
                if math.hypot(position.x() - point.x(), position.y() - point.y()) <= radius + 5.0
            ),
            None,
        )
        if hit is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPosition().toPoint(), self.hover_text(hit), self)
        event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            position = event.position()
            hit = next(
                (
                    record for record, point, radius in reversed(self._hover_targets)
                    if math.hypot(position.x() - point.x(), position.y() - point.y()) <= radius + 5.0
                ),
                None,
            )
            if hit is not None:
                vertex_id = str(hit.get("vertex_id", ""))
                self.select_vertex(vertex_id)
                self.vertexSelected.emit(vertex_id)
                event.accept()
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event: Any) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        background = self.palette().color(QPalette.Base)
        foreground = self.palette().color(QPalette.Text)
        muted = self.palette().color(QPalette.PlaceholderText)
        painter.fillRect(self.rect(), background)
        if not self.records:
            painter.setPen(muted)
            painter.drawText(
                self.rect(), Qt.AlignCenter,
                "Run Layer 2.1 with per-vertex QA enabled to inspect the vertices layout",
            )
            self._hover_targets = []
            return

        painter.save()
        center = QPointF(self.rect().center())
        painter.translate(center + self.pan)
        painter.rotate(self.rotation_degrees)
        painter.scale(self.zoom, self.zoom)
        painter.translate(-center.x(), -center.y())
        coords = [list(map(float, item["centroid_lps_mm"])) for item in self.records]
        spreads = [max(row[axis] for row in coords) - min(row[axis] for row in coords) for axis in range(3)]
        axes = {
            "axial": [0, 1], "sagittal": [1, 2], "coronal": [0, 2],
        }.get(self.projection, sorted(range(3), key=lambda axis: spreads[axis], reverse=True)[:2])
        axis_labels = ("LPS X", "LPS Y", "LPS Z")
        xs = [row[axes[0]] for row in coords]
        ys = [row[axes[1]] for row in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(self.width() - 110, 1)
        height = max(self.height() - 110, 1)

        def point(index: int) -> QPointF:
            x_value = 55 + (xs[index] - min_x) / (max_x - min_x or 1.0) * width
            y_value = 55 + (max_y - ys[index]) / (max_y - min_y or 1.0) * height
            return QPointF(x_value, y_value)

        names = [str(item.get("vertex_id")) for item in self.records]
        by_name = {name: index for index, name in enumerate(names)}
        distance_labels: list[tuple[QPointF, str]] = []
        for connection in self.connections:
            connection_nodes = connection.get("nodes") or []
            if len(connection_nodes) != 2:
                continue
            first_name, second_name = connection_nodes
            if first_name not in by_name or second_name not in by_name:
                continue
            first, second = point(by_name[first_name]), point(by_name[second_name])
            painter.setPen(QPen(QColor("#7890a6"), 2.0))
            painter.drawLine(first, second)
            distance = connection.get("distance_mm")
            label = f"{float(distance):.1f} mm" if isinstance(distance, (int, float)) else "—"
            distance_labels.append((QPointF((first.x() + second.x()) / 2, (first.y() + second.y()) / 2), label))
        if self.show_distance_labels:
            label_font = painter.font()
            label_font.setPointSize(8)
            label_font.setBold(True)
            painter.setFont(label_font)
            for anchor, label in distance_labels:
                bounds = painter.fontMetrics().boundingRect(label)
                box = QRectF(
                    anchor.x() - bounds.width() / 2 - 5, anchor.y() - bounds.height() / 2 - 3,
                    bounds.width() + 10, bounds.height() + 6,
                )
                painter.setPen(QPen(QColor("#627d98"), 1.0))
                painter.setBrush(background)
                painter.drawRoundedRect(box, 4, 4)
                painter.drawText(box, Qt.AlignCenter, label)

        fwhm_values = [
            float(item["local_fwhm_mm"]) for item in self.records
            if isinstance(item.get("local_fwhm_mm"), (int, float))
        ]
        minimum_fwhm = min(fwhm_values) if fwhm_values else 0.0
        maximum_fwhm = max(fwhm_values) if fwhm_values else 0.0
        volume_values = [
            float(item["volume_cc"]) for item in self.records
            if isinstance(item.get("volume_cc"), (int, float))
        ]
        minimum_volume = min(volume_values) if volume_values else 0.0
        maximum_volume = max(volume_values) if volume_values else 0.0
        transform = painter.worldTransform()
        hover_targets: list[tuple[dict[str, Any], QPointF, float]] = []
        node_font = painter.font()
        node_font.setPointSize(9)
        node_font.setBold(False)
        painter.setFont(node_font)
        for index, record in enumerate(self.records):
            anchor = point(index)
            fwhm = record.get("local_fwhm_mm")
            volume = record.get("volume_cc")
            if isinstance(volume, (int, float)) and maximum_volume > minimum_volume:
                radius = 10.0 + 10.0 * (float(volume) - minimum_volume) / (maximum_volume - minimum_volume)
            else:
                radius = 14.0
            colour = self.fwhm_colour(fwhm, minimum_fwhm, maximum_fwhm)
            selected = str(record.get("vertex_id")) == self.selected_vertex_id
            painter.setPen(QPen(QColor("#ef7c22") if selected else foreground, 3.2 if selected else 1.2))
            painter.setBrush(colour)
            painter.drawEllipse(anchor, radius, radius)
            if self.show_vertex_labels:
                label = f"{record.get('vertex_id')}\nFWHM {self._display(fwhm, ' mm')}"
                label_x = radius + 6 if anchor.x() < self.width() - 150 else -140
                painter.drawText(QRectF(anchor.x() + label_x, anchor.y() - 18, 135, 38), Qt.AlignVCenter, label)
            hover_targets.append((record, transform.map(anchor), radius * self.zoom))
        painter.restore()
        self._hover_targets = hover_targets
        gradient = QLinearGradient(12, self.height() - 31, 128, self.height() - 31)
        gradient.setColorAt(0.0, self.fwhm_colour(minimum_fwhm, minimum_fwhm, maximum_fwhm))
        gradient.setColorAt(1.0, self.fwhm_colour(maximum_fwhm, minimum_fwhm, maximum_fwhm))
        painter.setPen(QPen(muted, 1.0))
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(12, self.height() - 36, 116, 9), 3, 3)
        fwhm_range = (
            f"{minimum_fwhm:.2f}–{maximum_fwhm:.2f} mm"
            if fwhm_values else "not available"
        )
        painter.drawText(136, self.height() - 27, f"Local FWHM low → high · {fwhm_range}")
        painter.setPen(muted)
        painter.drawText(
            12, self.height() - 14,
            f"Projection: {axis_labels[axes[0]]} × {axis_labels[axes[1]]} · colour: local FWHM · marker size: volume · "
            f"hover for QA · zoom {self.zoom:.2g}× · rotation {self.rotation_degrees:.0f}°",
        )


def heading(title: str, subtitle: str = "") -> tuple[QLabel, QLabel]:
    first = QLabel(title)
    first.setObjectName("title")
    second = QLabel(subtitle)
    second.setObjectName("subtitle")
    second.setWordWrap(True)
    return first, second


def text_view() -> QTextEdit:
    widget = QTextEdit()
    widget.setReadOnly(True)
    widget.setFont(QFont("Menlo", 11))
    return widget


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setAlternatingRowColors(True)
    widget.setWordWrap(True)
    widget.setShowGrid(False)
    widget.verticalHeader().setVisible(False)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def set_table(widget: QTableWidget, rows: list[list[Any]], empty_message: str = "No records available.") -> None:
    widget.clearSpans()
    if not rows:
        widget.setRowCount(1)
        item = QTableWidgetItem(empty_message)
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor("#6b7785"))
        widget.setItem(0, 0, item)
        widget.setSpan(0, 0, 1, widget.columnCount())
        widget.setSelectionMode(QAbstractItemView.NoSelection)
        return
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            text = "" if value is None else str(value)
            widget.setItem(row_index, column_index, QTableWidgetItem(text))
    widget.resizeRowsToContents()


def compact_table(widget: QTableWidget, minimum: int = 82, maximum: int = 360) -> int:
    """Fit a result table to visible rows while retaining a bounded scroll area."""
    widget.resizeRowsToContents()
    content_height = (
        widget.horizontalHeader().height()
        + sum(widget.rowHeight(row) for row in range(widget.rowCount()))
        + 2 * widget.frameWidth()
        + 8
    )
    target = min(max(int(content_height), int(minimum)), int(maximum))
    widget.setMinimumHeight(min(int(minimum), target))
    widget.setMaximumHeight(target)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    widget.updateGeometry()
    return target


def _friendly_field_name(value: str) -> str:
    abbreviations = {
        "d50": "D50", "d95": "D95", "dmean": "Dmean", "dmax": "Dmax",
        "gtv": "GTV", "vtvh": "VTVH", "vtvl": "VTVL", "rxh": "RxH",
        "rxl": "RxL", "qa": "QA", "rtdose": "RTDOSE", "uid": "UID",
        "sha256": "SHA-256", "v95": "V95", "gy": "Gy", "cc": "cc",
        "mm": "mm", "pct": "%", "95pct": "95%",
    }
    return " ".join(
        abbreviations.get(word.lower(), word.capitalize())
        for word in str(value).replace("-", "_").split("_")
    )


def _supporting_unit(field_name: str) -> str:
    name = field_name.lower()
    if name.endswith("_gy"):
        return "Gy"
    if name.endswith("_cc"):
        return "cc"
    if name.endswith("_pct") or name.endswith("_percentage"):
        return "%"
    if name.endswith("_mm") or "spacing_mm" in name or "distance_mm" in name:
        return "mm"
    if name.endswith("_voxels") or name.endswith("_voxel_count") or name == "voxel_count":
        return "voxels"
    return ""


def _supporting_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value):
        return "; ".join(_supporting_value(item) for item in value) if value else "None"
    return str(value)


def supporting_output_rows(payload: dict[str, Any]) -> list[list[str]]:
    """Flatten stored supporting records without deriving scientific values."""
    rows: list[list[str]] = []
    context_keys = {"status", "applicability", "warnings", "reason"}

    def context(record: dict[str, Any]) -> str:
        parts = []
        for key in ("status", "applicability", "reason"):
            value = record.get(key)
            if value not in (None, "", []):
                parts.append(_supporting_value(value))
        warnings = record.get("warnings")
        if warnings:
            parts.append("Warnings: " + _supporting_value(warnings))
        return " · ".join(parts)

    def walk(section: str, path: list[str], value: Any, inherited_context: str = "") -> None:
        if isinstance(value, dict):
            current_context = context(value) or inherited_context
            ordinary_keys = [key for key in value if key not in context_keys]
            if not ordinary_keys:
                rows.append([section, " › ".join(path) or section, "—", "", current_context])
                return
            for key in ordinary_keys:
                walk(section, [*path, _friendly_field_name(key)], value[key], current_context)
            return
        if isinstance(value, list) and value and any(isinstance(item, (dict, list)) for item in value):
            for index, item in enumerate(value, 1):
                identity = None
                if isinstance(item, dict):
                    identity = next((
                        item.get(key)
                        for key in ("metric_id", "vertex_id", "oar_name", "endpoint_id", "id")
                        if item.get(key)
                    ), None)
                label = _friendly_field_name(str(identity)) if identity is not None else f"Record {index}"
                walk(section, [*path, label], item, inherited_context)
            return
        field = path[-1] if path else section
        rows.append([
            section,
            " › ".join(path) if path else section,
            _supporting_value(value),
            _supporting_unit(field.replace(" ", "_")),
            inherited_context,
        ])

    for key, value in payload.items():
        section = _friendly_field_name(key)
        walk(section, [], value)
    return rows
