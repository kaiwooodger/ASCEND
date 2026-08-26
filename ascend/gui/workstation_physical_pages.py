"""Physical-analysis page construction for the ASCEND workstation."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ascend.gui.theme import METRIC_LABELS, MetricCard, StatePanel, StatusPill, WarningBanner
from ascend.gui.workstation_widgets import GraphCanvas, WorkstationToolBox
from ascend.gui.workstation_widgets import table as _table
from ascend.gui.workstation_widgets import text_view as _text_view
from ascend.workflow.preferences import DEFAULT_SUPPORTING_OUTPUT_CATEGORIES


class WorkstationPhysicalPagesMixin:
    """Build Layer 2 physical-analysis pages."""

    def _build_layer21_page(self) -> None:
        _, layout = self._new_page(
            "Layer 2.1 — LRT physical metrics",
            "Warnings and applicability precede the six locked results. Cards are presentation adapters over stored Layer 2.1 records.",
        )
        selection_card, selection_layout = self._card(
            "Select optional supporting calculations before running Layer 2.1",
            "The locked six primary metrics always run. Unselected per-vertex QA, protocol endpoints, and OAR geometry are skipped rather than calculated and hidden.",
        )
        selection_row = QHBoxLayout()
        self._current_supporting_outputs: dict[str, Any] = {}
        self.supporting_outputs_enabled = QCheckBox("Calculate supporting outputs")
        self.supporting_outputs_enabled.setChecked(True)
        selection_row.addWidget(self.supporting_outputs_enabled)
        supporting_labels = {
            "coverage": "Coverage and volume context",
            "peak_valley": "Peak, valley and ratio context",
            "per_vertex": "Per-vertex QA",
            "protocol_native": "Protocol endpoints",
            "oar_geometry": "OAR / target geometry",
            "integrity": "Integrity and provenance",
        }
        self.supporting_output_checks: dict[str, QCheckBox] = {}
        for category in DEFAULT_SUPPORTING_OUTPUT_CATEGORIES:
            checkbox = QCheckBox(supporting_labels[category])
            checkbox.setChecked(True)
            checkbox.toggled.connect(lambda _checked: self._refresh_layer21(self.controller.case) if self.controller.case else None)
            self.supporting_output_checks[category] = checkbox
            selection_row.addWidget(checkbox)
        selection_row.addStretch()
        selection_layout.addLayout(selection_row)
        self.supporting_outputs_enabled.toggled.connect(self._toggle_supporting_output_controls)
        layout.addWidget(selection_card)
        row = QHBoxLayout()
        run = QPushButton("Run Layer 2.1")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer21)
        physical = QPushButton("Run physical analysis (2.1 + 2.2)")
        physical.clicked.connect(self._run_physical)
        self.layer21_status_pill = StatusPill("NOT RUN")
        self.layer21_interpretation_pill = StatusPill("NOT RUN")
        self.layer21_card = QLabel("No Layer 2.1 result")
        self.layer21_card.setObjectName("sectionDescription")
        row.addWidget(run)
        row.addWidget(physical)
        row.addWidget(self.layer21_status_pill)
        row.addWidget(self.layer21_interpretation_pill)
        row.addWidget(self.layer21_card, 1)
        layout.addLayout(row)
        self.layer21_warnings = WarningBanner("No Layer 2.1 warnings recorded.")
        layout.addWidget(self.layer21_warnings)
        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        self.metric_cards: dict[str, MetricCard] = {}
        metric_order = list(METRIC_LABELS)
        for index, metric_id in enumerate(metric_order):
            card = MetricCard(metric_id)
            self.metric_cards[metric_id] = card
            cards.addWidget(card, index // 3, index % 3)
        layout.addLayout(cards)
        self.metric_table = _table(["Metric", "Value", "Units", "Applicability", "Warnings"])
        self.metric_table.setMaximumHeight(220)
        layout.addWidget(self.metric_table)
        self.layer21_tabs = WorkstationToolBox()
        self.layer21_tabs.setMinimumHeight(390)
        supporting_page = QWidget()
        supporting_layout = QVBoxLayout(supporting_page)
        supporting_layout.setContentsMargins(8, 8, 8, 8)
        supporting_header = QHBoxLayout()
        supporting_description = QLabel("Stored supporting-output fields; no GUI recalculation.")
        supporting_description.setObjectName("sectionDescription")
        self.export_supporting_json_button = QPushButton("Export supporting outputs JSON")
        self.export_supporting_json_button.clicked.connect(self._export_supporting_outputs_json)
        self.export_supporting_json_button.setEnabled(False)
        supporting_header.addWidget(supporting_description, 1)
        supporting_header.addWidget(self.export_supporting_json_button)
        supporting_layout.addLayout(supporting_header)
        self.layer21_support = _table(["Section", "Item", "Value", "Units", "Recorded status / warnings"])
        self.layer21_support.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.layer21_support.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.layer21_support.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.layer21_support.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.layer21_support.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        supporting_layout.addWidget(self.layer21_support)
        vertex_page = QWidget()
        vertex_layout = QVBoxLayout(vertex_page)
        vertex_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_vertex_summary = QLabel("No per-vertex QA records")
        self.layer21_vertex_summary.setObjectName("sectionDescription")
        self.layer21_vertex_table = _table(["Vertex", "V95 RxH (%)", "Applicability", "Dmean (Gy)", "D95 (Gy)", "Dmax (Gy)", "Volume (cc)"])
        self.layer21_vertex = _text_view()
        self.layer21_vertex.setMaximumHeight(150)
        vertex_layout.addWidget(self.layer21_vertex_summary)
        vertex_layout.addWidget(self.layer21_vertex_table)
        vertex_layout.addWidget(self.layer21_vertex)
        oar_page = QWidget()
        oar_layout = QVBoxLayout(oar_page)
        oar_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_oar_status = QLabel("No optional OAR or internal-target geometry result.")
        self.layer21_oar_status.setObjectName("sectionDescription")
        self.layer21_oar_table = _table(
            [
                "Structure",
                "Classification",
                "Volume (cc)",
                "VTVH min separation (mm)",
                "Relationship",
                "Overlap (cc)",
                "Overlap of structure (%)",
                "Nearest vertex",
                "Vertex min separation (mm)",
                "Audit",
            ]
        )
        oar_layout.addWidget(self.layer21_oar_status)
        oar_layout.addWidget(self.layer21_oar_table)
        self.layer21_oar_vertex_table = _table(
            [
                "Structure",
                "Vertex",
                "Min separation (mm)",
                "Overlap (cc)",
                "Relationship",
                "Zero-distance reason",
            ]
        )
        oar_layout.addWidget(self.layer21_oar_vertex_table)
        provenance_page = QWidget()
        provenance_layout = QVBoxLayout(provenance_page)
        provenance_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_provenance = _text_view()
        provenance_layout.addWidget(self.layer21_provenance)
        self.layer21_tabs.addItem(supporting_page, "Supporting outputs")
        self.layer21_tabs.addItem(vertex_page, "Per-vertex QA")
        self.layer21_tabs.addItem(oar_page, "OAR geometry")
        self.layer21_tabs.addItem(provenance_page, "Provenance")
        layout.addWidget(self.layer21_tabs, 1)

    def _toggle_supporting_output_controls(self, enabled: bool) -> None:
        for checkbox in getattr(self, "supporting_output_checks", {}).values():
            checkbox.setEnabled(enabled)
        if hasattr(self, "export_supporting_json_button"):
            self.export_supporting_json_button.setEnabled(enabled and bool(self._current_supporting_outputs))
        if self.controller.case and not self._loading_configuration:
            self._refresh_layer21(self.controller.case)

    def _build_layer22_page(self) -> None:
        _, layout = self._new_page(
            "Layer 2.2 — Spatial PVDR", "Profile-orientation-independent nearest-neighbour graph using validated native geometry."
        )
        row = QHBoxLayout()
        run = QPushButton("Run Layer 2.2")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer22)
        build_viewer = QPushButton("Build / refresh 3D viewer")
        build_viewer.clicked.connect(self._build_layer22_visualization)
        self.layer22_status_pill = StatusPill("NOT RUN")
        self.layer22_interpretation_pill = StatusPill("NOT RUN")
        self.layer22_card = QLabel("No Layer 2.2 result")
        self.layer22_card.setObjectName("sectionDescription")
        row.addWidget(run)
        row.addWidget(build_viewer)
        row.addWidget(self.layer22_status_pill)
        row.addWidget(self.layer22_interpretation_pill)
        row.addWidget(self.layer22_card, 1)
        layout.addLayout(row)
        self.layer22_warnings = WarningBanner("No Layer 2.2 warnings recorded.")
        layout.addWidget(self.layer22_warnings)
        self.layer22_display_tabs = QTabWidget()
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        controls_card, controls_layout = self._card(
            "Graph controls", "Projection and visibility affect presentation only; stored node and edge records remain unchanged."
        )
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Projection"))
        self.graph_projection = QComboBox()
        for label, value in (("Automatic", "auto"), ("Axial (X–Y)", "axial"), ("Sagittal (Y–Z)", "sagittal"), ("Coronal (X–Z)", "coronal")):
            self.graph_projection.addItem(label, value)
        self.graph_projection.currentIndexChanged.connect(
            lambda _index: self.graph_canvas.set_projection(str(self.graph_projection.currentData()))
        )
        self.graph_edge_labels = QCheckBox("Edge iPVDR labels")
        self.graph_edge_labels.setChecked(True)
        self.graph_edge_labels.toggled.connect(lambda visible: self.graph_canvas.set_edge_labels_visible(visible))
        self.graph_invalid_edges = QCheckBox("Invalid edges")
        self.graph_invalid_edges.setChecked(True)
        self.graph_invalid_edges.toggled.connect(lambda visible: self.graph_canvas.set_invalid_edges_visible(visible))
        self.graph_result_summary = QLabel("No graph result")
        self.graph_result_summary.setObjectName("sectionDescription")
        controls.addWidget(self.graph_projection)
        controls.addWidget(self.graph_edge_labels)
        controls.addWidget(self.graph_invalid_edges)
        for label, operation in (
            ("−", lambda: self.graph_canvas.zoom_by(1 / 1.2)),
            ("+", lambda: self.graph_canvas.zoom_by(1.2)),
            ("↺", lambda: self.graph_canvas.rotate_by(-15)),
            ("↻", lambda: self.graph_canvas.rotate_by(15)),
            ("Fit", lambda: self.graph_canvas.reset_view()),
        ):
            button = QPushButton(label)
            button.clicked.connect(operation)
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.graph_result_summary)
        controls_layout.addLayout(controls)
        overview_layout.addWidget(controls_card)
        split = QSplitter(Qt.Horizontal)
        self.graph_canvas = GraphCanvas()
        split.addWidget(self.graph_canvas)
        tabs = QTabWidget()
        self.graph_summary = _table(["Summary item", "Value", "Meaning"])
        self.graph_nodes = _table(["Node", "X", "Y", "Z", "D50 (Gy)"])
        self.graph_edges = _table(["Edge", "Nodes", "Length (mm)", "Valley D50", "iPVDR", "Status"])
        self.graph_provenance = _text_view()
        tabs.addTab(self.graph_summary, "Summary")
        tabs.addTab(self.graph_nodes, "Nodes")
        tabs.addTab(self.graph_edges, "Edges")
        tabs.addTab(self.graph_provenance, "Provenance")
        split.addWidget(tabs)
        split.setSizes([620, 560])
        overview_layout.addWidget(split)
        self.layer22_display_tabs.addTab(overview, "Graph overview")
        viewer_page = QWidget()
        self.layer22_viewer_layout = QVBoxLayout(viewer_page)
        self.layer22_viewer_layout.setContentsMargins(0, 0, 0, 0)
        self.layer22_viewer_status = QLabel("Run Layer 2.2, then build the 3D viewer.")
        self.layer22_viewer_status.setWordWrap(True)
        self.layer22_viewer_layout.addWidget(self.layer22_viewer_status)
        self.layer22_viewer_layout.addStretch()
        self.layer22_display_tabs.addTab(viewer_page, "3D masks / dose views")
        layout.addWidget(self.layer22_display_tabs, 1)

    def _build_placeholder_page(self, title: str, detail: str) -> None:
        _, layout = self._new_page(title)
        layout.addWidget(StatePanel("NOT IMPLEMENTED", "Module interface only", detail), 1)
        layout.addStretch()
