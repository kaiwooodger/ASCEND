"""Small widgets presenting stored Layer 3.1 regional result records."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ascend.gui.layer31_viewer_models import Layer31ViewerData


class RegionalResultCard(QFrame):
    """Compact, non-authoritative presentation of one stored regional record."""
    selected = Signal(str)

    def __init__(self, region_id: str, title: str) -> None:
        super().__init__(); self.region_id = region_id; self.setObjectName("metricCard")
        self.setCursor(Qt.PointingHandCursor); self.setMinimumWidth(165)
        layout = QVBoxLayout(self); self.title = QLabel(title); self.title.setObjectName("metricTitle")
        self.volume = QLabel("Volume  —"); self.survival = QLabel("Mean SF  —"); self.contribution = QLabel("Contribution  —")
        layout.addWidget(self.title); layout.addWidget(self.volume); layout.addWidget(self.survival); layout.addWidget(self.contribution)

    def set_record(self, record: dict[str, Any] | None) -> None:
        record = record or {}
        fraction = record.get("tumour_volume_fraction"); survival = record.get("mean_surviving_fraction")
        contribution = record.get("survivor_contribution_fraction")
        self.volume.setText(f"Volume  {100.0 * float(fraction):.2f}%" if fraction is not None else "Volume  —")
        self.survival.setText(f"Mean SF  {float(survival):.4g}" if survival is not None else "Mean SF  —")
        self.contribution.setText(f"Contribution  {100.0 * float(contribution):.2f}%" if contribution is not None else "Contribution  —")

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.region_id); event.accept(); return
        super().mousePressEvent(event)


class SurvivalContributionBar(QWidget):
    """Clickable 100% bar over the stored regional survivor contributions."""
    selected = Signal(str)
    COLOURS = {"H": QColor("#e85d75"), "V": QColor("#33b5a5"), "O": QColor("#7689de")}
    LABELS = {"H": "Vertex", "V": "Valley", "O": "Other GTV"}

    def __init__(self) -> None:
        super().__init__(); self.records: list[dict[str, Any]] = []; self.setMinimumHeight(92); self.setCursor(Qt.PointingHandCursor)

    def set_records(self, records: list[dict[str, Any]]) -> None:
        self.records = list(records); self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#13263a")); painter.drawText(0, 16, "Residual tumour-survival contribution")
        bar = QRectF(0, 27, max(self.width(), 1), 24); offset = 0.0
        total = sum(max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) for item in self.records)
        denominator = total if total > 0 else 1.0
        for item in self.records:
            region = str(item.get("region_id")); value = max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) / denominator
            width = bar.width() * value; painter.fillRect(QRectF(offset, bar.y(), width, bar.height()), self.COLOURS.get(region, QColor("#718096"))); offset += width
        painter.setPen(QPen(QColor("#9fb0c1"), 1)); painter.drawRect(bar)
        x = 0
        for item in self.records:
            region = str(item.get("region_id")); value = 100.0 * float(item.get("survivor_contribution_fraction") or 0.0)
            painter.setPen(self.COLOURS.get(region, QColor("#718096"))); painter.drawText(x, 76, f"■ {self.LABELS.get(region, region)} {value:.2f}%")
            x += max(self.width() // 3, 150)

    def mousePressEvent(self, event: Any) -> None:
        if not self.records or event.button() != Qt.LeftButton: return
        total = sum(max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) for item in self.records)
        if total <= 0: return
        position = float(event.position().x()) / max(float(self.width()), 1.0); cumulative = 0.0
        for item in self.records:
            cumulative += max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) / total
            if position <= cumulative:
                self.selected.emit(str(item.get("region_id"))); break


class SurvivalDistributionCanvas(QWidget):
    """Display-only regional histogram of −log10(SF) from stored fields."""
    COLOURS = SurvivalContributionBar.COLOURS

    def __init__(self) -> None:
        super().__init__(); self.series: dict[str, np.ndarray] = {}; self.setMinimumHeight(150)

    def set_data(self, data: Layer31ViewerData | None) -> None:
        self.series = {}
        if data is not None and "negative_log10_survival_MLQ" in data.fields:
            values = data.fields["negative_log10_survival_MLQ"]
            for region, name in (("H", "Region: Vertices"), ("V", "Region: Valleys"), ("O", "Region: Other GTV")):
                mask = data.masks.get(name)
                if mask is not None and np.any(mask): self.series[region] = np.asarray(values[mask], dtype=float)
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#f8fbfe")); painter.setPen(QColor("#13263a"))
        painter.drawText(10, 18, "Regional MLQ survival distribution · −log₁₀(SF)")
        plot = self.rect().adjusted(42, 28, -12, -28)
        if not self.series:
            painter.setPen(QColor("#62758a")); painter.drawText(plot, Qt.AlignCenter, "No stored MLQ survival field"); return
        finite = np.concatenate([item[np.isfinite(item)] for item in self.series.values()]); maximum = max(float(np.max(finite)), 1.0)
        bins = np.linspace(0.0, maximum, 33); band = plot.height() / max(len(self.series), 1)
        for row, (region, values) in enumerate(self.series.items()):
            counts, _ = np.histogram(values[np.isfinite(values)], bins=bins); peak = max(int(np.max(counts)), 1); y0 = plot.top() + row * band
            for index, count in enumerate(counts):
                width = plot.width() / len(counts); height = (band - 8) * float(count) / peak
                painter.fillRect(QRectF(plot.left() + index * width, y0 + band - height - 4, max(width - 1, 1), height), self.COLOURS[region])
            painter.setPen(self.COLOURS[region]); painter.drawText(4, int(y0 + band / 2), region)
        painter.setPen(QColor("#62758a")); painter.drawText(plot.left(), self.height() - 6, "0"); painter.drawText(plot.right() - 45, self.height() - 6, f"{maximum:.3g}")
