"""Qt widget construction for the Layer 3.1 biological viewer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ascend.gui.biology_pyvista_scene import PyVistaBiologicalScene3D
from ascend.gui.layer31_result_widgets import (
    RegionalResultCard,
    SurvivalContributionBar,
    SurvivalDistributionCanvas,
)
from ascend.gui.layer31_slice_renderer import BiologicalSliceCanvas, BiologyColorBar
from ascend.gui.viewer_guidance import show_viewer_guide


def build_layer31_viewer_ui(self: Any) -> None:
    """Build the viewer from cohesive, independently readable sections."""
    self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    self.setMinimumHeight(600)
    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    _build_header(self, layout)
    self.workflow_tabs = QTabWidget(self)
    self.workflow_tabs.setDocumentMode(True)
    self.workflow_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
    self.workflow_tabs.tabBar().setUsesScrollButtons(True)
    self.workflow_tabs.tabBar().setElideMode(Qt.ElideRight)
    self.workflow_tabs.hide()

    map_page = QWidget()
    map_layout = QVBoxLayout(map_page)
    map_layout.setContentsMargins(0, 0, 0, 0)
    map_layout.setSpacing(0)
    self.workspace_splitter = QSplitter(Qt.Horizontal)
    self.workspace_splitter.setChildrenCollapsible(False)
    self.workspace_splitter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    map_layout.addWidget(self.workspace_splitter, 1)
    _build_analysis_controls(self, self.workspace_splitter)
    _build_map_workspace(self, self.workspace_splitter)
    self.workspace_splitter.setSizes([190, 940])
    self.workspace_splitter.setStretchFactor(0, 0)
    self.workspace_splitter.setStretchFactor(1, 1)
    layout.addWidget(map_page, 1)
    self.workflow_tabs.addTab(QWidget(), "1  Maps and controls")

    result_page = QWidget()
    result_layout = QVBoxLayout(result_page)
    result_layout.setContentsMargins(4, 4, 4, 4)
    _build_result_summary(self, result_layout)
    self.workflow_tabs.addTab(result_page, "2  Whole-tumour result")

    regional_page = QWidget()
    regional_layout = QVBoxLayout(regional_page)
    regional_layout.setContentsMargins(4, 4, 4, 4)
    _build_regional_summary(self, regional_layout)
    self.workflow_tabs.addTab(regional_page, "3  Regional explanation")
    _connect_viewer_signals(self)


def _build_header(self: Any, layout: QVBoxLayout) -> None:
    header = QFrame()
    header.setObjectName("card")
    header_layout = QHBoxLayout(header)
    header_text = QVBoxLayout()
    title = QLabel("Layer 3.1 — Spatial Radiobiological Evaluation")
    title.setObjectName("sectionTitle")
    self.context_label = QLabel("Same validated anatomy, camera, masks, and crosshair with switchable biological fields.")
    self.context_label.setObjectName("sectionDescription")
    self.context_label.setWordWrap(True)
    header_text.addWidget(title)
    header_text.addWidget(self.context_label)
    header_layout.addLayout(header_text, 1)
    self.context_status = QLabel("NOT LOADED")
    self.context_status.setObjectName("statusPill")
    header_layout.addWidget(self.context_status)
    layout.addWidget(header)
    self.viewer_header = header

    self.hierarchy_label = QLabel("1  MAP  →  2  WHOLE-TUMOUR RESULT  →  3  REGIONAL EXPLANATION")
    self.hierarchy_label.setObjectName("sectionTitle")
    self.hierarchy_label.setAlignment(Qt.AlignCenter)
    self.hierarchy_label.setWordWrap(True)
    self.hierarchy_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    self.hierarchy_label.setToolTip(
        "Interpret the spatial field first, then the whole-tumour SF/EUD, then the regional survivor-contribution decomposition."
    )
    layout.addWidget(self.hierarchy_label)
    self.viewer_header.hide()
    self.hierarchy_label.hide()


def _build_analysis_controls(self: Any, workspace: Any) -> None:
    left = QFrame()
    left.setObjectName("card")
    left.setMinimumWidth(230)
    left.setMaximumWidth(300)
    left.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    left_scroll = QScrollArea()
    left_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    left_scroll.setWidgetResizable(True)
    left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    left_scroll.setFrameShape(QFrame.NoFrame)
    left_content = QWidget()
    left_layout = QVBoxLayout(left_content)
    left_title = QLabel("ANALYSIS CONTROLS")
    left_title.setObjectName("sectionTitle")
    left_layout.addWidget(left_title)
    left_layout.addWidget(QLabel("Displayed biological quantity"))
    self.field = QComboBox()
    self.field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    left_layout.addWidget(self.field)
    self.quantity_group = QButtonGroup(self)
    self.quantity_buttons: dict[str, QRadioButton] = {}
    for key, label in (
        ("dose", "Physical dose"),
        ("bed", "Spatial BED"),
        ("eqd2", "Spatial EQD2"),
        ("sf_log", "MLQ −log₁₀(SF)"),
        ("sf", "MLQ surviving fraction"),
        ("effect", "MLQ effect K"),
    ):
        button = QRadioButton(label)
        button.setEnabled(False)
        button.toggled.connect(lambda checked, value=key: self._quantity_button_changed(value, checked))
        self.quantity_group.addButton(button)
        self.quantity_buttons[key] = button
        left_layout.addWidget(button)
    left_layout.addSpacing(8)
    left_layout.addWidget(QLabel("Tissue / anatomical focus"))
    self.roi = QComboBox()
    left_layout.addWidget(self.roi)
    self.show_structures = QCheckBox("Structures")
    self.show_structures.setChecked(True)
    self.show_warning = QCheckBox("High-dose LQ warning")
    self.show_warning.setChecked(True)
    left_layout.addWidget(self.show_structures)
    left_layout.addWidget(self.show_warning)
    anatomy_title = QLabel("Anatomy visibility")
    anatomy_title.setObjectName("sectionDescription")
    left_layout.addWidget(anatomy_title)
    self.anatomy_checks: dict[str, QCheckBox] = {}
    for key, label in (("GTV", "GTV"), ("Vertices", "Vertices"), ("Valleys", "Valleys"), ("OAR", "OARs")):
        checkbox = QCheckBox(label)
        checkbox.setChecked(key in {"GTV", "Vertices", "Valleys"})
        checkbox.toggled.connect(self._anatomy_changed)
        self.anatomy_checks[key] = checkbox
        left_layout.addWidget(checkbox)
    left_layout.addSpacing(8)
    left_layout.addWidget(QLabel("Tumour sensitivity scenario"))
    scenario_row = QHBoxLayout()
    self.scenario_buttons: dict[str, QPushButton] = {}
    for scenario in ("C1", "C2", "C3"):
        button = QPushButton(scenario)
        button.setCheckable(True)
        button.setEnabled(False)
        button.setToolTip("Select a standard tumour-sensitivity scenario and recalculate through the Layer 3.1 scientific service.")
        button.clicked.connect(lambda _checked=False, value=scenario: self._request_scenario(value))
        self.scenario_buttons[scenario] = button
        scenario_row.addWidget(button)
    left_layout.addLayout(scenario_row)
    self.scenario_note = QLabel(
        "Scenario changes are recalculated by the Layer 3.1 scientific service; no model calculation occurs in this viewer."
    )
    self.scenario_note.setObjectName("sectionDescription")
    self.scenario_note.setWordWrap(True)
    left_layout.addWidget(self.scenario_note)
    left_layout.addSpacing(8)
    left_layout.addWidget(QLabel("Complete-volume colour scale"))
    self.range_mode = QComboBox()
    self.range_mode.addItems(["Robust 2–98 percentile", "Full range", "Manual fixed range", "Percentile"])
    self.range_min = QLineEdit()
    self.range_min.setPlaceholderText("minimum")
    self.range_max = QLineEdit()
    self.range_max.setPlaceholderText("maximum")
    self.percentile_min = QLineEdit("5")
    self.percentile_min.setPlaceholderText("P low")
    self.percentile_max = QLineEdit("95")
    self.percentile_max.setPlaceholderText("P high")
    self.apply_range = QPushButton("Apply range")
    self.lock_scale = QCheckBox("Lock scale across views / comparisons")
    self.lock_scale.setChecked(True)
    left_layout.addWidget(self.range_mode)
    range_row = QHBoxLayout()
    range_row.addWidget(self.range_min)
    range_row.addWidget(self.range_max)
    left_layout.addLayout(range_row)
    percentile_row = QHBoxLayout()
    percentile_row.addWidget(self.percentile_min)
    percentile_row.addWidget(self.percentile_max)
    left_layout.addLayout(percentile_row)
    left_layout.addWidget(self.apply_range)
    left_layout.addWidget(self.lock_scale)
    self.display_smoothing = QCheckBox("Display-only anatomical smoothing")
    self.display_smoothing.setChecked(True)
    left_layout.addWidget(self.display_smoothing)
    smoothing = QLabel("Display smoothing: configured presentation surface\nAnalysis smoothing: NONE")
    smoothing.setObjectName("sectionDescription")
    smoothing.setWordWrap(True)
    left_layout.addWidget(smoothing)
    left_layout.addStretch()
    left_scroll.setWidget(left_content)
    outer = QVBoxLayout(left)
    outer.setContentsMargins(4, 4, 4, 4)
    outer.addWidget(left_scroll)
    workspace.addWidget(left)


def _build_map_workspace(self: Any, workspace: Any) -> None:
    centre = QFrame()
    centre.setObjectName("card")
    centre.setMinimumWidth(300)
    centre.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    centre_layout = QVBoxLayout(centre)
    centre_layout.setContentsMargins(2, 2, 2, 2)
    centre_layout.setSpacing(2)
    self.map_help = QLabel("Select a stored biological map.")
    self.map_help.setWordWrap(True)
    self.map_help.setObjectName("sectionDescription")
    self.map_help.setMaximumHeight(38)
    centre_layout.addWidget(self.map_help)
    self.overlay_tabs = QTabBar()
    self.overlay_tabs.setObjectName("viewerOverlayTabs")
    self.overlay_tabs.setExpanding(False)
    self.overlay_tabs.setUsesScrollButtons(True)
    self.overlay_tab_keys = ["dose", "bed", "eqd2", "sf_log", "sf", "effect"]
    for label in ("DOSE", "s-BED", "s-EQD2", "−log₁₀(SF)", "SURVIVING FRACTION", "MLQ EFFECT K"):
        self.overlay_tabs.addTab(label)
    self.overlay_tabs.currentChanged.connect(self._overlay_tab_changed)
    centre_layout.addWidget(self.overlay_tabs)
    _build_linked_navigation(self, centre_layout)
    self.tabs = QTabWidget()
    self.tabs.setTabPosition(QTabWidget.South)
    self.tabs.setDocumentMode(True)
    self.tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    self.tabs.tabBar().setUsesScrollButtons(True)
    self.tabs.tabBar().setElideMode(Qt.ElideRight)
    centre_layout.addWidget(self.tabs, 1)
    _build_unified_viewer_tab(self)
    _build_comparison_tab(self)
    workspace.addWidget(centre)


def _build_linked_navigation(self: Any, layout: QVBoxLayout) -> None:
    navigation = QFrame()
    navigation.setObjectName("linkedNavigation")
    grid = QGridLayout(navigation)
    grid.setContentsMargins(3, 3, 3, 3)
    grid.setHorizontalSpacing(3)
    grid.setVerticalSpacing(0)
    self.navigation_controls: dict[str, QPushButton] = {}
    for column, (key, text, orientation) in enumerate((
        ("perspective", "Perspective", "perspective"),
        ("axial", "Axial", "axial"),
        ("sagittal", "Sagittal", "sagittal"),
        ("coronal", "Coronal", "coronal"),
    )):
        button = QPushButton(text)
        button.setToolTip("Set the CAD camera and focus the corresponding linked slice view.")
        button.clicked.connect(lambda _checked=False, value=orientation: self._set_linked_view(value))
        self.navigation_controls[key] = button
        grid.addWidget(button, 0, column)
    operations = (
        ("zoom_out", "Zoom out", lambda: self._zoom_linked_views(False)),
        ("zoom_in", "Zoom in", lambda: self._zoom_linked_views(True)),
        ("rotate_left", "Rotate left", lambda: self._rotate_linked_views(-15)),
        ("rotate_right", "Rotate right", lambda: self._rotate_linked_views(15)),
        ("fit", "Fit all", self._fit_linked_views),
    )
    for column, (key, text, operation) in enumerate(operations):
        button = QPushButton(text)
        button.setToolTip("Apply the same navigation action to every 2D plane and the CAD view.")
        button.clicked.connect(operation)
        self.navigation_controls[key] = button
        grid.addWidget(button, 0, column + 4)
    self.cad_controls_button = QPushButton("CAD controls…")
    self.cad_controls_button.setObjectName("cadControlsButton")
    self.cad_controls_button.setToolTip("Open optional 3D display, geometry, opacity, and export controls.")
    self.cad_controls_button.clicked.connect(self._show_cad_controls)
    grid.addWidget(self.cad_controls_button, 0, 9)
    self.viewer_guide_button = QPushButton("Viewer guide…")
    self.viewer_guide_button.setToolTip("Explain every Layer 3.1 map, navigation, CAD, and interpretation control")
    self.viewer_guide_button.clicked.connect(lambda: show_viewer_guide(self, "layer3_1"))
    grid.addWidget(self.viewer_guide_button, 0, 10)
    layout.addWidget(navigation)


def _viewport_pane(title: str, widget: QWidget, footer: QWidget | None = None) -> QFrame:
    pane = QFrame()
    pane.setObjectName("viewerPane")
    layout = QVBoxLayout(pane)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    heading = QLabel(title)
    heading.setObjectName("viewerPaneTitle")
    heading.setContentsMargins(8, 3, 8, 3)
    layout.addWidget(heading)
    layout.addWidget(widget, 1)
    if footer is not None:
        layout.addWidget(footer)
    return pane


def _build_unified_viewer_tab(self: Any) -> None:
    unified = QWidget()
    unified.setObjectName("unifiedViewerWorkspace")
    outer = QVBoxLayout(unified)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(2)
    self.cad_show_anatomy = self.show_structures
    self.cad_biology_overlay = QCheckBox("Selected endpoint 3D", self)
    self.cad_biology_overlay.setChecked(True)
    self.cad_biology_overlay.hide()
    self.cad_bed_overlay = QCheckBox("s-BED 3D overlay", self)
    self.cad_bed_overlay.setChecked(True)
    self.cad_bed_overlay.hide()
    self.cad_eqd2_overlay = QCheckBox("s-EQD2 3D overlay", self)
    self.cad_eqd2_overlay.hide()
    self.cad_physical_overlay = QCheckBox("Physical-dose 3D map", self)
    self.cad_physical_overlay.hide()

    four_pane = QWidget()
    four_pane.setObjectName("fourPaneViewport")
    grid = QGridLayout(four_pane)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(2)
    grid.setVerticalSpacing(2)
    self.canvases = {}
    self.sliders = {}
    positions = {"axial": (0, 0), "sagittal": (0, 1), "coronal": (1, 0)}
    titles = {"axial": "TRANSVERSE / AXIAL", "sagittal": "SAGITTAL", "coronal": "CORONAL"}
    for orientation in ("axial", "sagittal", "coronal"):
        canvas = BiologicalSliceCanvas(orientation)
        slider = QSlider(Qt.Horizontal)
        slider.setObjectName("viewerSliceSlider")
        slider.valueChanged.connect(lambda value, view=orientation: self._slice_changed(view, value))
        canvas.voxelSelected.connect(self._voxel_selected)
        canvas.linkedZoomRequested.connect(self._slice_zoom_requested)
        canvas.linkedPanRequested.connect(self._slice_pan_requested)
        self.canvases[orientation] = canvas
        self.sliders[orientation] = slider
        pane = _viewport_pane(titles[orientation], canvas, slider)
        row, column = positions[orientation]
        grid.addWidget(pane, row, column)

    self.scene = PyVistaBiologicalScene3D()
    self.scene.pointPicked.connect(self._cad_point_picked)
    self.scene.linkedZoomRequested.connect(self._cad_zoom_requested)
    self.scene.linkedPanRequested.connect(self._cad_pan_requested)
    self.scene.linkedRotationRequested.connect(self._cad_rotation_requested)
    metric_widget = QWidget()
    metric_widget.setObjectName("cadMetricStrip")
    metric_row = QHBoxLayout(metric_widget)
    metric_row.setContentsMargins(2, 2, 2, 2)
    metric_row.setSpacing(2)
    self.cad_metric_cards: dict[str, QLabel] = {}
    for key, title_text in (("mean", "MEAN"), ("max", "MAX"), ("d95", "D95"), ("min", "MIN")):
        card = QLabel(f"{title_text}  —")
        card.setObjectName("metricCard")
        card.setAlignment(Qt.AlignCenter)
        card.setMinimumHeight(28)
        self.cad_metric_cards[key] = card
        metric_row.addWidget(card, 1)
    grid.addWidget(_viewport_pane("3D BIOLOGICAL / CAD", self.scene, metric_widget), 1, 1)
    grid.setRowStretch(0, 1)
    grid.setRowStretch(1, 1)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    self.cad_splitter = QSplitter(Qt.Horizontal)
    self.cad_splitter.setChildrenCollapsible(False)
    self.cad_splitter.addWidget(four_pane)
    outer.addWidget(self.cad_splitter, 1)
    self.colour_bar = BiologyColorBar()
    self.colour_bar.setMaximumHeight(62)
    outer.addWidget(self.colour_bar)
    self.tabs.addTab(unified, "UNIFIED FOUR-PANE VIEWER")
    _build_cad_controls_dialog(self)


def _build_cad_controls_dialog(self: Any) -> None:
    self.cad_controls_dialog = QDialog(self)
    self.cad_controls_dialog.setWindowTitle("ASCEND — 3D CAD Viewer Controls")
    self.cad_controls_dialog.setModal(False)
    self.cad_controls_dialog.resize(460, 620)
    control_layout = QVBoxLayout(self.cad_controls_dialog)
    control_layout.setContentsMargins(8, 8, 8, 8)
    title = QLabel("3D DISPLAY CONTROLS")
    title.setObjectName("sectionTitle")
    control_layout.addWidget(title)
    self.cad_legend = QLabel(
        "Controls affect the 3D pane while endpoint, colour range, anatomy, crosshair, zoom, pan, rotation, and fit remain synchronised across the four-view workspace."
    )
    self.cad_legend.setObjectName("sectionDescription")
    self.cad_legend.setWordWrap(True)
    control_layout.addWidget(self.cad_legend)
    settings_tabs = QTabWidget()
    self.cad_settings_tabs = settings_tabs
    settings_tabs.setDocumentMode(True)
    display_page = QWidget()
    display_grid = QGridLayout(display_page)
    display_grid.setContentsMargins(8, 6, 8, 6)
    display_grid.addWidget(QLabel("3D mode"), 0, 0)
    self.cad_mode = QComboBox()
    self.cad_mode.addItem("Biological surface map", "SURFACE")
    self.cad_mode.addItem("True biological volume", "VOLUME")
    self.cad_mode.addItem("Biological isosurfaces", "ISOSURFACE")
    self.cad_mode.addItem("Orthogonal biological slices", "SLICE")
    self.cad_mode.addItem("Combined biology", "COMBINED")
    self.cad_mode.setCurrentIndex(self.cad_mode.findData("SLICE"))
    self.cad_mode.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    display_grid.addWidget(self.cad_mode, 0, 1)
    display_grid.addWidget(QLabel("Region focus"), 1, 0)
    self.cad_region = QComboBox()
    self.cad_region.addItem("Whole GTV", "Region: Whole GTV")
    self.cad_region.addItem("Vertices", "Region: Vertices")
    self.cad_region.addItem("Valleys", "Region: Valleys")
    self.cad_region.addItem("Neither", "Region: Other GTV")
    self.cad_region.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    display_grid.addWidget(self.cad_region, 1, 1)
    display_grid.addWidget(QLabel("Tissue parameter"), 2, 0)
    self.cad_overlay_parameter = QComboBox()
    self.cad_overlay_parameter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    display_grid.addWidget(self.cad_overlay_parameter, 2, 1)
    self.show_vertex_centres = QCheckBox("Vertex centres")
    self.show_vertex_centres.setChecked(True)
    self.show_neighbour_graph = QCheckBox("Layer 2.2 graph")
    self.show_neighbour_graph.setChecked(False)
    self.cad_contours = QCheckBox("Biological contour bands")
    display_grid.addWidget(self.show_vertex_centres, 3, 0)
    display_grid.addWidget(self.show_neighbour_graph, 3, 1)
    display_grid.addWidget(self.cad_contours, 4, 0, 1, 2)
    display_grid.setColumnStretch(1, 1)
    settings_tabs.addTab(display_page, "Display")

    geometry_page = QWidget()
    geometry_grid = QGridLayout(geometry_page)
    geometry_grid.setContentsMargins(8, 6, 8, 6)
    geometry_grid.addWidget(QLabel("Cut plane"), 0, 0)
    self.cut_axis = QComboBox()
    self.cut_axis.addItems(["Axial", "Sagittal", "Coronal"])
    geometry_grid.addWidget(self.cut_axis, 0, 1)
    self.cut_offset = QSlider(Qt.Horizontal)
    self.cut_offset.setRange(0, 100)
    self.cut_offset.setValue(50)
    geometry_grid.addWidget(QLabel("Position"), 1, 0)
    geometry_grid.addWidget(self.cut_offset, 1, 1)
    self.cut_invert = QCheckBox("Invert")
    geometry_grid.addWidget(self.cut_invert, 2, 1)
    self.cut_azimuth = QSlider(Qt.Horizontal)
    self.cut_azimuth.setRange(-90, 90)
    self.cut_azimuth.setValue(0)
    self.cut_azimuth.setToolTip("Rotate clipping-plane normal in degrees")
    geometry_grid.addWidget(QLabel("Azimuth"), 3, 0)
    geometry_grid.addWidget(self.cut_azimuth, 3, 1)
    self.cut_elevation = QSlider(Qt.Horizontal)
    self.cut_elevation.setRange(-90, 90)
    self.cut_elevation.setValue(0)
    self.cut_elevation.setToolTip("Tilt clipping-plane normal in degrees")
    geometry_grid.addWidget(QLabel("Elevation"), 4, 0)
    geometry_grid.addWidget(self.cut_elevation, 4, 1)
    self.cut_reset = QPushButton("Reset cut")
    geometry_grid.addWidget(self.cut_reset, 5, 0, 1, 2)
    geometry_grid.addWidget(QLabel("Isosurfaces"), 6, 0)
    self.isosurface_thresholds = QLineEdit("P90")
    self.isosurface_thresholds.setPlaceholderText("e.g. P75,P90 or 60,80 Gy")
    geometry_grid.addWidget(self.isosurface_thresholds, 6, 1)
    self.biological_landscape = QPushButton("Biological Landscape")
    self.biological_landscape.setToolTip("Display preset only; it does not modify Layer 3.1 calculations.")
    geometry_grid.addWidget(self.biological_landscape, 7, 0, 1, 2)
    geometry_grid.setColumnStretch(1, 1)
    settings_tabs.addTab(geometry_page, "Geometry")

    output_page = QWidget()
    output_grid = QGridLayout(output_page)
    output_grid.setContentsMargins(8, 6, 8, 6)
    output_grid.addWidget(QLabel("GTV opacity"), 0, 0)
    self.gtv_opacity = QSlider(Qt.Horizontal)
    self.gtv_opacity.setRange(5, 100)
    self.gtv_opacity.setValue(96)
    output_grid.addWidget(self.gtv_opacity, 0, 1)
    output_grid.addWidget(QLabel("OAR opacity"), 1, 0)
    self.oar_opacity = QSlider(Qt.Horizontal)
    self.oar_opacity.setRange(0, 100)
    self.oar_opacity.setValue(25)
    output_grid.addWidget(self.oar_opacity, 1, 1)
    output_grid.addWidget(QLabel("Biology opacity"), 2, 0)
    self.iso_opacity = QSlider(Qt.Horizontal)
    self.iso_opacity.setRange(5, 100)
    self.iso_opacity.setValue(45)
    output_grid.addWidget(self.iso_opacity, 2, 1)
    self.volume_opacity_preset = QComboBox()
    self.volume_opacity_preset.addItem("Biological effect", "biological_effect")
    self.volume_opacity_preset.addItem("High effect", "high_effect")
    self.volume_opacity_preset.addItem("Linear", "linear")
    self.volume_opacity_preset.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    output_grid.addWidget(QLabel("Volume preset"), 3, 0)
    output_grid.addWidget(self.volume_opacity_preset, 3, 1)
    self.export_button = QPushButton("Export STL + VTP")
    self.export_button.clicked.connect(self._export)
    output_grid.addWidget(self.export_button, 4, 0, 1, 2)
    self.screenshot_button = QPushButton("Export view PNG")
    self.screenshot_button.clicked.connect(self._export_screenshot)
    output_grid.addWidget(self.screenshot_button, 5, 0, 1, 2)
    output_grid.setColumnStretch(1, 1)
    settings_tabs.addTab(output_page, "Output")
    control_layout.addWidget(settings_tabs, 1)
    status_tabs = QTabWidget()
    status_tabs.setDocumentMode(True)
    status_tabs.setMaximumHeight(112)
    self.mesh_status = QLabel("Select a stored map and ROI.")
    self.mesh_status.setWordWrap(True)
    self.mesh_status.setContentsMargins(8, 4, 8, 4)
    status_tabs.addTab(self.mesh_status, "Build")
    self.biological_map_status = QLabel("BIOLOGICAL MAP STATUS\nNOT LOADED")
    self.biological_map_status.setObjectName("sectionDescription")
    self.biological_map_status.setWordWrap(True)
    self.biological_map_status.setContentsMargins(8, 4, 8, 4)
    status_scroll = QScrollArea()
    status_scroll.setWidgetResizable(True)
    status_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    status_scroll.setWidget(self.biological_map_status)
    status_tabs.addTab(status_scroll, "Map")
    cad_help = QLabel(
        "Left-drag rotates, middle-drag pans, and the mouse wheel zooms. The shared toolbar applies navigation to 2D and CAD together."
    )
    cad_help.setObjectName("sectionDescription")
    cad_help.setWordWrap(True)
    cad_help.setContentsMargins(8, 4, 8, 4)
    status_tabs.addTab(cad_help, "Help")
    control_layout.addWidget(status_tabs)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(self.cad_controls_dialog.hide)
    control_layout.addWidget(buttons)


def _build_comparison_tab(self: Any) -> None:
    comparison = QWidget()
    comparison_layout = QHBoxLayout(comparison)
    self.comparison_left = QLabel()
    self.comparison_right = QLabel()
    for widget in (self.comparison_left, self.comparison_right):
        widget.setAlignment(Qt.AlignCenter)
        widget.setWordWrap(True)
        widget.setObjectName("metricCard")
        widget.setMinimumHeight(280)
        comparison_layout.addWidget(widget, 1)
    self.tabs.addTab(comparison, "Comparison")


def _build_result_summary(self: Any, workspace: QVBoxLayout) -> None:
    right = QFrame()
    right.setObjectName("card")
    right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    right_layout = QVBoxLayout(right)
    heading = QLabel("2  WHOLE-TUMOUR RESULT")
    heading.setObjectName("sectionTitle")
    right_layout.addWidget(heading)
    primary_note = QLabel(
        "The major 3.1B outputs are mean tumour surviving fraction and tumour EUD. The map explains their spatial origin."
    )
    primary_note.setObjectName("sectionDescription")
    primary_note.setWordWrap(True)
    right_layout.addWidget(primary_note)
    self.primary_sf = QLabel("MEAN TUMOUR SF\n—")
    self.primary_sf.setObjectName("metricCard")
    self.primary_sf.setAlignment(Qt.AlignCenter)
    self.primary_sf.setMinimumHeight(82)
    self.primary_eud = QLabel("MLQ TUMOUR EUD\n—")
    self.primary_eud.setObjectName("metricCard")
    self.primary_eud.setAlignment(Qt.AlignCenter)
    self.primary_eud.setMinimumHeight(82)
    right_layout.addWidget(self.primary_sf)
    right_layout.addWidget(self.primary_eud)
    map_detail_heading = QLabel("Selected map interpretation")
    map_detail_heading.setObjectName("sectionTitle")
    right_layout.addWidget(map_detail_heading)
    self.summary_title = QLabel("No map loaded")
    self.summary_title.setObjectName("metricTitle")
    self.summary_title.setWordWrap(True)
    right_layout.addWidget(self.summary_title)
    self.summary_equation = QLabel("—")
    self.summary_equation.setWordWrap(True)
    right_layout.addWidget(self.summary_equation)
    self.summary_details = QLabel("—")
    self.summary_details.setObjectName("sectionDescription")
    self.summary_details.setWordWrap(True)
    right_layout.addWidget(self.summary_details)
    voxel_title = QLabel("Voxel under crosshair")
    voxel_title.setObjectName("sectionTitle")
    right_layout.addWidget(voxel_title)
    self.voxel_chain = QLabel("Double-click a 2D view to inspect one voxel.")
    self.voxel_chain.setWordWrap(True)
    self.voxel_chain.setTextInteractionFlags(Qt.TextSelectableByMouse)
    right_layout.addWidget(self.voxel_chain)
    self.warning_summary = QLabel("")
    self.warning_summary.setWordWrap(True)
    self.warning_summary.setObjectName("warningBanner")
    right_layout.addWidget(self.warning_summary)
    right_layout.addStretch()
    workspace.addWidget(right, 1)


def _build_regional_summary(self: Any, layout: QVBoxLayout) -> None:
    regional = QFrame()
    regional.setObjectName("card")
    regional_layout = QVBoxLayout(regional)
    self.regional_title = QLabel("3  REGIONAL EXPLANATION · WHO DRIVES RESIDUAL TUMOUR SURVIVAL?")
    self.regional_title.setObjectName("sectionTitle")
    regional_layout.addWidget(self.regional_title)
    contribution_note = QLabel(
        "Primary regional visual · 100% residual-survival contribution bar. Select a segment to focus the linked vertex, valley, or other-GTV mask in 2D and 3D."
    )
    contribution_note.setObjectName("sectionDescription")
    contribution_note.setWordWrap(True)
    regional_layout.addWidget(contribution_note)
    self.contribution_bar = SurvivalContributionBar()
    self.contribution_bar.selected.connect(self._focus_region)
    regional_layout.addWidget(self.contribution_bar)
    cards = QGridLayout()
    self.regional_cards: dict[str, RegionalResultCard] = {}
    for index, (region, title_text) in enumerate((("H", "VERTICES"), ("V", "VALLEYS"), ("O", "OTHER GTV"))):
        card = RegionalResultCard(region, title_text)
        card.selected.connect(self._focus_region)
        self.regional_cards[region] = card
        cards.addWidget(card, index // 2, index % 2)
    self.whole_tumour_card = QLabel("WHOLE TUMOUR\nMean SF  —\nEUD  —")
    self.whole_tumour_card.setObjectName("metricCard")
    self.whole_tumour_card.setAlignment(Qt.AlignCenter)
    cards.addWidget(self.whole_tumour_card, 1, 1)
    cards.setColumnStretch(0, 1)
    cards.setColumnStretch(1, 1)
    regional_layout.addLayout(cards)
    self.distribution = SurvivalDistributionCanvas()
    regional_layout.addWidget(self.distribution)
    regional_note = QLabel(
        "Click a regional card or contribution segment to focus its validated mask. Smoothing is presentation-only; all metrics use raw stored voxel fields."
    )
    regional_note.setObjectName("sectionDescription")
    regional_note.setWordWrap(True)
    regional_layout.addWidget(regional_note)
    layout.addWidget(regional)


def _connect_viewer_signals(self: Any) -> None:
    self.field.currentIndexChanged.connect(self._selection_changed)
    self.roi.currentIndexChanged.connect(self._selection_changed)
    self.show_structures.toggled.connect(self._refresh_views)
    self.show_warning.toggled.connect(self._refresh_views)
    self.apply_range.clicked.connect(self._apply_range_requested)
    self.range_mode.currentIndexChanged.connect(self._apply_range_requested)
    self.lock_scale.toggled.connect(self._selection_changed)
    self.tabs.currentChanged.connect(self._viewer_tab_changed)
    self.display_smoothing.toggled.connect(self._cad_controls_changed)
    self.cad_show_anatomy.toggled.connect(self._cad_controls_changed)
    self.cad_biology_overlay.toggled.connect(self._cad_controls_changed)
    self.cad_bed_overlay.toggled.connect(lambda checked: self._cad_overlay_toggled("bed", checked))
    self.cad_eqd2_overlay.toggled.connect(lambda checked: self._cad_overlay_toggled("eqd2", checked))
    self.cad_overlay_parameter.currentIndexChanged.connect(self._cad_controls_changed)
    self.cad_physical_overlay.toggled.connect(self._cad_physical_toggled)
    self.cad_mode.currentIndexChanged.connect(self._cad_mode_changed)
    self.cad_region.currentIndexChanged.connect(self._cad_region_changed)
    self.cut_axis.currentIndexChanged.connect(self._cad_controls_changed)
    self.cut_offset.valueChanged.connect(self._cad_controls_changed)
    self.cut_invert.toggled.connect(self._cad_controls_changed)
    self.cut_azimuth.valueChanged.connect(self._cad_controls_changed)
    self.cut_elevation.valueChanged.connect(self._cad_controls_changed)
    self.cut_reset.clicked.connect(self._reset_cut_plane)
    self.isosurface_thresholds.editingFinished.connect(self._cad_controls_changed)
    self.cad_contours.toggled.connect(self._cad_controls_changed)
    self.show_vertex_centres.toggled.connect(self._cad_controls_changed)
    self.show_neighbour_graph.toggled.connect(self._cad_controls_changed)
    self.gtv_opacity.valueChanged.connect(self._cad_opacity_changed)
    self.oar_opacity.valueChanged.connect(self._cad_opacity_changed)
    self.iso_opacity.valueChanged.connect(self._cad_opacity_changed)
    self.gtv_opacity.sliderReleased.connect(self._apply_cad_opacity)
    self.oar_opacity.sliderReleased.connect(self._apply_cad_opacity)
    self.iso_opacity.sliderReleased.connect(self._apply_cad_opacity)
    self.volume_opacity_preset.currentIndexChanged.connect(self._cad_controls_changed)
    self.biological_landscape.clicked.connect(self._apply_landscape_preset)
    self._cad_mode_changed()
