"""Review and export page construction for the ASCEND workstation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ascend.gui.workstation_widgets import text_view as _text_view
from ascend.report_options import PDF_REPORT_OPTION_GROUPS


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
        pdf_box = QGroupBox("Coherent PDF report")
        pdf_layout = QVBoxLayout(pdf_box)
        description = QLabel(
            "Select the stored results to include. The PDF keeps calculation status, applicability, warnings, units, and provenance with the chosen metrics."
        )
        description.setWordWrap(True)
        description.setObjectName("sectionDescription")
        pdf_layout.addWidget(description)
        self.pdf_report_checks: dict[str, QCheckBox] = {}
        for group_name, options in PDF_REPORT_OPTION_GROUPS:
            group = QGroupBox(group_name)
            grid = QGridLayout(group)
            for index, (option, label) in enumerate(options):
                checkbox = QCheckBox(label)
                checkbox.setChecked(True)
                self.pdf_report_checks[option] = checkbox
                grid.addWidget(checkbox, index // 2, index % 2)
            pdf_layout.addWidget(group)
        controls = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_pdf_report_options(True))
        clear_all = QPushButton("Clear all")
        clear_all.clicked.connect(lambda: self._set_pdf_report_options(False))
        pdf_button = QPushButton("Export selected PDF report")
        pdf_button.setObjectName("primary")
        pdf_button.clicked.connect(self._export_pdf)
        controls.addWidget(select_all)
        controls.addWidget(clear_all)
        controls.addStretch()
        controls.addWidget(pdf_button)
        pdf_layout.addLayout(controls)
        layout.addWidget(pdf_box)
        self.export_path = QLabel("Open a case first")
        self.export_path.setWordWrap(True)
        layout.addWidget(self.export_path)
        self.export_result = _text_view()
        layout.addWidget(self.export_result, 1)

    def _set_pdf_report_options(self, checked: bool) -> None:
        for checkbox in self.pdf_report_checks.values():
            checkbox.setChecked(checked)
