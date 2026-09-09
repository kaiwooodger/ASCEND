"""Read-only presentation of stored ICRU 91-context dose-gradient evidence."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGridLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class DoseGradientCanvas(QWidget):
    """Plot a stored isodose-volume profile without recalculating dose metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.result: dict[str, Any] = {}
        self.setMinimumHeight(280)

    def set_result(self, result: dict[str, Any] | None) -> None:
        self.result = dict(result or {})
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        points = list(self.result.get("isodose_volume_profile") or [])
        if not points:
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No stored ICRU 91 dose-gradient result")
            return
        plot = QRectF(64, 24, max(self.width() - 88, 1), max(self.height() - 70, 1))
        x_values = [float(item["relative_prescription_percent"]) for item in points]
        y_values = [float(item["isodose_volume_cc"]) for item in points]
        x_min, x_max = min(x_values), max(x_values)
        y_max = max(y_values) or 1.0

        def position(x_value: float, y_value: float) -> QPointF:
            return QPointF(
                plot.left() + (x_value - x_min) / max(x_max - x_min, 1.0) * plot.width(),
                plot.bottom() - y_value / y_max * plot.height(),
            )

        painter.setPen(QPen(self.palette().mid().color(), 1.0))
        painter.drawRect(plot)
        for level, label, colour in ((50.0, "50% Rx", QColor("#d97706")), (100.0, "100% Rx", QColor("#7c3aed"))):
            if x_min <= level <= x_max:
                marker_x = position(level, 0.0).x()
                painter.setPen(QPen(colour, 1.2, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(marker_x, plot.top()), QPointF(marker_x, plot.bottom()))
                painter.drawText(QPointF(marker_x + 4, plot.top() + 14), label)
        path = QPainterPath()
        for index, item in enumerate(points):
            current = position(float(item["relative_prescription_percent"]), float(item["isodose_volume_cc"]))
            path.moveTo(current) if index == 0 else path.lineTo(current)
        painter.setPen(QPen(QColor("#1769aa"), 2.6))
        painter.drawPath(path)
        painter.setPen(self.palette().text().color())
        painter.drawText(QPointF(plot.left(), self.height() - 12), "Dose threshold (% of Rx_H)")
        painter.drawText(QPointF(plot.left(), 16), "Isodose volume (cc)")


class DoseGradientPanel(QWidget):
    """Present PIV, PIV-half, Paddick GI, and the supporting isodose-volume profile."""

    def __init__(self) -> None:
        super().__init__()
        self.result: dict[str, Any] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.definition = QLabel(
            "Paddick GI = PIV_half / PIV, using the whole selected RTDOSE distribution at 50% and 100% of Rx_H. "
            "Lower values indicate steeper fall-off only in matched-volume, similarly conformal comparisons."
        )
        self.definition.setWordWrap(True)
        self.definition.setObjectName("sectionDescription")
        layout.addWidget(self.definition)
        self.canvas = DoseGradientCanvas()
        layout.addWidget(self.canvas, 2)
        cards = QGridLayout()
        self.metric_cards: dict[str, QLabel] = {}
        for column, (key, label) in enumerate((
            ("gradient_index", "PADDICK GI"),
            ("piv_half_cc", "PIV HALF (50% Rx)"),
            ("piv_cc", "PIV (100% Rx)"),
            ("prescription", "Rx_H"),
        )):
            card = QLabel(f"{label}\n—")
            card.setObjectName("metricCard")
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.metric_cards[key] = card
            cards.addWidget(card, 0, column)
        layout.addLayout(cards)
        self.status = QLabel("No stored dose-gradient result.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Relative Rx (%)", "Threshold (Gy)", "Isodose volume (cc)", "Voxel count"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

    @staticmethod
    def _display(value: Any, units: str = "") -> str:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            return f"{float(value):.5g}{(' ' + units) if units else ''}"
        return "—"

    def set_result(self, result: dict[str, Any] | None) -> None:
        self.result = dict(result or {})
        self.canvas.set_result(self.result)
        prescription = self.result.get("prescription") or {}
        values = {
            "gradient_index": self._display(self.result.get("gradient_index")),
            "piv_half_cc": self._display(self.result.get("piv_half_cc"), "cc"),
            "piv_cc": self._display(self.result.get("piv_cc"), "cc"),
            "prescription": self._display(prescription.get("dose_gy"), "Gy"),
        }
        labels = {
            "gradient_index": "PADDICK GI",
            "piv_half_cc": "PIV HALF (50% Rx)",
            "piv_cc": "PIV (100% Rx)",
            "prescription": "Rx_H",
        }
        for key, card in self.metric_cards.items():
            card.setText(f"{labels[key]}\n{values[key]}")
        warnings = list(self.result.get("warnings") or [])
        status = str(self.result.get("calculation_status") or "NOT AVAILABLE")
        self.status.setText(f"Status: {status}" + (f" · {' · '.join(warnings)}" if warnings else ""))
        points = list(self.result.get("isodose_volume_profile") or [])
        self.table.setRowCount(len(points))
        for row, point in enumerate(points):
            row_values = (
                self._display(point.get("relative_prescription_percent")),
                self._display(point.get("threshold_dose_gy"), "Gy"),
                self._display(point.get("isodose_volume_cc"), "cc"),
                str(point.get("voxel_count", "—")),
            )
            for column, value in enumerate(row_values):
                self.table.setItem(row, column, QTableWidgetItem(value))
