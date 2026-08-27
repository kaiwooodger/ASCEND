"""Responsive screen-space allocation for the unified Layer 3.1 viewer."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QResizeEvent, QShowEvent


class Layer31ResponsiveMixin:
    """Prioritise graphical canvases across desktop monitor sizes."""

    _responsive_width_band: int | None

    def showEvent(self, event: QShowEvent) -> None:
        """Allocate monitor space to image canvases after native layout."""
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_responsive_splitter_sizes)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rebalance controls only when crossing a material width band."""
        super().resizeEvent(event)
        width_band = max(self.width(), 1) // 240
        if width_band != getattr(self, "_responsive_width_band", None):
            self._responsive_width_band = width_band
            QTimer.singleShot(0, self._apply_responsive_splitter_sizes)

    def _apply_responsive_splitter_sizes(self) -> None:
        """Keep controls usable while assigning remaining width to graphics."""
        width = max(self.width(), 720)
        analysis_width = max(180, min(230, round(width * 0.13)))
        self.workspace_splitter.setSizes([analysis_width, max(width - analysis_width, 1)])

        centre_width = max(self.workspace_splitter.sizes()[1], 490)
        cad_controls_width = max(230, min(320, round(centre_width * 0.20)))
        self.cad_splitter.setSizes([max(centre_width - cad_controls_width, 1), cad_controls_width])
