"""Qt widget construction for the Layer 3.1 biological viewer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
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


def build_layer31_viewer_ui(self: Any) -> None:
    """Build the viewer from cohesive, independently readable sections."""
    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    _build_header(self, layout)
    workspace = QHBoxLayout()
    layout.addLayout(workspace, 1)
    _build_analysis_controls(self, workspace)
    _build_map_workspace(self, workspace)
    _build_result_summary(self, workspace)
    _build_regional_summary(self, layout)
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

    self.hierarchy_label = QLabel("1  MAP  →  2  WHOLE-TUMOUR RESULT  →  3  REGIONAL EXPLANATION")
    self.hierarchy_label.setObjectName("sectionTitle")
    self.hierarchy_label.setAlignment(Qt.AlignCenter)
    self.hierarchy_label.setToolTip(
        "Interpret the spatial field first, then the whole-tumour SF/EUD, then the regional survivor-contribution decomposition."
    )
    layout.addWidget(self.hierarchy_label)


def _build_analysis_controls(self: Any, workspace: QHBoxLayout) -> None:
    left = QFrame()
    left.setObjectName("card")
    left.setFixedWidth(255)
    left_layout = QVBoxLayout(left)
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
    workspace.addWidget(left)


def _build_map_workspace(self: Any, workspace: QHBoxLayout) -> None:
    centre = QFrame()
    centre.setObjectName("card")
    centre_layout = QVBoxLayout(centre)
    map_heading = QLabel("1  MAP · PRIMARY SPATIAL OUTPUT")
    map_heading.setObjectName("sectionTitle")
    centre_layout.addWidget(map_heading)
    self.map_help = QLabel("Select a stored biological map.")
    self.map_help.setWordWrap(True)
    self.map_help.setObjectName("sectionDescription")
    centre_layout.addWidget(self.map_help)
    self.tabs = QTabWidget()
    centre_layout.addWidget(self.tabs, 1)
    _build_plane_tab(self)
    _build_spatial_tab(self)
    _build_comparison_tab(self)
    workspace.addWidget(centre, 1)


def _build_plane_tab(self: Any) -> None:
    planes = QWidget()
    grid = QGridLayout(planes)
    self.canvases = {}
    self.sliders = {}
    for column, orientation in enumerate(("axial", "sagittal", "coronal")):
        canvas = BiologicalSliceCanvas(orientation)
        slider = QSlider(Qt.Horizontal)
        slider.valueChanged.connect(lambda value, view=orientation: self._slice_changed(view, value))
        canvas.voxelSelected.connect(self._voxel_selected)
        self.canvases[orientation] = canvas
        self.sliders[orientation] = slider
        tools = QHBoxLayout()
        for label, operation in (
            ("−", lambda target=canvas: target.zoom_by(1 / 1.2)),
            ("+", lambda target=canvas: target.zoom_by(1.2)),
            ("↺", lambda target=canvas: target.rotate_by(-90)),
            ("↻", lambda target=canvas: target.rotate_by(90)),
            ("Fit", canvas.reset_view),
        ):
            button = QPushButton(label)
            button.setToolTip("Zoom, rotate, or reset this plane")
            button.clicked.connect(operation)
            tools.addWidget(button)
        tools.addStretch()
        grid.addWidget(canvas, 0, column)
        grid.addLayout(tools, 1, column)
        grid.addWidget(slider, 2, column)
    plane_help = QLabel("Interaction: mouse wheel zooms; left-drag pans; toolbar buttons rotate in 90° steps or reset to fit.")
    plane_help.setObjectName("sectionDescription")
    grid.addWidget(plane_help, 3, 0, 1, 3)
    self.tabs.addTab(planes, "Linked axial / sagittal / coronal")


def _build_spatial_tab(self: Any) -> None:
    spatial = QWidget()
    spatial_layout = QVBoxLayout(spatial)
    row = QHBoxLayout()
    for label, orientation in (("Perspective", "perspective"), ("Axial", "axial"), ("Sagittal", "sagittal"), ("Coronal", "coronal")):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False, value=orientation: self.scene.set_view(value))
        row.addWidget(button)
    for label, operation in (
        ("Zoom in", lambda: self.scene.zoom_by(0.82)),
        ("Zoom out", lambda: self.scene.zoom_by(1.22)),
        ("Rotate left", lambda: self.scene.rotate_by(-15)),
        ("Rotate right", lambda: self.scene.rotate_by(15)),
    ):
        button = QPushButton(label)
        button.clicked.connect(operation)
        row.addWidget(button)
    row.addStretch()
    self.export_button = QPushButton("Export anatomical STL + scalar VTP")
    self.export_button.clicked.connect(self._export)
    row.addWidget(self.export_button)
    self.screenshot_button = QPushButton("Export view PNG")
    self.screenshot_button.clicked.connect(self._export_screenshot)
    row.addWidget(self.screenshot_button)
    spatial_layout.addLayout(row)
    overlay_row = QHBoxLayout()
    self.cad_show_anatomy = QCheckBox("Anatomical CAD")
    self.cad_show_anatomy.setChecked(True)
    self.cad_biology_overlay = QCheckBox("Selected endpoint 3D")
    self.cad_biology_overlay.setChecked(True)
    self.cad_bed_overlay = QCheckBox("s-BED 3D overlay")
    self.cad_bed_overlay.setChecked(True)
    self.cad_eqd2_overlay = QCheckBox("s-EQD2 3D overlay")
    self.cad_overlay_parameter = QComboBox()
    self.cad_overlay_parameter.setMinimumWidth(220)
    self.show_vertex_centres = QCheckBox("Vertex centres")
    self.show_vertex_centres.setChecked(True)
    self.show_neighbour_graph = QCheckBox("Layer 2.2 graph")
    self.show_neighbour_graph.setChecked(False)
    overlay_row.addWidget(self.cad_show_anatomy)
    overlay_row.addWidget(self.cad_biology_overlay)
    overlay_row.addWidget(self.cad_bed_overlay)
    overlay_row.addWidget(self.cad_eqd2_overlay)
    overlay_row.addWidget(QLabel("Tissue parameter"))
    overlay_row.addWidget(self.cad_overlay_parameter, 1)
    overlay_row.addWidget(self.show_vertex_centres)
    overlay_row.addWidget(self.show_neighbour_graph)
    spatial_layout.addLayout(overlay_row)
    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("3D mode"))
    self.cad_mode = QComboBox()
    self.cad_mode.addItem("Biological surface map", "SURFACE")
    self.cad_mode.addItem("True biological volume", "VOLUME")
    self.cad_mode.addItem("Biological isosurfaces", "ISOSURFACE")
    self.cad_mode.addItem("Orthogonal biological slices", "SLICE")
    self.cad_mode.addItem("Combined biology", "COMBINED")
    self.cad_region = QComboBox()
    self.cad_region.addItem("Whole GTV", "Region: Whole GTV")
    self.cad_region.addItem("Vertices", "Region: Vertices")
    self.cad_region.addItem("Valleys", "Region: Valleys")
    self.cad_region.addItem("Neither", "Region: Other GTV")
    self.cad_physical_overlay = QCheckBox("Physical-dose 3D map")
    mode_row.addWidget(self.cad_mode)
    mode_row.addWidget(QLabel("Region focus"))
    mode_row.addWidget(self.cad_region)
    mode_row.addWidget(self.cad_physical_overlay)
    mode_row.addStretch()
    spatial_layout.addLayout(mode_row)
    advanced_row = QHBoxLayout()
    advanced_row.addWidget(QLabel("Cut plane"))
    self.cut_axis = QComboBox()
    self.cut_axis.addItems(["Axial", "Sagittal", "Coronal"])
    self.cut_offset = QSlider(Qt.Horizontal)
    self.cut_offset.setRange(0, 100)
    self.cut_offset.setValue(50)
    self.cut_offset.setMinimumWidth(160)
    self.cut_invert = QCheckBox("Invert")
    self.cut_azimuth = QSlider(Qt.Horizontal)
    self.cut_azimuth.setRange(-90, 90)
    self.cut_azimuth.setValue(0)
    self.cut_azimuth.setToolTip("Rotate clipping-plane normal in degrees")
    self.cut_elevation = QSlider(Qt.Horizontal)
    self.cut_elevation.setRange(-90, 90)
    self.cut_elevation.setValue(0)
    self.cut_elevation.setToolTip("Tilt clipping-plane normal in degrees")
    self.cut_reset = QPushButton("Reset cut")
    self.isosurface_thresholds = QLineEdit("P90")
    self.isosurface_thresholds.setPlaceholderText("e.g. P75,P90 or 60,80 Gy")
    self.cad_contours = QCheckBox("Biological contour bands")
    self.biological_landscape = QPushButton("Biological Landscape")
    self.biological_landscape.setToolTip("Display preset only; it does not modify Layer 3.1 calculations.")
    advanced_row.addWidget(self.cut_axis)
    advanced_row.addWidget(self.cut_offset)
    advanced_row.addWidget(self.cut_invert)
    advanced_row.addWidget(QLabel("Isosurfaces"))
    advanced_row.addWidget(self.isosurface_thresholds, 1)
    advanced_row.addWidget(self.cad_contours)
    advanced_row.addWidget(self.biological_landscape)
    spatial_layout.addLayout(advanced_row)
    cut_rotation_row = QHBoxLayout()
    cut_rotation_row.addWidget(QLabel("Cut rotation  azimuth"))
    cut_rotation_row.addWidget(self.cut_azimuth, 1)
    cut_rotation_row.addWidget(QLabel("elevation"))
    cut_rotation_row.addWidget(self.cut_elevation, 1)
    cut_rotation_row.addWidget(self.cut_reset)
    spatial_layout.addLayout(cut_rotation_row)
    opacity_row = QHBoxLayout()
    opacity_row.addWidget(QLabel("Opacity  GTV"))
    self.gtv_opacity = QSlider(Qt.Horizontal)
    self.gtv_opacity.setRange(5, 100)
    self.gtv_opacity.setValue(96)
    self.oar_opacity = QSlider(Qt.Horizontal)
    self.oar_opacity.setRange(0, 100)
    self.oar_opacity.setValue(25)
    self.iso_opacity = QSlider(Qt.Horizontal)
    self.iso_opacity.setRange(5, 100)
    self.iso_opacity.setValue(45)
    self.volume_opacity_preset = QComboBox()
    self.volume_opacity_preset.addItem("Biological effect", "biological_effect")
    self.volume_opacity_preset.addItem("High effect", "high_effect")
    self.volume_opacity_preset.addItem("Linear", "linear")
    opacity_row.addWidget(self.gtv_opacity)
    opacity_row.addWidget(QLabel("OAR"))
    opacity_row.addWidget(self.oar_opacity)
    opacity_row.addWidget(QLabel("Biology"))
    opacity_row.addWidget(self.iso_opacity)
    opacity_row.addWidget(self.volume_opacity_preset)
    spatial_layout.addLayout(opacity_row)
    metric_row = QHBoxLayout()
    self.cad_metric_cards: dict[str, QLabel] = {}
    for key, title_text in (("mean", "MEAN"), ("max", "MAX"), ("d95", "D95"), ("min", "MIN")):
        card = QLabel(f"{title_text}\n—")
        card.setObjectName("metricCard")
        card.setAlignment(Qt.AlignCenter)
        card.setMinimumHeight(54)
        self.cad_metric_cards[key] = card
        metric_row.addWidget(card, 1)
    spatial_layout.addLayout(metric_row)
    self.cad_legend = QLabel(
        "Anatomical surfaces use validated Layer 1 masks in DICOM patient LPS. Select s-BED or s-EQD2 to map the stored field onto the smoothed GTV surface."
    )
    self.cad_legend.setObjectName("sectionDescription")
    self.cad_legend.setWordWrap(True)
    spatial_layout.addWidget(self.cad_legend)
    self.colour_bar = BiologyColorBar()
    spatial_layout.addWidget(self.colour_bar)
    # VTK renders off-screen into this Qt widget. This supplies one stable
    # path for oriented volumes, isosurfaces and sampled CAD on every OS.
    self.scene = PyVistaBiologicalScene3D()
    spatial_layout.addWidget(self.scene, 1)
    self.mesh_status = QLabel("Select a stored map and ROI.")
    self.mesh_status.setWordWrap(True)
    spatial_layout.addWidget(self.mesh_status)
    self.biological_map_status = QLabel("BIOLOGICAL MAP STATUS\nNOT LOADED")
    self.biological_map_status.setObjectName("sectionDescription")
    self.biological_map_status.setWordWrap(True)
    spatial_layout.addWidget(self.biological_map_status)
    cad_help = QLabel(
        "Interaction: left-drag rotates; middle-drag pans; mouse wheel zooms. GTV is gold, vertices cyan, valleys violet, configured OARs magenta, and the selected s-BED/s-EQD2 field is shown as ten quantitative surface bands."
    )
    cad_help.setObjectName("sectionDescription")
    spatial_layout.addWidget(cad_help)
    self.tabs.addTab(spatial, "Interactive 3D GTV / structure")


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
    self.tabs.addTab(comparison, "Compare LRT vs LRT+cERT")


def _build_result_summary(self: Any, workspace: QHBoxLayout) -> None:
    right = QFrame()
    right.setObjectName("card")
    right.setFixedWidth(300)
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
    workspace.addWidget(right)


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
    cards = QHBoxLayout()
    self.regional_cards: dict[str, RegionalResultCard] = {}
    for region, title_text in (("H", "VERTICES"), ("V", "VALLEYS"), ("O", "OTHER GTV")):
        card = RegionalResultCard(region, title_text)
        card.selected.connect(self._focus_region)
        self.regional_cards[region] = card
        cards.addWidget(card)
    self.whole_tumour_card = QLabel("WHOLE TUMOUR\nMean SF  —\nEUD  —")
    self.whole_tumour_card.setObjectName("metricCard")
    self.whole_tumour_card.setAlignment(Qt.AlignCenter)
    cards.addWidget(self.whole_tumour_card)
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
    self.volume_opacity_preset.currentIndexChanged.connect(self._cad_controls_changed)
    self.biological_landscape.clicked.connect(self._apply_landscape_preset)
    self.scene.pointPicked.connect(self._cad_point_picked)
    self._cad_mode_changed()
