"""Read-only controls and evidence panel for stored Layer 2.2 saddles."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget


class SaddleGraphPanel(QWidget):
    """Present stored saddle edges and drive display-only graph modes."""

    edgeSelected = Signal(int)
    displayModeChanged = Signal(str)
    saddleMarkersChanged = Signal(bool)
    saddlePathsChanged = Signal(bool)
    diagnosticCorridorChanged = Signal(bool)

    MODES = (
        ("Midpoint PVDR", "midpoint_pvdr", "Edge colour: midpoint PVDR (dimensionless)"),
        ("Saddle PVDR", "saddle_pvdr", "Edge colour: saddle PVDR using local saddle D50 (dimensionless)"),
        ("Midpoint − saddle dose", "midpoint_minus_saddle_gy", "Edge colour: midpoint D50 minus saddle D50 (Gy)"),
        ("Edge length", "edge_length_mm", "Edge colour: centroid-to-centroid length (mm)"),
        ("Validation status", "validation_status", "Edge colour: valid versus explicitly excluded"),
    )

    def __init__(self) -> None:
        super().__init__(); self.extension: dict[str, Any] = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(4, 4, 4, 4)
        controls = QHBoxLayout(); controls.addWidget(QLabel("Edge colour"))
        self.mode = QComboBox()
        for label, value, definition in self.MODES:
            self.mode.addItem(label, value); self.mode.setItemData(self.mode.count() - 1, definition, 3)
        self.saddle_markers = QCheckBox("Saddle markers"); self.saddle_markers.setChecked(True)
        self.saddle_paths = QCheckBox("Saddle path")
        self.diagnostic_corridor = QCheckBox("Diagnostic corridor")
        controls.addWidget(self.mode); controls.addWidget(self.saddle_markers); controls.addWidget(self.saddle_paths); controls.addWidget(self.diagnostic_corridor); controls.addStretch()
        layout.addLayout(controls)
        legend_row = QHBoxLayout()
        self.legend_scale = QLabel()
        self.legend_scale.setFixedSize(150, 12)
        self.legend_scale.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 2px; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2463a0, stop:1 #b42318);"
        )
        self.legend = QLabel(self.MODES[0][2]); self.legend.setWordWrap(True)
        legend_row.addWidget(self.legend_scale); legend_row.addWidget(self.legend, 1); layout.addLayout(legend_row)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Edge", "Vertices", "Midpoint D50", "Saddle D50", "Midpoint PVDR", "Saddle PVDR", "Displacement", "Status"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 2)
        self.evidence = QTextEdit(); self.evidence.setReadOnly(True); layout.addWidget(self.evidence, 1)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.saddle_markers.toggled.connect(self.saddleMarkersChanged)
        self.saddle_paths.toggled.connect(self.saddlePathsChanged)
        self.diagnostic_corridor.toggled.connect(self.diagnosticCorridorChanged)
        self.table.cellClicked.connect(lambda row, _column: self.select_edge(row))

    @staticmethod
    def _display(value: Any, units: str = "") -> str:
        return f"{float(value):.4g}{(' ' + units) if units else ''}" if isinstance(value, (int, float)) and math.isfinite(float(value)) else "—"

    def set_result(self, extension: dict[str, Any] | None) -> None:
        self.extension = dict(extension or {})
        edges = list(self.extension.get("edges") or [])
        self.table.setRowCount(len(edges))
        for row, edge in enumerate(edges):
            values = (
                edge.get("edge_id"), " — ".join((edge.get("vertex_i_id"), edge.get("vertex_j_id"))),
                self._display(edge.get("midpoint_d50_gy"), "Gy"), self._display(edge.get("saddle_local_d50_gy"), "Gy"),
                self._display(edge.get("midpoint_pvdr")), self._display(edge.get("saddle_pvdr")),
                self._display(edge.get("saddle_to_midpoint_mm"), "mm"), edge.get("edge_status"),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value) if value is not None else "—"))
        self._update_legend()
        self.select_edge(0)

    def _mode_changed(self, index: int) -> None:
        self._update_legend()
        self.displayModeChanged.emit(str(self.mode.currentData()))

    def _update_legend(self) -> None:
        index = max(self.mode.currentIndex(), 0)
        _label, mode, definition = self.MODES[index]
        if mode == "validation_status":
            self.legend_scale.setStyleSheet(
                "border: 1px solid palette(mid); border-radius: 2px; "
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b42318, stop:0.5 #b42318, stop:0.5 #15803d, stop:1 #15803d);"
            )
            self.legend.setText(f"{definition} · excluded / valid")
            return
        self.legend_scale.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 2px; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2463a0, stop:1 #b42318);"
        )
        field = {
            "midpoint_pvdr": "midpoint_pvdr", "saddle_pvdr": "saddle_pvdr",
            "midpoint_minus_saddle_gy": "midpoint_minus_saddle_gy", "edge_length_mm": "edge_length_mm",
        }[mode]
        values = [
            float(edge[field]) for edge in self.extension.get("edges", [])
            if isinstance(edge.get(field), (int, float)) and math.isfinite(float(edge[field]))
        ]
        value_range = f" · {min(values):.4g} → {max(values):.4g}" if values else " · no valid values"
        self.legend.setText(definition + value_range)

    def select_edge(self, index: int) -> None:
        edges = list(self.extension.get("edges") or [])
        if index < 0 or index >= len(edges):
            self.evidence.setPlainText("No stored saddle edge is selected.")
            return
        self.table.selectRow(index); edge = edges[index]
        warnings = ", ".join(map(str, edge.get("warnings") or [])) or "None"
        self.evidence.setPlainText(
            f"{edge.get('vertex_i_id')} — {edge.get('vertex_j_id')}\n\n"
            f"Endpoint D50: {self._display(edge.get('vertex_i_d50_gy'), 'Gy')} / {self._display(edge.get('vertex_j_d50_gy'), 'Gy')}\n"
            f"Midpoint dose: {self._display(edge.get('midpoint_d50_gy'), 'Gy')}\n"
            f"Saddle dose: {self._display(edge.get('saddle_local_d50_gy'), 'Gy')}\n"
            f"Midpoint PVDR: {self._display(edge.get('midpoint_pvdr'))}\n"
            f"Saddle PVDR: {self._display(edge.get('saddle_pvdr'))}\n"
            f"Saddle displacement: {self._display(edge.get('saddle_to_midpoint_mm'), 'mm')}\n"
            f"Corridor radius: {self._display(edge.get('corridor_radius_mm'), 'mm')}\n"
            f"Status: {edge.get('edge_status')}\nExclusion: {edge.get('exclusion_reason') or 'None'}\nWarnings: {warnings}"
        )
        self.edgeSelected.emit(index)
