"""Reusable workstation presentation widgets.

This module owns generic Qt rendering and table adaptation.  It consumes
stored result records and must not calculate scientific quantities.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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

    PALETTE = ("#2463a0", "#b35c1e", "#21835b", "#7a4ea3", "#a33b56")

    def __init__(self) -> None:
        super().__init__()
        self.result: dict[str, Any] | None = None
        self.projection = "auto"
        self.show_edge_labels = True
        self.show_invalid_edges = True
        self.zoom = 1.0
        self.rotation_degrees = 0.0
        self.pan = QPointF()
        self._drag_position: QPointF | None = None
        self.setMinimumSize(520, 400)
        self.setCursor(Qt.OpenHandCursor)

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

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()

    def set_result(self, result: dict[str, Any] | None) -> None:
        self.result = result
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

    @staticmethod
    def _edge_label(edge: dict[str, Any]) -> str:
        edge_id = edge.get("edge_id", "?")
        value = edge.get("ipvdr")
        formatted = f"{float(value):.3f}" if isinstance(value, (int, float)) and math.isfinite(float(value)) else "—"
        return f"E{edge_id}  iPVDR {formatted}"

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
        edge_labels: list[tuple[QPointF, str, QColor]] = []
        for edge in edges:
            if not edge.get("valid", False) and not self.show_invalid_edges:
                continue
            a, b = edge.get("nodes", [None, None])
            if a not in by_name or b not in by_name:
                continue
            color = QColor("#b42318" if not edge.get("valid", False) else "#627d98")
            painter.setPen(QPen(color, 2.0))
            first, second = point(by_name[a]), point(by_name[b])
            painter.drawLine(first, second)
            delta_x, delta_y = second.x() - first.x(), second.y() - first.y()
            length = math.hypot(delta_x, delta_y) or 1.0
            anchor = QPointF(
                (first.x() + second.x()) / 2.0 - 12.0 * delta_y / length,
                (first.y() + second.y()) / 2.0 + 12.0 * delta_x / length,
            )
            edge_labels.append((anchor, self._edge_label(edge), color))
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
        for index, name in enumerate(names):
            point_value = point(index)
            color = QColor(self.PALETTE[components[name] % len(self.PALETTE)])
            painter.setPen(QPen(foreground, 1.0))
            painter.setBrush(color)
            painter.drawEllipse(point_value, 7, 7)
            label_x = 10 if point_value.x() < self.width() - 105 else -72
            label_y = -8 if point_value.y() > 32 else 19
            painter.drawText(point_value + QPointF(label_x, label_y), name)
        painter.restore()
        painter.setPen(muted)
        painter.drawText(
            12, self.height() - 14,
            f"Projection: {axis_labels[axes[0]]} × {axis_labels[axes[1]]}; iPVDR labels; "
            f"zoom {self.zoom:.2g}×; rotation {self.rotation_degrees:.0f}°",
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
