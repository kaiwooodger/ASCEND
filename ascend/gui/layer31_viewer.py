"""Read-only 2D/3D presentation of stored Layer 3.1 biological fields."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton,
    QSizePolicy, QSlider, QTabWidget, QVBoxLayout, QWidget, QLineEdit,
)

from ascend.gui.biology_pyvista_scene import PyVistaBiologicalScene3D
from ascend.gui.layer31_cad_projector import _build_cad_scene_bundle, build_cad_scene_bundle
from ascend.gui.layer31_field_adapter import prepare_layer31_viewer_data
from ascend.gui.layer31_legacy_renderers import BiologicalScene3D, SoftwareBiologicalScene3D
from ascend.gui.layer31_result_widgets import RegionalResultCard, SurvivalContributionBar, SurvivalDistributionCanvas
from ascend.gui.layer31_slice_renderer import BiologicalSliceCanvas, BiologyColorBar
from ascend.gui.layer31_viewer_models import CADProjectionOptions, CADSceneBundle, Layer31ViewerData
from ascend.layer3.visualization import BiologicalMeshResult
from ascend.layer3.spatial_biology import (
    BiologyColorScaleController, BiologyViewerState,
    voxel_spacing_zyx_mm, voxel_to_world_lps, world_to_voxel_lps,
)
from ascend.visualization.biology.validation import validate_volume


__all__ = [
    "BiologicalScene3D",
    "BiologicalSliceCanvas",
    "BiologyColorBar",
    "CADProjectionOptions",
    "CADSceneBundle",
    "Layer31Viewer",
    "Layer31ViewerData",
    "RegionalResultCard",
    "SoftwareBiologicalScene3D",
    "SurvivalContributionBar",
    "SurvivalDistributionCanvas",
    "_build_cad_scene_bundle",
    "prepare_layer31_viewer_data",
]




















class _MeshSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class _MeshWorker(QRunnable):
    def __init__(self, generation: int, operation: Any) -> None:
        super().__init__(); self.generation = generation; self.operation = operation; self.signals = _MeshSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.generation, self.operation())
        except Exception as exc:
            self.signals.failed.emit(self.generation, str(exc))








class Layer31Viewer(QWidget):
    """Integrated biological-map viewer; it performs display processing only."""
    scenarioRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__(); self.data: Layer31ViewerData | None = None; self.mesh_result: BiologicalMeshResult | None = None
        self.viewer_state = BiologyViewerState()
        self.cad_bundle: CADSceneBundle | None = None
        self._mesh_generation = 0; self._mesh_workers: set[_MeshWorker] = set(); self._thread_pool = QThreadPool.globalInstance()
        self._mesh_cache: dict[tuple[Any, ...], CADSceneBundle] = {}
        self._display_scales: dict[str, tuple[float, float]] = {}
        self._last_mesh_coverage: float | None = None
        self.crosshair: tuple[int, int, int] | None = None
        self._mesh_timer = QTimer(self); self._mesh_timer.setSingleShot(True); self._mesh_timer.setInterval(220); self._mesh_timer.timeout.connect(self._start_mesh_generation)

        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        header = QFrame(); header.setObjectName("card"); header_layout = QHBoxLayout(header)
        header_text = QVBoxLayout(); title = QLabel("Layer 3.1 — Spatial Radiobiological Evaluation"); title.setObjectName("sectionTitle")
        self.context_label = QLabel("Same validated anatomy, camera, masks, and crosshair with switchable biological fields.")
        self.context_label.setObjectName("sectionDescription"); self.context_label.setWordWrap(True)
        header_text.addWidget(title); header_text.addWidget(self.context_label); header_layout.addLayout(header_text, 1)
        self.context_status = QLabel("NOT LOADED"); self.context_status.setObjectName("statusPill"); header_layout.addWidget(self.context_status)
        layout.addWidget(header)

        self.hierarchy_label = QLabel(
            "1  MAP  →  2  WHOLE-TUMOUR RESULT  →  3  REGIONAL EXPLANATION"
        )
        self.hierarchy_label.setObjectName("sectionTitle")
        self.hierarchy_label.setAlignment(Qt.AlignCenter)
        self.hierarchy_label.setToolTip(
            "Interpret the spatial field first, then the whole-tumour SF/EUD, then the regional survivor-contribution decomposition."
        )
        layout.addWidget(self.hierarchy_label)

        workspace = QHBoxLayout(); layout.addLayout(workspace, 1)

        left = QFrame(); left.setObjectName("card"); left.setFixedWidth(255); left_layout = QVBoxLayout(left)
        left_title = QLabel("ANALYSIS CONTROLS"); left_title.setObjectName("sectionTitle"); left_layout.addWidget(left_title)
        left_layout.addWidget(QLabel("Displayed biological quantity"))
        self.field = QComboBox(); self.field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed); left_layout.addWidget(self.field)
        self.quantity_group = QButtonGroup(self); self.quantity_buttons: dict[str, QRadioButton] = {}
        for key, label in (("dose", "Physical dose"), ("bed", "Spatial BED"), ("eqd2", "Spatial EQD2"),
                           ("sf_log", "MLQ −log₁₀(SF)"), ("sf", "MLQ surviving fraction"), ("effect", "MLQ effect K")):
            button = QRadioButton(label); button.setEnabled(False); button.toggled.connect(lambda checked, value=key: self._quantity_button_changed(value, checked))
            self.quantity_group.addButton(button); self.quantity_buttons[key] = button; left_layout.addWidget(button)
        left_layout.addSpacing(8); left_layout.addWidget(QLabel("Tissue / anatomical focus"))
        self.roi = QComboBox(); left_layout.addWidget(self.roi)
        self.show_structures = QCheckBox("Structures"); self.show_structures.setChecked(True)
        self.show_warning = QCheckBox("High-dose LQ warning"); self.show_warning.setChecked(True)
        left_layout.addWidget(self.show_structures); left_layout.addWidget(self.show_warning)
        anatomy_title = QLabel("Anatomy visibility"); anatomy_title.setObjectName("sectionDescription"); left_layout.addWidget(anatomy_title)
        self.anatomy_checks: dict[str, QCheckBox] = {}
        for key, label in (("GTV", "GTV"), ("Vertices", "Vertices"), ("Valleys", "Valleys"), ("OAR", "OARs")):
            checkbox = QCheckBox(label); checkbox.setChecked(key in {"GTV", "Vertices", "Valleys"}); checkbox.toggled.connect(self._anatomy_changed)
            self.anatomy_checks[key] = checkbox; left_layout.addWidget(checkbox)
        left_layout.addSpacing(8); left_layout.addWidget(QLabel("Tumour sensitivity scenario"))
        scenario_row = QHBoxLayout(); self.scenario_buttons: dict[str, QPushButton] = {}
        for scenario in ("C1", "C2", "C3"):
            button = QPushButton(scenario); button.setCheckable(True); button.setEnabled(False)
            button.setToolTip("Select a standard tumour-sensitivity scenario and recalculate through the Layer 3.1 scientific service.")
            button.clicked.connect(lambda _checked=False, value=scenario: self._request_scenario(value))
            self.scenario_buttons[scenario] = button; scenario_row.addWidget(button)
        left_layout.addLayout(scenario_row)
        self.scenario_note = QLabel("Scenario changes are recalculated by the Layer 3.1 scientific service; no model calculation occurs in this viewer.")
        self.scenario_note.setObjectName("sectionDescription"); self.scenario_note.setWordWrap(True); left_layout.addWidget(self.scenario_note)
        left_layout.addSpacing(8); left_layout.addWidget(QLabel("Complete-volume colour scale"))
        self.range_mode = QComboBox(); self.range_mode.addItems(["Robust 2–98 percentile", "Full range", "Manual fixed range", "Percentile"])
        self.range_min = QLineEdit(); self.range_min.setPlaceholderText("minimum")
        self.range_max = QLineEdit(); self.range_max.setPlaceholderText("maximum")
        self.percentile_min = QLineEdit("5"); self.percentile_min.setPlaceholderText("P low")
        self.percentile_max = QLineEdit("95"); self.percentile_max.setPlaceholderText("P high")
        self.apply_range = QPushButton("Apply range")
        self.lock_scale = QCheckBox("Lock scale across views / comparisons"); self.lock_scale.setChecked(True)
        left_layout.addWidget(self.range_mode); range_row = QHBoxLayout(); range_row.addWidget(self.range_min); range_row.addWidget(self.range_max); left_layout.addLayout(range_row)
        percentile_row = QHBoxLayout(); percentile_row.addWidget(self.percentile_min); percentile_row.addWidget(self.percentile_max); left_layout.addLayout(percentile_row); left_layout.addWidget(self.apply_range); left_layout.addWidget(self.lock_scale)
        self.display_smoothing = QCheckBox("Display-only anatomical smoothing"); self.display_smoothing.setChecked(True)
        left_layout.addWidget(self.display_smoothing); smoothing = QLabel("Display smoothing: configured presentation surface\nAnalysis smoothing: NONE")
        smoothing.setObjectName("sectionDescription"); smoothing.setWordWrap(True); left_layout.addWidget(smoothing); left_layout.addStretch()
        workspace.addWidget(left)

        centre = QFrame(); centre.setObjectName("card"); centre_layout = QVBoxLayout(centre)
        map_heading = QLabel("1  MAP · PRIMARY SPATIAL OUTPUT"); map_heading.setObjectName("sectionTitle"); centre_layout.addWidget(map_heading)
        self.map_help = QLabel("Select a stored biological map."); self.map_help.setWordWrap(True); self.map_help.setObjectName("sectionDescription"); centre_layout.addWidget(self.map_help)
        self.tabs = QTabWidget(); centre_layout.addWidget(self.tabs, 1)
        planes = QWidget(); grid = QGridLayout(planes); self.canvases = {}
        self.sliders = {}
        for column, orientation in enumerate(("axial", "sagittal", "coronal")):
            canvas = BiologicalSliceCanvas(orientation); slider = QSlider(Qt.Horizontal)
            slider.valueChanged.connect(lambda value, view=orientation: self._slice_changed(view, value)); canvas.voxelSelected.connect(self._voxel_selected)
            self.canvases[orientation] = canvas; self.sliders[orientation] = slider
            tools = QHBoxLayout()
            for label, operation in (("−", lambda target=canvas: target.zoom_by(1/1.2)), ("+", lambda target=canvas: target.zoom_by(1.2)),
                                     ("↺", lambda target=canvas: target.rotate_by(-90)), ("↻", lambda target=canvas: target.rotate_by(90)),
                                     ("Fit", canvas.reset_view)):
                button = QPushButton(label); button.setToolTip("Zoom, rotate, or reset this plane"); button.clicked.connect(operation); tools.addWidget(button)
            tools.addStretch()
            grid.addWidget(canvas, 0, column); grid.addLayout(tools, 1, column); grid.addWidget(slider, 2, column)
        plane_help = QLabel("Interaction: mouse wheel zooms; left-drag pans; toolbar buttons rotate in 90° steps or reset to fit.")
        plane_help.setObjectName("sectionDescription"); grid.addWidget(plane_help, 3, 0, 1, 3)
        self.tabs.addTab(planes, "Linked axial / sagittal / coronal")
        spatial = QWidget(); spatial_layout = QVBoxLayout(spatial); row = QHBoxLayout()
        for label, orientation in (("Perspective", "perspective"), ("Axial", "axial"), ("Sagittal", "sagittal"), ("Coronal", "coronal")):
            button = QPushButton(label); button.clicked.connect(lambda _checked=False, value=orientation: self.scene.set_view(value)); row.addWidget(button)
        for label, operation in (("Zoom in", lambda: self.scene.zoom_by(0.82)), ("Zoom out", lambda: self.scene.zoom_by(1.22)),
                                 ("Rotate left", lambda: self.scene.rotate_by(-15)), ("Rotate right", lambda: self.scene.rotate_by(15))):
            button = QPushButton(label); button.clicked.connect(operation); row.addWidget(button)
        row.addStretch()
        self.export_button = QPushButton("Export anatomical STL + scalar VTP"); self.export_button.clicked.connect(self._export); row.addWidget(self.export_button)
        self.screenshot_button = QPushButton("Export view PNG"); self.screenshot_button.clicked.connect(self._export_screenshot); row.addWidget(self.screenshot_button)
        spatial_layout.addLayout(row)
        overlay_row = QHBoxLayout()
        self.cad_show_anatomy = QCheckBox("Anatomical CAD"); self.cad_show_anatomy.setChecked(True)
        self.cad_biology_overlay = QCheckBox("Selected endpoint 3D"); self.cad_biology_overlay.setChecked(True)
        self.cad_bed_overlay = QCheckBox("s-BED 3D overlay"); self.cad_bed_overlay.setChecked(True)
        self.cad_eqd2_overlay = QCheckBox("s-EQD2 3D overlay")
        self.cad_overlay_parameter = QComboBox(); self.cad_overlay_parameter.setMinimumWidth(220)
        self.show_vertex_centres = QCheckBox("Vertex centres"); self.show_vertex_centres.setChecked(True)
        self.show_neighbour_graph = QCheckBox("Layer 2.2 graph"); self.show_neighbour_graph.setChecked(False)
        overlay_row.addWidget(self.cad_show_anatomy); overlay_row.addWidget(self.cad_biology_overlay); overlay_row.addWidget(self.cad_bed_overlay)
        overlay_row.addWidget(self.cad_eqd2_overlay); overlay_row.addWidget(QLabel("Tissue parameter"))
        overlay_row.addWidget(self.cad_overlay_parameter, 1); overlay_row.addWidget(self.show_vertex_centres); overlay_row.addWidget(self.show_neighbour_graph)
        spatial_layout.addLayout(overlay_row)
        mode_row = QHBoxLayout(); mode_row.addWidget(QLabel("3D mode"))
        self.cad_mode = QComboBox()
        self.cad_mode.addItem("Biological surface map", "SURFACE")
        self.cad_mode.addItem("True biological volume", "VOLUME")
        self.cad_mode.addItem("Biological isosurfaces", "ISOSURFACE")
        self.cad_mode.addItem("Orthogonal biological slices", "SLICE")
        self.cad_mode.addItem("Combined biology", "COMBINED")
        self.cad_region = QComboBox(); self.cad_region.addItem("Whole GTV", "Region: Whole GTV"); self.cad_region.addItem("Vertices", "Region: Vertices"); self.cad_region.addItem("Valleys", "Region: Valleys"); self.cad_region.addItem("Neither", "Region: Other GTV")
        self.cad_physical_overlay = QCheckBox("Physical-dose 3D map")
        mode_row.addWidget(self.cad_mode); mode_row.addWidget(QLabel("Region focus")); mode_row.addWidget(self.cad_region); mode_row.addWidget(self.cad_physical_overlay); mode_row.addStretch()
        spatial_layout.addLayout(mode_row)
        advanced_row = QHBoxLayout(); advanced_row.addWidget(QLabel("Cut plane"))
        self.cut_axis = QComboBox(); self.cut_axis.addItems(["Axial", "Sagittal", "Coronal"])
        self.cut_offset = QSlider(Qt.Horizontal); self.cut_offset.setRange(0, 100); self.cut_offset.setValue(50); self.cut_offset.setMinimumWidth(160)
        self.cut_invert = QCheckBox("Invert")
        self.cut_azimuth = QSlider(Qt.Horizontal); self.cut_azimuth.setRange(-90, 90); self.cut_azimuth.setValue(0); self.cut_azimuth.setToolTip("Rotate clipping-plane normal in degrees")
        self.cut_elevation = QSlider(Qt.Horizontal); self.cut_elevation.setRange(-90, 90); self.cut_elevation.setValue(0); self.cut_elevation.setToolTip("Tilt clipping-plane normal in degrees")
        self.cut_reset = QPushButton("Reset cut")
        self.isosurface_thresholds = QLineEdit("P90"); self.isosurface_thresholds.setPlaceholderText("e.g. P75,P90 or 60,80 Gy")
        self.cad_contours = QCheckBox("Biological contour bands")
        self.biological_landscape = QPushButton("Biological Landscape"); self.biological_landscape.setToolTip("Display preset only; it does not modify Layer 3.1 calculations.")
        advanced_row.addWidget(self.cut_axis); advanced_row.addWidget(self.cut_offset); advanced_row.addWidget(self.cut_invert)
        advanced_row.addWidget(QLabel("Isosurfaces")); advanced_row.addWidget(self.isosurface_thresholds, 1); advanced_row.addWidget(self.cad_contours); advanced_row.addWidget(self.biological_landscape)
        spatial_layout.addLayout(advanced_row)
        cut_rotation_row = QHBoxLayout(); cut_rotation_row.addWidget(QLabel("Cut rotation  azimuth")); cut_rotation_row.addWidget(self.cut_azimuth, 1)
        cut_rotation_row.addWidget(QLabel("elevation")); cut_rotation_row.addWidget(self.cut_elevation, 1); cut_rotation_row.addWidget(self.cut_reset)
        spatial_layout.addLayout(cut_rotation_row)
        opacity_row = QHBoxLayout(); opacity_row.addWidget(QLabel("Opacity  GTV"))
        self.gtv_opacity = QSlider(Qt.Horizontal); self.gtv_opacity.setRange(5, 100); self.gtv_opacity.setValue(96)
        self.oar_opacity = QSlider(Qt.Horizontal); self.oar_opacity.setRange(0, 100); self.oar_opacity.setValue(25)
        self.iso_opacity = QSlider(Qt.Horizontal); self.iso_opacity.setRange(5, 100); self.iso_opacity.setValue(45)
        self.volume_opacity_preset = QComboBox()
        self.volume_opacity_preset.addItem("Biological effect", "biological_effect")
        self.volume_opacity_preset.addItem("High effect", "high_effect")
        self.volume_opacity_preset.addItem("Linear", "linear")
        opacity_row.addWidget(self.gtv_opacity); opacity_row.addWidget(QLabel("OAR")); opacity_row.addWidget(self.oar_opacity)
        opacity_row.addWidget(QLabel("Biology")); opacity_row.addWidget(self.iso_opacity); opacity_row.addWidget(self.volume_opacity_preset); spatial_layout.addLayout(opacity_row)
        metric_row = QHBoxLayout(); self.cad_metric_cards: dict[str, QLabel] = {}
        for key, title_text in (("mean", "MEAN"), ("max", "MAX"), ("d95", "D95"), ("min", "MIN")):
            card = QLabel(f"{title_text}\n—"); card.setObjectName("metricCard"); card.setAlignment(Qt.AlignCenter); card.setMinimumHeight(54)
            self.cad_metric_cards[key] = card; metric_row.addWidget(card, 1)
        spatial_layout.addLayout(metric_row)
        self.cad_legend = QLabel("Anatomical surfaces use validated Layer 1 masks in DICOM patient LPS. Select s-BED or s-EQD2 to map the stored field onto the smoothed GTV surface.")
        self.cad_legend.setObjectName("sectionDescription"); self.cad_legend.setWordWrap(True); spatial_layout.addWidget(self.cad_legend)
        self.colour_bar = BiologyColorBar(); spatial_layout.addWidget(self.colour_bar)
        # VTK renders off-screen into this Qt widget. This supplies one stable
        # path for oriented volumes, isosurfaces and sampled CAD on every OS.
        self.scene = PyVistaBiologicalScene3D()
        spatial_layout.addWidget(self.scene, 1)
        self.mesh_status = QLabel("Select a stored map and ROI."); self.mesh_status.setWordWrap(True); spatial_layout.addWidget(self.mesh_status)
        self.biological_map_status = QLabel("BIOLOGICAL MAP STATUS\nNOT LOADED")
        self.biological_map_status.setObjectName("sectionDescription"); self.biological_map_status.setWordWrap(True); spatial_layout.addWidget(self.biological_map_status)
        cad_help = QLabel("Interaction: left-drag rotates; middle-drag pans; mouse wheel zooms. GTV is gold, vertices cyan, valleys violet, configured OARs magenta, and the selected s-BED/s-EQD2 field is shown as ten quantitative surface bands.")
        cad_help.setObjectName("sectionDescription"); spatial_layout.addWidget(cad_help)
        self.tabs.addTab(spatial, "Interactive 3D GTV / structure")
        comparison = QWidget(); comparison_layout = QHBoxLayout(comparison)
        self.comparison_left = QLabel(); self.comparison_right = QLabel()
        for widget in (self.comparison_left, self.comparison_right):
            widget.setAlignment(Qt.AlignCenter); widget.setWordWrap(True); widget.setObjectName("metricCard"); widget.setMinimumHeight(280); comparison_layout.addWidget(widget, 1)
        self.tabs.addTab(comparison, "Compare LRT vs LRT+cERT")
        workspace.addWidget(centre, 1)

        right = QFrame(); right.setObjectName("card"); right.setFixedWidth(300); right_layout = QVBoxLayout(right)
        heading = QLabel("2  WHOLE-TUMOUR RESULT"); heading.setObjectName("sectionTitle"); right_layout.addWidget(heading)
        primary_note = QLabel("The major 3.1B outputs are mean tumour surviving fraction and tumour EUD. The map explains their spatial origin.")
        primary_note.setObjectName("sectionDescription"); primary_note.setWordWrap(True); right_layout.addWidget(primary_note)
        self.primary_sf = QLabel("MEAN TUMOUR SF\n—"); self.primary_sf.setObjectName("metricCard"); self.primary_sf.setAlignment(Qt.AlignCenter); self.primary_sf.setMinimumHeight(82)
        self.primary_eud = QLabel("MLQ TUMOUR EUD\n—"); self.primary_eud.setObjectName("metricCard"); self.primary_eud.setAlignment(Qt.AlignCenter); self.primary_eud.setMinimumHeight(82)
        right_layout.addWidget(self.primary_sf); right_layout.addWidget(self.primary_eud)
        map_detail_heading = QLabel("Selected map interpretation"); map_detail_heading.setObjectName("sectionTitle"); right_layout.addWidget(map_detail_heading)
        self.summary_title = QLabel("No map loaded"); self.summary_title.setObjectName("metricTitle"); self.summary_title.setWordWrap(True); right_layout.addWidget(self.summary_title)
        self.summary_equation = QLabel("—"); self.summary_equation.setWordWrap(True); right_layout.addWidget(self.summary_equation)
        self.summary_details = QLabel("—"); self.summary_details.setObjectName("sectionDescription"); self.summary_details.setWordWrap(True); right_layout.addWidget(self.summary_details)
        voxel_title = QLabel("Voxel under crosshair"); voxel_title.setObjectName("sectionTitle"); right_layout.addWidget(voxel_title)
        self.voxel_chain = QLabel("Double-click a 2D view to inspect one voxel."); self.voxel_chain.setWordWrap(True); self.voxel_chain.setTextInteractionFlags(Qt.TextSelectableByMouse); right_layout.addWidget(self.voxel_chain)
        self.warning_summary = QLabel(""); self.warning_summary.setWordWrap(True); self.warning_summary.setObjectName("warningBanner"); right_layout.addWidget(self.warning_summary); right_layout.addStretch()
        workspace.addWidget(right)

        regional = QFrame(); regional.setObjectName("card"); regional_layout = QVBoxLayout(regional)
        self.regional_title = QLabel("3  REGIONAL EXPLANATION · WHO DRIVES RESIDUAL TUMOUR SURVIVAL?")
        self.regional_title.setObjectName("sectionTitle"); regional_layout.addWidget(self.regional_title)
        contribution_note = QLabel("Primary regional visual · 100% residual-survival contribution bar. Select a segment to focus the linked vertex, valley, or other-GTV mask in 2D and 3D.")
        contribution_note.setObjectName("sectionDescription"); contribution_note.setWordWrap(True); regional_layout.addWidget(contribution_note)
        self.contribution_bar = SurvivalContributionBar(); self.contribution_bar.selected.connect(self._focus_region); regional_layout.addWidget(self.contribution_bar)
        cards = QHBoxLayout(); self.regional_cards: dict[str, RegionalResultCard] = {}
        for region, title_text in (("H", "VERTICES"), ("V", "VALLEYS"), ("O", "OTHER GTV")):
            card = RegionalResultCard(region, title_text); card.selected.connect(self._focus_region); self.regional_cards[region] = card; cards.addWidget(card)
        self.whole_tumour_card = QLabel("WHOLE TUMOUR\nMean SF  —\nEUD  —"); self.whole_tumour_card.setObjectName("metricCard"); self.whole_tumour_card.setAlignment(Qt.AlignCenter); cards.addWidget(self.whole_tumour_card)
        regional_layout.addLayout(cards)
        self.distribution = SurvivalDistributionCanvas(); regional_layout.addWidget(self.distribution)
        regional_note = QLabel("Click a regional card or contribution segment to focus its validated mask. Smoothing is presentation-only; all metrics use raw stored voxel fields.")
        regional_note.setObjectName("sectionDescription"); regional_note.setWordWrap(True); regional_layout.addWidget(regional_note)
        layout.addWidget(regional)

        self.field.currentIndexChanged.connect(self._selection_changed); self.roi.currentIndexChanged.connect(self._selection_changed)
        self.show_structures.toggled.connect(self._refresh_views); self.show_warning.toggled.connect(self._refresh_views)
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
        self.gtv_opacity.valueChanged.connect(self._cad_opacity_changed); self.oar_opacity.valueChanged.connect(self._cad_opacity_changed); self.iso_opacity.valueChanged.connect(self._cad_opacity_changed)
        self.volume_opacity_preset.currentIndexChanged.connect(self._cad_controls_changed)
        self.biological_landscape.clicked.connect(self._apply_landscape_preset)
        self.scene.pointPicked.connect(self._cad_point_picked)
        self._cad_mode_changed()

    def set_data(self, data: Layer31ViewerData) -> None:
        self.data = data; self.field.clear(); self.roi.clear(); self._display_scales.clear(); self._last_mesh_coverage = None
        preferred_order = [
            "physical_course_dose_gy", "negative_log10_survival_MLQ", "voxel_survival_MLQ", "course_effect_MLQ",
            *[key for key in data.fields if "BED" in key], *[key for key in data.fields if "EQD2" in key], "LQ_high_dose_warning_mask",
        ]
        seen: set[str] = set()
        for key in preferred_order:
            if key in seen or key not in data.field_metadata: continue
            seen.add(key); item = data.field_metadata[key]; self.field.addItem(f"{item['label']} · {item['units']}", key)
        for key, item in data.field_metadata.items():
            if key not in seen: self.field.addItem(f"{item['label']} · {item['units']}", key)
        for name in data.masks: self.roi.addItem(name, name)
        self._configure_cad_overlays()
        has_oars = any(name.startswith("OAR:") for name in data.masks)
        self.anatomy_checks["OAR"].blockSignals(True)
        self.anatomy_checks["OAR"].setEnabled(has_oars)
        self.anatomy_checks["OAR"].setChecked(has_oars)
        self.anatomy_checks["OAR"].blockSignals(False)
        self.show_vertex_centres.setEnabled(bool(data.vertex_centres_lps_mm))
        self.show_neighbour_graph.setEnabled(bool(data.graph_edges_lps_mm))
        if not data.vertex_centres_lps_mm: self.show_vertex_centres.setChecked(False)
        if not data.graph_edges_lps_mm: self.show_neighbour_graph.setChecked(False)
        shape = next(iter(data.fields.values())).shape
        gtv = data.masks.get("Region: Whole GTV")
        if gtv is not None and np.any(gtv):
            coordinates = np.nonzero(gtv); self.crosshair = tuple(int(round(float(np.mean(axis)))) for axis in coordinates)
        else:
            self.crosshair = tuple(int(value // 2) for value in shape)
        for orientation, axis in (("axial", 0), ("sagittal", 2), ("coronal", 1)):
            self.sliders[orientation].blockSignals(True); self.sliders[orientation].setRange(0, shape[axis]-1); self.sliders[orientation].setValue(shape[axis]//2); self.sliders[orientation].blockSignals(False)
        self._configure_quantity_buttons()
        branch = data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        scenario = str(branch.get("scenario_id") or "")
        for key, button in self.scenario_buttons.items():
            button.setChecked(key == scenario); button.setEnabled(True)
        context = data.result.get("treatment_context") or {}
        history = data.result.get("fraction_history") or {}
        treatment = context.get("treatment_delivery_mode") or context.get("treatment_context") or "Resolved treatment"
        self.context_label.setText(
            f"Treatment: {treatment} · {history.get('number_of_biological_fraction_events', 0)} biological fraction event(s) · "
            "same validated anatomy and navigation across every field."
        )
        self.context_status.setText(str(branch.get("status") or branch.get("calculation_status") or "LOADED").upper())
        self._update_regional_results(); self.distribution.set_data(data); self._update_comparison_state()
        self._selection_changed()

    def _request_scenario(self, scenario: str) -> None:
        if self.data is None: return
        current = str((self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}).get("scenario_id") or "")
        if scenario == current: return
        for button in self.scenario_buttons.values(): button.setEnabled(False)
        self.scenario_note.setText(f"RECALCULATING {scenario} through the Layer 3.1 scientific service…")
        self.scenarioRequested.emit(scenario)

    def _configure_quantity_buttons(self) -> None:
        if self.data is None: return
        mapping = {
            "dose": "physical_course_dose_gy",
            "sf_log": "negative_log10_survival_MLQ",
            "sf": "voxel_survival_MLQ",
            "effect": "course_effect_MLQ",
        }
        mapping["bed"] = next((key for key in self.data.fields if "BED" in key), "")
        mapping["eqd2"] = next((key for key in self.data.fields if "EQD2" in key), "")
        self._quantity_fields = mapping
        for key, button in self.quantity_buttons.items():
            available = bool(mapping.get(key) and mapping[key] in self.data.fields); button.setEnabled(available)
        default_key = "effect" if mapping.get("effect") in self.data.fields else "bed" if mapping.get("bed") in self.data.fields else "dose"
        if mapping.get(default_key) in self.data.fields:
            self.field.setCurrentIndex(max(self.field.findData(mapping[default_key]), 0)); self.quantity_buttons[default_key].setChecked(True)

    def _quantity_button_changed(self, key: str, checked: bool) -> None:
        if not checked or not hasattr(self, "_quantity_fields"): return
        self.cad_biology_overlay.setChecked(True)
        field = self._quantity_fields.get(key); index = self.field.findData(field)
        if index >= 0: self.field.setCurrentIndex(index)

    def _sync_quantity_button(self, field: str) -> None:
        if not hasattr(self, "_quantity_fields"): return
        for key, mapped in self._quantity_fields.items():
            if mapped == field:
                self.quantity_buttons[key].blockSignals(True); self.quantity_buttons[key].setChecked(True); self.quantity_buttons[key].blockSignals(False); break

    def _slice_changed(self, orientation: str, value: int) -> None:
        if self.data is None: return
        current = list(self.crosshair or (0, 0, 0)); axis = {"axial": 0, "coronal": 1, "sagittal": 2}[orientation]
        current[axis] = int(value); self.crosshair = tuple(current); self._refresh_views(); self._update_voxel_chain()

    def _voxel_selected(self, z_index: int, y_index: int, x_index: int) -> None:
        self.crosshair = (z_index, y_index, x_index)
        for orientation, value in (("axial", z_index), ("coronal", y_index), ("sagittal", x_index)):
            self.sliders[orientation].blockSignals(True); self.sliders[orientation].setValue(value); self.sliders[orientation].blockSignals(False)
        self._refresh_views(); self._update_voxel_chain()

    def _anatomy_changed(self, _checked: bool = False) -> None:
        if self.data is None: return
        current = str(self.roi.currentData() or "")
        categories = {
            "GTV": lambda name: "GTV" in name and "Other" not in name,
            "Vertices": lambda name: "VTV_H" in name or "Vertices" in name,
            "Valleys": lambda name: "VTV_L" in name or "Valleys" in name,
            "OAR": lambda name: name.startswith("OAR:"),
        }
        allowed = [name for name in self.data.masks if any(self.anatomy_checks[key].isChecked() and predicate(name) for key, predicate in categories.items())]
        if current not in allowed and allowed:
            index = self.roi.findData(allowed[0]);
            if index >= 0: self.roi.setCurrentIndex(index)
        self.show_structures.setChecked(bool(allowed)); self._refresh_views()
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _configure_cad_overlays(self) -> None:
        """Expose stored BED/EQD2 field pairs without creating GUI calculations."""
        if self.data is None:
            return
        grouped: dict[float, dict[str, str]] = {}
        for field_id, meta in self.data.field_metadata.items():
            alpha_beta = meta.get("alpha_beta_gy")
            if alpha_beta is None:
                continue
            record = grouped.setdefault(float(alpha_beta), {})
            if "EQD2" in field_id:
                record["eqd2"] = field_id
            elif "BED" in field_id:
                record["bed"] = field_id
        tumour_alpha_beta = next((
            float(item["assignment"]["alpha_beta_gy"])
            for item in ((self.data.result.get("layer3_1a_conventional_lq") or {}).get("roi_summaries") or [])
            if (item.get("assignment") or {}).get("canonical_role") in {"GTV", "VTV_H", "VTV_L"}
            and (item.get("assignment") or {}).get("alpha_beta_gy") is not None
        ), None)
        self.cad_overlay_parameter.blockSignals(True); self.cad_overlay_parameter.clear()
        selected_index = 0
        for index, alpha_beta in enumerate(sorted(grouped)):
            record = {**grouped[alpha_beta], "alpha_beta_gy": alpha_beta}
            self.cad_overlay_parameter.addItem(f"α/β {alpha_beta:g} Gy", record)
            if tumour_alpha_beta is not None and np.isclose(alpha_beta, tumour_alpha_beta):
                selected_index = index
        if self.cad_overlay_parameter.count():
            self.cad_overlay_parameter.setCurrentIndex(selected_index)
        self.cad_overlay_parameter.blockSignals(False)
        current = self.cad_overlay_parameter.currentData() or {}
        self.cad_bed_overlay.setEnabled(bool(current.get("bed")))
        self.cad_eqd2_overlay.setEnabled(bool(current.get("eqd2")))
        if not self.cad_bed_overlay.isEnabled():
            self.cad_bed_overlay.setChecked(False)
        if not self.cad_eqd2_overlay.isEnabled():
            self.cad_eqd2_overlay.setChecked(False)
        self._update_cad_metric_cards(self._cad_overlay_field())

    def _cad_overlay_toggled(self, kind: str, checked: bool) -> None:
        """Keep BED and EQD2 overlays individually switchable but non-overlapping."""
        if getattr(self, "_cad_toggle_guard", False):
            return
        self._cad_toggle_guard = True
        try:
            if checked and kind == "bed":
                self.cad_eqd2_overlay.setChecked(False)
            elif checked and kind == "eqd2":
                self.cad_bed_overlay.setChecked(False)
            if checked:
                self.cad_physical_overlay.setChecked(False)
        finally:
            self._cad_toggle_guard = False
        if checked:
            self.cad_biology_overlay.setChecked(True)
            selected = self._cad_overlay_field(); index = self.field.findData(selected)
            if index >= 0: self.field.setCurrentIndex(index)
        elif not self.cad_bed_overlay.isChecked() and not self.cad_eqd2_overlay.isChecked() and not self.cad_physical_overlay.isChecked():
            self.cad_biology_overlay.setChecked(False)
        self._cad_controls_changed()

    def _cad_physical_toggled(self, checked: bool) -> None:
        if getattr(self, "_cad_toggle_guard", False): return
        self._cad_toggle_guard = True
        try:
            if checked:
                self.cad_bed_overlay.setChecked(False); self.cad_eqd2_overlay.setChecked(False)
        finally:
            self._cad_toggle_guard = False
        if checked:
            self.cad_biology_overlay.setChecked(True)
            index = self.field.findData("physical_course_dose_gy")
            if index >= 0: self.field.setCurrentIndex(index)
        elif not self.cad_bed_overlay.isChecked() and not self.cad_eqd2_overlay.isChecked():
            self.cad_biology_overlay.setChecked(False)
        self._cad_controls_changed()

    def _cad_overlay_field(self) -> str | None:
        if not self.cad_biology_overlay.isChecked():
            return None
        if self.data is not None and self.field.currentData() is not None:
            selected = str(self.field.currentData())
            if selected in self.data.biological_volumes:
                return selected
        if self.cad_physical_overlay.isChecked():
            return "physical_course_dose_gy"
        record = self.cad_overlay_parameter.currentData()
        if not isinstance(record, dict):
            return None
        if self.cad_bed_overlay.isChecked():
            return str(record.get("bed")) if record.get("bed") else None
        if self.cad_eqd2_overlay.isChecked():
            return str(record.get("eqd2")) if record.get("eqd2") else None
        return None

    def _cad_mode_changed(self, _index: int = -1) -> None:
        mode = str(self.cad_mode.currentData() or "SURFACE")
        cutaway = mode == "SLICE"; iso = mode in {"ISOSURFACE", "COMBINED"}
        for widget in (self.cut_axis, self.cut_offset, self.cut_invert, self.cut_azimuth, self.cut_elevation, self.cut_reset): widget.setEnabled(cutaway)
        self.isosurface_thresholds.setEnabled(iso)
        self.viewer_state.display_mode = mode
        self._cad_controls_changed()

    def _reset_cut_plane(self) -> None:
        self.cut_offset.setValue(50); self.cut_azimuth.setValue(0); self.cut_elevation.setValue(0); self.cut_invert.setChecked(False)
        self._cad_controls_changed()

    def _cad_region_changed(self, _index: int = -1) -> None:
        name = str(self.cad_region.currentData() or "Region: Whole GTV")
        self.viewer_state.active_region = name
        if self.data is not None:
            index = self.roi.findData(name)
            if index >= 0: self.roi.setCurrentIndex(index)
        if self.cad_bundle is not None:
            self.cad_bundle.selected_region_name = name
            self._set_scene_bundle(self.cad_bundle, name)

    def _cad_opacity_changed(self, _value: int = 0) -> None:
        self.viewer_state.gtv_opacity = self.gtv_opacity.value() / 100.0
        self.viewer_state.oar_opacity = self.oar_opacity.value() / 100.0
        self.viewer_state.isosurface_opacity = self.iso_opacity.value() / 100.0
        if self.cad_bundle is None: return
        self.cad_bundle.gtv_opacity = self.viewer_state.gtv_opacity
        self.cad_bundle.oar_opacity = self.viewer_state.oar_opacity
        self.cad_bundle.isosurface_opacity = self.viewer_state.isosurface_opacity
        self.cad_bundle.volume_opacity = self.viewer_state.isosurface_opacity
        self._set_scene_bundle(self.cad_bundle, str(self.cad_region.currentData() or ""))

    def _apply_landscape_preset(self) -> None:
        self.cad_show_anatomy.setChecked(True); self.anatomy_checks["GTV"].setChecked(True)
        self.anatomy_checks["Vertices"].setChecked(True); self.anatomy_checks["OAR"].setChecked(True)
        self.show_vertex_centres.setChecked(bool(self.data and self.data.vertex_centres_lps_mm))
        self.cad_bed_overlay.setChecked(True); self.cad_mode.setCurrentIndex(self.cad_mode.findData("COMBINED"))
        self.isosurface_thresholds.setText("P50,P75,P90"); self.cad_contours.setChecked(True); self._cad_controls_changed()

    def _resolved_isosurface_thresholds(self) -> tuple[float, ...]:
        if self.data is None: return ()
        field_id = self._cad_overlay_field(); gtv = self.data.masks.get("Region: Whole GTV")
        if not field_id or gtv is None: return ()
        values = np.asarray(self.data.fields[field_id], dtype=float)[np.asarray(gtv, dtype=bool)]
        values = values[np.isfinite(values)]
        if not values.size: return ()
        thresholds: list[float] = []
        for token in self.isosurface_thresholds.text().split(",")[:4]:
            value = token.strip().upper()
            try:
                thresholds.append(float(np.percentile(values, float(value[1:]))) if value.startswith("P") else float(value.split()[0]))
            except ValueError:
                continue
        return tuple(sorted(set(item for item in thresholds if np.isfinite(item))))

    def _cad_point_picked(self, x_lps: float, y_lps: float, z_lps: float) -> None:
        if self.data is None: return
        point = (float(x_lps), float(y_lps), float(z_lps)); indices = world_to_voxel_lps(np.asarray([point]), self.data.geometry)[0]
        if not np.isfinite(indices).all(): return
        shape = np.asarray(next(iter(self.data.fields.values())).shape)
        voxel = np.clip(np.rint(indices).astype(int), 0, shape - 1)
        self.viewer_state.selected_world_position_lps = point
        self.scene.selected_world_position = np.asarray(point)
        if self.cad_bundle: self._set_scene_bundle(self.cad_bundle, str(self.cad_region.currentData() or ""))
        self._voxel_selected(int(voxel[0]), int(voxel[1]), int(voxel[2]))

    def _cad_mask_names(self) -> tuple[str, ...]:
        if self.data is None or not self.cad_show_anatomy.isChecked():
            return ()
        candidates: list[str] = []
        if self.anatomy_checks["GTV"].isChecked(): candidates.append("Region: Whole GTV")
        if self.anatomy_checks["Vertices"].isChecked(): candidates.append("Region: Vertices")
        if self.anatomy_checks["Valleys"].isChecked(): candidates.append("Region: Valleys")
        if self.anatomy_checks["OAR"].isChecked():
            candidates.extend(
                name for name in self.data.masks
                if name.startswith("OAR:") and name.split(":", 1)[-1].strip().upper() not in {"BODY", "EXTERNAL", "BODY-PTV"}
            )
        return tuple(name for name in dict.fromkeys(candidates) if name in self.data.masks and np.asarray(self.data.masks[name]).any())

    def _cad_smoothing(self) -> dict[str, Any]:
        if not self.display_smoothing.isChecked():
            return {"method": "none", "iterations": 0, "lambda": 0.0, "mu": 0.0}
        if self.data is None:
            return {"method": "taubin_non_shrinking", "iterations": 12, "lambda": 0.25, "mu": -0.27}
        return dict((self.data.result.get("visualisation") or {}).get("smoothing") or {
            "method": "taubin_non_shrinking", "iterations": 12, "lambda": 0.25, "mu": -0.27,
        })

    def _cad_controls_changed(self, _value: Any = None) -> None:
        if self.data is None:
            return
        record = self.cad_overlay_parameter.currentData() or {}
        bed_available = bool(record.get("bed")); eqd2_available = bool(record.get("eqd2"))
        self.cad_bed_overlay.setEnabled(bed_available)
        self.cad_eqd2_overlay.setEnabled(eqd2_available)
        # Never retain a checked state for a field that is absent at the newly
        # selected tissue parameter.  Block signals because this method already
        # owns the single required CAD refresh.
        if not bed_available and self.cad_bed_overlay.isChecked():
            self.cad_bed_overlay.blockSignals(True); self.cad_bed_overlay.setChecked(False); self.cad_bed_overlay.blockSignals(False)
        if not eqd2_available and self.cad_eqd2_overlay.isChecked():
            self.cad_eqd2_overlay.blockSignals(True); self.cad_eqd2_overlay.setChecked(False); self.cad_eqd2_overlay.blockSignals(False)
        overlay_field = self._cad_overlay_field()
        self._update_cad_metric_cards(overlay_field)
        if overlay_field:
            index = self.field.findData(overlay_field)
            if index >= 0 and self.field.currentIndex() != index: self.field.setCurrentIndex(index)
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _update_cad_metric_cards(self, field_id: str | None) -> None:
        if self.data is None or not field_id or field_id not in self.data.field_metadata:
            for key, card in self.cad_metric_cards.items(): card.setText(f"{key.upper()}\nNOT STORED")
            return
        meta = self.data.field_metadata[field_id]; alpha_beta = meta.get("alpha_beta_gy")
        kind = "eqd2" if "EQD2" in field_id else "bed" if "BED" in field_id else None
        summary = next((
            item for item in ((self.data.result.get("layer3_1a_conventional_lq") or {}).get("roi_summaries") or [])
            if (item.get("assignment") or {}).get("canonical_role") == "GTV"
            and alpha_beta is not None
            and np.isclose(float((item.get("assignment") or {}).get("alpha_beta_gy", np.nan)), float(alpha_beta))
        ), None)
        for key, card in self.cad_metric_cards.items():
            title_text = key.upper(); metric = f"{kind}_{key}" if kind else ""
            value = (summary.get("metrics") or {}).get(metric) if summary else None
            card.setText(f"{title_text}\n{float(value):.5g} {meta['units']}" if value is not None else f"{title_text}\nNOT STORED")

    def _focus_region(self, region_id: str) -> None:
        names = {"H": "Region: Vertices", "V": "Region: Valleys", "O": "Region: Other GTV"}
        index = self.roi.findData(names.get(region_id, ""))
        if index >= 0:
            self.show_structures.setChecked(True); self.roi.setCurrentIndex(index)

    def _update_regional_results(self) -> None:
        if self.data is None: return
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        records = list((branch.get("regional_survival") or {}).get("records", [])); by_region = {str(item.get("region_id")): item for item in records}
        for region, card in self.regional_cards.items(): card.set_record(by_region.get(region))
        self.contribution_bar.set_records(records)
        mean_sf = branch.get("mean_tumour_survival_fraction"); eud = branch.get("tumour_eud_gy")
        sf_text = f"{float(mean_sf):.5g}" if mean_sf is not None else "—"; eud_text = f"{float(eud):.4g} Gy" if eud is not None else "NOT APPLICABLE"
        self.primary_sf.setText(f"MEAN TUMOUR SF\n{sf_text}"); self.primary_eud.setText(f"MLQ TUMOUR EUD\n{eud_text}")
        self.whole_tumour_card.setText(f"WHOLE TUMOUR\nMean SF  {sf_text}\nEUD  {eud_text}")

    def _update_comparison_state(self) -> None:
        if self.data is None: return
        context = self.data.result.get("treatment_context") or {}; branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        mean_sf = branch.get("mean_tumour_survival_fraction"); eud = branch.get("tumour_eud_gy")
        self.comparison_left.setText(
            "CURRENT VALIDATED COURSE\n\n"
            f"{context.get('treatment_delivery_mode') or context.get('dose_context') or 'Configured treatment'}\n\n"
            f"Mean SF: {float(mean_sf):.5g}\n" if mean_sf is not None else "CURRENT VALIDATED COURSE\n\nMean SF: —\n"
        )
        if eud is not None: self.comparison_left.setText(self.comparison_left.text() + f"EUD: {float(eud):.4g} Gy")
        self.comparison_right.setText(
            "PAIRED COMPARISON COURSE\n\nNOT CONFIGURED\n\n"
            "Direct comparison requires a second hash-verified Layer 3.1 field set on the same validated geometry. ASCEND does not duplicate or infer one course."
        )

    def _refresh_views(self, _value: Any = None) -> None:
        if self.data is None: return
        field, roi = str(self.field.currentData()), str(self.roi.currentData())
        visible = self._visible_mask_names()
        for orientation, canvas in self.canvases.items():
            canvas.set_view(self.data, field, self.sliders[orientation].value(), roi, self.show_structures.isChecked(), self.show_warning.isChecked(), self._scalar_range(), self.crosshair, visible)

    def _visible_mask_names(self) -> list[str]:
        if self.data is None: return []
        selected: list[str] = []
        preferred = {
            "GTV": ["Region: Whole GTV"], "Vertices": ["Region: Vertices"],
            "Valleys": ["Region: Valleys"], "OAR": [name for name in self.data.masks if name.startswith("OAR:")],
        }
        for category, names in preferred.items():
            if self.anatomy_checks[category].isChecked(): selected.extend(name for name in names if name in self.data.masks)
        return selected

    def _scalar_range(self) -> tuple[float, float] | None:
        if self.data is None or self.field.currentData() is None: return None
        field_id = str(self.field.currentData())
        if field_id in self._display_scales:
            return self._display_scales[field_id]
        contract = self.data.spatial_fields.get(field_id)
        mode = self.range_mode.currentIndex(); manual = None; percentiles = (2.0, 98.0)
        if mode == 2:
            try: manual = (float(self.range_min.text()), float(self.range_max.text()))
            except ValueError: return tuple(self.data.field_metadata[field_id]["display_range"])
            controller_mode = "MANUAL"
        elif mode == 1:
            controller_mode = "FULL RANGE"
        elif mode == 3:
            try: percentiles = (float(self.percentile_min.text()), float(self.percentile_max.text()))
            except ValueError: return tuple(self.data.field_metadata[field_id]["display_range"])
            if not 0.0 <= percentiles[0] < percentiles[1] <= 100.0:
                return tuple(self.data.field_metadata[field_id]["display_range"])
            controller_mode = "PERCENTILE"
        else:
            controller_mode = "ROBUST"
        try:
            if contract is not None:
                resolved = BiologyColorScaleController(controller_mode, percentiles).resolve(contract, roi_mask=None, manual=manual)
            else:
                values = np.asarray(self.data.fields[field_id], dtype=float)
                stable_mask = np.isfinite(values)
                tumour = self.data.masks.get("Region: Whole GTV")
                if tumour is not None and np.any(tumour):
                    stable_mask &= np.asarray(tumour, dtype=bool)
                selected = values[stable_mask]
                if not selected.size: raise ValueError("BIOLOGY_FIELD_UNAVAILABLE")
                if controller_mode == "MANUAL":
                    if manual is None or manual[1] <= manual[0]: raise ValueError("INVALID_DISPLAY_RANGE")
                    resolved = manual
                elif controller_mode == "FULL RANGE": resolved = (float(np.min(selected)), float(np.max(selected)))
                else: resolved = tuple(map(float, np.percentile(selected, percentiles)))
            self._display_scales[field_id] = (float(resolved[0]), float(resolved[1]))
            return self._display_scales[field_id]
        except ValueError:
            return tuple(self.data.field_metadata[field_id]["display_range"])

    def _apply_range_requested(self, _value: Any = None) -> None:
        if self.field.currentData() is not None:
            self._display_scales.pop(str(self.field.currentData()), None)
        self._selection_changed()

    def _selection_changed(self, _index: int = -1) -> None:
        if self.data is None or self.field.currentData() is None or self.roi.currentData() is None: return
        self._refresh_views(); field = str(self.field.currentData()); meta = self.data.field_metadata[field]
        self._sync_quantity_button(field)
        low, high = self._scalar_range() or tuple(meta["display_range"])
        actual_range = tuple(meta["display_range"])
        self.colour_bar.set_scale(meta, (float(low), float(high)), (float(actual_range[0]), float(actual_range[1])))
        self.map_help.setText(
            f"{meta['category']}  |  {meta['label']}  |  {meta['equation']}\n"
            f"{meta['interpretation']}  Complete-field colour range: {low:.5g} to {high:.5g} {meta['units']}."
        )
        self.summary_title.setText(str(meta["label"]).upper()); self.summary_equation.setText(f"Equation\n{meta['equation']}")
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}; parameters = branch.get("model_parameters") or {}
        parameter_lines = []
        for key, label, units in (("alpha_beta_gy", "α/β", "Gy"), ("alpha_per_gy", "α", "Gy⁻¹"), ("beta_per_gy2", "β", "Gy⁻²"),
                                  ("delta_per_gy", "δ", "Gy⁻¹"), ("repair_half_time", "Repair half-time", str(parameters.get("time_unit") or ""))):
            if parameters.get(key) is not None: parameter_lines.append(f"{label}: {parameters[key]} {units}".rstrip())
        self.summary_details.setText(
            f"Units: {meta['units']}\nComplete-volume range: {low:.5g} – {high:.5g}\n"
            f"Model: {branch.get('formalism_id') or 'LQ reference'}\nScenario: {branch.get('scenario_id') or 'Not applicable'}\n"
            + "\n".join(parameter_lines) + f"\n\n{meta['interpretation']}"
        )
        warnings = list(branch.get("warnings", [])); self.warning_summary.setText("WARNINGS\n" + "\n".join(warnings) if warnings else "No stored model warning for this view.")
        self._update_biological_map_status(field)
        self._update_voxel_chain()
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _update_voxel_chain(self) -> None:
        if self.data is None or self.crosshair is None or self.field.currentData() is None: return
        z_index, y_index, x_index = self.crosshair; point = (z_index, y_index, x_index)
        field = str(self.field.currentData()); displayed = float(self.data.fields[field][point])
        physical = self.data.fields.get("physical_course_dose_gy"); survival = self.data.fields.get("voxel_survival_MLQ")
        log_survival = self.data.fields.get("negative_log10_survival_MLQ")
        effect = self.data.fields.get("course_effect_MLQ")
        region = next((name.replace("Region: ", "") for name in ("Region: Vertices", "Region: Valleys", "Region: Other GTV", "Region: Whole GTV") if name in self.data.masks and self.data.masks[name][point]), "Outside selected tumour regions")
        lps = voxel_to_world_lps(np.asarray([[z_index, y_index, x_index]], dtype=float), self.data.geometry)[0]
        lines = [
            f"Position LPS: x {lps[0]:.3f}, y {lps[1]:.3f}, z {lps[2]:.3f} mm",
            f"Grid index: z {z_index}, y {y_index}, x {x_index}",
            f"Displayed: {displayed:.6g} {self.data.field_metadata[field]['units']}",
        ]
        if physical is not None: lines.append(f"Physical course dose: {float(physical[point]):.6g} Gy")
        alpha_beta = self.data.field_metadata[field].get("alpha_beta_gy")
        if alpha_beta is not None:
            bed_id = next((key for key, meta in self.data.field_metadata.items() if "BED" in key and meta.get("alpha_beta_gy") is not None and np.isclose(float(meta["alpha_beta_gy"]), float(alpha_beta))), None)
            eqd2_id = next((key for key, meta in self.data.field_metadata.items() if "EQD2" in key and meta.get("alpha_beta_gy") is not None and np.isclose(float(meta["alpha_beta_gy"]), float(alpha_beta))), None)
            if bed_id: lines.append(f"s-BED: {float(self.data.fields[bed_id][point]):.6g} {self.data.field_metadata[bed_id]['units']}")
            if eqd2_id: lines.append(f"s-EQD2: {float(self.data.fields[eqd2_id][point]):.6g} {self.data.field_metadata[eqd2_id]['units']}")
            lines.append(f"Tissue parameter: α/β {float(alpha_beta):g} Gy")
        if survival is not None:
            lines.append(f"MLQ SF: {float(survival[point]):.6g}")
        if log_survival is not None:
            lines.append(f"−log₁₀(SF): {float(log_survival[point]):.6g}")
        if effect is not None: lines.append(f"MLQ K: {float(effect[point]):.6g}")
        lines.append(f"Region: {region}")
        contract = self.data.spatial_fields.get(field)
        if contract and contract.treatment_components:
            component_names = [str(item.get("component_id") or item.get("component_type") or "component") for item in contract.treatment_components]
            lines.append("Treatment components: " + ", ".join(component_names))
        self.voxel_chain.setText("\n".join(lines))

    def _viewer_tab_changed(self, index: int) -> None:
        if index == 1 and self.data is not None:
            self._mesh_timer.start(10)

    def _mesh_key(self) -> tuple[Any, ...]:
        smoothing = self._cad_smoothing()
        return (
            self._cad_mask_names(), self._cad_overlay_field(), self._scalar_range(),
            str(self.cad_region.currentData() or "Region: Whole GTV"),
            str(self.cad_mode.currentData() or "SURFACE"), self.cut_axis.currentText().lower(),
            self.cut_offset.value(), self.cut_invert.isChecked(), self.cut_azimuth.value(), self.cut_elevation.value(),
            self._resolved_isosurface_thresholds(), self.show_vertex_centres.isChecked(), self.show_neighbour_graph.isChecked(),
            self.cad_contours.isChecked(), str(self.volume_opacity_preset.currentData() or "biological_effect"),
            (
                str(smoothing.get("method", "none")), int(smoothing.get("iterations", 0)),
                float(smoothing.get("lambda", 0.0)), float(smoothing.get("mu", 0.0)),
            ),
        )

    def _start_mesh_generation(self) -> None:
        if self.data is None:
            return
        anatomy_names = self._cad_mask_names(); overlay_field = self._cad_overlay_field()
        if not anatomy_names and not overlay_field:
            self.scene.clear(); self.mesh_status.setText("CAD display is off. Enable Anatomical CAD, s-BED overlay, or s-EQD2 overlay.")
            self.export_button.setEnabled(False); return
        key = self._mesh_key()
        if key in self._mesh_cache:
            self._apply_mesh_result(self._mesh_cache[key], cached=True); return
        settings = self._cad_smoothing(); scalar_range = self._scalar_range(); data = self.data
        mode = str(self.cad_mode.currentData() or "SURFACE"); cut_axis = self.cut_axis.currentText().lower()
        cut_fraction = self.cut_offset.value() / 100.0; cut_inverted = self.cut_invert.isChecked()
        cut_azimuth = float(self.cut_azimuth.value()); cut_elevation = float(self.cut_elevation.value())
        thresholds = self._resolved_isosurface_thresholds()
        opacity_preset = str(self.volume_opacity_preset.currentData() or "biological_effect")
        show_contours = self.cad_contours.isChecked(); gtv_opacity = self.gtv_opacity.value() / 100.0
        oar_opacity = self.oar_opacity.value() / 100.0; iso_opacity = self.iso_opacity.value() / 100.0
        self._mesh_generation += 1; generation = self._mesh_generation
        self.mesh_status.setText("BUILDING — validated anatomical surfaces and biological overlay are generated outside the GUI thread."); self.export_button.setEnabled(False)
        projection_options = CADProjectionOptions(
            smoothing=settings,
            scalar_range=scalar_range,
            display_mode=mode,
            cut_axis=cut_axis,
            cut_fraction=cut_fraction,
            cut_inverted=cut_inverted,
            cut_azimuth_degrees=cut_azimuth,
            cut_elevation_degrees=cut_elevation,
            isosurface_thresholds=thresholds,
            show_contours=show_contours,
            gtv_opacity=gtv_opacity,
            oar_opacity=oar_opacity,
            isosurface_opacity=iso_opacity,
            show_vertex_centres=self.show_vertex_centres.isChecked(),
            show_graph=self.show_neighbour_graph.isChecked(),
            selected_region_name=str(self.cad_region.currentData() or "Region: Whole GTV"),
            volume_opacity_preset=opacity_preset,
        )
        worker = _MeshWorker(
            generation,
            lambda: build_cad_scene_bundle(data, anatomy_names, overlay_field, projection_options),
        )
        worker.cache_key = key
        self._mesh_workers.add(worker)
        worker.signals.finished.connect(self._mesh_finished); worker.signals.failed.connect(self._mesh_failed)
        self._thread_pool.start(worker)

    def _mesh_finished(self, generation: int, result: CADSceneBundle) -> None:
        self._mesh_workers = {item for item in self._mesh_workers if item.generation != generation}
        if generation != self._mesh_generation: return
        self._mesh_cache[self._mesh_key()] = result
        self._apply_mesh_result(result, cached=False)

    def _set_scene_bundle(self, bundle: CADSceneBundle, focused_name: str) -> bool:
        try:
            self.scene.set_bundle(bundle, focused_name)
            return True
        except Exception as exc:
            self.scene.clear(); self.export_button.setEnabled(False)
            self.mesh_status.setText(f"FAILED — BIOLOGICAL_RENDERER_INITIALISATION_FAILED: {exc}")
            return False

    def _apply_mesh_result(self, result: CADSceneBundle, *, cached: bool) -> None:
        self.cad_bundle = result
        available = bool(result.anatomy_meshes or result.overlay_mesh or result.special_meshes or result.biological_volume)
        self.mesh_result = result.overlay_mesh or next(iter(result.special_meshes.values()), None) or next(iter(result.anatomy_meshes.values()), None)
        self.export_button.setEnabled(available)
        if not available:
            self.scene.clear(); self.mesh_status.setText("3D visualisation unavailable: no selected anatomical or biological surface passed display QC. Numerical results remain valid.")
            return
        if not self._set_scene_bundle(result, str(self.cad_region.currentData() or "")):
            return
        vertex_count = sum(
            len(item.display_surface.vertices_lps_mm) for item in result.anatomy_meshes.values() if item.display_surface is not None
        )
        if result.overlay_mesh and result.overlay_mesh.display_surface is not None:
            vertex_count += len(result.overlay_mesh.display_surface.vertices_lps_mm)
        vertex_count += sum(len(item.display_surface.vertices_lps_mm) for item in result.special_meshes.values() if item.display_surface is not None)
        overlay = result.overlay_label or "OFF"
        failures = f" · {len(result.failures)} unavailable surface(s)" if result.failures else ""
        self.mesh_status.setText(
            f"{'CACHED' if cached else 'COMPLETED'} · {len(result.anatomy_meshes)} anatomical surface(s) · "
            f"mode {result.mode} · overlay {overlay} · {vertex_count} displayed vertices · DICOM patient LPS · "
            f"display smoothing {'ON' if result.smoothing_enabled else 'OFF'}{failures} · scientific voxel fields unchanged."
        )
        scalar_mesh = result.overlay_mesh or next(iter(result.special_meshes.values()), None)
        if scalar_mesh and scalar_mesh.display_surface is not None:
            values = np.asarray(scalar_mesh.display_surface.scalar_values, dtype=float)
            finite = values[np.isfinite(values)]
            value_range = f"{float(finite.min()):.4g}–{float(finite.max()):.4g} {result.overlay_units}" if finite.size else "no valid surface samples"
            qc = scalar_mesh.qc; coverage = float(qc.get("mesh_coverage_percent", qc.get("scalar_sampling_coverage_percent", 0.0)))
            self._last_mesh_coverage = coverage
            alignment = str(qc.get("mesh_alignment_status") or ("GREEN" if coverage >= 99.0 else "AMBER"))
            median = qc.get("median_sampling_distance_mm"); maximum = qc.get("maximum_sampling_distance_mm")
            distance_text = f"median {float(median):.3g} mm · max {float(maximum):.3g} mm" if median is not None and maximum is not None else "sampling distance unavailable"
            self.cad_legend.setText(
                f"{result.mode.title()} · {result.overlay_label} · surface range {value_range} · ten fixed scalar bands. "
                f"FIELD VALIDATED | MESH {alignment} | COVERAGE {coverage:.2f}% | {distance_text} | PATIENT LPS | mm. "
                "Invalid samples are NaN, never zero. Smoothing affects display vertices only."
            )
            if result.overlay_field_id in self.data.field_metadata:
                meta = self.data.field_metadata[result.overlay_field_id]
                actual = tuple(meta["display_range"]); display = self._scalar_range() or actual
                self.colour_bar.set_scale(meta, display, actual)
        elif result.biological_volume is not None and result.overlay_field_id:
            report = validate_volume(result.biological_volume)
            self.cad_legend.setText(
                f"{result.mode.title()} · {result.overlay_label} · true volume range "
                f"{report.diagnostics['true_minimum']:.4g}–{report.diagnostics['true_maximum']:.4g} {result.overlay_units}. "
                f"Grid {result.biological_volume.geometry.dimensions_xyz} · spacing "
                f"{tuple(np.round(result.biological_volume.geometry.spacing_mm, 4))} mm · DICOM patient LPS. "
                "Masked voxels are unavailable, never zero; percentage isosurface thresholds are visualisation-only."
            )
        else:
            if result.overlay_field_id:
                reason = "; ".join(f"{key}: {value}" for key, value in result.failures.items()) or "BIOLOGY_FIELD_UNAVAILABLE"
                self.mesh_status.setText(f"BLOCKED — requested biological map was not rendered: {reason}. Neutral anatomy only; no fallback field was substituted.")
                self.cad_legend.setText("BIOLOGICAL MAP BLOCKED | INVALID VALUES REMAIN NaN | no fallback to physical dose, BED, EQD2, nearest-neighbour, or zero.")
            else:
                self.cad_legend.setText("Biological overlay OFF · showing smoothed validated anatomical masks only. GTV gold, vertices cyan, valleys violet, configured OARs magenta.")
        if result.overlay_field_id:
            self._update_biological_map_status(result.overlay_field_id)

    def _update_biological_map_status(self, field_id: str) -> None:
        if self.data is None or field_id not in self.data.field_metadata: return
        meta = self.data.field_metadata[field_id]; volume = self.data.biological_volumes.get(field_id)
        shape = np.asarray(self.data.fields[field_id]).shape
        valid = int(np.count_nonzero(np.isfinite(self.data.fields[field_id])))
        vertex = int(np.count_nonzero(self.data.masks.get("Region: Vertices", np.zeros(shape, bool))))
        valley = int(np.count_nonzero(self.data.masks.get("Region: Valleys", np.zeros(shape, bool))))
        if volume is not None:
            spacing = tuple(map(float, volume.geometry.spacing_mm))
            components = ", ".join(volume.treatment_components) or "Not declared"
            model = str(volume.metadata.get("model_name") or meta.get("category") or "Stored Layer 3.1 model")
        else:
            spacing = tuple(map(float, voxel_spacing_zyx_mm(self.data.geometry)[::-1]))
            components = "Not declared"; model = str(meta.get("category") or "Stored Layer 3.1 model")
        coverage = f"{self._last_mesh_coverage:.2f}%" if self._last_mesh_coverage is not None else "N/A"
        self.biological_map_status.setText(
            "BIOLOGICAL MAP STATUS\n"
            f"Endpoint: {meta.get('label')}\nModel: {model}\nTissue: {self.roi.currentData() or 'N/A'}\n"
            f"Treatment components: {components}\nGrid z,y,x: {shape}\nSpacing x,y,z: {spacing} mm\n"
            f"Valid voxels: {valid}\nVertex voxels: {vertex}\nValley voxels: {valley}\n"
            f"Mesh sample validity: {coverage}\nRendering: PASS"
        )

    def _mesh_failed(self, generation: int, message: str) -> None:
        self._mesh_workers = {item for item in self._mesh_workers if item.generation != generation}
        if generation != self._mesh_generation: return
        self.scene.clear(); self.mesh_result = None; self.cad_bundle = None; self.export_button.setEnabled(False)
        self.mesh_status.setText(f"FAILED — 3D visualisation unavailable: {message}. Numerical results remain valid.")

    def _export(self) -> None:
        bundle = getattr(self, "cad_bundle", None)
        if bundle is None or not (bundle.anatomy_meshes or bundle.overlay_mesh or bundle.special_meshes): return
        folder = QFileDialog.getExistingDirectory(self, "Export Layer 3.1 biological surface")
        if not folder: return
        target = Path(folder); copied = []
        groups: list[tuple[str, BiologicalMeshResult]] = [
            (f"anatomy_{name}", mesh) for name, mesh in bundle.anatomy_meshes.items()
        ]
        if bundle.overlay_mesh is not None:
            groups.append((f"overlay_{bundle.overlay_field_id}", bundle.overlay_mesh))
        groups.extend((f"special_{label}", mesh) for label, mesh in bundle.special_meshes.items())
        for label, mesh in groups:
            safe = "".join(character if character.isalnum() else "_" for character in label).strip("_")
            for key in ("raw_stl", "stl", "vtp", "metadata"):
                source = mesh.artifacts.get(key)
                if source:
                    destination = target / f"layer31_{safe}_{key}{Path(source).suffix}"
                    shutil.copy2(source, destination); copied.append(destination.name)
        QMessageBox.information(self, "ASCEND Layer 3.1 export", f"Exported {len(copied)} files.")

    def _export_screenshot(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export Spatial Biology Viewer", "layer31_spatial_biology.png", "PNG image (*.png)")
        if not path: return
        native_window = getattr(self.scene, "window", None)
        if callable(native_window):
            native_window = None
        screen = native_window.screen() if native_window is not None else None
        image = screen.grabWindow(int(native_window.winId())) if screen is not None else self.scene.grab()
        if image.isNull() or not image.save(path, "PNG"):
            QMessageBox.warning(self, "ASCEND Layer 3.1 export", "PNG_EXPORT_FAILED")
            return
        QMessageBox.information(self, "ASCEND Layer 3.1 export", "Exported the current quantitative 3D view as PNG.")
