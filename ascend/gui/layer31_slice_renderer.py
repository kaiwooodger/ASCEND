"""Two-dimensional rendering of validated Layer 3.1 display fields."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget
from scipy import ndimage

from ascend.gui.layer31_viewer_models import Layer31ViewerData
from ascend.visualization.biology.anatomy_colours import anatomy_colour_map


def _slice(values: np.ndarray, orientation: str, index: int) -> np.ndarray:
    if orientation == "axial":
        return values[index]
    if orientation == "sagittal":
        return values[:, :, index]
    return values[:, index, :]


def _colour_map(values: np.ndarray, low: float, high: float, palette: str = "biological_lq") -> np.ndarray:
    fraction = np.zeros_like(values, dtype=float) if high <= low else np.clip((values - low) / (high - low), 0, 1)
    anchors_by_palette = {
        "physical_dose": [[8, 29, 88], [21, 101, 192], [23, 190, 207], [253, 210, 52], [211, 38, 49]],
        "survival": [[0, 0, 4], [68, 15, 118], [166, 54, 94], [238, 104, 60], [252, 253, 191]],
        "effect": [[8, 29, 88], [43, 97, 155], [30, 158, 137], [137, 213, 72], [255, 237, 77]],
        "warning": [[25, 25, 25], [255, 72, 60]],
        "biological_lq": [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]],
    }
    anchors = np.asarray(anchors_by_palette.get(palette, anchors_by_palette["biological_lq"]), dtype=float)
    position = fraction * (len(anchors) - 1)
    lower = np.floor(position).astype(int); upper = np.minimum(lower + 1, len(anchors) - 1)
    weight = (position - lower)[..., None]
    return np.round(anchors[lower] * (1 - weight) + anchors[upper] * weight).astype(np.uint8)


class BiologicalSliceCanvas(QWidget):
    voxelSelected = Signal(int, int, int)

    def __init__(self, orientation: str) -> None:
        super().__init__(); self.orientation = orientation; self.data: Layer31ViewerData | None = None
        self.field = ""; self.index = 0; self.roi = ""; self.show_structures = True; self.show_warning = True
        self.visible_rois: list[str] = []
        self.scalar_range: tuple[float, float] | None = None
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self._drag_position: QPointF | None = None
        self.crosshair: tuple[int, int, int] | None = None
        self._image_size: tuple[int, int] = (0, 0); self._display_size: tuple[int, int] = (0, 0); self._display_center = QPointF()
        self.setMinimumSize(120, 180)
        self.setCursor(Qt.OpenHandCursor)

    def zoom_by(self, factor: float) -> None:
        self.zoom = float(np.clip(self.zoom * factor, 0.5, 8.0)); self.update()

    def rotate_by(self, degrees: float) -> None:
        self.rotation_degrees = (self.rotation_degrees + float(degrees)) % 360.0; self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15); event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = event.position(); self.setCursor(Qt.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is not None:
            current = event.position(); self.pan += current - self._drag_position; self._drag_position = current; self.update(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = None; self.setCursor(Qt.OpenHandCursor); event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        """Map a display click back to one validated dose-grid voxel."""
        if self.data is None or min(self._image_size) <= 0 or min(self._display_size) <= 0:
            return
        transform = QTransform()
        transform.translate(self._display_center.x(), self._display_center.y())
        transform.rotate(self.rotation_degrees); transform.scale(self.zoom, self.zoom)
        inverse, valid = transform.inverted()
        if not valid:
            return
        local = inverse.map(event.position())
        display_width, display_height = self._display_size
        column = int(np.floor((local.x() + display_width / 2.0) * self._image_size[0] / display_width))
        row = int(np.floor((local.y() + display_height / 2.0) * self._image_size[1] / display_height))
        column = int(np.clip(column, 0, self._image_size[0] - 1)); row = int(np.clip(row, 0, self._image_size[1] - 1))
        if self.orientation == "axial":
            voxel = (self.index, row, column)
        elif self.orientation == "sagittal":
            voxel = (row, column, self.index)
        else:
            voxel = (row, self.index, column)
        self.voxelSelected.emit(*voxel); event.accept()

    def set_view(self, data: Layer31ViewerData, field: str, index: int, roi: str, show_structures: bool, show_warning: bool,
                 scalar_range: tuple[float, float] | None = None, crosshair: tuple[int, int, int] | None = None,
                 visible_rois: list[str] | None = None) -> None:
        self.data, self.field, self.index, self.roi = data, field, index, roi
        self.show_structures, self.show_warning, self.scalar_range, self.crosshair = show_structures, show_warning, scalar_range, crosshair
        self.visible_rois = list(visible_rois or []); self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#071a38"))
        if self.data is None or not self.field:
            painter.setPen(QColor("#d6e2ef")); painter.drawText(self.rect(), Qt.AlignCenter, "No Layer 3.1 field")
            return
        field = self.data.fields[self.field]; plane = np.asarray(_slice(field, self.orientation, self.index))
        low, high = self.scalar_range or tuple(self.data.field_metadata[self.field]["display_range"])
        meta = self.data.field_metadata[self.field]
        rgb = _colour_map(plane, low, high, str(meta.get("palette") or "biological_lq"))
        if self.show_structures:
            overlay_colours = anatomy_colour_map(self.data.masks)
            for name in self.visible_rois:
                if name not in self.data.masks: continue
                mask = np.asarray(_slice(self.data.masks[name], self.orientation, self.index), dtype=bool)
                colour = QColor(overlay_colours.get(name, "#dce6f0"))
                rgb[mask & ~ndimage.binary_erosion(mask)] = [colour.red(), colour.green(), colour.blue()]
            if self.roi in self.data.masks:
                mask = np.asarray(_slice(self.data.masks[self.roi], self.orientation, self.index), dtype=bool)
                rgb[mask & ~ndimage.binary_erosion(mask)] = [255, 255, 255]
        if self.show_warning and "LQ_high_dose_warning_mask" in self.data.fields:
            warning = np.asarray(_slice(self.data.fields["LQ_high_dose_warning_mask"], self.orientation, self.index), dtype=bool)
            rgb[warning & ~ndimage.binary_erosion(warning)] = [255, 70, 60]
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
        target = self.rect().adjusted(10, 35, -10, -50)
        scaled = image.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_size = (image.width(), image.height()); self._display_size = (scaled.width(), scaled.height())
        self._display_center = QPointF(target.center()) + self.pan
        painter.save(); painter.setClipRect(target)
        center = self._display_center
        painter.translate(center); painter.rotate(self.rotation_degrees); painter.scale(self.zoom, self.zoom)
        origin = QPointF(-scaled.width() / 2.0, -scaled.height() / 2.0)
        painter.drawImage(origin, scaled)
        if self.crosshair is not None:
            z_index, y_index, x_index = self.crosshair
            if self.orientation == "axial": row, column = y_index, x_index
            elif self.orientation == "sagittal": row, column = z_index, y_index
            else: row, column = z_index, x_index
            x_pos = origin.x() + (column + 0.5) * scaled.width() / max(image.width(), 1)
            y_pos = origin.y() + (row + 0.5) * scaled.height() / max(image.height(), 1)
            painter.setPen(QPen(QColor("#ffffff"), max(1.0 / self.zoom, 0.35), Qt.DashLine))
            painter.drawLine(QPointF(origin.x(), y_pos), QPointF(origin.x() + scaled.width(), y_pos))
            painter.drawLine(QPointF(x_pos, origin.y()), QPointF(x_pos, origin.y() + scaled.height()))
        painter.restore()
        painter.setPen(QColor("#eef5fb")); painter.drawText(10, 22, f"{self.orientation.upper()} · slice {self.index}")
        bar_left, bar_right, bar_y = 66, max(self.width() - 66, 67), self.height() - 34
        width = max(bar_right - bar_left, 1)
        colours = _colour_map(np.linspace(low, high, width)[None, :], low, high, str(meta.get("palette") or "biological_lq"))[0]
        for offset, colour in enumerate(colours):
            painter.setPen(QColor(*map(int, colour))); painter.drawLine(bar_left + offset, bar_y, bar_left + offset, bar_y + 8)
        painter.setPen(QColor("#eef5fb")); painter.drawText(8, bar_y + 8, f"{low:.3g}"); painter.drawText(bar_right + 4, bar_y + 8, f"{high:.3g}")
        painter.drawText(10, self.height() - 48, f"Complete 3D range · {meta['units']} · zoom {self.zoom:.2g}× · rotation {self.rotation_degrees:.0f}° · double-click selects voxel")


class BiologyColorBar(QWidget):
    """Shared quantitative legend for the 2D and 3D biological displays."""

    def __init__(self) -> None:
        super().__init__(); self.meta: dict[str, Any] = {}; self.display_range = (0.0, 1.0); self.actual_range = (0.0, 1.0)
        self.setMinimumHeight(62); self.setMaximumHeight(78)

    def set_scale(self, meta: dict[str, Any], display_range: tuple[float, float], actual_range: tuple[float, float]) -> None:
        self.meta = dict(meta); self.display_range = display_range; self.actual_range = actual_range; self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#f8fbfe"))
        if not self.meta:
            painter.setPen(QColor("#62758a")); painter.drawText(self.rect(), Qt.AlignCenter, "No quantitative field selected"); return
        low, high = self.display_range; actual_low, actual_high = self.actual_range
        left, right, top = 18, max(self.width() - 18, 19), 25; width = max(right - left, 1)
        colours = _colour_map(np.linspace(low, high, width)[None, :], low, high, str(self.meta.get("palette") or "biological_lq"))[0]
        for offset, colour in enumerate(colours):
            painter.setPen(QColor(*map(int, colour))); painter.drawLine(left + offset, top, left + offset, top + 13)
        painter.setPen(QColor("#13263a")); painter.drawText(left, 16, f"{self.meta.get('label', 'Field')} [{self.meta.get('units', '')}]")
        painter.drawText(left, 54, f"display {low:.4g}–{high:.4g}   ·   actual {actual_low:.4g}–{actual_high:.4g}")
        painter.drawText(max(right - 150, left), 16, "LOW EFFECT   →   HIGH EFFECT")
        painter.setPen(QColor("#7c8794")); painter.drawText(left, top + 13, "◁ below"); painter.drawText(right - 122, top + 13, "above ▷   invalid ▨")
