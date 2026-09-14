"""Case-configuration editing behaviour for the ASCEND workstation."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pydicom
from PySide6.QtWidgets import QComboBox, QLineEdit, QMessageBox

from ascend.gui.workstation_widgets import set_table as _set_table
from ascend.layer3.nonlocal_effect.models import resolved_parameters
from ascend.models.config import CaseConfiguration, Prescription
from ascend.treatment.models import TreatmentComponent
from ascend.workflow.preferences import protocol_endpoint_record


class WorkstationConfigurationMixin:
    """Edit and persist case configuration through controller-owned state."""

    _ECLIPSE_TARGET_ROLES = {"GTV", "T_L", "VTV_H", "VTV_L"}

    @staticmethod
    def _number(text: str) -> float | None:
        return float(text) if text.strip() else None

    @staticmethod
    def _integer(text: str) -> int | None:
        return int(text) if text.strip() else None

    @staticmethod
    def _endpoint_label(item: dict[str, Any]) -> str:
        value = float(item.get("value", 0))
        kind = item.get("kind")
        if kind == "d_percent":
            return f"D{value:g}"
        if kind == "coverage_relative_rx":
            return f"V{value * 100:g}%Rx"
        return f"V{value:g}Gy"

    def _refresh_protocol_endpoint_table(self) -> None:
        _set_table(
            self.protocol_endpoint_table,
            [
                [
                    item.get("role"),
                    self._endpoint_label(item),
                    item.get("value"),
                    "Eclipse auto-fill" if item.get("source") == "eclipse_reference_auto_fill" else "User selected",
                ]
                for item in self._protocol_endpoint_entries
            ],
            "No optional protocol endpoints selected.",
        )

    def _staged_structure_roles(self) -> dict[str, str | list[str]]:
        """Return the structure-role values currently displayed in the editor."""
        roles: dict[str, str | list[str]] = {}
        for role, widget in self.role_widgets.items():
            value = widget.text().strip() if isinstance(widget, QLineEdit) else widget.currentText().strip()
            if value:
                roles[role] = (
                    [item.strip() for item in value.split(",") if item.strip()]
                    if role == "VTV_H_individual"
                    else value
                )
        return roles

    @classmethod
    def _has_eclipse_target_roles(cls, roles: dict[str, str | list[str]]) -> bool:
        return any(role in cls._ECLIPSE_TARGET_ROLES and bool(value) for role, value in roles.items())

    @staticmethod
    def _reference_needs_case_role_mapping(source: str) -> bool:
        path = Path(source).expanduser()
        return path.is_dir() or path.suffix.lower() == ".txt"

    def _add_protocol_endpoint(self) -> None:
        try:
            value = float(self.protocol_endpoint_value.text())
            if not math.isfinite(value) or value <= 0:
                raise ValueError
            kind = str(self.protocol_endpoint_kind.currentData())
            if kind == "d_percent" and value > 100:
                raise ValueError
        except ValueError:
            QMessageBox.critical(self, "ASCEND configuration", "Enter a positive endpoint value. Dxx must not exceed 100%.")
            return
        record = protocol_endpoint_record(self.protocol_endpoint_role.currentText(), kind, value)
        semantic = (record["role"], record["kind"], float(record["value"]))
        if any((item["role"], item["kind"], float(item["value"])) == semantic for item in self._protocol_endpoint_entries):
            return
        self._protocol_endpoint_entries.append(record)
        self._refresh_protocol_endpoint_table()

    def _remove_protocol_endpoint(self) -> None:
        row = self.protocol_endpoint_table.currentRow()
        if 0 <= row < len(self._protocol_endpoint_entries):
            self._protocol_endpoint_entries.pop(row)
            self._refresh_protocol_endpoint_table()

    def _prefill_protocol_endpoints(self, silent: bool = False) -> bool:
        """Persist the selected reference, map supported endpoints, and refresh the table."""
        case = self.controller.case
        source = self.tps_csv.text().strip()
        if self._loading_configuration:
            return False
        if not case:
            message = "Import or open an ASCEND case before mapping Eclipse endpoints."
            self.eclipse_import_status.setText(message)
            if not silent:
                QMessageBox.critical(self, "ASCEND Eclipse endpoint mapping", message)
            return False
        if not source:
            message = "No Eclipse DVH reference selected. Select a CSV/TXT file or Eclipse export folder on Import."
            self.eclipse_import_status.setText(message)
            if not silent:
                QMessageBox.critical(self, "ASCEND Eclipse endpoint mapping", message)
            return False
        try:
            configuration = CaseConfiguration.from_dict(case.configuration.to_dict())
            configuration.tps_metrics_csv = source
            configuration.protocol_native_endpoints = [dict(item) for item in self._protocol_endpoint_entries]
            self.controller.configure(configuration)

            # Eclipse text exports do not carry ASCEND role semantics.  Mapping
            # them before the case roles are saved classifies every structure as
            # supporting/unmapped and previously produced a misleading success
            # message saying that zero endpoints were mapped.
            staged_roles = self._staged_structure_roles()
            saved_roles = case.configuration.structure_roles
            if self._reference_needs_case_role_mapping(source) and (
                not self._has_eclipse_target_roles(saved_roles)
                or (self._has_eclipse_target_roles(staged_roles) and staged_roles != saved_roles)
            ):
                self._pending_eclipse_reference = source
                message = (
                    "Eclipse reference saved. Endpoint mapping is pending. "
                    "Assign at least one target role (GTV, T_L, VTV_H, or VTV_L), then save the structure mappings."
                    if not self._has_eclipse_target_roles(staged_roles)
                    else "Eclipse reference saved. Endpoint mapping is pending until the displayed structure-role changes are saved."
                )
                self.eclipse_import_status.setText(message)
                self._layer1_rasterisation_entries = [
                    {
                        "rtstruct_sop_instance_uid": item["rtstruct_sop_instance_uid"],
                        "roi_number": item["roi_number"],
                    }
                    for item in case.configuration.dvh_verified_rois
                ]
                self._load_role_options()
                if not silent:
                    QMessageBox.information(self, "ASCEND Eclipse reference", message)
                return False

            suggestions = self.controller.prefill_eclipse_endpoints()
            self._protocol_endpoint_entries = [dict(item) for item in case.configuration.protocol_native_endpoints]
            self._refresh_protocol_endpoint_table()
            summary = case.configuration.eclipse_endpoint_prefill
            supplied = int(summary.get("supplied_record_count", 0))
            added = int(summary.get("added_endpoint_count", 0))
            if suggestions:
                message = (
                    f"Imported {supplied} Eclipse record(s). Mapped {len(suggestions)} supported endpoint "
                    f"definition(s); {added} protocol endpoint(s) added. Existing selections were retained."
                )
            else:
                message = (
                    f"Imported {supplied} Eclipse record(s), but none mapped to a supported protocol endpoint. "
                    "Verify the saved target-role names and export Dxx, Vxx%Rx, or VxxGy endpoints."
                )
            if not silent:
                dialog = QMessageBox.information if suggestions else QMessageBox.warning
                dialog(self, "ASCEND Eclipse reference", message)
            self.eclipse_import_status.setText(message)
            self._pending_eclipse_reference = None
            self._layer1_rasterisation_entries = [
                {
                    "rtstruct_sop_instance_uid": item["rtstruct_sop_instance_uid"],
                    "roi_number": item["roi_number"],
                }
                for item in case.configuration.dvh_verified_rois
            ]
            self._load_role_options()
            self._prefill_oar_geometry(eclipse_only=True)
            return True
        except Exception as exc:
            self.eclipse_import_status.setText(f"Eclipse endpoint mapping failed: {exc}")
            self.activity.setText("BLOCKED")
            if not silent:
                QMessageBox.critical(self, "ASCEND Eclipse endpoint mapping", str(exc))
            return False

    def _add_treatment_component(self) -> None:
        """Validate and stage one structured treatment component without JSON entry."""
        component_id = self.component_id.text().strip()
        if not component_id:
            QMessageBox.critical(self, "ASCEND treatment context", "Component ID is required.")
            return
        try:
            component = TreatmentComponent(
                component_id=component_id,
                component_type=str(self.component_type.currentText()),
                dose_object_uid=self.component_dose_uid.text().strip() or None,
                plan_uid=self.component_plan_uid.text().strip() or None,
                fraction_count=self._integer(self.component_fractions.text()),
                prescription_gy=self._number(self.component_prescription.text()),
                rx_low_gy=self._number(self.component_rx_low.text()),
                rx_high_gy=self._number(self.component_rx_high.text()),
                source="user_supplied",
                start_time=self.component_start.text().strip() or None,
                end_time=self.component_end.text().strip() or None,
                preceding_gap_days=self._number(self.component_gap.text()),
                geometry_id=self.component_geometry.text().strip() or None,
                geometry_hash=self.component_geometry.text().strip() or None,
                prescription_source=self.component_prescription_source.text().strip() or None,
                provenance={
                    "entry_method": "qt_structured_treatment_component_editor",
                    "implicit_registration": False,
                    "implicit_dose_warping": False,
                },
            )
        except ValueError as exc:
            QMessageBox.critical(self, "ASCEND treatment context", str(exc))
            return
        self._treatment_component_entries = [
            item for item in self._treatment_component_entries if str(item.get("component_id")) != component_id
        ] + [component.to_dict()]
        self._refresh_treatment_component_table(select_component_id=component_id)
        self.footer_stage.setText("Treatment component staged; save configuration to apply")

    def _remove_treatment_component(self) -> None:
        """Remove the selected staged treatment component."""
        row = self.treatment_component_table.currentRow()
        if row < 0 or row >= len(self._treatment_component_entries):
            QMessageBox.information(self, "ASCEND treatment context", "Select a treatment component row to remove.")
            return
        del self._treatment_component_entries[row]
        self._refresh_treatment_component_table()
        self.footer_stage.setText("Treatment component removed; save configuration to apply")

    def _refresh_treatment_component_table(self, select_component_id: str | None = None) -> None:
        """Render staged component records and keep analysis selection identity stable."""
        selected = select_component_id or self.analysis_component.currentData()
        rows: list[list[Any]] = []
        for raw in self._treatment_component_entries:
            try:
                item = TreatmentComponent.from_dict(raw)
                rows.append(
                    [
                        item.component_id,
                        item.component_type,
                        item.prescription_gy,
                        item.fraction_count,
                        item.dose_per_fraction_gy,
                        item.rx_low_gy,
                        item.rx_high_gy,
                        item.start_time,
                        item.end_time,
                        item.preceding_gap_days,
                        item.prescription_source or item.source,
                        item.dose_object_uid,
                        item.geometry_hash or item.geometry_id,
                    ]
                )
            except (TypeError, ValueError) as exc:
                rows.append([raw.get("component_id", "—"), "INVALID", "—", "—", "—", "—", "—", "—", "—", "—", str(exc), "—", "—"])
        _set_table(self.treatment_component_table, rows, "No treatment components configured.")
        self.analysis_component.blockSignals(True)
        self.analysis_component.clear()
        self.analysis_component.addItem("No component selected", None)
        for raw in self._treatment_component_entries:
            component_id = str(raw.get("component_id") or "")
            if component_id:
                self.analysis_component.addItem(component_id, component_id)
        index = self.analysis_component.findData(selected)
        self.analysis_component.setCurrentIndex(index if index >= 0 else 0)
        self.analysis_component.blockSignals(False)
        if hasattr(self, "layer31_source_component"):
            retained = self.layer31_source_component.currentData()
            self.layer31_source_component.clear()
            self.layer31_source_component.addItem("Use current validated Layer 1 plan", None)
            for raw in self._treatment_component_entries:
                component_id = str(raw.get("component_id") or "")
                if component_id:
                    self.layer31_source_component.addItem(component_id, component_id)
            source_index = self.layer31_source_component.findData(retained)
            self.layer31_source_component.setCurrentIndex(source_index if source_index >= 0 else 0)

    def _save_configuration(self, silent: bool = False) -> bool:
        case = self.controller.case
        if not case:
            if not silent:
                QMessageBox.critical(self, "ASCEND", "Import a case first.")
            return False
        try:
            roles = self._staged_structure_roles()
            fractions = self._integer(self.fractions.currentText())
            previous = case.configuration
            layer32_parameters = resolved_parameters(previous.layer32_parameters)
            layer32_parameters.update(
                {
                    "nonlocal_scaling": self._number(self.layer32_scaling.text()),
                    "pde_steps": self._integer(self.layer32_steps.text()),
                    "pde_dt": self._number(self.layer32_dt.text()),
                    "model_grid_target_spacing_mm": self._number(self.layer32_grid_spacing.text()),
                    "model_domain_margin_mm": self._number(self.layer32_margin.text()),
                }
            )
            layer32_parameters = resolved_parameters(layer32_parameters)
            protocol_native_endpoints = [dict(item) for item in self._protocol_endpoint_entries]
            layer1_rasterisation_rois = [
                {"rtstruct_sop_instance_uid": str(item["rtstruct_sop_instance_uid"]), "roi_number": int(item["roi_number"])}
                for item in self._layer1_rasterisation_entries
            ]
            layer21_oar_geometry_rois = [
                {
                    "rtstruct_sop_instance_uid": str(item["rtstruct_sop_instance_uid"]),
                    "roi_number": int(item["roi_number"]),
                    "classification": str(item["classification"]),
                }
                for item in self._oar_entries
            ]
            layer31c_oar_rois = [
                {"rtstruct_sop_instance_uid": str(item["rtstruct_sop_instance_uid"]), "roi_number": int(item["roi_number"])}
                for item in self._layer31c_oar_entries
            ]
            tumour_scenario = self.layer31_tumour_scenario.currentText()
            normal_scenario = self.layer31_normal_scenario.currentText()
            tumour_parameters = self._layer31_kinetic_parameters(self.layer31_tumour_kinetics, tumour_scenario, "tumour")
            normal_parameters = self._layer31_kinetic_parameters(self.layer31_normal_kinetics, normal_scenario, "normal_cell")
            warning_mode = str(self.layer31_high_dose_criterion.currentData())
            warning_threshold = None
            warning_source = None
            if warning_mode != "not_configured":
                warning_threshold = self._number(self.layer31_high_dose_threshold.text())
                warning_source = self.layer31_high_dose_source.text().strip()
                if warning_threshold is None or warning_threshold <= 0:
                    raise ValueError("The Layer 3.1A warning criterion requires a positive Gy/fraction threshold.")
                if warning_mode == "literature_sensitivity" and not warning_source:
                    raise ValueError("A literature-defined warning criterion requires a citation and reproduction context.")
                if not warning_source:
                    warning_source = "user_defined_operational_warning_threshold_not_biological_cutoff"
            visualisation_settings = dict(previous.layer31_visualisation_settings)
            visualisation_settings["lq_high_dose_warning_criterion"] = {
                "mode": warning_mode,
                "threshold_gy_per_fraction": warning_threshold,
                "source": warning_source,
                "formalism_switching": False,
            }
            comparator_schedule: dict[str, Any] = {}
            if self.layer31_tr_enabled.isChecked():
                comparator_count = self._integer(self.layer31_tr_fraction_count.text())
                comparator_time = self._number(self.layer31_tr_delivery_time.text())
                if comparator_count is None or comparator_count <= 0 or comparator_time is None or comparator_time < 0:
                    raise ValueError("The therapeutic-ratio comparator requires positive fractions and a non-negative delivery time.")
                comparator_schedule = {
                    "schedule_type": "explicit_matched_uniform_schedule",
                    "fraction_count": comparator_count,
                    "delivery_time": comparator_time,
                    "source": self.layer31_tr_source.text().strip() or "explicit_case_configuration",
                }
            tcp_parameters: dict[str, Any] = {}
            tcp_density_text = self.layer31_tcp_density.text().strip()
            if tcp_density_text or self.layer31_tcp_source.text().strip() or self.layer31_tcp_parameter_set.text().strip():
                tcp_density = self._number(tcp_density_text)
                if tcp_density is None or tcp_density <= 0:
                    raise ValueError("Layer 3.1D requires a positive clonogen density.")
                tcp_parameters = {
                    "clonogen_density_per_cm3": tcp_density,
                    "units": self.layer31_tcp_units.currentText(),
                    "source": self.layer31_tcp_source.text().strip(),
                    "parameter_set_id": self.layer31_tcp_parameter_set.text().strip(),
                    "repopulation_enabled": self.layer31_tcp_repopulation.isChecked(),
                    "sensitivity_enabled": self.layer31_tcp_sensitivity.isChecked(),
                }
                if self.layer31_tcp_repopulation.isChecked():
                    tcp_parameters.update(
                        {
                            "overall_treatment_time_days": self._number(self.layer31_tcp_overall_time.text()),
                            "kickoff_time_days": self._number(self.layer31_tcp_kickoff.text()),
                            "potential_doubling_time_days": self._number(self.layer31_tcp_doubling.text()),
                        }
                    )
                if self.layer31_tcp_sensitivity.isChecked():
                    try:
                        tcp_parameters["sensitivity_clonogen_density_values"] = [
                            float(item.strip()) for item in self.layer31_tcp_sensitivity_values.text().split(",") if item.strip()
                        ]
                    except ValueError as exc:
                        raise ValueError("Layer 3.1D sensitivity densities must be comma-separated numbers.") from exc
            configuration = CaseConfiguration(
                treatment_delivery_mode=self.mode.currentText(),
                treatment_approach=str(self.treatment_approach.currentData()),
                dose_context=self.dose_context.currentText(),
                prescriptions={
                    "Rx_L": Prescription(self._number(self.rx_l.currentText()), fractions, self.rx_l_source.currentText()),
                    "Rx_H": Prescription(self._number(self.rx_h.currentText()), fractions, self.rx_h_source.currentText()),
                },
                fractionation={"fractions": fractions} if fractions is not None else {},
                structure_roles=roles,
                validation_structures=[item.strip() for item in self.validation_structures.text().split(",") if item.strip()],
                protocol_id=self.protocol_id.text().strip() or None,
                protocol_context={
                    "prescriptions_confirmed": self.confirm_prescriptions.isChecked(),
                    "roles_confirmed": self.confirm_roles.isChecked(),
                    "dose_object_confirmed": self.confirm_dose.isChecked(),
                    "valley_confirmed": self.confirm_valley.isChecked(),
                },
                protocol_native_endpoints=protocol_native_endpoints,
                layer1_rasterisation_rois=layer1_rasterisation_rois,
                layer21_oar_geometry_rois=layer21_oar_geometry_rois,
                layer31c_oar_rois=layer31c_oar_rois,
                oar_structures=[],
                equal_prescriptions_protocol_confirmed=self.confirm_equal.isChecked(),
                partial_volume_only=self.mode.currentText() == "partial_volume_lrt",
                valley_definition_source=previous.valley_definition_source,
                valley_overlap_tolerance_pct=previous.valley_overlap_tolerance_pct,
                tps_metrics_csv=self.tps_csv.text().strip() or None,
                treatment_components=[dict(item) for item in self._treatment_component_entries],
                selected_treatment_component_id=self.analysis_component.currentData(),
                prescription_context=previous.prescription_context,
                supporting_outputs_enabled=self.supporting_outputs_enabled.isChecked(),
                supporting_output_categories=[
                    category for category, checkbox in self.supporting_output_checks.items() if checkbox.isChecked()
                ],
                pdf_report_options=[
                    option for option, checkbox in self.pdf_report_checks.items() if checkbox.isChecked()
                ],
                layer31_roi_parameters=[dict(item) for item in self._layer31_roi_entries],
                layer31_component_sources=[dict(item) for item in self._layer31_component_entries],
                layer31_lq_high_dose_warning_gy_per_fraction=warning_threshold,
                layer31_mlq_tumour_parameters=tumour_parameters,
                layer31_mlq_normal_parameters=normal_parameters,
                layer31_tumour_scenario=None if tumour_scenario == "Not configured" else tumour_scenario,
                layer31_normal_scenario=None if normal_scenario == "Not configured" else normal_scenario,
                layer31_tr_reference_schedule=comparator_schedule,
                layer31_paired_course_reference_result_path=self.layer31_paired_course_path.text().strip() or None,
                layer31_tcp_parameters=tcp_parameters,
                layer31_visualisation_settings=visualisation_settings,
                layer31_materialise_full_maps_on_run=previous.layer31_materialise_full_maps_on_run,
                layer31_sensitivity_sweep_enabled=previous.layer31_sensitivity_sweep_enabled,
                layer31_sensitivity_sweep_mode=previous.layer31_sensitivity_sweep_mode,
                layer31_sensitivity_sweep_start=previous.layer31_sensitivity_sweep_start,
                layer31_sensitivity_sweep_end=previous.layer31_sensitivity_sweep_end,
                layer31_sensitivity_sweep_custom_values=previous.layer31_sensitivity_sweep_custom_values,
                layer32_enabled=self.layer32_enabled.isChecked(),
                layer32_parameters=layer32_parameters,
                eclipse_endpoint_prefill=previous.eclipse_endpoint_prefill,
            )
            self.controller.configure(configuration)
            pending_reference = self._pending_eclipse_reference
            mapping_deferred = bool(
                pending_reference
                and configuration.tps_metrics_csv
                and self._reference_needs_case_role_mapping(configuration.tps_metrics_csv)
                and not self._has_eclipse_target_roles(roles)
            )
            if pending_reference and not mapping_deferred:
                self._prefill_protocol_endpoints(silent=silent)
            self.activity.setText("Configuration saved")
            self.refresh()
            if mapping_deferred:
                self._pending_eclipse_reference = pending_reference
                self.eclipse_import_status.setText(
                    "Eclipse reference saved. Endpoint mapping remains pending until at least one target role is assigned and saved."
                )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "ASCEND configuration", str(exc))
            return False

    def _load_configuration(self) -> None:
        case = self.controller.case
        if not case:
            return
        config = case.configuration
        self._loading_configuration = True
        approach_index = self.treatment_approach.findData(config.treatment_approach)
        self.treatment_approach.setCurrentIndex(approach_index if approach_index >= 0 else self.treatment_approach.findData("UNKNOWN"))
        self.mode.setCurrentText(config.treatment_delivery_mode)
        self.dose_context.setCurrentText(config.dose_context)
        evidence = case.provenance.get("dicom_configuration_prefill", {})
        prescription_values = sorted({str(item.get("dose_gy")) for item in evidence.get("prescription_candidates", [])})
        fraction_values = sorted(
            {str(item.get("fractions")) for item in evidence.get("fraction_candidates", [])}, key=lambda value: int(value)
        )
        for widget, selected in (
            (self.rx_l, "" if config.prescriptions["Rx_L"].gy is None else str(config.prescriptions["Rx_L"].gy)),
            (self.rx_h, "" if config.prescriptions["Rx_H"].gy is None else str(config.prescriptions["Rx_H"].gy)),
        ):
            widget.clear()
            widget.addItem("")
            widget.addItems(prescription_values)
            widget.setCurrentText(selected)
        self.rx_l_source.setCurrentText(config.prescriptions["Rx_L"].source)
        self.rx_h_source.setCurrentText(config.prescriptions["Rx_H"].source)
        fractions = config.fractionation.get("fractions") or config.prescriptions["Rx_L"].fractions
        self.fractions.clear()
        self.fractions.addItem("")
        self.fractions.addItems(fraction_values)
        self.fractions.setCurrentText("" if fractions is None else str(fractions))
        self.dicom_prefill_summary.setText(
            f"RTPLAN {evidence.get('plan_label') or '—'}  ·  Dose {evidence.get('dose_summation_type') or '—'}  ·  "
            f"{evidence.get('beam_count', 0)} beam(s)  ·  "
            f"Status: {str(evidence.get('status', 'not available')).replace('_', ' ')}"
        )
        fraction_candidates = evidence.get("fraction_candidates", [])
        prescription_candidates = evidence.get("prescription_candidates", [])
        _set_table(
            self.dicom_fraction_candidates,
            [
                [
                    item.get("fraction_group_number"),
                    item.get("fractions"),
                    item.get("referenced_beam_count"),
                    item.get("source"),
                ]
                for item in fraction_candidates
            ],
            "No RTPLAN fractionation candidates were found.",
        )
        _set_table(
            self.dicom_prescription_candidates,
            [
                [
                    item.get("dose_reference_number"),
                    item.get("dose_gy"),
                    item.get("label") or "Dose reference",
                    item.get("referenced_roi_number") if item.get("referenced_roi_number") is not None else "—",
                    " / ".join(
                        filter(
                            None,
                            (
                                str(item.get("dose_reference_type") or ""),
                                str(item.get("dose_reference_structure_type") or ""),
                            ),
                        )
                    )
                    or "—",
                    item.get("source"),
                ]
                for item in prescription_candidates
            ],
            "No RTPLAN prescription candidates were found.",
        )
        warnings = [str(item).replace("_", " ") for item in evidence.get("warnings", [])]
        self.dicom_candidate_warnings.set_messages(warnings)
        self.protocol_id.setText(config.protocol_id or "")
        self._treatment_component_entries = [dict(item) for item in config.treatment_components]
        self._refresh_treatment_component_table(config.selected_treatment_component_id)
        self._protocol_endpoint_entries = [dict(item) for item in config.protocol_native_endpoints]
        self._refresh_protocol_endpoint_table()
        self._layer1_rasterisation_entries = [dict(item) for item in config.layer1_rasterisation_rois]
        self._oar_entries = [dict(item) for item in config.layer21_oar_geometry_rois]
        self._layer31c_oar_entries = [dict(item) for item in config.layer31c_oar_rois]
        self._refresh_layer1_rasterisation_table()
        self._refresh_oar_table()
        self._refresh_layer31c_oar_table()
        self.validation_structures.setText(
            ", ".join(str(item.get("display_name") or item.get("roi_number")) for item in config.validation_structures)
        )
        retained_reference = config.tps_metrics_csv or self._pending_eclipse_reference or ""
        self.tps_csv.setText(retained_reference)
        self.eclipse_import_status.setText(
            f"Selected Eclipse reference: {retained_reference}" if retained_reference else "No Eclipse DVH reference selected."
        )
        context = config.protocol_context
        self.confirm_prescriptions.setChecked(bool(context.get("prescriptions_confirmed")))
        self.confirm_roles.setChecked(bool(context.get("roles_confirmed")))
        self.confirm_dose.setChecked(bool(context.get("dose_object_confirmed")))
        self.confirm_valley.setChecked(bool(context.get("valley_confirmed")))
        self.confirm_equal.setChecked(config.equal_prescriptions_protocol_confirmed)
        self.supporting_outputs_enabled.setChecked(config.supporting_outputs_enabled)
        for category, checkbox in self.supporting_output_checks.items():
            checkbox.setChecked(category in config.supporting_output_categories)
        for option, checkbox in self.pdf_report_checks.items():
            checkbox.setChecked(option in config.pdf_report_options)
        self._toggle_supporting_output_controls(config.supporting_outputs_enabled)
        self.layer32_enabled.setChecked(config.layer32_enabled)
        self._update_layer32_enabled_controls(config.layer32_enabled)
        layer32 = resolved_parameters(config.layer32_parameters)
        self.layer32_scaling.setText(str(layer32["nonlocal_scaling"]))
        self.layer32_steps.setText(str(layer32["pde_steps"]))
        self.layer32_dt.setText(str(layer32["pde_dt"]))
        self.layer32_grid_spacing.setText(str(layer32["model_grid_target_spacing_mm"]))
        self.layer32_margin.setText(str(layer32["model_domain_margin_mm"]))
        self._layer31_roi_entries = [dict(item) for item in config.layer31_roi_parameters]
        self._refresh_layer31_roi_table()
        self._layer31_component_entries = [dict(item) for item in config.layer31_component_sources]
        self._refresh_layer31_component_table()
        criterion = dict(config.layer31_visualisation_settings.get("lq_high_dose_warning_criterion") or {})
        criterion_mode = str(
            criterion.get("mode")
            or ("custom_operational" if config.layer31_lq_high_dose_warning_gy_per_fraction is not None else "not_configured")
        )
        criterion_index = self.layer31_high_dose_criterion.findData(criterion_mode)
        self.layer31_high_dose_criterion.setCurrentIndex(max(criterion_index, 0))
        self.layer31_high_dose_threshold.setText(
            "" if config.layer31_lq_high_dose_warning_gy_per_fraction is None else str(config.layer31_lq_high_dose_warning_gy_per_fraction)
        )
        self.layer31_high_dose_source.setText(str(criterion.get("source") or ""))
        self._update_layer31_high_dose_controls()
        self.layer31_tumour_scenario.setCurrentText(config.layer31_tumour_scenario or "Not configured")
        self.layer31_normal_scenario.setCurrentText(config.layer31_normal_scenario or "Not configured")
        for tissue, editor, parameters in (
            ("tumour", self.layer31_tumour_kinetics, config.layer31_mlq_tumour_parameters),
            ("normal_cell", self.layer31_normal_kinetics, config.layer31_mlq_normal_parameters),
        ):
            parameter_set_id = str(parameters.get("parameter_set_id") or "")
            scenario_only_normal = (
                tissue == "normal_cell"
                and bool(parameters)
                and parameter_set_id.endswith("-scenario-only")
                and any(parameters.get(key) in (None, "") for key in ("delta_per_gy", "repair_half_time"))
            )
            known = (
                "rss_grid_reference"
                if any(marker in parameter_set_id for marker in ("zhang-grid-2022", "rss-grid")) or scenario_only_normal
                else ("custom" if parameters else "not_configured")
            )
            preset_index = editor["kinetic_preset"].findData(known)
            editor["kinetic_preset"].setCurrentIndex(max(preset_index, 0))
            self._update_layer31_model_preset(tissue)
            for key in ("parameter_set_id", "parameter_source", "delta_per_gy", "repair_half_time", "treatment_delivery_time"):
                if scenario_only_normal and key in ("parameter_set_id", "parameter_source", "delta_per_gy", "repair_half_time"):
                    continue
                value = parameters.get(key)
                editor[key].setText("" if value is None else str(value))
            editor["time_unit"].setCurrentText(str(parameters.get("time_unit") or "minutes"))
            delivery_source = str(parameters.get("delivery_time_source") or "manual_case_configuration")
            if delivery_source == "explicit_case_configuration":
                delivery_source = "manual_case_configuration"
            source_index = editor["delivery_time_source"].findData(delivery_source)
            editor["delivery_time_source"].setCurrentIndex(max(source_index, 0))
            if tissue == "tumour":
                override = bool(parameters.get("scenario_parameter_override"))
                editor["scenario_override"].setChecked(override)
                editor["scenario_override_source"].setText(str(parameters.get("scenario_parameter_source") or ""))
                if override:
                    for key in ("alpha_per_gy", "beta_per_gy2"):
                        value = parameters.get(key)
                        editor[key].setText("" if value is None else str(value))
                    self._update_layer31_override_derivatives()
            self._update_layer31_delivery_time_source(tissue)
        schedule = config.layer31_tr_reference_schedule
        self.layer31_tr_enabled.setChecked(bool(schedule))
        self.layer31_tr_fraction_count.setText("" if not schedule.get("fraction_count") else str(schedule["fraction_count"]))
        self.layer31_tr_delivery_time.setText("" if schedule.get("delivery_time") is None else str(schedule["delivery_time"]))
        self.layer31_tr_source.setText(str(schedule.get("source") or ""))
        self.layer31_paired_course_path.setText(config.layer31_paired_course_reference_result_path or "")
        tcp = config.layer31_tcp_parameters
        self.layer31_tcp_density.setText("" if tcp.get("clonogen_density_per_cm3") is None else str(tcp["clonogen_density_per_cm3"]))
        self.layer31_tcp_units.setCurrentText(str(tcp.get("units") or "clonogens/cm3"))
        self.layer31_tcp_source.setText(str(tcp.get("source") or ""))
        self.layer31_tcp_parameter_set.setText(str(tcp.get("parameter_set_id") or ""))
        self.layer31_tcp_repopulation.setChecked(bool(tcp.get("repopulation_enabled")))
        self.layer31_tcp_overall_time.setText(
            "" if tcp.get("overall_treatment_time_days") is None else str(tcp["overall_treatment_time_days"])
        )
        self.layer31_tcp_kickoff.setText("" if tcp.get("kickoff_time_days") is None else str(tcp["kickoff_time_days"]))
        self.layer31_tcp_doubling.setText(
            "" if tcp.get("potential_doubling_time_days") is None else str(tcp["potential_doubling_time_days"])
        )
        self.layer31_tcp_sensitivity.setChecked(bool(tcp.get("sensitivity_enabled")))
        self.layer31_tcp_sensitivity_values.setText(",".join(str(item) for item in tcp.get("sensitivity_clonogen_density_values", [])))
        self._update_layer31_tcp_controls()
        self._update_layer31_tr_controls(bool(schedule))
        for role, widget in self.role_widgets.items():
            value = config.structure_roles.get(role, "")
            text = ", ".join(value) if isinstance(value, list) else value
            if isinstance(widget, QLineEdit):
                widget.setText(text)
            else:
                widget.setCurrentText(text)
        self._loading_configuration = False

    def _load_role_options(self) -> None:
        case = self.controller.case
        if not case or not case.selected_objects.get("rtstruct"):
            return
        rtstruct_path = Path(str(case.selected_objects["rtstruct"]))
        if not rtstruct_path.is_file():
            return
        dataset = pydicom.dcmread(str(rtstruct_path), stop_before_pixels=True)
        rtstruct_uid = str(getattr(dataset, "SOPInstanceUID", ""))
        eligible = [
            dict(item) for item in case.configuration.dvh_verified_rois
            if item.get("dvh_verification_status") == "verified"
            and str(item.get("rtstruct_sop_instance_uid")) == rtstruct_uid
        ]
        names = [str(item.get("display_name") or item.get("dvh_structure_name") or "") for item in eligible]
        for role, widget in self.role_widgets.items():
            if isinstance(widget, QComboBox):
                current = widget.currentText()
                widget.clear()
                widget.addItem("")
                widget.addItems(names)
                widget.setCurrentText(current if current in names else "")
        current_oar_identity = self.oar_roi_selector.currentData()
        current_layer31c_identity = self.layer31c_oar_selector.currentData()
        current_layer31_assignment = self.layer31_roi_selector.currentData()
        self.layer1_rasterisation_roi_selector.clear()
        self.layer1_rasterisation_roi_selector.addItem("Select a DVH-verified RTSTRUCT ROI…", None)
        self.oar_roi_selector.clear()
        self.oar_roi_selector.addItem("Select a DVH-verified RTSTRUCT ROI…", None)
        self.layer31c_oar_selector.clear()
        self.layer31c_oar_selector.addItem("Select a DVH-verified RTSTRUCT OAR…", None)
        self.layer31_roi_selector.clear()
        self.layer31_roi_selector.addItem("Select a DVH-verified RTSTRUCT ROI…", None)
        for item in eligible:
            name = str(item.get("display_name") or item.get("dvh_structure_name") or "")
            identity = {
                "rtstruct_sop_instance_uid": rtstruct_uid,
                "roi_number": int(item["roi_number"]),
            }
            candidate = {**identity, "display_name": name}
            label = f"{name}  ·  ROI {identity['roi_number']}"
            self.layer1_rasterisation_roi_selector.addItem(
                label,
                candidate,
            )
            self.oar_roi_selector.addItem(label, candidate)
            self.layer31c_oar_selector.addItem(label, candidate)
            self.layer31_roi_selector.addItem(label, {
                "name": name, "display_name": name, "roi_identity": identity,
            })
        self._refresh_layer1_rasterisation_table()
        self._refresh_oar_table()
        self._refresh_layer31c_oar_table()
        self._refresh_layer31_roi_table()
        self._restore_identity_selection(self.oar_roi_selector, current_oar_identity)
        self._restore_identity_selection(self.layer31c_oar_selector, current_layer31c_identity)
        current_layer31_identity = (
            current_layer31_assignment.get("roi_identity")
            if isinstance(current_layer31_assignment, dict)
            else None
        )
        self._restore_identity_selection(
            self.layer31_roi_selector,
            current_layer31_identity,
            nested_key="roi_identity",
        )

    def _restore_identity_selection(
        self,
        selector: QComboBox,
        identity: Any,
        *,
        nested_key: str | None = None,
    ) -> None:
        if not isinstance(identity, dict):
            return
        current_key = self._oar_identity_key(identity)
        for index in range(selector.count()):
            candidate = selector.itemData(index)
            if nested_key and isinstance(candidate, dict):
                candidate = candidate.get(nested_key)
            if isinstance(candidate, dict) and self._oar_identity_key(candidate) == current_key:
                selector.setCurrentIndex(index)
                return

    @staticmethod
    def _oar_identity_key(identity: dict[str, Any]) -> tuple[str, int]:
        return (
            str(identity.get("rtstruct_sop_instance_uid", "")),
            int(identity.get("roi_number", -1)),
        )

    def _refresh_oar_table(self) -> None:
        classification_labels = {
            "containing_organ": "Containing organ",
            "target_excluded_oar": "Target-excluded OAR",
            "separate_critical_oar": "Separate critical OAR",
            "internal_target_structure": "Internal target structure",
        }
        _set_table(
            self.oar_table,
            [
                [
                    self._roi_display_name(item),
                    item.get("roi_number"),
                    classification_labels.get(str(item.get("classification")), item.get("classification")),
                    "RTSTRUCT UID + ROI number",
                ]
                for item in self._oar_entries
            ],
            "No optional OAR geometry structures selected.",
        )

    def _roi_display_name(self, identity: dict[str, Any]) -> str:
        key = self._oar_identity_key(identity)
        case = self.controller.case
        inventory = ((case.layer1.result or {}).get("manifest", {}).get("roi_inventory", [])) if case else []
        for item in inventory:
            if isinstance(item.get("roi_identity"), dict) and self._oar_identity_key(item["roi_identity"]) == key:
                return str(item.get("original_name") or item.get("canonical_mapping") or f"ROI {key[1]}")
        for selector in (
            self.layer1_rasterisation_roi_selector, self.oar_roi_selector, self.layer31c_oar_selector,
        ):
            for index in range(selector.count()):
                candidate = selector.itemData(index)
                if isinstance(candidate, dict) and self._oar_identity_key(candidate) == key:
                    return str(candidate.get("display_name") or f"ROI {key[1]}")
        return f"ROI {key[1]}"

    def _refresh_layer1_rasterisation_table(self) -> None:
        _set_table(
            self.layer1_rasterisation_table,
            [[self._roi_display_name(item), item.get("roi_number"), "RTSTRUCT UID + ROI number"] for item in self._layer1_rasterisation_entries],
            "No DVH-verified Layer 1 rasterisation ROIs are available.",
        )

    def _refresh_layer31c_oar_table(self) -> None:
        _set_table(
            self.layer31c_oar_table,
            [[self._roi_display_name(item), item.get("roi_number"), "RTSTRUCT UID + ROI number"] for item in self._layer31c_oar_entries],
            "No Layer 3.1C analytical OARs selected; 3.1C will be NOT_ASSESSED.",
        )

    def _add_layer1_rasterisation_roi(self) -> None:
        selected = self.layer1_rasterisation_roi_selector.currentData()
        if not isinstance(selected, dict):
            QMessageBox.critical(self, "ASCEND Layer 1", "Select a DVH-verified RTSTRUCT ROI.")
            return
        identity = {"rtstruct_sop_instance_uid": str(selected["rtstruct_sop_instance_uid"]), "roi_number": int(selected["roi_number"])}
        key = self._oar_identity_key(identity)
        self._layer1_rasterisation_entries = [item for item in self._layer1_rasterisation_entries if self._oar_identity_key(item) != key]
        self._layer1_rasterisation_entries.append(identity)
        self._refresh_layer1_rasterisation_table()

    def _remove_layer1_rasterisation_roi(self) -> None:
        row = self.layer1_rasterisation_table.currentRow()
        if 0 <= row < len(self._layer1_rasterisation_entries):
            del self._layer1_rasterisation_entries[row]
            self._refresh_layer1_rasterisation_table()

    def _add_layer31c_oar(self) -> None:
        selected = self.layer31c_oar_selector.currentData()
        if not isinstance(selected, dict):
            QMessageBox.critical(self, "ASCEND Layer 3.1C", "Select a DVH-verified RTSTRUCT OAR.")
            return
        identity = {"rtstruct_sop_instance_uid": str(selected["rtstruct_sop_instance_uid"]), "roi_number": int(selected["roi_number"])}
        key = self._oar_identity_key(identity)
        self._layer31c_oar_entries = [item for item in self._layer31c_oar_entries if self._oar_identity_key(item) != key]
        self._layer31c_oar_entries.append(identity)
        self._refresh_layer31c_oar_table()

    def _remove_layer31c_oar(self) -> None:
        row = self.layer31c_oar_table.currentRow()
        if 0 <= row < len(self._layer31c_oar_entries):
            del self._layer31c_oar_entries[row]
            self._refresh_layer31c_oar_table()

    def _infer_geometry_classification(self, _index: int = -1) -> None:
        selected = self.oar_roi_selector.currentData()
        if not isinstance(selected, dict):
            return
        name = re.sub(r"[^A-Z0-9]+", "", str(selected.get("display_name", "")).upper())
        classification = "internal_target_structure" if name in {"ALLVERTICES", "ALLVALLEYS", "VTVH", "VTVL"} else None
        if classification:
            index = self.oar_classification_selector.findData(classification)
            if index >= 0:
                self.oar_classification_selector.setCurrentIndex(index)

    @staticmethod
    def _looks_like_oar(name: str) -> bool:
        normalised = re.sub(r"[^A-Z0-9]+", "", name.upper())
        tokens = (
            "HEART",
            "LUNG",
            "CORD",
            "ESOPH",
            "BOWEL",
            "KIDNEY",
            "LIVER",
            "STOMACH",
            "BLADDER",
            "RECTUM",
            "BRAINSTEM",
            "PAROTID",
            "OPTIC",
            "CHESTWALL",
            "SKIN",
        )
        return any(token in normalised for token in tokens)

    def _prefill_oar_geometry(self, _checked: bool = False, *, eclipse_only: bool = False) -> None:
        case = self.controller.case
        if not case:
            return
        eclipse_records = [
            item for item in case.configuration.eclipse_endpoint_prefill.get("supplied_records", []) if item.get("import_status") == "valid"
        ]
        eclipse_names = {str(item.get("roi_name") or "") for item in eclipse_records}
        eclipse_normalised = {re.sub(r"[^A-Z0-9]+", "", name.upper()) for name in eclipse_names}
        eclipse_roi_numbers = {int(item["roi_number"]) for item in eclipse_records if item.get("roi_number") is not None}
        configured_roles = case.configuration.structure_roles.values()
        target_names = {str(name) for value in configured_roles for name in (value if isinstance(value, list) else [value])}
        existing = {self._oar_identity_key(item) for item in self._oar_entries}
        added = 0
        for index in range(1, self.oar_roi_selector.count()):
            candidate = self.oar_roi_selector.itemData(index)
            if not isinstance(candidate, dict):
                continue
            name = str(candidate.get("display_name") or "")
            number = int(candidate.get("roi_number", -1))
            supplied_by_eclipse = re.sub(r"[^A-Z0-9]+", "", name.upper()) in eclipse_normalised or number in eclipse_roi_numbers
            if name in target_names:
                continue
            if eclipse_only and not supplied_by_eclipse:
                continue
            if not eclipse_only and not (self._looks_like_oar(name) or supplied_by_eclipse):
                continue
            key = self._oar_identity_key(candidate)
            if key in existing:
                continue
            self._oar_entries.append(
                {
                    "rtstruct_sop_instance_uid": str(candidate["rtstruct_sop_instance_uid"]),
                    "roi_number": number,
                    "classification": "separate_critical_oar",
                }
            )
            existing.add(key)
            added += 1
        self._refresh_oar_table()
        self.activity.setText(f"{added} GEOMETRY CANDIDATE(S) ADDED")

    def _add_or_update_oar(self) -> None:
        selected = self.oar_roi_selector.currentData()
        if not isinstance(selected, dict):
            QMessageBox.critical(self, "ASCEND OAR geometry", "Select a DVH-verified RTSTRUCT ROI.")
            return
        classification = self.oar_classification_selector.currentData()
        entry = {
            "rtstruct_sop_instance_uid": str(selected["rtstruct_sop_instance_uid"]),
            "roi_number": int(selected["roi_number"]),
            "classification": str(classification),
        }
        key = self._oar_identity_key(entry)
        retained = [item for item in self._oar_entries if self._oar_identity_key(item) != key]
        self._oar_entries = [*retained, entry]
        self._refresh_oar_table()
        self.activity.setText("OAR LIST EDITED")
        self.footer_stage.setText("Save mappings to apply OAR changes")

    def _remove_selected_oar(self) -> None:
        row = self.oar_table.currentRow()
        if row < 0 or row >= len(self._oar_entries):
            QMessageBox.information(self, "ASCEND OAR geometry", "Select an OAR row to remove.")
            return
        del self._oar_entries[row]
        self._refresh_oar_table()
        self.activity.setText("OAR LIST EDITED")
        self.footer_stage.setText("Save mappings to apply OAR changes")

    def _select_oar_table_row(self, row: int, _column: int) -> None:
        if row < 0 or row >= len(self._oar_entries):
            return
        entry = self._oar_entries[row]
        key = self._oar_identity_key(entry)
        for index in range(self.oar_roi_selector.count()):
            candidate = self.oar_roi_selector.itemData(index)
            if isinstance(candidate, dict) and self._oar_identity_key(candidate) == key:
                self.oar_roi_selector.setCurrentIndex(index)
                break
        classification_index = self.oar_classification_selector.findData(entry.get("classification"))
        if classification_index >= 0:
            self.oar_classification_selector.setCurrentIndex(classification_index)
