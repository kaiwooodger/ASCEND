"""Read-only Layer 2.2 vertex-profile presentation widgets."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)


class VertexProfileCanvas(QWidget):
    """Plot stored shell statistics without performing physical calculations."""

    def __init__(self) -> None:
        super().__init__()
        self.extension: dict[str, Any] = {}
        self.vertex_ids: list[str] = []
        self.corrected = False
        self.show_mean = False
        self.setMinimumHeight(260)

    def set_view(self, extension: dict[str, Any], vertex_ids: list[str], *, corrected: bool, show_mean: bool) -> None:
        self.extension = extension
        self.vertex_ids = vertex_ids
        self.corrected = corrected
        self.show_mean = show_mean
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        profiles = self.extension.get("profiles") or {}
        vertices = {str(item.get("vertex_id")): item for item in self.extension.get("vertices", [])}
        selected = [(vertex_id, profiles.get(vertex_id) or []) for vertex_id in self.vertex_ids]
        selected = [(vertex_id, rows) for vertex_id, rows in selected if rows]
        if not selected:
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.rect(), Qt.AlignCenter, "No valid stored radial profile")
            return
        plot = QRectF(54, 18, max(self.width() - 76, 1), max(self.height() - 58, 1))
        x_max = max(float(row.get("radius_mm") or 0.0) for _vertex, rows in selected for row in rows) or 1.0
        value_key = "corrected_profile" if self.corrected else "median_dose_gy"
        values = [float(row[value_key]) for _vertex, rows in selected for row in rows if row.get(value_key) is not None]
        y_min = min(0.0, min(values)) if self.corrected else 0.0
        y_max = max(values) if values else 1.0
        if math.isclose(y_min, y_max):
            y_max = y_min + 1.0

        def point(radius: float, value: float) -> QPointF:
            return QPointF(
                plot.left() + radius / x_max * plot.width(),
                plot.bottom() - (value - y_min) / (y_max - y_min) * plot.height(),
            )

        painter.setPen(QPen(self.palette().mid().color(), 1.0))
        painter.drawRect(plot)
        colours = (QColor("#1769aa"), QColor("#c43c39"), QColor("#20845f"), QColor("#7a4ea3"), QColor("#bb6a13"))
        for series_index, (vertex_id, rows) in enumerate(selected):
            colour = colours[series_index % len(colours)]
            if len(selected) == 1 and not self.corrected:
                upper = QPainterPath(); lower: list[QPointF] = []
                for index, row in enumerate(rows):
                    radius = float(row["radius_mm"])
                    upper_value = float(row.get("q75_dose_gy", row["median_dose_gy"]))
                    lower_value = float(row.get("q25_dose_gy", row["median_dose_gy"]))
                    upper_point = point(radius, upper_value)
                    lower.append(point(radius, lower_value))
                    upper.moveTo(upper_point) if index == 0 else upper.lineTo(upper_point)
                for lower_point in reversed(lower):
                    upper.lineTo(lower_point)
                upper.closeSubpath()
                band = QColor(colour); band.setAlpha(45)
                painter.fillPath(upper, band)
            path = QPainterPath()
            for index, row in enumerate(rows):
                value = row.get(value_key)
                if value is None:
                    continue
                current = point(float(row["radius_mm"]), float(value))
                path.moveTo(current) if index == 0 else path.lineTo(current)
            painter.setPen(QPen(colour, 2.4 if len(selected) == 1 else 1.5))
            painter.drawPath(path)
            if self.show_mean and not self.corrected:
                mean_path = QPainterPath()
                for index, row in enumerate(rows):
                    current = point(float(row["radius_mm"]), float(row["mean_dose_gy"]))
                    mean_path.moveTo(current) if index == 0 else mean_path.lineTo(current)
                painter.setPen(QPen(colour.lighter(135), 1.0, Qt.DashLine)); painter.drawPath(mean_path)
            if len(selected) == 1:
                record = vertices.get(vertex_id, {})
                for key, label, marker_colour in (
                    ("r80_mm", "r80", QColor("#d97706")), ("r50_mm", "r50", QColor("#7c3aed")),
                    ("r20_mm", "r20", QColor("#b42318")),
                ):
                    radius = record.get(key)
                    if radius is None:
                        continue
                    marker_x = point(float(radius), y_min).x()
                    painter.setPen(QPen(marker_colour, 1.1, Qt.DashLine)); painter.drawLine(QPointF(marker_x, plot.top()), QPointF(marker_x, plot.bottom()))
                    painter.drawText(QPointF(marker_x + 3, plot.top() + 13), label)
                diameter = record.get("geometric_equivalent_diameter_mm")
                if diameter is not None:
                    marker_x = point(float(diameter) / 2.0, y_min).x()
                    painter.setPen(QPen(QColor("#334155"), 1.0, Qt.DotLine)); painter.drawLine(QPointF(marker_x, plot.top()), QPointF(marker_x, plot.bottom()))
        painter.setPen(self.palette().text().color())
        painter.drawText(QPointF(plot.left(), self.height() - 10), "Radius (mm)")
        painter.drawText(QPointF(plot.left(), 14), "Corrected excess-dose profile" if self.corrected else "Dose (Gy)")


class VertexProfilePanel(QWidget):
    """Dedicated selector, plot, metrics and validation table for stored profiles."""

    vertexSelected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.extension: dict[str, Any] = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(4, 4, 4, 4)
        controls = QHBoxLayout(); controls.addWidget(QLabel("Vertex"))
        self.selector = QComboBox(); controls.addWidget(self.selector)
        self.overlay_all = QCheckBox("Overlay all vertices")
        self.corrected = QCheckBox("Background-corrected profile")
        self.show_mean = QCheckBox("Show shell mean")
        controls.addWidget(self.overlay_all); controls.addWidget(self.corrected); controls.addWidget(self.show_mean); controls.addStretch()
        layout.addLayout(controls)
        self.canvas = VertexProfileCanvas(); layout.addWidget(self.canvas, 2)
        cards = QGridLayout(); self.metric_cards: dict[str, QLabel] = {}
        for column, (key, label) in enumerate((
            ("dosimetric_diameter_mm", "DOSIMETRIC DIAMETER"), ("penumbra_80_20_mm", "80–20 PENUMBRA"),
            ("maximum_gradient_gy_per_mm", "MAX GRADIENT"), ("background_d50_gy", "BACKGROUND D50"),
        )):
            card = QLabel(f"{label}\n—"); card.setObjectName("metricCard"); card.setAlignment(Qt.AlignCenter)
            self.metric_cards[key] = card; cards.addWidget(card, 0, column)
        layout.addLayout(cards)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Vertex", "Status", "Dose diameter", "Geometric diameter", "Penumbra", "Max gradient", "Background D50"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.selector.currentIndexChanged.connect(self._selection_changed)
        self.overlay_all.toggled.connect(self._update_plot); self.corrected.toggled.connect(self._update_plot); self.show_mean.toggled.connect(self._update_plot)
        self.table.cellClicked.connect(lambda row, _column: self.selector.setCurrentIndex(row))

    @staticmethod
    def _display(value: Any, units: str) -> str:
        return f"{float(value):.4g} {units}" if isinstance(value, (int, float)) and math.isfinite(float(value)) else "—"

    def set_result(self, extension: dict[str, Any] | None) -> None:
        self.extension = dict(extension or {})
        vertices = list(self.extension.get("vertices") or [])
        self.selector.blockSignals(True); self.selector.clear()
        for item in vertices:
            self.selector.addItem(str(item.get("vertex_id")), str(item.get("vertex_id")))
        self.selector.blockSignals(False)
        self.table.setRowCount(len(vertices))
        for row, item in enumerate(vertices):
            values = (
                item.get("vertex_id"), item.get("profile_status"), self._display(item.get("dosimetric_diameter_mm"), "mm"),
                self._display(item.get("geometric_equivalent_diameter_mm"), "mm"), self._display(item.get("penumbra_80_20_mm"), "mm"),
                self._display(item.get("maximum_gradient_gy_per_mm"), "Gy/mm"), self._display(item.get("background_d50_gy"), "Gy"),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value) if value is not None else "—"))
        self._selection_changed(0)

    def _selection_changed(self, _index: int) -> None:
        vertex_id = str(self.selector.currentData() or "")
        record = next((item for item in self.extension.get("vertices", []) if str(item.get("vertex_id")) == vertex_id), {})
        labels = {
            "dosimetric_diameter_mm": ("DOSIMETRIC DIAMETER", "mm"), "penumbra_80_20_mm": ("80–20 PENUMBRA", "mm"),
            "maximum_gradient_gy_per_mm": ("MAX GRADIENT", "Gy/mm"), "background_d50_gy": ("BACKGROUND D50", "Gy"),
        }
        for key, card in self.metric_cards.items():
            title, units = labels[key]; card.setText(f"{title}\n{self._display(record.get(key), units)}")
        if vertex_id:
            self.vertexSelected.emit(vertex_id)
        self._update_plot()

    def _update_plot(self) -> None:
        selected = [str(item.get("vertex_id")) for item in self.extension.get("vertices", [])] if self.overlay_all.isChecked() else [str(self.selector.currentData() or "")]
        self.canvas.set_view(self.extension, selected, corrected=self.corrected.isChecked(), show_mean=self.show_mean.isChecked())
