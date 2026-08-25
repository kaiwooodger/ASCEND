"""Review and export page construction for the ASCEND workstation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

from ascend.gui.workstation_widgets import text_view as _text_view


class WorkstationOutputPagesMixin:
    """Build review and export pages."""

    def _build_review_page(self) -> None:
        _, layout = self._new_page("Review", "Calculation state, interpretation state, warnings, and provenance remain separate.")
        self.review_text = _text_view()
        layout.addWidget(self.review_text, 1)

    def _build_export_page(self) -> None:
        _, layout = self._new_page(
            "Export", "JSON is authoritative. CSV files are rendered from stored result objects without recalculation."
        )
        button = QPushButton("Export JSON and CSV")
        button.setObjectName("primary")
        button.clicked.connect(self._export)
        layout.addWidget(button, 0, Qt.AlignLeft)
        self.export_path = QLabel("Open a case first")
        self.export_path.setWordWrap(True)
        layout.addWidget(self.export_path)
        self.export_result = _text_view()
        layout.addWidget(self.export_result, 1)
