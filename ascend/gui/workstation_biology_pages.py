"""Biological-analysis page construction for the ASCEND workstation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ascend.gui.theme import StatusPill, WarningBanner
from ascend.gui.layer31_result_widgets import SurvivalContributionBar
from ascend.gui.viewer_guidance import show_viewer_guide
from ascend.gui.workstation_widgets import table as _table
from ascend.gui.workstation_widgets import text_view as _text_view
from ascend.layer3.response.mlq import (
    NORMAL_KINETIC_PRESETS,
    NORMAL_SCENARIOS,
    SCENARIO_SOURCE,
    TUMOUR_KINETIC_PRESETS,
    TUMOUR_SCENARIOS,
)
from ascend.models.config import CaseConfiguration


class WorkstationBiologyPagesMixin:
    """Build Layer 3 biological-analysis pages."""

    def _build_layer32_page(self) -> None:
        """Build structured controls and read-only views for stored Layer 3.2 evidence."""
        _, layout = self._new_page(
            "Layer 3.2 — Non-local biological reinterpretation",
            "Research-only downstream reinterpretation using current Layer 1, Layer 2.2, and Layer 3.1 evidence.",
        )
        self.layer32_research_banner = WarningBanner("RESEARCH MODEL · NOT CLINICALLY CALIBRATED · NOT A TOXICITY PREDICTION")
        layout.addWidget(self.layer32_research_banner)
        enable_row = QHBoxLayout()
        self.layer32_enabled = QCheckBox("Enable Layer 3.2 non-local research model")
        self.layer32_enabled.setObjectName("layer32EnableToggle")
        self.layer32_enabled.setToolTip("Layer 3.2 is excluded from calculation, result presentation, and export until explicitly enabled.")
        self.layer32_enabled.toggled.connect(self._layer32_enabled_changed)
        enable_detail = QLabel("Optional downstream research analysis. Disabled by default.")
        enable_detail.setObjectName("sectionDescription")
        enable_row.addWidget(self.layer32_enabled)
        enable_row.addWidget(enable_detail, 1)
        layout.addLayout(enable_row)
        status = QHBoxLayout()
        self.layer32_run_button = QPushButton("Run Layer 3.2")
        self.layer32_run_button.setObjectName("primary")
        self.layer32_run_button.clicked.connect(self._run_layer32)
        self.layer32_viewer_button = QPushButton("Build / refresh 3D biological field viewer")
        self.layer32_viewer_button.clicked.connect(self._build_layer32_visualization)
        self.layer32_guide_button = QPushButton("Interactive viewer guide…")
        self.layer32_guide_button.clicked.connect(lambda: show_viewer_guide(self, "layer3_2"))
        self.layer32_status_pill = StatusPill("NOT RUN")
        self.layer32_interpretation_pill = StatusPill("NOT RUN")
        self.layer32_status_text = QLabel("Requires current Layer 1, Layer 2.2, and Layer 3.1 results.")
        self.layer32_status_text.setObjectName("sectionDescription")
        status.addWidget(self.layer32_run_button)
        status.addWidget(self.layer32_viewer_button)
        status.addWidget(self.layer32_guide_button)
        status.addWidget(self.layer32_status_pill)
        status.addWidget(self.layer32_interpretation_pill)
        status.addWidget(self.layer32_status_text, 1)
        layout.addLayout(status)
        self.layer32_warnings = WarningBanner("No Layer 3.2 warnings recorded.")
        layout.addWidget(self.layer32_warnings)

        parameters_card, parameters_layout = self._card(
            "Model parameters",
            "Reference parameters are structured controls. Vascular geometry and vascular uptake are absent from this layer; the PDE has no sink term.",
        )
        exposure_definition = QLabel(
            "Cumulative non-local mediator exposure, H: Time-integrated weighted exposure to the modelled "
            "ROS-like and cytokine-like fields. Higher values indicate stronger accumulated modelled signalling. "
            "Dimensionless; not physical dose, measured concentration, toxicity probability or clinical risk."
        )
        exposure_definition.setObjectName("sectionDescription")
        exposure_definition.setWordWrap(True)
        parameters_layout.addWidget(exposure_definition)
        parameter_row = QHBoxLayout()
        self.layer32_preset = QComboBox()
        self.layer32_preset.addItem("SFRT-MODEL1 reference · no vascular uptake", "sfrt_model1_no_uptake")
        self.layer32_scaling = QLineEdit()
        self.layer32_scaling.setPlaceholderText("Non-local scaling")
        self.layer32_steps = QLineEdit()
        self.layer32_steps.setPlaceholderText("PDE steps")
        self.layer32_dt = QLineEdit()
        self.layer32_dt.setPlaceholderText("PDE dt")
        self.layer32_grid_spacing = QLineEdit()
        self.layer32_grid_spacing.setPlaceholderText("Model grid mm")
        self.layer32_margin = QLineEdit()
        self.layer32_margin.setPlaceholderText("GTV margin mm")
        for label, widget in (
            ("Preset", self.layer32_preset),
            ("Scaling", self.layer32_scaling),
            ("Steps", self.layer32_steps),
            ("dt", self.layer32_dt),
            ("Grid", self.layer32_grid_spacing),
            ("Domain", self.layer32_margin),
        ):
            parameter_row.addWidget(QLabel(label))
            parameter_row.addWidget(widget)
        parameters_layout.addLayout(parameter_row)
        self.layer32_parameter_table = _table(["Parameter", "Value", "Units", "Source"])
        self.layer32_parameter_table.setMaximumHeight(210)
        parameters_layout.addWidget(self.layer32_parameter_table)
        self.layer32_configuration_summary = _table(["Current configuration", "Value", "State / units"])
        self.layer32_configuration_summary.setMaximumHeight(235)
        parameters_layout.addWidget(self.layer32_configuration_summary)
        self.layer32_scenario_table = _table(["Comparison scenario", "Status", "Meaning"])
        self.layer32_scenario_table.setMaximumHeight(160)
        parameters_layout.addWidget(self.layer32_scenario_table)
        layout.addWidget(parameters_card)

        summary_card, summary_layout = self._card(
            "Biological graph reinterpretation",
            "iPVDR shift is signed. A negative shift indicates biological contrast compression; ASCEND does not assume an uplift.",
        )
        self.layer32_graph_summary = _table(["Metric", "Value", "Meaning"])
        self.layer32_edge_table = _table(
            [
                "Edge",
                "Nodes",
                "Physical iPVDR",
                "Baseline LQ iPVDR",
                "Biological iPVDR",
                "Biological shift",
                "Non-local-only shift",
                "Valley effect shift",
            ]
        )
        summary_layout.addWidget(self.layer32_graph_summary)
        summary_layout.addWidget(self.layer32_edge_table)
        layout.addWidget(summary_card)

        self.layer32_context_tabs = QTabWidget()
        self.layer32_context_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.layer32_context_tabs.setMaximumHeight(260)
        gtv_page = QWidget()
        gtv_layout = QVBoxLayout(gtv_page)
        self.layer32_gtv_table = _table(["Field", "Mean", "D95", "D50", "D2", "Units"])
        gtv_layout.addWidget(self.layer32_gtv_table)
        spill_page = QWidget()
        spill_layout = QVBoxLayout(spill_page)
        self.layer32_shell_table = _table(
            ["Shell (mm)", "Voxels", "Physical mean", "Biological mean", "Additional effect mean", "Survival mean"]
        )
        spill_layout.addWidget(self.layer32_shell_table)
        oar_page = QWidget()
        oar_layout = QVBoxLayout(oar_page)
        self.layer32_oar_table = _table(
            [
                "OAR",
                "Classification",
                "Nearest vertex",
                "Distance (mm)",
                "Physical mean",
                "Biological mean",
                "Biological D2",
                "Additional effect mean",
                "Compliance",
            ]
        )
        oar_layout.addWidget(self.layer32_oar_table)
        assay_page = QWidget()
        assay_layout = QVBoxLayout(assay_page)
        self.layer32_assay_table = _table(["Observable", "Mean", "Maximum", "Units", "Scope"])
        assay_layout.addWidget(self.layer32_assay_table)
        regional_page = QWidget()
        regional_layout = QVBoxLayout(regional_page)
        regional_title = QLabel("Modelled regional exposure and consequence")
        regional_title.setObjectName("sectionTitle")
        regional_layout.addWidget(regional_title)
        self.layer32_regional_table = _table(
            [
                "Structure",
                "Mean H",
                "P95 H",
                "Mean additional reduction",
                "Maximum reduction",
                "Volume ≥5%",
                "Final survival change",
            ]
        )
        regional_layout.addWidget(self.layer32_regional_table)
        self.layer32_context_tabs.addTab(gtv_page, "GTV context")
        self.layer32_context_tabs.addTab(spill_page, "Peri-GTV spill")
        self.layer32_context_tabs.addTab(oar_page, "Adjacent OAR spill")
        self.layer32_context_tabs.addTab(assay_page, "Model observables")
        self.layer32_context_tabs.addTab(regional_page, "Regional exposure and consequence")
        layout.addWidget(self.layer32_context_tabs)

        viewer_card, viewer_layout = self._card(
            "3D biological fields, anatomical CAD surfaces, and graph profiles",
            "The viewer renders stored scalar isosurfaces in DICOM LPS coordinates. Edge profiles are visualisation-only; Layer 3.2 iPVDR still uses the unchanged Layer 2.2 node and 3 mm midpoint-sphere definitions.",
        )
        self.layer32_viewer_status = QLabel("Run Layer 3.2, then build the hash-verified 3D/2D stored-field viewer.")
        self.layer32_viewer_status.setObjectName("sectionDescription")
        self.layer32_viewer_status.setWordWrap(True)
        self.layer32_viewer_layout = viewer_layout
        viewer_layout.addWidget(self.layer32_viewer_status)
        layout.addWidget(viewer_card, 1)

        provenance_card, provenance_layout = self._card(
            "Model provenance", "Source commit, hashes, dependency run IDs, and no-uptake assertions."
        )
        self.layer32_provenance = _text_view()
        self.layer32_provenance.setMaximumHeight(240)
        provenance_layout.addWidget(self.layer32_provenance)
        layout.addWidget(provenance_card)
        self.layer32_parameter_controls = [
            self.layer32_preset,
            self.layer32_scaling,
            self.layer32_steps,
            self.layer32_dt,
            self.layer32_grid_spacing,
            self.layer32_margin,
        ]
        self._update_layer32_enabled_controls(False)

    def _update_layer32_enabled_controls(self, enabled: bool) -> None:
        """Apply the optional Layer 3.2 gate to controls without running science."""
        self.layer32_run_button.setEnabled(enabled)
        has_current_result = bool(
            self.controller.case and self.controller.case.layer3_2.calculation_status in {"completed", "completed_with_warnings"}
        )
        self.layer32_viewer_button.setEnabled(enabled and has_current_result)
        for widget in self.layer32_parameter_controls:
            widget.setEnabled(enabled)
        if self.layer32_viewer is not None:
            self.layer32_viewer.setEnabled(enabled and has_current_result)

    def _layer32_enabled_changed(self, enabled: bool) -> None:
        """Persist the Layer 3.2 inclusion decision and invalidate only its evidence."""
        self._update_layer32_enabled_controls(enabled)
        case = self.controller.case
        if case is None or self._loading_configuration:
            return
        configuration = CaseConfiguration.from_dict(case.configuration.to_dict())
        configuration.layer32_enabled = enabled
        self.controller.configure(configuration)
        self.activity.setText("L3.2 ENABLED" if enabled else "L3.2 DISABLED")
        self.refresh()

    def _build_layer31_page(self) -> None:
        self.layer31_page, layout = self._new_page(
            "Layer 3.1 — Spatial Radiobiological Evaluation",
            "One linked anatomical workspace for physical dose, spatial LQ BED/EQD2, Guerrero–Li tumour survival/effect, regional decomposition, and gated therapeutic ratio.",
        )
        self.layer31_page_layout = layout
        self.layer31_page_title = layout.itemAt(0).widget()
        self.layer31_page_subtitle = layout.itemAt(1).widget()
        scope = WarningBanner()
        scope.set_messages(
            [
                "RESEARCH MODEL — NOT CLINICALLY VALIDATED. Outputs are model-derived comparative quantities, not TCP, NTCP, toxicity, or treatment recommendations."
            ],
            blocked=False,
        )
        self.layer31_scope_banner = scope
        layout.addWidget(scope)
        self.layer31_action_bar = QWidget()
        actions = QHBoxLayout(self.layer31_action_bar)
        actions.setContentsMargins(0, 0, 0, 0)
        run = QPushButton("Run complete Layer 3.1")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer31)
        build = QPushButton("Load / refresh unified viewer")
        build.clicked.connect(self._build_layer31_visualization)
        export = QPushButton("Export Layer 3.1")
        export.clicked.connect(self._export_layer31)
        self.layer31_guide_button = QPushButton("Interactive viewer guide…")
        self.layer31_guide_button.clicked.connect(lambda: show_viewer_guide(self, "layer3_1"))
        actions.addWidget(run)
        actions.addWidget(build)
        actions.addWidget(export)
        actions.addWidget(self.layer31_guide_button)
        actions.addStretch()
        layout.addWidget(self.layer31_action_bar)
        self.layer31_status_pill = StatusPill("NOT RUN")
        self.layer31_interpretation_pill = StatusPill("NOT INTERPRETABLE")
        self.layer31_status_text = QLabel("Configure tissue and model parameters, then run the gated workflow.")
        self.layer31_status_text.setWordWrap(True)
        self.layer31_status_bar = QWidget()
        status = QHBoxLayout(self.layer31_status_bar)
        status.setContentsMargins(0, 0, 0, 0)
        status.addWidget(self.layer31_status_pill)
        status.addWidget(self.layer31_interpretation_pill)
        status.addWidget(self.layer31_status_text, 1)
        layout.addWidget(self.layer31_status_bar)

        self.layer31_tabs = QTabWidget()
        self.layer31_tabs.tabBar().setUsesScrollButtons(True)
        self.layer31_tabs.tabBar().setElideMode(Qt.ElideRight)
        self.layer31_tabs.currentChanged.connect(self._update_layer31_tab_size_policy)
        layout.addWidget(self.layer31_tabs, 1)

        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        workflow_card, workflow_layout = self._card(
            "Layer 3.1 guided order · steps 1–17",
            "Complete the workflow from left to right. Results are deliberately presented as map → whole-tumour result → regional explanation.",
        )
        self.layer31_workflow_order = QLabel(
            "1 Prepare case  →  2 Review treatment history  →  3 Select dose sources  →  "
            "4 Assign tissue parameters  →  5 Configure LQ warning  →  6 Configure tumour model  →  "
            "7 Configure normal-cell model when required  →  8 Define TR comparator when required  →  "
            "9 Save and run  →  10 Select map  →  11 Navigate linked planes  →  12 Control anatomy  →  "
            "13 Inspect/export CAD  →  14 Read tumour SF/EUD  →  15 Explain regional contribution  →  "
            "16 Recalculate scenarios/assess TR  →  17 Audit provenance/export"
        )
        self.layer31_workflow_order.setObjectName("sectionDescription")
        self.layer31_workflow_order.setWordWrap(True)
        workflow_layout.addWidget(self.layer31_workflow_order)
        overview_layout.addWidget(workflow_card)
        gate_card, gate_layout = self._card(
            "Step 1 — Prepare case and pass analysis gates",
            "A blocked prerequisite cannot produce normal-looking biological outputs. Complete DICOM selection, treatment configuration, structure mapping, and Layer 1 validation before biological analysis.",
        )
        self.layer31_gate_table = _table(["Gate / branch", "State", "Reason / evidence"])
        gate_layout.addWidget(self.layer31_gate_table)
        overview_layout.addWidget(gate_card)
        history_card, history_layout = self._card(
            "Step 2 — Review treatment and dose history",
            "Physical components are grouped into biologically distinct fraction events before nonlinear transformation. Confirm that every contributing dose shares one validated physical geometry.",
        )
        self.layer31_history_table = _table(
            ["Order", "Event", "Components", "Fraction index", "Geometry", "Delivery time", "Source dose / plan"]
        )
        history_layout.addWidget(self.layer31_history_table)
        overview_layout.addWidget(history_card)

        config_card, config_layout = self._card(
            "Steps 3–9 — Biological configuration and run",
            "All parameters are explicit, identity-bound, and stored with provenance. No JSON entry is required.",
        )
        source_title = QLabel("Step 3 — Select treatment-source representation")
        source_title.setObjectName("sectionTitle")
        config_layout.addWidget(source_title)
        source_note = QLabel(
            "Use repeated identical fractions for a validated component total, or explicit per-fraction validated doses when fraction fields differ."
        )
        source_note.setObjectName("sectionDescription")
        source_note.setWordWrap(True)
        config_layout.addWidget(source_note)
        source_row = QHBoxLayout()
        self.layer31_source_component = QComboBox()
        self.layer31_source_component.addItem("Use current validated Layer 1 plan", None)
        self.layer31_source_model = QComboBox()
        self.layer31_source_model.addItem("Repeated identical fractions", "identical_fractions")
        self.layer31_source_model.addItem("Explicit per-fraction validated doses", "explicit_fraction_doses")
        add_source = QPushButton("Add validated component source")
        add_source.clicked.connect(self._add_layer31_component_source)
        remove_source = QPushButton("Remove selected source")
        remove_source.clicked.connect(self._remove_layer31_component_source)
        source_row.addWidget(self.layer31_source_component, 2)
        source_row.addWidget(self.layer31_source_model, 2)
        source_row.addWidget(add_source)
        source_row.addWidget(remove_source)
        config_layout.addLayout(source_row)
        self.layer31_component_table = _table(["Component", "Fraction-dose model", "Validated Layer 1 source(s)"])
        self.layer31_component_table.setMaximumHeight(150)
        config_layout.addWidget(self.layer31_component_table)
        roi_title = QLabel("Step 4 — Assign identity-bound tissue parameters")
        roi_title.setObjectName("sectionTitle")
        config_layout.addWidget(roi_title)
        roi_note = QLabel(
            "Only rasterised Layer 1 ROIs are eligible. Every assignment requires a positive α/β value and explicit parameter provenance."
        )
        roi_note.setObjectName("sectionDescription")
        roi_note.setWordWrap(True)
        config_layout.addWidget(roi_note)
        roi_form = QHBoxLayout()
        self.layer31_roi_selector = QComboBox()
        self.layer31_roi_selector.setMinimumWidth(250)
        self.layer31_alpha_beta = QLineEdit()
        self.layer31_alpha_beta.setPlaceholderText("α/β (Gy)")
        self.layer31_parameter_source_type = QComboBox()
        self.layer31_parameter_source_type.addItem("User-declared", "user_selected")
        self.layer31_parameter_source_type.addItem("Protocol / configured reference", "configured_reference")
        self.layer31_parameter_source_type.addItem("Imported literature parameter set", "imported_parameter_set")
        self.layer31_parameter_source = QLineEdit("User-declared exploratory tissue parameter")
        self.layer31_parameter_source.setPlaceholderText("Source / citation")
        self.layer31_parameter_set = QLineEdit("manual-v1")
        self.layer31_parameter_set.setPlaceholderText("Parameter-set ID")
        self.layer31_parameter_source_type.currentIndexChanged.connect(self._update_layer31_tissue_source_defaults)
        add_roi = QPushButton("Add / replace tissue assignment")
        add_roi.clicked.connect(self._add_layer31_roi_assignment)
        roi_form.addWidget(self.layer31_roi_selector, 2)
        roi_form.addWidget(self.layer31_alpha_beta)
        roi_form.addWidget(self.layer31_parameter_source_type)
        roi_form.addWidget(self.layer31_parameter_source, 2)
        roi_form.addWidget(self.layer31_parameter_set)
        roi_form.addWidget(add_roi)
        config_layout.addLayout(roi_form)
        self.layer31_roi_table = _table(["ROI", "ROI number", "α/β (Gy)", "Source type", "Source", "Parameter set"])
        self.layer31_roi_table.setMaximumHeight(190)
        config_layout.addWidget(self.layer31_roi_table)
        remove_roi = QPushButton("Remove selected assignment")
        remove_roi.clicked.connect(self._remove_layer31_roi_assignment)
        config_layout.addWidget(remove_roi, 0, Qt.AlignLeft)

        warning_card, warning_layout = self._card(
            "Step 5 — Configure the optional 3.1A LQ high-dose warning",
            "The criterion marks model-domain extrapolation only. BED/EQD2 continues on both sides and never switches to MLQ.",
        )
        warning_row = QHBoxLayout()
        self.layer31_high_dose_criterion = QComboBox()
        self.layer31_high_dose_criterion.addItem("Not configured", "not_configured")
        self.layer31_high_dose_criterion.addItem("Custom operational threshold…", "custom_operational")
        self.layer31_high_dose_criterion.addItem("Literature-defined sensitivity threshold…", "literature_sensitivity")
        self.layer31_high_dose_threshold = QLineEdit()
        self.layer31_high_dose_threshold.setPlaceholderText("Gy per fraction")
        self.layer31_high_dose_source = QLineEdit()
        self.layer31_high_dose_source.setPlaceholderText("Threshold source / rationale")
        warning_row.addWidget(QLabel("LQ high-dose warning criterion"))
        warning_row.addWidget(self.layer31_high_dose_criterion, 2)
        warning_row.addWidget(self.layer31_high_dose_threshold)
        warning_row.addWidget(self.layer31_high_dose_source, 2)
        warning_layout.addLayout(warning_row)
        config_layout.addWidget(warning_card)
        self.layer31_high_dose_criterion.currentIndexChanged.connect(self._update_layer31_high_dose_controls)

        tumour_card, tumour_layout = self._layer31_model_editor("Step 6 — Configure the 3.1B tumour model", "tumour")
        normal_card, normal_layout = self._layer31_model_editor(
            "Step 7 — Configure the 3.1C normal-cell model only when required", "normal_cell"
        )
        self.layer31_tumour_kinetics = tumour_layout
        self.layer31_normal_kinetics = normal_layout
        self.layer31_tumour_scenario = tumour_layout["scenario"]
        self.layer31_normal_scenario = normal_layout["scenario"]
        self._update_layer31_model_preset("tumour")
        self._update_layer31_model_preset("normal_cell")
        config_layout.addWidget(tumour_card)
        config_layout.addWidget(normal_card)
        comparator_title = QLabel("Step 8 — Define an explicit therapeutic-ratio comparator when 3.1C is required")
        comparator_title.setObjectName("sectionTitle")
        config_layout.addWidget(comparator_title)
        comparator = QHBoxLayout()
        self.layer31_tr_enabled = QCheckBox("Define therapeutic-ratio comparator schedule")
        self.layer31_tr_fraction_count = QLineEdit()
        self.layer31_tr_fraction_count.setPlaceholderText("Fractions")
        self.layer31_tr_delivery_time = QLineEdit()
        self.layer31_tr_delivery_time.setPlaceholderText("Delivery time per fraction")
        self.layer31_tr_source = QLineEdit()
        self.layer31_tr_source.setPlaceholderText("Comparator source / protocol")
        comparator.addWidget(self.layer31_tr_enabled)
        comparator.addWidget(self.layer31_tr_fraction_count)
        comparator.addWidget(self.layer31_tr_delivery_time)
        comparator.addWidget(self.layer31_tr_source, 1)
        config_layout.addLayout(comparator)
        self.layer31_tr_note = QLabel(
            "Disabled unless 3.1C is explicitly configured. Sequential mixed-fraction LRT+cERT requires a protocol-defined comparator; ASCEND does not invent one."
        )
        self.layer31_tr_note.setObjectName("sectionDescription")
        self.layer31_tr_note.setWordWrap(True)
        config_layout.addWidget(self.layer31_tr_note)
        self.layer31_tr_enabled.toggled.connect(self._update_layer31_tr_controls)
        paired_row = QHBoxLayout()
        self.layer31_paired_course_path = QLineEdit()
        self.layer31_paired_course_path.setReadOnly(True)
        self.layer31_paired_course_path.setPlaceholderText("Optional prior Layer 3.1 result for formal LRT versus LRT+cERT comparison")
        paired_select = QPushButton("Select paired-course result…")
        paired_clear = QPushButton("Clear")
        paired_select.clicked.connect(self._select_layer31_paired_course_result)
        paired_clear.clicked.connect(self.layer31_paired_course_path.clear)
        paired_row.addWidget(QLabel("Paired-course reference"))
        paired_row.addWidget(self.layer31_paired_course_path, 1)
        paired_row.addWidget(paired_select)
        paired_row.addWidget(paired_clear)
        config_layout.addLayout(paired_row)
        tcp_title = QLabel("Layer 3.1D — Configure direct-clonogenic Poisson TCP")
        tcp_title.setObjectName("sectionTitle")
        config_layout.addWidget(tcp_title)
        tcp_note = QLabel(
            "TCP consumes the Layer 3.1B MLQ survival field. It does not recalculate dose, MLQ, EUD, masks, or geometry. Parameters are research-model inputs and are not clinically validated."
        )
        tcp_note.setObjectName("sectionDescription")
        tcp_note.setWordWrap(True)
        config_layout.addWidget(tcp_note)
        tcp_grid = QGridLayout()
        self.layer31_tcp_density = QLineEdit()
        self.layer31_tcp_density.setPlaceholderText("Required clonogens/cm3")
        self.layer31_tcp_units = QComboBox()
        self.layer31_tcp_units.addItem("clonogens/cm3")
        self.layer31_tcp_source = QLineEdit()
        self.layer31_tcp_source.setPlaceholderText("Required source")
        self.layer31_tcp_parameter_set = QLineEdit()
        self.layer31_tcp_parameter_set.setPlaceholderText("Required parameter-set ID")
        tcp_grid.addWidget(QLabel("Clonogen density"), 0, 0)
        tcp_grid.addWidget(self.layer31_tcp_density, 0, 1)
        tcp_grid.addWidget(QLabel("Units"), 0, 2)
        tcp_grid.addWidget(self.layer31_tcp_units, 0, 3)
        tcp_grid.addWidget(QLabel("Source"), 1, 0)
        tcp_grid.addWidget(self.layer31_tcp_source, 1, 1)
        tcp_grid.addWidget(QLabel("Parameter-set ID"), 1, 2)
        tcp_grid.addWidget(self.layer31_tcp_parameter_set, 1, 3)
        self.layer31_tcp_repopulation = QCheckBox("Apply delayed exponential repopulation")
        self.layer31_tcp_overall_time = QLineEdit()
        self.layer31_tcp_overall_time.setPlaceholderText("Overall treatment time (days)")
        self.layer31_tcp_kickoff = QLineEdit()
        self.layer31_tcp_kickoff.setPlaceholderText("Kick-off time Tk (days)")
        self.layer31_tcp_doubling = QLineEdit()
        self.layer31_tcp_doubling.setPlaceholderText("Potential doubling time Tpot (days)")
        tcp_grid.addWidget(self.layer31_tcp_repopulation, 2, 0, 1, 2)
        tcp_grid.addWidget(self.layer31_tcp_overall_time, 2, 2)
        tcp_grid.addWidget(self.layer31_tcp_kickoff, 2, 3)
        tcp_grid.addWidget(self.layer31_tcp_doubling, 2, 4)
        self.layer31_tcp_sensitivity = QCheckBox("Enable clonogen-density sensitivity")
        self.layer31_tcp_sensitivity_values = QLineEdit()
        self.layer31_tcp_sensitivity_values.setPlaceholderText("Comma-separated positive densities")
        tcp_grid.addWidget(self.layer31_tcp_sensitivity, 3, 0, 1, 2)
        tcp_grid.addWidget(self.layer31_tcp_sensitivity_values, 3, 2, 1, 3)
        config_layout.addLayout(tcp_grid)
        self.layer31_tcp_repopulation.toggled.connect(self._update_layer31_tcp_controls)
        self.layer31_tcp_sensitivity.toggled.connect(self._update_layer31_tcp_controls)
        self._update_layer31_tcp_controls()
        self._update_layer31_high_dose_controls()
        self._update_layer31_tr_controls(False)
        run_title = QLabel("Step 9 — Save configuration and run the gated workflow")
        run_title.setObjectName("sectionTitle")
        config_layout.addWidget(run_title)
        run_note = QLabel(
            "A blocked or incomplete branch remains visibly blocked. ASCEND does not create normal-looking placeholder biological results."
        )
        run_note.setObjectName("sectionDescription")
        run_note.setWordWrap(True)
        config_layout.addWidget(run_note)
        save = QPushButton("Save biological configuration")
        save.clicked.connect(self._save_configuration)
        config_layout.addWidget(save, 0, Qt.AlignLeft)
        overview_layout.addWidget(config_card)
        self.layer31_tabs.addTab(overview, "1–9 Configure / run")

        spatial = QWidget()
        spatial_layout = QVBoxLayout(spatial)
        spatial_layout.setContentsMargins(0, 0, 0, 0)
        spatial_layout.setSpacing(4)
        self.layer31a_warning = WarningBanner()
        spatial_layout.addWidget(self.layer31a_warning)
        viewer_card, self.layer31_viewer_layout = self._card(
            "Embedded unified treatment-planning viewer",
            "Transverse, sagittal, coronal, and 3D biological/CAD views share one endpoint, colour range, anatomy state, crosshair, zoom, pan, rotation, and fit state.",
        )
        self.layer31_viewer_status = QLabel("Run Layer 3.1, then build the hash-verified field viewer.")
        self.layer31_viewer_status.setObjectName("sectionDescription")
        self.layer31_viewer_status.setWordWrap(True)
        self.layer31_viewer_layout.addWidget(self.layer31_viewer_status)
        spatial_layout.addWidget(viewer_card, 1)
        self.layer31a_table = _table(["ROI", "α/β (Gy)", "s-BED mean", "s-BED D95", "s-BED D50", "s-EQD2 mean", "s-EQD2 D95", "Flagged %"])
        self.layer31_tabs.addTab(spatial, "10–13 Map")

        survival = QWidget()
        survival_layout = QVBoxLayout(survival)
        whole_title = QLabel("Step 14 — WHOLE-TUMOUR RESULT")
        whole_title.setObjectName("sectionTitle")
        survival_layout.addWidget(whole_title)
        whole_note = QLabel(
            "For 3.1B, mean tumour surviving fraction SFᵀ and tumour EUDᵀ are the major numerical outputs. The spatial survival map explains how those whole-tumour values arise."
        )
        whole_note.setObjectName("sectionDescription")
        whole_note.setWordWrap(True)
        survival_layout.addWidget(whole_note)
        self.layer31b_summary = _table(["Result", "Value", "Units / state", "Evidence"])
        survival_layout.addWidget(self.layer31b_summary)
        self.layer31b_comparison = _table(["Paired-course output", "Value", "State", "Evidence"])
        survival_layout.addWidget(self.layer31b_comparison)
        lq_table_title = QLabel("Stored 3.1A ROI summaries")
        lq_table_title.setObjectName("sectionTitle")
        survival_layout.addWidget(lq_table_title)
        survival_layout.addWidget(self.layer31a_table)
        self.layer31_tabs.addTab(survival, "14 Whole-tumour SF / EUD")

        regional = QWidget()
        regional_layout = QVBoxLayout(regional)
        regional_title = QLabel("Step 15 — REGIONAL EXPLANATION")
        regional_title.setObjectName("sectionTitle")
        regional_layout.addWidget(regional_title)
        regional_note = QLabel(
            "The primary regional visual is the 100% residual-survival contribution bar in the unified viewer. Selecting its vertex, valley, or other-GTV segment focuses the corresponding validated mask in both 2D and 3D."
        )
        regional_note.setObjectName("sectionDescription")
        regional_note.setWordWrap(True)
        regional_layout.addWidget(regional_note)
        self.layer31b_contribution_bar = SurvivalContributionBar()
        self.layer31b_contribution_bar.selected.connect(self._focus_layer31_region)
        regional_layout.addWidget(self.layer31b_contribution_bar)
        self.layer31b_regional = _table(
            ["Region", "Voxel count", "Tumour volume fraction", "Mean surviving fraction", "Survivor contribution φ"]
        )
        regional_layout.addWidget(self.layer31b_regional)
        self.layer31b_hf_reconciliation = _table(
            ["High-dose fraction representation", "Value (%)", "Basis", "Difference (percentage points)"]
        )
        regional_layout.addWidget(self.layer31b_hf_reconciliation)
        self.layer31_tabs.addTab(regional, "15 Regional explanation")

        ratio = QWidget()
        ratio_layout = QVBoxLayout(ratio)
        ratio_title = QLabel("Step 16 — Recalculate declared sensitivity scenarios and assess gated therapeutic ratio")
        ratio_title.setObjectName("sectionTitle")
        ratio_layout.addWidget(ratio_title)
        self.layer31c_summary = _table(["Result", "Value", "Applicability", "Comparator"])
        ratio_layout.addWidget(self.layer31c_summary)
        oar_eud_title = QLabel("Configured OAR normal-tissue survival-equivalent EUD summary")
        oar_eud_title.setObjectName("sectionTitle")
        ratio_layout.addWidget(oar_eud_title)
        self.layer31c_oar_eud = _table(
            ["OAR", "Classification", "Voxel count", "Volume (cm³)", "Mean normal SF", "Normal-tissue EUD (Gy)", "State"]
        )
        ratio_layout.addWidget(self.layer31c_oar_eud)
        matrix_note = QLabel("C1–C3 and N1–N3 are standardised sensitivity scenarios, not patient-specific radiosensitivity estimates.")
        matrix_note.setWordWrap(True)
        matrix_note.setObjectName("sectionDescription")
        ratio_layout.addWidget(matrix_note)
        self.layer31c_matrix = _table(
            ["Tumour scenario", "Normal scenario", "TR", "Tumour EUD (Gy)", "Normal SF actual", "Normal SF reference", "Applicability"]
        )
        ratio_layout.addWidget(self.layer31c_matrix)
        self.layer31_tabs.addTab(ratio, "16 Scenarios / therapeutic ratio")

        tcp = QWidget()
        tcp_layout = QVBoxLayout(tcp)
        tcp_title = QLabel("Layer 3.1D — Spatial MLQ-Poisson tumour control probability")
        tcp_title.setObjectName("sectionTitle")
        tcp_layout.addWidget(tcp_title)
        tcp_scope = QLabel("RESEARCH MODEL · BIOLOGICALLY UNVALIDATED · DIRECT RADIATION KILL ONLY · NOT A CLINICAL OUTCOME PREDICTION")
        tcp_scope.setObjectName("sectionDescription")
        tcp_scope.setWordWrap(True)
        tcp_layout.addWidget(tcp_scope)
        self.layer31d_warning = WarningBanner()
        tcp_layout.addWidget(self.layer31d_warning)
        self.layer31d_summary = _table(["Endpoint", "Value", "Units / state", "Interpretation"])
        tcp_layout.addWidget(self.layer31d_summary)
        self.layer31d_comparison = _table(["Model", "TCP", "ln(TCP)", "Expected surviving clonogens"])
        tcp_layout.addWidget(self.layer31d_comparison)
        self.layer31d_spatial = _table(["Region", "Volume (cm3)", "Mean MLQ survival", "Residual clonogens", "Residual fraction", "P0"])
        tcp_layout.addWidget(self.layer31d_spatial)
        self.layer31d_sensitivity = _table(["Parameter", "Value", "Radiation-only TCP", "Expected surviving clonogens"])
        tcp_layout.addWidget(self.layer31d_sensitivity)
        provenance_label = QLabel("Dependency provenance and model assumptions")
        provenance_label.setObjectName("sectionTitle")
        tcp_layout.addWidget(provenance_label)
        self.layer31d_provenance = _text_view()
        tcp_layout.addWidget(self.layer31d_provenance)
        self.layer31_tabs.addTab(tcp, "3.1D TCP")

        provenance = QWidget()
        provenance_layout = QVBoxLayout(provenance)
        provenance_title = QLabel("Step 17 — Audit provenance, validation state, hashes, and exports")
        provenance_title.setObjectName("sectionTitle")
        provenance_layout.addWidget(provenance_title)
        self.layer31_provenance = _text_view()
        provenance_layout.addWidget(self.layer31_provenance)
        self.layer31_tabs.addTab(provenance, "17 Provenance / export")
        self._update_layer31_tab_size_policy(self.layer31_tabs.currentIndex())

    def _update_layer31_tab_size_policy(self, index: int) -> None:
        """Let result tabs shrink to the viewport without clipping configuration."""
        viewer_focus = index == 1
        for widget in (
            self.layer31_page_title,
            self.layer31_page_subtitle,
            self.layer31_scope_banner,
            self.layer31_action_bar,
            self.layer31_status_bar,
        ):
            widget.setVisible(not viewer_focus)
        margins = (8, 6, 8, 8) if viewer_focus else (28, 24, 28, 24)
        self.layer31_page_layout.setContentsMargins(*margins)
        self.layer31_page_layout.setSpacing(4 if viewer_focus else 12)
        horizontal = QSizePolicy.Preferred if index == 0 else QSizePolicy.Ignored
        vertical = QSizePolicy.Preferred if index == 0 else QSizePolicy.Ignored
        self.layer31_tabs.setSizePolicy(horizontal, vertical)
        if index == 0:
            maximum_height = 16777215
        elif viewer_focus:
            maximum_height = max(700, self.height() - 130)
        else:
            maximum_height = max(560, self.height() - 290)
        self.layer31_tabs.setMaximumHeight(maximum_height)
        self.layer31_tabs.updateGeometry()

    def _layer31_model_editor(self, title: str, tissue: str) -> tuple[QFrame, dict[str, Any]]:
        """Build one preset-driven MLQ editor with explicit provenance."""
        description = (
            "C1–C3 are tumour sensitivity scenarios. The Zhang 2022 tumour kinetic preset is explicit; delivery time remains treatment-derived or user supplied."
            if tissue == "tumour"
            else "N1–N3 set SF2, α/β, α and β. The registered Zhang 2022 normal-cell kinetic reproduction is selected visibly by default and can be replaced by sourced custom kinetics."
        )
        card, layout = self._card(title, description)
        grid = QGridLayout()
        model = QLineEdit("Guerrero–Li MLQ")
        model.setReadOnly(True)
        model.setMinimumWidth(155)
        model.setCursorPosition(0)
        scenario = QComboBox()
        scenario.addItems(["Not configured", *(TUMOUR_SCENARIOS if tissue == "tumour" else NORMAL_SCENARIOS)])
        kinetic_preset = QComboBox()
        kinetic_preset.setMinimumWidth(250)
        kinetic_preset.addItem("Not configured", "not_configured")
        presets = TUMOUR_KINETIC_PRESETS if tissue == "tumour" else NORMAL_KINETIC_PRESETS
        for preset_id, preset in presets.items():
            kinetic_preset.addItem(str(preset["label"]), preset_id)
        kinetic_preset.addItem("Custom sourced kinetic parameters…", "custom")
        grid.addWidget(QLabel("Model"), 0, 0)
        grid.addWidget(model, 0, 1)
        grid.addWidget(QLabel("Scenario"), 0, 2)
        grid.addWidget(scenario, 0, 3)
        grid.addWidget(QLabel("Kinetic preset"), 0, 4)
        grid.addWidget(kinetic_preset, 0, 5)
        fields: dict[str, Any] = {"model": model, "scenario": scenario, "kinetic_preset": kinetic_preset}
        for column, (key, label) in enumerate(
            (("alpha_beta_gy", "α/β (Gy)"), ("sf2", "SF2"), ("alpha_per_gy", "α (Gy⁻¹)"), ("beta_per_gy2", "β (Gy⁻²)"))
        ):
            widget = QLineEdit()
            widget.setReadOnly(True)
            widget.setPlaceholderText("Select scenario")
            grid.addWidget(QLabel(label), 1, column * 2)
            grid.addWidget(widget, 1, column * 2 + 1)
            fields[key] = widget
        delta = QLineEdit()
        delta.setPlaceholderText("δ (Gy⁻¹)")
        half_time = QLineEdit()
        half_time.setPlaceholderText("Repair half-time")
        delivery = QLineEdit()
        delivery.setPlaceholderText("Required delivery time")
        unit = QComboBox()
        unit.addItems(["minutes", "seconds", "hours"])
        grid.addWidget(QLabel("δ (Gy⁻¹)"), 2, 0)
        grid.addWidget(delta, 2, 1)
        grid.addWidget(QLabel("Repair half-time"), 2, 2)
        grid.addWidget(half_time, 2, 3)
        grid.addWidget(QLabel("Delivery time"), 2, 4)
        grid.addWidget(delivery, 2, 5)
        grid.addWidget(unit, 2, 6)
        source = QLineEdit()
        source.setPlaceholderText("Required source / citation")
        set_id = QLineEdit()
        set_id.setPlaceholderText("Required parameter-set ID")
        grid.addWidget(QLabel("Parameter source"), 3, 0)
        grid.addWidget(source, 3, 1, 1, 3)
        grid.addWidget(QLabel("Parameter-set ID"), 3, 4)
        grid.addWidget(set_id, 3, 5, 1, 2)
        status = QLabel()
        status.setWordWrap(True)
        status.setObjectName("sectionDescription")
        grid.addWidget(status, 4, 0, 1, 8)
        fields.update(
            {
                "parameter_set_id": set_id,
                "parameter_source": source,
                "delta_per_gy": delta,
                "repair_half_time": half_time,
                "treatment_delivery_time": delivery,
                "time_unit": unit,
                "status": status,
                "tissue": tissue,
            }
        )
        scenario.currentIndexChanged.connect(lambda _index, kind=tissue: self._update_layer31_model_preset(kind))
        kinetic_preset.currentIndexChanged.connect(lambda _index, kind=tissue: self._update_layer31_model_preset(kind))
        layout.addLayout(grid)
        return card, fields

    def _update_layer31_high_dose_controls(self, _index: int = -1) -> None:
        """Keep the warning criterion visibly optional and provenance-bound."""
        mode = str(self.layer31_high_dose_criterion.currentData())
        configured = mode != "not_configured"
        self.layer31_high_dose_threshold.setEnabled(configured)
        self.layer31_high_dose_source.setEnabled(configured)
        if not configured and not getattr(self, "_loading_configuration", False):
            self.layer31_high_dose_threshold.clear()
            self.layer31_high_dose_source.clear()
        if mode == "custom_operational":
            self.layer31_high_dose_source.setPlaceholderText("Operational rationale (not a biological cutoff)")
        elif mode == "literature_sensitivity":
            self.layer31_high_dose_source.setPlaceholderText("Required citation and reproduction context")

    def _update_layer31_tissue_source_defaults(self, _index: int = -1) -> None:
        """Supply honest manual provenance while requiring explicit external sources."""
        source_type = str(self.layer31_parameter_source_type.currentData())
        if source_type == "user_selected":
            if not self.layer31_parameter_source.text().strip():
                self.layer31_parameter_source.setText("User-declared exploratory tissue parameter")
            if not self.layer31_parameter_set.text().strip():
                self.layer31_parameter_set.setText("manual-v1")
        elif self.layer31_parameter_source.text().strip() == "User-declared exploratory tissue parameter":
            self.layer31_parameter_source.clear()
            self.layer31_parameter_set.clear()

    def _update_layer31_model_preset(self, tissue: str) -> None:
        """Populate named scenario values without conflating tumour and normal kinetics."""
        editor = self.layer31_tumour_kinetics if tissue == "tumour" else self.layer31_normal_kinetics
        scenario_id = editor["scenario"].currentText()
        scenarios = TUMOUR_SCENARIOS if tissue == "tumour" else NORMAL_SCENARIOS
        presets = TUMOUR_KINETIC_PRESETS if tissue == "tumour" else NORMAL_KINETIC_PRESETS
        if scenario_id in scenarios:
            scenario = scenarios[scenario_id]
            for key in ("alpha_beta_gy", "sf2", "alpha_per_gy", "beta_per_gy2"):
                editor[key].setText(str(scenario[key]))
            # A named scenario uses the matching registered GRID kinetic
            # reproduction by default. The choice remains explicit and visible
            # in the editor and can be replaced with sourced custom kinetics.
            if editor["kinetic_preset"].currentData() == "not_configured":
                index = editor["kinetic_preset"].findData("zhang_grid_2022")
                editor["kinetic_preset"].blockSignals(True)
                editor["kinetic_preset"].setCurrentIndex(index)
                editor["kinetic_preset"].blockSignals(False)
        else:
            for key in ("alpha_beta_gy", "sf2", "alpha_per_gy", "beta_per_gy2"):
                editor[key].clear()

        preset_id = str(editor["kinetic_preset"].currentData())
        custom = preset_id == "custom"
        preset = presets.get(preset_id)
        previously_locked = editor["parameter_source"].isReadOnly()
        for key in ("delta_per_gy", "repair_half_time"):
            editor[key].setReadOnly(not custom)
        editor["parameter_source"].setReadOnly(not custom)
        editor["parameter_set_id"].setReadOnly(not custom)
        if preset:
            editor["delta_per_gy"].setText(str(preset["delta_per_gy"]))
            editor["repair_half_time"].setText(str(preset["repair_half_time"]))
            editor["time_unit"].setCurrentText(str(preset["time_unit"]))
            editor["parameter_source"].setText(str(preset["parameter_source"]))
            editor["parameter_set_id"].setText(
                f"{preset['parameter_set_id']}-{scenario_id.lower()}" if scenario_id in scenarios else str(preset["parameter_set_id"])
            )
            editor["status"].setText("PRESET · scenario and kinetic provenance locked. Delivery time remains required and case-specific.")
        elif custom:
            if previously_locked or editor["parameter_source"].text().startswith("Scenario only"):
                editor["delta_per_gy"].clear()
                editor["repair_half_time"].clear()
                editor["parameter_source"].clear()
                editor["parameter_set_id"].clear()
            editor["status"].setText("CUSTOM · δ, repair half-time, source, parameter-set ID, and delivery time are required.")
        else:
            editor["delta_per_gy"].clear()
            editor["repair_half_time"].clear()
            source = SCENARIO_SOURCE[tissue]
            if scenario_id in scenarios:
                editor["parameter_source"].setText(f"Scenario only: {source['citation']}; kinetic model not configured")
                editor["parameter_set_id"].setText(f"{source['parameter_set_prefix']}-{scenario_id.lower()}-scenario-only")
            else:
                editor["parameter_source"].clear()
                editor["parameter_set_id"].clear()
            editor["status"].setText("INCOMPLETE · select a defined kinetic preset or custom sourced kinetics before calculation.")
        editor["time_unit"].setEnabled(custom or not preset)

    def _update_layer31_tr_controls(self, checked: bool | None = None) -> None:
        """Expose comparator inputs only when 3.1C is explicitly enabled."""
        enabled = self.layer31_tr_enabled.isChecked() if checked is None else bool(checked)
        for widget in (self.layer31_tr_fraction_count, self.layer31_tr_delivery_time, self.layer31_tr_source):
            widget.setEnabled(enabled)
        self.layer31_tr_note.setText(
            "Comparator enabled. Define the uniform reference schedule and its protocol source explicitly."
            if enabled
            else "Comparator disabled. Sequential mixed-fraction LRT+cERT returns TR_REFERENCE_SCHEDULE_UNDEFINED / NOT_APPLICABLE; ASCEND does not invent a schedule."
        )

    def _select_layer31_paired_course_result(self) -> None:
        """Select a prior result; comparison gates are enforced by the service."""
        start = str(self.controller.case.root if self.controller.case else Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select paired Layer 3.1 result",
            start,
            "Layer 3.1 results (*.json);;JSON files (*.json)",
        )
        if path:
            self.layer31_paired_course_path.setText(path)

    def _update_layer31_tcp_controls(self, _checked: bool | None = None) -> None:
        """Expose only TCP inputs that are active in the selected model."""
        repopulation = self.layer31_tcp_repopulation.isChecked()
        for widget in (self.layer31_tcp_overall_time, self.layer31_tcp_kickoff, self.layer31_tcp_doubling):
            widget.setEnabled(repopulation)
        self.layer31_tcp_sensitivity_values.setEnabled(self.layer31_tcp_sensitivity.isChecked())
