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
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ascend.gui.theme import METRIC_LABELS, MetricCard, StatePanel, StatusPill, WarningBanner
from ascend.gui.saddle_graph_panel import SaddleGraphPanel
from ascend.gui.vertex_profile_panel import VertexProfilePanel
from ascend.gui.viewer_guidance import show_viewer_guide
from ascend.gui.workstation_widgets import GraphCanvas, VerticesQACanvas, WorkstationToolBox
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
        self.layer21_guide_button = QPushButton("Interactive viewer guide…")
        self.layer21_guide_button.setToolTip("Explain every Layer 2.1 viewer control, visual encoding, and FWHM output")
        self.layer21_guide_button.clicked.connect(lambda: show_viewer_guide(self, "layer2_1"))
        self.layer21_status_pill = StatusPill("NOT RUN")
        self.layer21_interpretation_pill = StatusPill("NOT RUN")
        self.layer21_card = QLabel("No Layer 2.1 result")
        self.layer21_card.setObjectName("sectionDescription")
        row.addWidget(run)
        row.addWidget(physical)
        row.addWidget(self.layer21_guide_button)
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
        self.layer21_tabs.setMinimumHeight(760)
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
        self.layer21_vertex_table = _table([
            "Vertex", "V95 RxH (%)", "Applicability", "Dmean (Gy)", "D95 (Gy)",
            "Dmax (Gy)", "Volume (cc)", "Local FWHM (mm)", "Nearest distance (mm)",
        ])
        self.layer21_vertex = _text_view()
        self.layer21_vertex.setMaximumHeight(150)
        vertex_layout.addWidget(self.layer21_vertex_summary)
        vertex_layout.addWidget(self.layer21_vertex_table)
        vertex_layout.addWidget(self.layer21_vertex)
        self.individual_vertex_qa_table_page = vertex_page
        vertices_layout_page = QWidget()
        vertices_layout = QVBoxLayout(vertices_layout_page)
        vertices_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_vertices_tabs = QTabWidget()
        interactive_page = QWidget()
        interactive_layout = QVBoxLayout(interactive_page)
        interactive_layout.setContentsMargins(0, 0, 0, 0)
        self.vertices_controls_card, vertices_controls_layout = self._card(
            "Vertices QA controls",
            "Centroid layout and nearest-neighbour distances use stored Layer 2.1 evidence. Colour encodes local FWHM, marker size encodes volume, and hover opens the vertex QA menu.",
        )
        self.vertices_controls_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        vertices_controls = QHBoxLayout()
        vertices_controls.addWidget(QLabel("Projection"))
        self.vertices_projection = QComboBox()
        for label, value in (
            ("Automatic", "auto"), ("Axial (X–Y)", "axial"),
            ("Sagittal (Y–Z)", "sagittal"), ("Coronal (X–Z)", "coronal"),
        ):
            self.vertices_projection.addItem(label, value)
        self.vertices_labels = QCheckBox("Vertex labels")
        self.vertices_labels.setChecked(True)
        self.vertices_distance_labels = QCheckBox("Distance labels")
        self.vertices_distance_labels.setChecked(True)
        vertices_controls.addWidget(self.vertices_projection)
        vertices_controls.addWidget(self.vertices_labels)
        vertices_controls.addWidget(self.vertices_distance_labels)
        self.vertices_canvas = VerticesQACanvas()
        self.vertices_projection.currentIndexChanged.connect(
            lambda _index: self.vertices_canvas.set_projection(str(self.vertices_projection.currentData()))
        )
        self.vertices_labels.toggled.connect(self.vertices_canvas.set_vertex_labels_visible)
        self.vertices_distance_labels.toggled.connect(self.vertices_canvas.set_distance_labels_visible)
        for label, operation, description in (
            ("−", lambda: self.vertices_canvas.zoom_by(1 / 1.2), "Zoom out"),
            ("+", lambda: self.vertices_canvas.zoom_by(1.2), "Zoom in"),
            ("↺", lambda: self.vertices_canvas.rotate_by(-15), "Rotate left 15 degrees"),
            ("↻", lambda: self.vertices_canvas.rotate_by(15), "Rotate right 15 degrees"),
            ("Fit", self.vertices_canvas.reset_view, "Reset zoom, rotation, and pan"),
        ):
            button = QPushButton(label)
            button.setToolTip(description)
            button.clicked.connect(operation)
            vertices_controls.addWidget(button)
        vertices_controls.addStretch()
        self.vertices_layout_summary = QLabel("No vertices QA layout")
        self.vertices_layout_summary.setObjectName("sectionDescription")
        vertices_controls.addWidget(self.vertices_layout_summary)
        vertices_controls_layout.addLayout(vertices_controls)
        interactive_layout.addWidget(self.vertices_controls_card)
        interactive_layout.addWidget(self.vertices_canvas, 1)
        self.layer21_vertices_tabs.addTab(interactive_page, "Interactive layout")

        global_fwhm_page = QWidget()
        global_fwhm_layout = QVBoxLayout(global_fwhm_page)
        global_fwhm_layout.setContentsMargins(8, 8, 8, 8)
        fwhm_cards = QHBoxLayout()
        average_card, average_layout = self._card("Average global FWHM", "Arithmetic mean of stored local vertex FWHM values.")
        self.layer21_fwhm_average = QLabel("— mm")
        self.layer21_fwhm_average.setObjectName("metricValue")
        average_layout.addWidget(self.layer21_fwhm_average)
        median_card, median_layout = self._card("Median global FWHM", "50th percentile of stored local vertex FWHM values.")
        self.layer21_fwhm_median = QLabel("— mm")
        self.layer21_fwhm_median.setObjectName("metricValue")
        median_layout.addWidget(self.layer21_fwhm_median)
        range_card, range_layout = self._card("Observed range", "Minimum to maximum local FWHM across valid vertices.")
        self.layer21_fwhm_range = QLabel("— mm")
        self.layer21_fwhm_range.setObjectName("metricValue")
        range_layout.addWidget(self.layer21_fwhm_range)
        fwhm_cards.addWidget(average_card)
        fwhm_cards.addWidget(median_card)
        fwhm_cards.addWidget(range_card)
        global_fwhm_layout.addLayout(fwhm_cards)
        self.layer21_fwhm_status = QLabel("No global FWHM summary is available.")
        self.layer21_fwhm_status.setObjectName("sectionDescription")
        self.layer21_fwhm_status.setWordWrap(True)
        global_fwhm_layout.addWidget(self.layer21_fwhm_status)
        self.layer21_fwhm_table = _table([
            "Vertex", "Local FWHM (mm)", "Native X (mm)", "Native Y (mm)",
            "Native Z (mm)", "Half-max dose (Gy)",
        ])
        global_fwhm_layout.addWidget(self.layer21_fwhm_table)
        self.layer21_vertices_tabs.addTab(global_fwhm_page, "Global FWHM")
        vertices_layout.addWidget(self.layer21_vertices_tabs)
        self.individual_vertex_layout_page = vertices_layout_page
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
        self.individual_vertex_oar_page = oar_page
        provenance_page = QWidget()
        provenance_layout = QVBoxLayout(provenance_page)
        provenance_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_provenance = _text_view()
        provenance_layout.addWidget(self.layer21_provenance)
        self.layer21_tabs.addItem(supporting_page, "Supporting outputs")
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
        self.layer22_guide_button = QPushButton("Interactive viewer guide…")
        self.layer22_guide_button.setToolTip("Explain graph, vertex-profile, saddle, CAD, and orthogonal-view functions")
        self.layer22_guide_button.clicked.connect(lambda: show_viewer_guide(self, "layer2_2"))
        self.layer22_status_pill = StatusPill("NOT RUN")
        self.layer22_interpretation_pill = StatusPill("NOT RUN")
        self.layer22_card = QLabel("No Layer 2.2 result")
        self.layer22_card.setObjectName("sectionDescription")
        row.addWidget(run)
        row.addWidget(build_viewer)
        row.addWidget(self.layer22_guide_button)
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
        self.layer22_controls_card, controls_layout = self._card(
            "Graph controls", "Projection and visibility affect presentation only; stored node and edge records remain unchanged."
        )
        self.layer22_controls_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
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
        overview_layout.addWidget(self.layer22_controls_card)
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
        self.individual_vertex_graph_page = overview
        self.layer22_vertex_profiles_panel = VertexProfilePanel()
        self.layer22_saddle_panel = SaddleGraphPanel()
        self.layer22_saddle_panel.displayModeChanged.connect(self.graph_canvas.set_edge_metric_mode)
        self.layer22_saddle_panel.edgeSelected.connect(self.graph_canvas.select_edge)
        self.layer22_saddle_panel.saddleMarkersChanged.connect(self.graph_canvas.set_saddle_markers_visible)
        self.layer22_saddle_panel.saddlePathsChanged.connect(self.graph_canvas.set_saddle_paths_visible)
        self.layer22_saddle_panel.diagnosticCorridorChanged.connect(self.graph_canvas.set_diagnostic_corridors_visible)
        viewer_page = QWidget()
        self.layer22_viewer_layout = QVBoxLayout(viewer_page)
        self.layer22_viewer_layout.setContentsMargins(0, 0, 0, 0)
        self.layer22_viewer_status = QLabel("Run Layer 2.2, then build the 3D viewer.")
        self.layer22_viewer_status.setWordWrap(True)
        self.layer22_viewer_layout.addWidget(self.layer22_viewer_status)
        self.layer22_viewer_layout.addStretch()
        self.layer22_display_tabs.addTab(viewer_page, "3D masks / dose views")
        layout.addWidget(self.layer22_display_tabs, 1)

    def _build_individual_vertex_qa_page(self) -> None:
        """Build one interactive presentation workspace over stored Layer 2.1/2.2 vertex evidence."""
        _, layout = self._new_page(
            "Individual vertex QA",
            "Unified display-only workspace for graph, profile, dose, FWHM, saddle, and OAR-to-vertex evidence.",
        )
        action_row = QHBoxLayout()
        run = QPushButton("Run / refresh physical analysis")
        run.setObjectName("primary")
        run.clicked.connect(self._run_physical)
        guide = QPushButton("Interactive workspace guide…")
        guide.clicked.connect(lambda: show_viewer_guide(self, "individual_vertex_qa"))
        self.vertex_qa_layer21_status = StatusPill("NOT RUN")
        self.vertex_qa_layer22_status = StatusPill("NOT RUN")
        self.vertex_qa_run_summary = QLabel("Run Layers 2.1 and 2.2 to populate the unified workspace.")
        self.vertex_qa_run_summary.setObjectName("sectionDescription")
        action_row.addWidget(run)
        action_row.addWidget(guide)
        action_row.addWidget(QLabel("L2.1"))
        action_row.addWidget(self.vertex_qa_layer21_status)
        action_row.addWidget(QLabel("L2.2"))
        action_row.addWidget(self.vertex_qa_layer22_status)
        action_row.addWidget(self.vertex_qa_run_summary, 1)
        layout.addLayout(action_row)

        selection_card, selection_layout = self._card(
            "Linked workspace controls",
            "Select a vertex or edge once. Graph, profile, QA table, vertex layout, and saddle evidence follow the same stored identity.",
        )
        selection_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel("View"))
        self.vertex_qa_view_selector = QComboBox()
        selection_row.addWidget(self.vertex_qa_view_selector)
        selection_row.addWidget(QLabel("Vertex"))
        self.vertex_qa_vertex_selector = QComboBox()
        self.vertex_qa_vertex_selector.setMinimumWidth(150)
        selection_row.addWidget(self.vertex_qa_vertex_selector)
        selection_row.addWidget(QLabel("Edge"))
        self.vertex_qa_edge_selector = QComboBox()
        self.vertex_qa_edge_selector.setMinimumWidth(190)
        selection_row.addWidget(self.vertex_qa_edge_selector)
        selection_row.addStretch()
        self.vertex_qa_selection_summary = QLabel("No vertex or edge selected")
        self.vertex_qa_selection_summary.setObjectName("sectionDescription")
        selection_row.addWidget(self.vertex_qa_selection_summary)
        selection_layout.addLayout(selection_row)
        layout.addWidget(selection_card)

        self.vertex_qa_tabs = QTabWidget()
        for page, label in (
            (self.individual_vertex_graph_page, "Hover graph overview"),
            (self.layer22_vertex_profiles_panel, "Vertex profiles"),
            (self.individual_vertex_qa_table_page, "Per-vertex QA"),
            (self.individual_vertex_layout_page, "Vertex layout / FWHM"),
            (self.layer22_saddle_panel, "Saddle graphs"),
            (self.individual_vertex_oar_page, "OAR geometry"),
        ):
            self.vertex_qa_tabs.addTab(page, label)
            self.vertex_qa_view_selector.addItem(label)
        self.vertex_qa_tabs.setMinimumHeight(720)
        layout.addWidget(self.vertex_qa_tabs, 1)

        self._syncing_vertex_qa_vertex = False
        self._syncing_vertex_qa_edge = False
        self.vertex_qa_view_selector.currentIndexChanged.connect(self.vertex_qa_tabs.setCurrentIndex)
        self.vertex_qa_tabs.currentChanged.connect(self.vertex_qa_view_selector.setCurrentIndex)
        self.vertex_qa_vertex_selector.currentIndexChanged.connect(self._vertex_qa_selector_changed)
        self.vertex_qa_edge_selector.currentIndexChanged.connect(self._vertex_qa_edge_selector_changed)
        self.graph_canvas.nodeSelected.connect(self._select_unified_vertex)
        self.graph_canvas.edgeSelected.connect(self._select_unified_edge)
        self.vertices_canvas.vertexSelected.connect(self._select_unified_vertex)
        self.layer22_vertex_profiles_panel.vertexSelected.connect(self._select_unified_vertex)
        self.layer22_saddle_panel.edgeSelected.connect(self._select_unified_edge)
        self.layer21_vertex_table.cellClicked.connect(self._vertex_qa_table_clicked)
        self.graph_nodes.cellClicked.connect(self._vertex_graph_node_clicked)
        self.graph_edges.cellClicked.connect(lambda row, _column: self._select_unified_edge(row))
        self.layer21_oar_table.cellClicked.connect(self._vertex_oar_table_clicked)
        self.layer21_oar_vertex_table.cellClicked.connect(self._vertex_oar_vertex_table_clicked)

    def _vertex_qa_selector_changed(self, _index: int) -> None:
        self._select_unified_vertex(str(self.vertex_qa_vertex_selector.currentData() or ""))

    def _vertex_qa_edge_selector_changed(self, _index: int) -> None:
        data = self.vertex_qa_edge_selector.currentData()
        if isinstance(data, int):
            self._select_unified_edge(data)

    def _select_unified_vertex(self, vertex_id: str) -> None:
        vertex_id = str(vertex_id or "")
        if not vertex_id or self._syncing_vertex_qa_vertex:
            return
        self._syncing_vertex_qa_vertex = True
        try:
            selector_index = self.vertex_qa_vertex_selector.findData(vertex_id)
            if selector_index >= 0 and selector_index != self.vertex_qa_vertex_selector.currentIndex():
                self.vertex_qa_vertex_selector.setCurrentIndex(selector_index)
            self.graph_canvas.select_node(vertex_id)
            self.vertices_canvas.select_vertex(vertex_id)
            profile_index = self.layer22_vertex_profiles_panel.selector.findData(vertex_id)
            if profile_index >= 0 and profile_index != self.layer22_vertex_profiles_panel.selector.currentIndex():
                self.layer22_vertex_profiles_panel.selector.setCurrentIndex(profile_index)
            for row in range(self.layer21_vertex_table.rowCount()):
                item = self.layer21_vertex_table.item(row, 0)
                if item and item.text() == vertex_id:
                    self.layer21_vertex_table.selectRow(row)
                    break
            self.vertex_qa_selection_summary.setText(f"Selected vertex: {vertex_id}")
        finally:
            self._syncing_vertex_qa_vertex = False

    def _select_unified_edge(self, edge_index: int) -> None:
        if edge_index < 0 or self._syncing_vertex_qa_edge:
            return
        self._syncing_vertex_qa_edge = True
        try:
            selector_index = self.vertex_qa_edge_selector.findData(int(edge_index))
            if selector_index >= 0 and selector_index != self.vertex_qa_edge_selector.currentIndex():
                self.vertex_qa_edge_selector.setCurrentIndex(selector_index)
            self.graph_canvas.select_edge(int(edge_index))
            self.layer22_saddle_panel.select_edge(int(edge_index))
            edges = (self.graph_canvas.result or {}).get("edges", [])
            if edge_index < len(edges):
                edge = edges[edge_index]
                nodes = " — ".join(map(str, edge.get("nodes") or []))
                self.vertex_qa_selection_summary.setText(f"Selected edge: {edge.get('edge_id', edge_index + 1)} · {nodes}")
        finally:
            self._syncing_vertex_qa_edge = False

    def _vertex_qa_table_clicked(self, row: int, _column: int) -> None:
        item = self.layer21_vertex_table.item(row, 0)
        if item:
            self._select_unified_vertex(item.text())

    def _vertex_graph_node_clicked(self, row: int, _column: int) -> None:
        item = self.graph_nodes.item(row, 0)
        if item:
            self._select_unified_vertex(item.text())

    def _vertex_oar_table_clicked(self, row: int, _column: int) -> None:
        item = self.layer21_oar_table.item(row, 7)
        if item and item.text() not in {"", "—", "None"}:
            self._select_unified_vertex(item.text())

    def _vertex_oar_vertex_table_clicked(self, row: int, _column: int) -> None:
        item = self.layer21_oar_vertex_table.item(row, 1)
        if item:
            self._select_unified_vertex(item.text())

    def _build_placeholder_page(self, title: str, detail: str) -> None:
        _, layout = self._new_page(title)
        layout.addWidget(StatePanel("NOT IMPLEMENTED", "Module interface only", detail), 1)
        layout.addStretch()
