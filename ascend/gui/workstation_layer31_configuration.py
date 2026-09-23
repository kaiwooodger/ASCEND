"""Layer 3.1 configuration and viewer coordination for the workstation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ascend.gui.workstation_widgets import set_table as _set_table
from ascend.layer3.response.mlq import TUMOUR_SCENARIOS


class WorkstationLayer31Mixin:
    """Manage Layer 3.1 inputs and presentation without scientific calculation."""

    @staticmethod
    def _layer31_identity_key(identity: dict[str, Any]) -> tuple[str, int]:
        return str(identity.get("rtstruct_sop_instance_uid", "")), int(identity.get("roi_number", -1))

    def _focus_layer31_region(self, region_id: str) -> None:
        """Open the unified map and focus its validated regional mask."""
        self.layer31_tabs.setCurrentIndex(1)
        if self.layer31_viewer is not None:
            self.layer31_viewer._focus_region(region_id)

    def _refresh_layer31_roi_table(self) -> None:
        by_identity: dict[tuple[str, int], str] = {}
        for index in range(self.layer31_roi_selector.count()):
            value = self.layer31_roi_selector.itemData(index)
            if isinstance(value, dict) and value.get("roi_identity"):
                by_identity[self._layer31_identity_key(value["roi_identity"])] = str(
                    value.get("name") or value.get("display_name") or "ROI"
                )
        rows = []
        for item in self._layer31_roi_entries:
            identity = item.get("roi_identity", {})
            rows.append(
                [
                    by_identity.get(self._layer31_identity_key(identity), item.get("roi_name") or "Identity-bound ROI"),
                    identity.get("roi_number"),
                    item.get("alpha_beta_gy"),
                    item.get("parameter_source_type"),
                    item.get("parameter_source"),
                    item.get("parameter_set_version"),
                ]
            )
        _set_table(self.layer31_roi_table, rows, "No tissue parameter assignments configured.")

    def _refresh_layer31_component_table(self) -> None:
        _set_table(
            self.layer31_component_table,
            [
                [
                    item.get("component_id"),
                    item.get("fraction_dose_model"),
                    "\n".join(item.get("fraction_layer1_result_paths", []))
                    if item.get("fraction_layer1_result_paths")
                    else item.get("layer1_result_path"),
                ]
                for item in self._layer31_component_entries
            ],
            "The current validated Layer 1 plan will be used.",
        )

    def _add_layer31_component_source(self) -> None:
        component_id = self.layer31_source_component.currentData()
        if not component_id:
            QMessageBox.information(
                self, "ASCEND Layer 3.1", "Add treatment components on Case configuration before assigning multiple validated dose sources."
            )
            return
        model = str(self.layer31_source_model.currentData())
        case = self.controller.case
        start = str(case.root if case else Path.cwd())
        if model == "explicit_fraction_doses":
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Select one validated layer1_result.json per delivered fraction",
                start,
                "Layer 1 results (layer1_result.json);;JSON files (*.json)",
            )
            if not paths:
                return
            record = {
                "component_id": str(component_id),
                "fraction_dose_model": model,
                "fraction_layer1_result_paths": paths,
                "fraction_count": len(paths),
            }
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select validated cumulative layer1_result.json", start, "Layer 1 result (layer1_result.json);;JSON files (*.json)"
            )
            if not path:
                return
            record = {"component_id": str(component_id), "fraction_dose_model": model, "layer1_result_path": path}
        self._layer31_component_entries = [
            item for item in self._layer31_component_entries if str(item.get("component_id")) != str(component_id)
        ] + [record]
        self._refresh_layer31_component_table()

    def _remove_layer31_component_source(self) -> None:
        row = self.layer31_component_table.currentRow()
        if row < 0 or row >= len(self._layer31_component_entries):
            QMessageBox.information(self, "ASCEND Layer 3.1", "Select a component-source row to remove.")
            return
        del self._layer31_component_entries[row]
        self._refresh_layer31_component_table()

    def _add_layer31_roi_assignment(self) -> None:
        selected = self.layer31_roi_selector.currentData()
        if not isinstance(selected, dict) or not selected.get("roi_identity"):
            QMessageBox.critical(self, "ASCEND Layer 3.1", "Select a rasterised RTSTRUCT ROI.")
            return
        try:
            alpha_beta = self._number(self.layer31_alpha_beta.text())
            if alpha_beta is None or alpha_beta <= 0:
                raise ValueError("Alpha/beta must be greater than zero.")
            source = self.layer31_parameter_source.text().strip()
            set_id = self.layer31_parameter_set.text().strip()
            if not source or not set_id:
                raise ValueError("Parameter source and parameter-set ID are required.")
        except ValueError as exc:
            QMessageBox.critical(self, "ASCEND Layer 3.1", str(exc))
            return
        record = {
            "roi_identity": dict(selected["roi_identity"]),
            "roi_name": selected.get("name"),
            "alpha_beta_gy": alpha_beta,
            "parameter_source": source,
            "parameter_source_type": str(self.layer31_parameter_source_type.currentData()),
            "parameter_set_version": set_id,
            "assignment_method": "qt_identity_bound",
        }
        key = self._layer31_identity_key(record["roi_identity"])
        self._layer31_roi_entries = [
            item for item in self._layer31_roi_entries if self._layer31_identity_key(item.get("roi_identity", {})) != key
        ] + [record]
        self._refresh_layer31_roi_table()
        self.footer_stage.setText("Layer 3.1 tissue assignment staged; save configuration to apply")

    def _remove_layer31_roi_assignment(self) -> None:
        row = self.layer31_roi_table.currentRow()
        if row < 0 or row >= len(self._layer31_roi_entries):
            QMessageBox.information(self, "ASCEND Layer 3.1", "Select an assignment row to remove.")
            return
        del self._layer31_roi_entries[row]
        self._refresh_layer31_roi_table()

    def _layer31_kinetic_parameters(self, editor: dict[str, Any], scenario: str, tissue: str) -> dict[str, Any]:
        if scenario == "Not configured":
            return {}
        delivery_source = str(editor["delivery_time_source"].currentData())
        delivery_time = self._number(editor["treatment_delivery_time"].text())
        delivery_evidence: dict[str, Any] = {}
        if delivery_source == "rtplan_control_point_integration":
            seconds, delivery_evidence = self._rtplan_delivery_time_seconds()
            if seconds is not None:
                delivery_time = self._seconds_to_time_unit(seconds, editor["time_unit"].currentText())
        values = {
            "parameter_set_id": editor["parameter_set_id"].text().strip(),
            "parameter_source": editor["parameter_source"].text().strip(),
            "model_source": "Guerrero–Li modified linear-quadratic model",
            "delta_per_gy": self._number(editor["delta_per_gy"].text()),
            "repair_half_time": self._number(editor["repair_half_time"].text()),
            "treatment_delivery_time": delivery_time,
            "time_unit": editor["time_unit"].currentText(),
            "delivery_time_source": delivery_source,
            "delivery_time_evidence": delivery_evidence,
            "tissue_identity": tissue,
        }
        if tissue == "tumour" and editor["scenario_override"].isChecked():
            alpha = self._number(editor["alpha_per_gy"].text())
            beta = self._number(editor["beta_per_gy2"].text())
            override_source = editor["scenario_override_source"].text().strip()
            if alpha is None or alpha <= 0 or beta is None or beta <= 0:
                raise ValueError("Manual tumour scenario override requires positive alpha and beta values.")
            if not override_source:
                raise ValueError("Manual tumour scenario override requires a source or exploratory rationale.")
            values.update({
                "scenario_parameter_override": True,
                "scenario_parameter_source": override_source,
                "alpha_per_gy": alpha,
                "beta_per_gy2": beta,
                "alpha_beta_gy": alpha / beta,
                "scenario_sf2": math.exp(-2.0 * alpha - 4.0 * beta),
            })
        missing = [
            key
            for key in ("parameter_set_id", "parameter_source", "delta_per_gy", "repair_half_time", "treatment_delivery_time")
            if values[key] in (None, "")
        ]
        if missing:
            editor["status"].setText(
                f"INCOMPLETE · calculation will be blocked until supplied: {', '.join(missing)}. Configuration can still be saved."
            )
        return values

    @staticmethod
    def _seconds_to_time_unit(seconds: float, unit: str) -> float:
        factors = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
        return float(seconds) / factors[unit]

    def _rtplan_delivery_time_seconds(self) -> tuple[float | None, dict[str, Any]]:
        case = self.controller.case
        if case is None:
            return None, {"status": "not_available", "reason": "NO_OPEN_CASE"}
        delivery = (case.layer1.result or {}).get("manifest", {}).get("rtplan_delivery") or case.provenance.get(
            "dicom_configuration_prefill", {}
        ).get("delivery_metadata", {})
        raw = delivery.get("beam_on_time_seconds_per_fraction")
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return None, {
                "status": "not_available",
                "reason": "RTPLAN_CONTROL_POINT_DELIVERY_TIME_UNAVAILABLE",
                "plan_uid": delivery.get("plan_uid"),
            }
        if not math.isfinite(seconds) or seconds < 0:
            return None, {
                "status": "not_available",
                "reason": "RTPLAN_CONTROL_POINT_DELIVERY_TIME_INVALID",
                "plan_uid": delivery.get("plan_uid"),
            }
        return seconds, {
            "status": "calculated",
            "method": "RTPLAN_CONTROL_POINT_METERSET_INTERVAL_INTEGRATION",
            "beam_on_time_seconds_per_fraction": seconds,
            "plan_uid": delivery.get("plan_uid"),
            "schema_version": delivery.get("schema_version"),
        }

    def _update_layer31_delivery_time_source(self, tissue: str) -> None:
        editor = self.layer31_tumour_kinetics if tissue == "tumour" else self.layer31_normal_kinetics
        source = str(editor["delivery_time_source"].currentData())
        rtplan = source == "rtplan_control_point_integration"
        editor["treatment_delivery_time"].setReadOnly(rtplan)
        if not rtplan:
            editor["treatment_delivery_time"].setToolTip("Manually entered delivery time per fraction.")
            return
        seconds, evidence = self._rtplan_delivery_time_seconds()
        if seconds is None:
            editor["treatment_delivery_time"].clear()
            editor["treatment_delivery_time"].setToolTip(str(evidence.get("reason")))
            editor["status"].setText(
                "INCOMPLETE - RTPLAN control-point delivery time is unavailable; select manual entry or repair RTPLAN timing evidence."
            )
            return
        converted = self._seconds_to_time_unit(seconds, editor["time_unit"].currentText())
        editor["treatment_delivery_time"].setText(f"{converted:.12g}")
        editor["treatment_delivery_time"].setToolTip(
            f"Derived from RTPLAN control-point meterset intervals: {seconds:.6g} seconds per fraction."
        )

    def _update_layer31_tumour_override(self) -> None:
        editor = self.layer31_tumour_kinetics
        enabled = editor["scenario_override"].isChecked()
        editor["alpha_per_gy"].setReadOnly(not enabled)
        editor["beta_per_gy2"].setReadOnly(not enabled)
        editor["scenario_override_source"].setEnabled(enabled)
        if enabled:
            self._update_layer31_override_derivatives()
            editor["status"].setText(
                "MANUAL TUMOUR OVERRIDE - alpha and beta are exploratory inputs; source or rationale is required."
            )
            return
        if not getattr(self, "_loading_configuration", False):
            editor["scenario_override_source"].clear()
        scenario = TUMOUR_SCENARIOS.get(editor["scenario"].currentText())
        if scenario:
            for key in ("alpha_beta_gy", "sf2", "alpha_per_gy", "beta_per_gy2"):
                editor[key].setText(f"{float(scenario[key]):.12g}")

    def _update_layer31_override_derivatives(self) -> None:
        editor = self.layer31_tumour_kinetics
        if not editor["scenario_override"].isChecked():
            return
        alpha = self._number(editor["alpha_per_gy"].text())
        beta = self._number(editor["beta_per_gy2"].text())
        if alpha is None or beta is None or alpha <= 0 or beta <= 0:
            editor["alpha_beta_gy"].clear()
            editor["sf2"].clear()
            return
        editor["alpha_beta_gy"].setText(f"{alpha / beta:.12g}")
        editor["sf2"].setText(f"{math.exp(-2.0 * alpha - 4.0 * beta):.12g}")

    def _update_layer31_alpha_beta_sensitivity_controls(self, _checked: bool = False) -> None:
        """Enable the tumour-site range controls only when the analysis is selected."""
        enabled = self.layer31_ab_sensitivity_enabled.isChecked()
        for widget in (
            self.layer31_ab_tumour_site, self.layer31_ab_minimum, self.layer31_ab_maximum,
            self.layer31_ab_samples, self.layer31_ab_scaling, self.layer31_ab_source,
        ):
            widget.setEnabled(enabled)

    def _run_layer31(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_layer31)

    def _build_layer31_visualization(self) -> None:
        case = self.controller.case
        if not case or not case.layer3_1.result:
            QMessageBox.critical(self, "ASCEND Layer 3.1", "Run Layer 3.1 before building the biological field viewer.")
            return
        self.layer31_viewer_status.setText("Loading hash-verified biological fields and generating display-only anatomical meshes…")
        from ascend.gui.layer31_viewer import prepare_layer31_viewer_data

        self._work(lambda: prepare_layer31_viewer_data(case), self._show_layer31_visualization)

    def _show_layer31_visualization(self, data: Any) -> None:
        if self.layer31_viewer is None:
            from ascend.gui.layer31_viewer import Layer31Viewer

            self.layer31_viewer = Layer31Viewer()
            self.layer31_viewer.scenarioRequested.connect(self._run_layer31_viewer_scenario)
            self.layer31_viewer_layout.addWidget(self.layer31_viewer, 1)
        self.layer31_viewer.set_data(data)
        self.layer31_viewer.setEnabled(True)
        self.layer31_viewer_run_id = self.controller.case.layer3_1.run_id if self.controller.case else None
        self.layer31_viewer_status.setText(
            "Embedded four-pane viewer loaded from authoritative stored fields. Surface smoothing is display-only."
        )
        self.layer31_viewer_status.hide()
        self.layer31_tabs.setCurrentIndex(1)

    def _run_layer31_viewer_scenario(self, scenario: str) -> None:
        """Route a viewer scenario change through configuration and services."""
        if scenario not in TUMOUR_SCENARIOS:
            return
        self.layer31_tumour_scenario.setCurrentText(scenario)
        self._update_layer31_model_preset("tumour")
        if self._save_configuration(silent=True):
            self.layer31_viewer_status.setText(f"Recalculating stored Layer 3.1 fields for {scenario}…")
            self._work(self.controller.run_layer31, lambda _value: self._build_layer31_visualization())

    def _export_layer31(self) -> None:
        case = self.controller.case
        if not case or not case.layer3_1.result:
            QMessageBox.information(self, "ASCEND Layer 3.1", "No current Layer 3.1 result is available to export.")
            return
        destination = QFileDialog.getExistingDirectory(self, "Select Layer 3.1 export directory", str(case.root / "exports"))
        if destination:
            self._work(lambda: self.controller.export_layer31(destination), self._show_exports)
