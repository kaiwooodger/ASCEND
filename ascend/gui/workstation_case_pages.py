"""Case setup page construction for the ASCEND workstation."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ascend.gui.theme import StatusPill, WarningBanner
from ascend.gui.workstation_widgets import table as _table
from ascend.gui.workstation_widgets import text_view as _text_view
from ascend.models.config import (
    DOSE_CONTEXTS,
    OAR_CLASSIFICATIONS,
    PRESCRIPTION_SOURCES,
    PROTOCOL_ENDPOINT_ROLES,
    TREATMENT_DELIVERY_MODES,
)


class WorkstationCasePagesMixin:
    """Build import, configuration, mapping, and Layer 1 pages."""

    def _build_import_page(self) -> None:
        _, layout = self._new_page("Import", "Header discovery only. Scientific calculations do not run during import.")
        row = QHBoxLayout()
        self.source_path = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_source)
        run = QPushButton("Import case")
        run.setObjectName("primary")
        run.clicked.connect(self._import_case)
        open_case = QPushButton("Open ASCEND case")
        open_case.clicked.connect(self._open_case)
        row.addWidget(self.source_path, 1)
        row.addWidget(browse)
        row.addWidget(run)
        row.addWidget(open_case)
        layout.addLayout(row)
        chain_explanation = QLabel(
            "DICOM chain selection chooses one UID-linked RTDOSE → RTPLAN → RTSTRUCT → planning-image series. "
            "It prevents dose, prescription, contours, and images from different plans or exports being combined. "
            "ASCEND selects automatically only when exactly one complete chain exists."
        )
        chain_explanation.setObjectName("sectionDescription")
        chain_explanation.setWordWrap(True)
        layout.addWidget(chain_explanation)
        chain_row = QHBoxLayout()
        self.chain_select = QComboBox()
        self.chain_override_reason = QLineEdit()
        self.chain_override_reason.setPlaceholderText("Reason required only for incomplete-reference override")
        select_chain_button = QPushButton("Select DICOM chain")
        select_chain_button.clicked.connect(self._select_dicom_chain)
        chain_row.addWidget(self.chain_select, 1)
        chain_row.addWidget(self.chain_override_reason, 1)
        chain_row.addWidget(select_chain_button)
        layout.addLayout(chain_row)
        self.chain_detail = QLabel("Import a DICOM directory to resolve treatment chains.")
        self.chain_detail.setObjectName("sectionDescription")
        self.chain_detail.setWordWrap(True)
        layout.addWidget(self.chain_detail)
        eclipse_card, eclipse_layout = self._card(
            "Eclipse DVH references",
            "Import Eclipse CSV/TXT reference data with the DICOM case. Supported protocol endpoint definitions can be auto-filled on Case configuration.",
        )
        self.tps_csv = QLineEdit()
        eclipse_row = QHBoxLayout()
        eclipse_row.addWidget(self.tps_csv, 1)
        tps_browse = QPushButton("Browse Eclipse file")
        tps_browse.clicked.connect(self._browse_tps_csv)
        tps_folder = QPushButton("Browse Eclipse folder")
        tps_folder.clicked.connect(self._browse_tps_folder)
        eclipse_row.addWidget(tps_browse)
        eclipse_row.addWidget(tps_folder)
        eclipse_layout.addLayout(eclipse_row)
        self.eclipse_import_status = QLabel("No Eclipse DVH reference selected.")
        self.eclipse_import_status.setObjectName("sectionDescription")
        self.eclipse_import_status.setWordWrap(True)
        eclipse_layout.addWidget(self.eclipse_import_status)
        layout.addWidget(eclipse_card)
        cache_row = QHBoxLayout()
        inspect_cache = QPushButton("Inspect Layer 1 cache")
        inspect_cache.clicked.connect(self._inspect_layer1_cache)
        clear_cache = QPushButton("Clear Layer 1 cache")
        clear_cache.clicked.connect(self._clear_layer1_cache)
        cache_row.addWidget(inspect_cache)
        cache_row.addWidget(clear_cache)
        cache_row.addStretch()
        layout.addLayout(cache_row)
        self.import_summary = _text_view()
        layout.addWidget(self.import_summary, 1)

    def _build_configuration_page(self) -> None:
        _, layout = self._new_page(
            "Case configuration",
            "Treatment meaning, prescription values, and prescription provenance are explicit inputs. Dose maxima are never used as prescriptions.",
        )
        form = QFormLayout()
        self.treatment_approach = QComboBox()
        for label, value in (
            ("LRT alone", "LRT_ALONE"),
            ("Sequential LRT + cERT", "LRT_SEQUENTIAL_CERT"),
            ("Integrated / SIB LRT", "LRT_INTEGRATED"),
            ("Unknown / not established", "UNKNOWN"),
        ):
            self.treatment_approach.addItem(label, value)
        self.mode = QComboBox()
        self.mode.addItems(TREATMENT_DELIVERY_MODES)
        self.dose_context = QComboBox()
        self.dose_context.addItems(DOSE_CONTEXTS)
        self.rx_l = QComboBox()
        self.rx_l.setEditable(True)
        self.rx_l.lineEdit().setPlaceholderText("Required for peripheral V95")
        self.rx_h = QComboBox()
        self.rx_h.setEditable(True)
        self.rx_h.lineEdit().setPlaceholderText("Required for high-dose V95")
        self.rx_l_source = QComboBox()
        self.rx_l_source.addItems(PRESCRIPTION_SOURCES)
        self.rx_h_source = QComboBox()
        self.rx_h_source.addItems(PRESCRIPTION_SOURCES)
        self.fractions = QComboBox()
        self.fractions.setEditable(True)
        self.protocol_id = QLineEdit()
        endpoint_widget = QWidget()
        endpoint_layout = QVBoxLayout(endpoint_widget)
        endpoint_layout.setContentsMargins(0, 0, 0, 0)
        endpoint_layout.setSpacing(7)
        endpoint_selectors = QHBoxLayout()
        self.protocol_endpoint_role = QComboBox()
        self.protocol_endpoint_role.addItems(PROTOCOL_ENDPOINT_ROLES)
        self.protocol_endpoint_kind = QComboBox()
        self.protocol_endpoint_kind.addItem("Dose received by a volume (Dxx)", "d_percent")
        self.protocol_endpoint_kind.addItem("Volume receiving a percentage of prescription (Vxx%Rx)", "coverage_relative_rx")
        self.protocol_endpoint_kind.addItem("Volume receiving an absolute dose (VxxGy)", "coverage_absolute_gy")
        self.protocol_endpoint_value = QLineEdit()
        self.protocol_endpoint_value.setValidator(QDoubleValidator(0.000001, 100000.0, 6))
        self.protocol_endpoint_value.setPlaceholderText("95")
        add_endpoint = QPushButton("Add endpoint")
        add_endpoint.clicked.connect(self._add_protocol_endpoint)
        remove_endpoint = QPushButton("Remove selected")
        remove_endpoint.clicked.connect(self._remove_protocol_endpoint)
        self.prefill_endpoint_button = QPushButton("Auto-fill from Eclipse reference")
        self.prefill_endpoint_button.clicked.connect(self._prefill_protocol_endpoints)
        endpoint_selectors.addWidget(self.protocol_endpoint_role)
        endpoint_selectors.addWidget(self.protocol_endpoint_kind, 1)
        endpoint_selectors.addWidget(self.protocol_endpoint_value)
        endpoint_layout.addLayout(endpoint_selectors)
        endpoint_actions = QHBoxLayout()
        endpoint_actions.addWidget(add_endpoint)
        endpoint_actions.addWidget(remove_endpoint)
        endpoint_actions.addWidget(self.prefill_endpoint_button)
        endpoint_actions.addStretch()
        endpoint_layout.addLayout(endpoint_actions)
        self.protocol_endpoint_table = _table(["Structure role", "Endpoint", "Configured value", "Source"])
        self.protocol_endpoint_table.setMaximumHeight(180)
        endpoint_layout.addWidget(self.protocol_endpoint_table)
        self._protocol_endpoint_entries: list[dict[str, Any]] = []
        form.addRow("Treatment approach", self.treatment_approach)
        form.addRow("Detailed delivery mode", self.mode)
        form.addRow("Dose context", self.dose_context)
        form.addRow("Peripheral prescription Rx_L (Gy)", self.rx_l)
        form.addRow("Rx_L source", self.rx_l_source)
        form.addRow("High-dose prescription Rx_H (Gy)", self.rx_h)
        form.addRow("Rx_H source", self.rx_h_source)
        form.addRow("Fractions", self.fractions)
        form.addRow("Protocol identifier", self.protocol_id)
        form.addRow("Protocol endpoints", endpoint_widget)
        layout.addLayout(form)

        component_card, component_layout = self._card(
            "Treatment components",
            "Enter only documented component history. Dates and preceding gaps are retained as provenance; no gap correction or dose warping is performed.",
        )
        component_controls = QGridLayout()
        self.component_id = QLineEdit()
        self.component_id.setPlaceholderText("Component ID")
        self.component_type = QComboBox()
        self.component_type.addItems(["LRT", "CERT", "OTHER"])
        self.component_prescription = QLineEdit()
        self.component_prescription.setPlaceholderText("Prescription Gy")
        self.component_fractions = QLineEdit()
        self.component_fractions.setPlaceholderText("Fractions")
        self.component_rx_low = QLineEdit()
        self.component_rx_low.setPlaceholderText("Component Rx_L Gy")
        self.component_rx_high = QLineEdit()
        self.component_rx_high.setPlaceholderText("Component Rx_H Gy")
        self.component_start = QLineEdit()
        self.component_start.setPlaceholderText("Start date/time optional")
        self.component_end = QLineEdit()
        self.component_end.setPlaceholderText("End date/time optional")
        self.component_gap = QLineEdit()
        self.component_gap.setPlaceholderText("Preceding gap days")
        self.component_prescription_source = QLineEdit()
        self.component_prescription_source.setPlaceholderText("Prescription source")
        self.component_dose_uid = QLineEdit()
        self.component_dose_uid.setPlaceholderText("RTDOSE SOP Instance UID optional")
        self.component_plan_uid = QLineEdit()
        self.component_plan_uid.setPlaceholderText("RTPLAN SOP Instance UID optional")
        self.component_geometry = QLineEdit()
        self.component_geometry.setPlaceholderText("Validated geometry ID/hash optional")
        for column, (label, widget) in enumerate(
            (
                ("ID", self.component_id),
                ("Type", self.component_type),
                ("Prescription", self.component_prescription),
                ("Fractions", self.component_fractions),
                ("Rx_L", self.component_rx_low),
                ("Rx_H", self.component_rx_high),
            )
        ):
            component_controls.addWidget(QLabel(label), 0, column)
            component_controls.addWidget(widget, 1, column)
        for column, (label, widget) in enumerate(
            (
                ("Start", self.component_start),
                ("End", self.component_end),
                ("Gap days", self.component_gap),
                ("Prescription source", self.component_prescription_source),
            )
        ):
            component_controls.addWidget(QLabel(label), 2, column)
            component_controls.addWidget(widget, 3, column)
        add_component = QPushButton("Add / replace component")
        add_component.clicked.connect(self._add_treatment_component)
        remove_component = QPushButton("Remove selected component")
        remove_component.clicked.connect(self._remove_treatment_component)
        component_controls.addWidget(add_component, 3, 4)
        component_controls.addWidget(remove_component, 3, 5)
        for column, (label, widget) in enumerate(
            (
                ("Dose source UID", self.component_dose_uid),
                ("Plan UID", self.component_plan_uid),
                ("Geometry identity", self.component_geometry),
            )
        ):
            component_controls.addWidget(QLabel(label), 4, column * 2)
            component_controls.addWidget(widget, 5, column * 2, 1, 2)
        component_layout.addLayout(component_controls)
        self.treatment_component_table = _table(
            [
                "Component",
                "Type",
                "Prescription Gy",
                "Fractions",
                "Dose/fraction Gy",
                "Rx_L",
                "Rx_H",
                "Start",
                "End",
                "Preceding gap days",
                "Source",
                "Dose UID",
                "Geometry",
            ]
        )
        self.treatment_component_table.setMaximumHeight(190)
        component_layout.addWidget(self.treatment_component_table)
        analysis_row = QHBoxLayout()
        self.analysis_component = QComboBox()
        analysis_row.addWidget(QLabel("Analysis component"))
        analysis_row.addWidget(self.analysis_component, 1)
        component_layout.addLayout(analysis_row)
        self._treatment_component_entries: list[dict[str, Any]] = []
        layout.addWidget(component_card)

        # Keep variable-length RTPLAN evidence outside QFormLayout.  A table in
        # one form row is compressed by the neighbouring label column on macOS,
        # making the candidates appear clipped or hidden below endpoint rows.
        candidate_card, candidate_layout = self._card(
            "DICOM-derived candidates",
            "Read-only RTPLAN/RTDOSE evidence. ASCEND prefills only a unique unambiguous value; multiple candidates require explicit selection above.",
        )
        self.dicom_prefill_summary = QLabel("No selected RTPLAN/RTDOSE configuration evidence.")
        self.dicom_prefill_summary.setObjectName("sectionDescription")
        self.dicom_prefill_summary.setWordWrap(True)
        candidate_layout.addWidget(self.dicom_prefill_summary)
        self.dicom_candidate_tabs = QTabWidget()
        self.dicom_fraction_candidates = _table(["Fraction group", "Fractions", "Referenced beams", "DICOM source"])
        self.dicom_prescription_candidates = _table(
            [
                "Dose reference",
                "Dose (Gy)",
                "Label",
                "Referenced ROI",
                "Reference type",
                "DICOM source",
            ]
        )
        self.dicom_fraction_candidates.setMinimumHeight(120)
        self.dicom_prescription_candidates.setMinimumHeight(120)
        self.dicom_candidate_tabs.addTab(self.dicom_fraction_candidates, "Fractionation")
        self.dicom_candidate_tabs.addTab(self.dicom_prescription_candidates, "Prescriptions")
        candidate_layout.addWidget(self.dicom_candidate_tabs)
        self.dicom_candidate_warnings = WarningBanner()
        candidate_layout.addWidget(self.dicom_candidate_warnings)
        layout.addWidget(candidate_card)

        checks = QGridLayout()
        self.confirm_prescriptions = QCheckBox("Prescriptions confirmed")
        self.confirm_roles = QCheckBox("Roles confirmed")
        self.confirm_dose = QCheckBox("Dose object confirmed")
        self.confirm_valley = QCheckBox("Valley definition confirmed")
        self.confirm_equal = QCheckBox("Equal prescriptions intentional")
        for index, widget in enumerate(
            (
                self.confirm_prescriptions,
                self.confirm_roles,
                self.confirm_dose,
                self.confirm_valley,
                self.confirm_equal,
            )
        ):
            checks.addWidget(widget, index // 3, index % 3)
        layout.addLayout(checks)
        save = QPushButton("Save configuration")
        save.setObjectName("primary")
        save.clicked.connect(self._save_configuration)
        layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addStretch()

    def _build_mapping_page(self) -> None:
        _, layout = self._new_page(
            "Structure-role mapping",
            "Bind RTSTRUCT ROI identities to vendor-independent ASCEND roles. Names are displayed for review; the stored ROI number and RTSTRUCT UID remain authoritative.",
        )
        mapping_card, mapping_layout = self._card(
            "Role assignments",
            "Required roles must resolve to one unambiguous ROI. Individual vertices may be comma-separated.",
        )
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        self.role_widgets: dict[str, QComboBox | QLineEdit] = {}
        labels = {
            "GTV": "GTV",
            "T_L": "Peripheral target T_L",
            "VTV_H": "Aggregate high-dose VTV_H",
            "VTV_L": "Planned valley VTV_L",
            "VTV_H_individual": "Individual high-dose vertices",
        }
        for role, label in labels.items():
            widget: QComboBox | QLineEdit
            if role == "VTV_H_individual":
                widget = QLineEdit()
            else:
                widget = QComboBox()
                widget.setEditable(True)
            self.role_widgets[role] = widget
            form.addRow(label, widget)
        self.validation_structures = QLineEdit()
        self.validation_structures.setPlaceholderText("Optional comma-separated RTSTRUCT names for Layer 1 validation")
        form.addRow("Additional validation structures", self.validation_structures)
        mapping_layout.addLayout(form)
        oar_heading = QLabel("Optional OAR and internal-target geometry")
        oar_heading.setObjectName("sectionTitle")
        mapping_layout.addWidget(oar_heading)
        oar_detail = QLabel(
            "Select structures for descriptive geometry only. OARs can be compared with vertices; all_vertices/all_valleys may instead be labelled internal target structures and are never treated as OAR compliance structures."
        )
        oar_detail.setObjectName("sectionDescription")
        oar_detail.setWordWrap(True)
        mapping_layout.addWidget(oar_detail)
        oar_controls = QHBoxLayout()
        self.oar_roi_selector = QComboBox()
        self.oar_roi_selector.addItem("Open a case to load RTSTRUCT ROIs…", None)
        self.oar_roi_selector.setMinimumWidth(280)
        self.oar_roi_selector.currentIndexChanged.connect(self._infer_geometry_classification)
        self.oar_classification_selector = QComboBox()
        classification_labels = {
            "containing_organ": "Containing organ",
            "target_excluded_oar": "Target-excluded OAR",
            "separate_critical_oar": "Separate critical OAR",
            "internal_target_structure": "Internal target structure (for example all_vertices/all_valleys)",
        }
        for classification in OAR_CLASSIFICATIONS:
            self.oar_classification_selector.addItem(classification_labels[classification], classification)
        add_oar = QPushButton("Add / update OAR")
        add_oar.clicked.connect(self._add_or_update_oar)
        remove_oar = QPushButton("Remove selected OAR")
        remove_oar.clicked.connect(self._remove_selected_oar)
        prefill_oars = QPushButton("Auto-fill OAR candidates")
        prefill_oars.clicked.connect(self._prefill_oar_geometry)
        oar_controls.addWidget(QLabel("RTSTRUCT ROI"))
        oar_controls.addWidget(self.oar_roi_selector, 2)
        oar_controls.addWidget(QLabel("Classification"))
        oar_controls.addWidget(self.oar_classification_selector, 1)
        oar_controls.addWidget(add_oar)
        oar_controls.addWidget(remove_oar)
        oar_controls.addWidget(prefill_oars)
        mapping_layout.addLayout(oar_controls)
        self.oar_table = _table(["OAR", "ROI number", "Classification", "Identity binding"])
        self.oar_table.setMaximumHeight(190)
        self.oar_table.cellClicked.connect(self._select_oar_table_row)
        mapping_layout.addWidget(self.oar_table)
        self._oar_entries: list[dict[str, Any]] = []
        save = QPushButton("Save mappings")
        save.setObjectName("primary")
        save.clicked.connect(self._save_configuration)
        mapping_layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addWidget(mapping_card)
        inventory_card, inventory_layout = self._card(
            "Resolved bindings",
            "The table reflects stored identity bindings and whether each required role is ready for Layer 1.",
        )
        self.mapping_table = _table(["ASCEND role", "Requirement", "RTSTRUCT ROI", "ROI number", "Binding state"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        inventory_layout.addWidget(self.mapping_table)
        layout.addWidget(inventory_card, 1)

    def _build_layer1_page(self) -> None:
        _, layout = self._new_page("Layer 1 validation", "Data-quality gateway and immutable canonical native archive.")
        row = QHBoxLayout()
        run = QPushButton("Validate case")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer1)
        self.layer1_status_pill = StatusPill("NOT RUN")
        self.layer1_card = QLabel("Downstream eligibility is closed until validation completes.")
        self.layer1_card.setObjectName("sectionDescription")
        row.addWidget(run)
        row.addWidget(self.layer1_status_pill)
        row.addWidget(self.layer1_card, 1)
        layout.addLayout(row)
        self.layer1_banner = WarningBanner("No Layer 1 warnings recorded.")
        layout.addWidget(self.layer1_banner)
        self.layer1_tabs = QTabWidget()
        self.layer1_findings = _table(["Severity", "Check", "Detail", "Downstream effect"])
        self.layer1_eclipse_audit = _table(
            [
                "Eclipse structure",
                "ASCEND role",
                "Validated structure",
                "Metric",
                "Eclipse",
                "ASCEND",
                "Difference",
                "Unit",
                "Status",
            ]
        )
        self.layer1_eclipse_import = _text_view()
        self.layer1_tabs.addTab(self.layer1_findings, "Validation findings")
        self.layer1_tabs.addTab(self.layer1_eclipse_audit, "Eclipse DVH audit")
        self.layer1_tabs.addTab(self.layer1_eclipse_import, "Import provenance")
        layout.addWidget(self.layer1_tabs, 1)
