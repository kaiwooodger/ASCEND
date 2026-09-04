"""Stored-result presentation and navigation refresh for the workstation."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtGui import QColor, QPalette

from ascend.gui.layer3_presenters import refresh_layer31, refresh_layer32
from ascend.gui.theme import METRIC_LABELS, canonical_state
from ascend.gui.workstation_widgets import set_table as _set_table
from ascend.gui.workstation_widgets import compact_table, supporting_output_rows
from ascend.models.case import ASCENDCase
from ascend.workflow.preferences import normalise_vertex_records, selected_supporting_outputs


class WorkstationRefreshMixin:
    """Render controller-owned case state into existing workstation widgets."""

    def _tps_and_dose_labels(self, case: ASCENDCase) -> tuple[str, str]:
        plan_path = case.selected_objects.get("rtplan")
        dose_path = case.selected_objects.get("rtdose")
        plan = next((item for item in case.dicom_objects.get("RTPLAN", []) if item.get("path") == plan_path), {})
        dose = next((item for item in case.dicom_objects.get("RTDOSE", []) if item.get("path") == dose_path), {})
        tps = plan.get("manufacturer") or plan.get("plan_label") or "not recorded"
        dose_label = dose.get("dose_summation_type") or "selected RTDOSE"
        return str(tps), str(dose_label)

    def _set_navigation_status(self, index: int, status: Any) -> None:
        item = self.navigation_items[index]
        state = canonical_state(status)
        compact = {
            "NOT IMPLEMENTED": "N/I",
            "NOT APPLICABLE": "N/A",
            "OUTSIDE SCOPE": "SCOPE",
            "PROVISIONAL": "PROV.",
        }.get(state, state)
        item.setText(f"{self.navigation_labels[index]}   [{compact}]")
        item.setToolTip(f"{self.navigation_labels[index]} — {state}")
        colours = {
            "PASS": QColor("#18704d"),
            "WARN": QColor("#9a6500"),
            "PROVISIONAL": QColor("#6744a4"),
            "BLOCKED": QColor("#aa2e2e"),
            "INVALID": QColor("#aa2e2e"),
            "OUTSIDE SCOPE": QColor("#596979"),
        }
        item.setForeground(colours.get(state, self.palette().color(QPalette.Text)))

    def _refresh_navigation(self, case: ASCENDCase | None) -> None:
        if case is None:
            for index in self.navigation_items:
                self._set_navigation_status(index, "not_run")
            return
        has_bindings = bool(case.configuration.structure_bindings or case.configuration.structure_roles)
        statuses = {
            0: "pass",
            1: "pass",
            2: "pass" if has_bindings else "warn",
            3: case.layer1_status,
            4: case.layer2_1.calculation_status,
            5: case.layer2_2.calculation_status,
            6: case.layer2_2.calculation_status if case.layer2_2.result else case.layer2_1.calculation_status,
            7: case.layer3_1.calculation_status,
            8: case.layer3_2.calculation_status if case.configuration.layer32_enabled else "not_applicable",
            9: "provisional" if case.layer2_1.result or case.layer2_2.result else "not_run",
            10: "pass" if case.layer2_1.result or case.layer2_2.result or case.layer3_1.result or case.layer3_2.result else "not_run",
        }
        for index, status in statuses.items():
            self._set_navigation_status(index, status)

    def _refresh_mapping_table(self, case: ASCENDCase) -> None:
        rows: list[list[Any]] = []
        requirements = {
            "GTV": "Required",
            "T_L": "Required",
            "VTV_H": "Required",
            "VTV_L": "Required",
            "VTV_H_individual": "Optional",
        }
        for role, requirement in requirements.items():
            binding = case.configuration.structure_bindings.get(role)
            legacy = case.configuration.structure_roles.get(role)
            identities = binding if isinstance(binding, list) else ([binding] if binding else [])
            names = [str(item.get("display_name") or "Unnamed ROI") for item in identities]
            numbers = [str(item.get("roi_number", "—")) for item in identities]
            if identities:
                state = "Bound by ROI identity"
            elif legacy:
                legacy_values = legacy if isinstance(legacy, list) else [legacy]
                names = [str(item) for item in legacy_values]
                numbers = ["—"] * len(names)
                state = "Legacy name; resolved when saved"
            else:
                state = "Not configured" if requirement == "Optional" else "Required mapping missing"
            rows.append([role, requirement, ", ".join(names) or "—", ", ".join(numbers) or "—", state])
        _set_table(self.mapping_table, rows)

    @staticmethod
    def _json_or_state(value: Any, empty_text: str) -> str:
        if value in (None, {}, []):
            return empty_text
        return json.dumps(value, indent=2)

    @staticmethod
    def _delivery_number(value: Any, suffix: str = "") -> str:
        if value is None or value == []:
            return "—"
        if isinstance(value, list):
            return ", ".join(WorkstationRefreshMixin._delivery_number(item) for item in value)
        if isinstance(value, float):
            text = f"{value:.3f}".rstrip("0").rstrip(".")
        else:
            text = str(value)
        return f"{text}{suffix}"

    @staticmethod
    def _duration(value: Any) -> str:
        if value is None:
            return "—"
        seconds = float(value)
        minutes, remainder = divmod(seconds, 60.0)
        return f"{int(minutes)}m {remainder:.1f}s" if minutes else f"{remainder:.1f}s"

    def refresh(self) -> None:
        """Handle refresh for the enclosing ASCEND workflow."""
        case = self.controller.case
        if not case:
            self.header_case.setText("No case open")
            self.header_status.setText("TPS —  ·  Dose —")
            for pill in (self.header_layer1, self.header_layer21, self.header_layer22, self.header_interpretation):
                pill.set_status("not_run")
            self.sidebar_case.setText("No case open\nImport or open an ASCEND case")
            self.footer_run.setText("Run —")
            self._refresh_navigation(None)
            return
        interpretations = (case.layer2_1.interpretation_status, case.layer2_2.interpretation_status)
        interpretation = (
            "protocol_interpretable"
            if all(item == "protocol_interpretable" for item in interpretations)
            else ("provisional" if "provisional" in interpretations else "not_interpretable")
        )
        tps, dose = self._tps_and_dose_labels(case)
        self.header_case.setText(case.case_id)
        self.header_status.setText(f"TPS {tps}  ·  Dose {dose}")
        self.header_layer1.set_status(case.layer1_status)
        self.header_layer21.set_status(case.layer2_1.calculation_status)
        self.header_layer22.set_status(case.layer2_2.calculation_status)
        self.header_interpretation.set_status(interpretation)
        self.sidebar_case.setText(
            f"{case.case_id}\nLayer 1 {canonical_state(case.layer1_status)}\nChain {case.selected_chain_id or 'selection required'}"
        )
        latest_run = case.layer3_2.run_id or case.layer3_1.run_id or case.layer2_2.run_id or case.layer2_1.run_id or case.layer1.run_id
        self.footer_run.setText(f"Run {latest_run or '—'}")
        self._refresh_navigation(case)
        counts = {key: len(value) for key, value in case.dicom_objects.items()}
        self.import_summary.setPlainText(
            json.dumps({"case_id": case.case_id, "detected_objects": counts, "selected": case.selected_objects}, indent=2)
        )
        self.chain_select.clear()
        for chain in case.dicom_chains:
            self.chain_select.addItem(
                f"{chain.get('display', {}).get('plan_label', '') or 'Unlabelled plan'}  ·  "
                f"Dose {chain.get('display', {}).get('dose_summation_type', '') or '—'}  ·  "
                f"{chain['validity_status']}  ·  {chain['chain_id']}",
                chain["chain_id"],
            )
        if case.selected_chain_id:
            index = self.chain_select.findData(case.selected_chain_id)
            if index >= 0:
                self.chain_select.setCurrentIndex(index)
        selected_chain = next((item for item in case.dicom_chains if item.get("chain_id") == case.selected_chain_id), None)
        if selected_chain:
            self.chain_detail.setText(
                f"Selected chain is {selected_chain.get('validity_status')}. It binds RTDOSE, RTPLAN, RTSTRUCT, "
                f"and {selected_chain.get('display', {}).get('image_count', 0)} planning image(s). "
                f"Unresolved references: {', '.join(selected_chain.get('unresolved_references', [])) or 'none'}."
            )
        elif len(case.dicom_chains) > 1:
            self.chain_detail.setText(
                f"Selection required: {len(case.dicom_chains)} candidate treatment chains were found. "
                "Layer 1 remains blocked until one chain is explicitly selected."
            )
        l1 = case.layer1.result or {}
        eligibility = l1.get("eligibility", {})
        self.layer1_status_pill.set_status(case.layer1_status)
        self.layer1_card.setText(
            f"Calculation: {canonical_state(case.layer1.calculation_status)}  ·  "
            f"Layer 2 eligible: {'yes' if eligibility.get('layer_2_eligible', False) else 'no'}"
        )
        findings = list(l1.get("findings", []))
        finding_messages = [f"{item.get('check', 'finding')}: {item.get('detail', '')}" for item in findings]
        blocked = canonical_state(case.layer1_status) == "BLOCKED" or any(
            str(item.get("level", "")).upper() in {"BLOCK", "BLOCKED"} or bool(item.get("blocks")) for item in findings
        )
        self.layer1_banner.set_messages(finding_messages, blocked=blocked)
        _set_table(
            self.layer1_findings,
            [[item.get("level"), item.get("check"), item.get("detail"), item.get("blocks", "")] for item in findings],
            "Run Layer 1 to populate validation findings.",
        )
        _set_table(
            self.layer1_eclipse_audit,
            [
                [
                    item.get("original_structure"),
                    item.get("ascend_role"),
                    item.get("validated_structure"),
                    item.get("metric"),
                    item.get("eclipse_value"),
                    item.get("ascend_value"),
                    item.get("difference"),
                    item.get("unit"),
                    item.get("status"),
                ]
                for item in l1.get("eclipse_dvh_audit", [])
            ],
            "No Eclipse comparison records are available.",
        )
        self.layer1_eclipse_import.setPlainText(
            self._json_or_state(l1.get("eclipse_dvh_import"), "No Eclipse DVH reference has been imported.")
        )
        delivery = l1.get("manifest", {}).get("rtplan_delivery") or case.provenance.get(
            "dicom_configuration_prefill", {}
        ).get("delivery_metadata", {})
        if delivery.get("status") == "available":
            self.layer1_rtplan_summary.setText(
                f"Plan {delivery.get('plan_label') or '—'}  ·  "
                f"{delivery.get('vmat_arc_count', 0)} VMAT arc(s)  ·  "
                f"{delivery.get('treatment_beam_count', delivery.get('beam_count', 0))} treatment beam(s)  ·  "
                f"{self._delivery_number(delivery.get('total_mu_per_fraction'))} MU/fraction  ·  "
                f"{self._delivery_number(delivery.get('total_planned_mu'))} planned MU  ·  "
                f"{self._duration(delivery.get('estimated_beam_on_time_seconds_per_fraction'))} estimated beam-on/fraction"
            )
        else:
            self.layer1_rtplan_summary.setText("No RTPLAN delivery metadata is available for the selected DICOM chain.")
        _set_table(
            self.layer1_rtplan_beams,
            [
                [
                    f"{item.get('beam_number', '—')}: {item.get('beam_name') or 'Unnamed'}",
                    item.get("delivery_technique") or "—",
                    ", ".join(str(value) for value in item.get("fraction_group_numbers", [])) or "—",
                    self._delivery_number(item.get("meterset_mu")),
                    self._delivery_number(item.get("beam_dose_gy")),
                    self._delivery_number(item.get("mu_per_gy")),
                    self._delivery_number(item.get("nominal_energy_mv")),
                    self._delivery_number(item.get("dose_rate_mu_per_min")),
                    f"{self._delivery_number(item.get('gantry_start_deg'), '°')} → {self._delivery_number(item.get('gantry_end_deg'), '°')}",
                    item.get("gantry_rotation_direction") or "—",
                    self._delivery_number(item.get("gantry_rotation_deg"), "°"),
                    f"{self._delivery_number(item.get('collimator_start_deg'), '°')} → {self._delivery_number(item.get('collimator_end_deg'), '°')}",
                    f"{self._delivery_number(item.get('couch_start_deg'), '°')} → {self._delivery_number(item.get('couch_end_deg'), '°')}",
                    item.get("control_point_count") or "—",
                    self._duration(item.get("delivery_duration_limit_seconds")),
                    self._duration(item.get("estimated_beam_on_time_seconds")),
                ]
                for item in delivery.get("beams", [])
            ],
            "No RTPLAN beams are available.",
        )
        compact_table(self.layer1_findings, maximum=360)
        compact_table(self.layer1_eclipse_audit, maximum=360)
        compact_table(self.layer1_rtplan_beams, maximum=300)
        self.layer1_rtplan_notes.setText("\n".join(str(item) for item in delivery.get("notes", [])))
        self._resize_layer1_tabs(self.layer1_tabs.currentIndex())
        self._refresh_mapping_table(case)
        self._refresh_layer21(case)
        self._refresh_layer22(case)
        self._refresh_layer31(case)
        self._refresh_layer32(case)
        self.review_text.setPlainText(json.dumps(case.to_dict(include_results=False), indent=2))
        self.export_path.setText(str(case.root / "exports"))

    def _refresh_layer21(self, case: ASCENDCase) -> None:
        record = case.layer2_1
        result = record.result or {}
        self.layer21_status_pill.set_status(record.calculation_status)
        self.vertex_qa_layer21_status.set_status(record.calculation_status)
        self.layer21_interpretation_pill.set_status(record.interpretation_status)
        self.layer21_card.setText(f"Run {record.run_id or '—'}")
        warnings = list(result.get("warnings", record.warnings))
        if record.error:
            warnings.insert(0, record.error)
        blocked = canonical_state(record.calculation_status) in {"BLOCKED", "INVALID"}
        self.layer21_warnings.set_messages(warnings, blocked=blocked)
        rows = []
        metrics = result.get("harmonised_metrics", [])
        for card in self.metric_cards.values():
            card.set_metric(None)
        for item in metrics:
            value = item.get("value")
            metric_id = str(item.get("metric_id", ""))
            if metric_id in self.metric_cards:
                self.metric_cards[metric_id].set_metric(item)
            rows.append(
                [
                    METRIC_LABELS.get(metric_id, metric_id),
                    "" if value is None else value,
                    item.get("units"),
                    item.get("applicability"),
                    ", ".join(item.get("warnings", [])),
                ]
            )
        _set_table(self.metric_table, rows, "Run Layer 2.1 to populate harmonised metrics.")
        stored_supporting = result.get("supporting_outputs") or {}
        categories = [category for category, checkbox in self.supporting_output_checks.items() if checkbox.isChecked()]
        supporting = selected_supporting_outputs(
            stored_supporting,
            self.supporting_outputs_enabled.isChecked(),
            categories,
        )
        self._current_supporting_outputs = supporting
        self.export_supporting_json_button.setEnabled(bool(supporting))
        _set_table(
            self.layer21_support,
            supporting_output_rows(supporting),
            "No stored supporting output is available for this run.",
        )
        vertex_analysis = (
            stored_supporting.get("vertex_analysis", {})
            if "per_vertex" in categories and self.supporting_outputs_enabled.isChecked()
            else {}
        )
        vertex_records = (
            stored_supporting.get(
                "per_vertex_qa",
                result.get("per_vertex_quality_control", result.get("per_vertex_qa", [])),
            )
            or []
            if "per_vertex" in categories and self.supporting_outputs_enabled.isChecked()
            else []
        )
        vertex_records = normalise_vertex_records(vertex_records)
        selected_vertex = self.vertex_qa_vertex_selector.currentData()
        self.vertex_qa_vertex_selector.blockSignals(True)
        self.vertex_qa_vertex_selector.clear()
        self.vertex_qa_vertex_selector.addItem("Select vertex", "")
        for item in vertex_records:
            vertex_id = str(item.get("vertex_id") or "")
            if vertex_id:
                self.vertex_qa_vertex_selector.addItem(vertex_id, vertex_id)
        selected_index = self.vertex_qa_vertex_selector.findData(selected_vertex)
        self.vertex_qa_vertex_selector.setCurrentIndex(selected_index if selected_index >= 0 else (1 if vertex_records else 0))
        self.vertex_qa_vertex_selector.blockSignals(False)
        source = vertex_analysis.get("source", "not recorded")
        self.layer21_vertex_summary.setText(
            f"{len(vertex_records)} stored record(s)  ·  Source: {source}  ·  Status: {vertex_analysis.get('status', 'not calculated')}"
        )
        _set_table(
            self.layer21_vertex_table,
            [
                [
                    item.get("vertex_id"),
                    item.get("v95_rxh_pct"),
                    item.get("v95_rxh_applicability", item.get("applicability")),
                    item.get("dmean_gy"),
                    item.get("d95_gy"),
                    item.get("dmax_gy"),
                    item.get("volume_cc"),
                    item.get("local_fwhm_mm"),
                    item.get("nearest_vertex_distance_mm"),
                ]
                for item in vertex_records
            ],
            "No per-vertex QA records were stored for this run.",
        )
        self.layer21_vertex.setPlainText(self._json_or_state(vertex_analysis, "Per-vertex analysis metadata is not available."))
        vertex_connections = supporting.get("vertex_connections", [])
        self.vertices_canvas.set_vertex_qa(vertex_records, vertex_connections)
        visible_vertices = sum(item.get("centroid_lps_mm") is not None for item in vertex_records)
        self.vertices_layout_summary.setText(
            f"{visible_vertices} vertex mask(s) · {len(vertex_connections)} nearest-neighbour connection(s) · hover for QA"
            if visible_vertices else "No stored vertex centroids are available for layout."
        )
        global_fwhm = supporting.get("global_fwhm_summary", {})

        def fwhm_display(value: Any) -> str:
            return f"{float(value):.2f} mm" if isinstance(value, (int, float)) else "— mm"

        self.layer21_fwhm_average.setText(fwhm_display(global_fwhm.get("average_fwhm_mm")))
        self.layer21_fwhm_median.setText(fwhm_display(global_fwhm.get("median_fwhm_mm")))
        minimum_fwhm = global_fwhm.get("minimum_fwhm_mm")
        maximum_fwhm = global_fwhm.get("maximum_fwhm_mm")
        self.layer21_fwhm_range.setText(
            f"{float(minimum_fwhm):.2f}–{float(maximum_fwhm):.2f} mm"
            if isinstance(minimum_fwhm, (int, float)) and isinstance(maximum_fwhm, (int, float)) else "— mm"
        )
        self.layer21_fwhm_status.setText(
            f"Status: {global_fwhm.get('status', 'not available')} · "
            f"{global_fwhm.get('vertex_count', 0)} valid vertex record(s). "
            f"{global_fwhm.get('method', 'Run Layer 2.1 with per-vertex QA enabled.') }"
        )
        _set_table(
            self.layer21_fwhm_table,
            [
                [
                    item.get("vertex_id"),
                    item.get("local_fwhm_mm"),
                    (item.get("fwhm_axes_mm") or {}).get("grid_x"),
                    (item.get("fwhm_axes_mm") or {}).get("grid_y"),
                    (item.get("fwhm_axes_mm") or {}).get("grid_z"),
                    item.get("fwhm_half_max_dose_gy"),
                ]
                for item in vertex_records
            ],
            "No local FWHM records were stored for this run.",
        )
        oar = (
            result.get("oar_vertex_geometry", stored_supporting.get("oar_vertex_geometry", {}))
            if "oar_geometry" in categories and self.supporting_outputs_enabled.isChecked()
            else {}
        )
        self.layer21_oar_status.setText(
            f"Status: {oar.get('status', 'not selected')}  ·  "
            f"{oar.get('scope', 'No optional OAR or internal-target geometry result is available.')}"
        )
        _set_table(
            self.layer21_oar_table,
            [
                [
                    item.get("oar_name"),
                    item.get("classification"),
                    item.get("oar_volume_cc"),
                    item.get("aggregate_vtvh_minimum_surface_distance_mm"),
                    item.get("aggregate_vtvh_spatial_relationship"),
                    item.get("overlap_volume_cc"),
                    item.get("overlap_percentage_of_oar"),
                    item.get("nearest_vertex_id"),
                    item.get("nearest_vertex_distance_mm"),
                    "; ".join(finding.get("code", "") for finding in item.get("geometry_audit", {}).get("findings", []))
                    or item.get("status")
                    or item.get("reason"),
                ]
                for item in oar.get("records", [])
            ],
            "No selected OAR or internal-target geometry records were calculated.",
        )
        _set_table(
            self.layer21_oar_vertex_table,
            [
                [
                    item.get("oar_name"),
                    vertex.get("vertex_id"),
                    vertex.get("minimum_surface_distance_mm"),
                    vertex.get("overlap_volume_cc"),
                    vertex.get("spatial_relationship"),
                    vertex.get("zero_distance_reason"),
                ]
                for item in oar.get("records", [])
                for vertex in item.get("per_vertex_geometry", [])
            ],
            "No per-vertex OAR geometry audit records were calculated.",
        )
        provenance = result.get("provenance") if "integrity" in categories and self.supporting_outputs_enabled.isChecked() else None
        self.layer21_provenance.setPlainText(self._json_or_state(provenance, "Provenance output is not selected."))
        current_vertex = str(self.vertex_qa_vertex_selector.currentData() or "")
        if current_vertex:
            self._select_unified_vertex(current_vertex)

    def _refresh_layer22(self, case: ASCENDCase) -> None:
        record = case.layer2_2
        result = record.result or {}
        self.layer22_status_pill.set_status(record.calculation_status)
        self.vertex_qa_layer22_status.set_status(record.calculation_status)
        self.layer22_interpretation_pill.set_status(record.interpretation_status)
        self.layer22_card.setText(f"Run {record.run_id or '—'}")
        warnings = list(result.get("warnings", record.warnings))
        if record.error:
            warnings.insert(0, record.error)
        vertex_source = result.get("vertex_source") or result.get("frozen_definitions", {}).get("vertex_source")
        blocked = canonical_state(record.calculation_status) in {"BLOCKED", "INVALID"}
        self.layer22_warnings.set_messages(warnings, blocked=blocked)
        self.graph_canvas.set_result(result or None)
        extensions = result.get("layer2_2_extensions") or {}
        self.layer22_vertex_profiles_panel.set_result(extensions.get("vertex_profiles"))
        self.layer22_saddle_panel.set_result(extensions.get("saddle_graph"))
        known_vertices = {
            str(self.vertex_qa_vertex_selector.itemData(index))
            for index in range(self.vertex_qa_vertex_selector.count())
        }
        for node in result.get("nodes", []):
            vertex_id = str(node.get("node") or "")
            if vertex_id and vertex_id not in known_vertices:
                self.vertex_qa_vertex_selector.addItem(vertex_id, vertex_id)
                known_vertices.add(vertex_id)
        selected_edge = self.vertex_qa_edge_selector.currentData()
        self.vertex_qa_edge_selector.blockSignals(True)
        self.vertex_qa_edge_selector.clear()
        self.vertex_qa_edge_selector.addItem("Select edge", None)
        for edge_index, edge in enumerate(result.get("edges", [])):
            nodes = " — ".join(map(str, edge.get("nodes") or []))
            self.vertex_qa_edge_selector.addItem(f"E{edge.get('edge_id', edge_index + 1)} · {nodes}", edge_index)
        selected_edge_index = self.vertex_qa_edge_selector.findData(selected_edge)
        self.vertex_qa_edge_selector.setCurrentIndex(
            selected_edge_index if selected_edge_index >= 0 else (1 if result.get("edges") else 0)
        )
        self.vertex_qa_edge_selector.blockSignals(False)
        summary = result.get("graph_summary") or {}
        plan_ipvdr = result.get("plan_ipvdr") or {}
        median = plan_ipvdr.get("primary_median")
        median_text = f"{float(median):.3f}" if isinstance(median, (int, float)) else "—"
        self.graph_result_summary.setText(
            f"{summary.get('number_of_nodes', 0)} nodes  ·  {summary.get('number_of_edges', 0)} edges  ·  "
            f"Median iPVDR {median_text}  ·  {vertex_source or 'vertex source not recorded'}"
        )
        self.vertex_qa_run_summary.setText(
            f"Layer 2.1 {canonical_state(case.layer2_1.calculation_status)} · "
            f"Layer 2.2 {canonical_state(record.calculation_status)} · "
            f"{summary.get('number_of_nodes', 0)} vertices · {summary.get('number_of_edges', 0)} graph edges"
        )
        pass_meaning = (
            "Computational PASS: required valid edges were available and no framework warning was raised. "
            "It is not a clinical PVDR acceptance threshold or treatment-plan approval."
        )
        _set_table(
            self.graph_summary,
            [
                ["Calculation state", canonical_state(record.calculation_status), pass_meaning],
                [
                    "Interpretation state",
                    canonical_state(record.interpretation_status),
                    "Layer 2.2 remains provisional; graph metrics require expert interpretation.",
                ],
                ["Vertex source", vertex_source, "Explicit RTSTRUCT vertices are preferred; connected components generate a warning."],
                ["Nodes", summary.get("number_of_nodes"), "Validated vertex masks represented as graph nodes."],
                ["Edges", summary.get("number_of_edges"), "Deterministic nearest-neighbour connections."],
                [
                    "Connected components",
                    summary.get("number_of_components"),
                    "PASS requires one connected graph under the current warning policy.",
                ],
                ["Valid edges", summary.get("valid_edges"), "Edges meeting the frozen midpoint-sphere support rule."],
                [
                    "Excluded edges",
                    summary.get("excluded_edges", len(result.get("excluded_edges", []))),
                    "Excluded edges are retained for audit and raise a warning.",
                ],
                [
                    "Median edge iPVDR",
                    plan_ipvdr.get("primary_median"),
                    "Descriptive median of valid edge-local iPVDR values; no clinical cutoff is applied.",
                ],
                ["Warnings", ", ".join(warnings) or "None", "Any warning changes calculation display from PASS to WARN."],
            ]
            if result
            else [],
            "No Layer 2.2 graph result is available.",
        )
        _set_table(
            self.graph_nodes,
            [
                [item.get("node"), *(item.get("centroid_lps_mm") or [None, None, None]), item.get("peak_d50_gy")]
                for item in result.get("nodes", [])
            ],
            "No graph nodes are available.",
        )
        _set_table(
            self.graph_edges,
            [
                [
                    item.get("edge_id"),
                    " — ".join(item.get("nodes", [])),
                    item.get("length_mm"),
                    item.get("edge_local_valley_d50_gy"),
                    item.get("ipvdr"),
                    item.get("edge_status"),
                ]
                for item in result.get("edges", [])
            ],
            "No graph edges are available.",
        )
        self.graph_provenance.setPlainText(self._json_or_state(result.get("provenance"), "No Layer 2.2 provenance is available."))
        current_edge = self.vertex_qa_edge_selector.currentData()
        if isinstance(current_edge, int):
            self._select_unified_edge(current_edge)
        current_vertex = str(self.vertex_qa_vertex_selector.currentData() or "")
        if current_vertex:
            self._select_unified_vertex(current_vertex)
        if self.layer22_viewer_run_id and self.layer22_viewer_run_id != record.run_id:
            self.layer22_viewer_status.setText("STALE — Layer 2.2 changed. Rebuild the 3D viewer before interpretation.")
            if self.layer22_viewer is not None:
                self.layer22_viewer.setEnabled(False)

    def _refresh_layer31(self, case: ASCENDCase) -> None:
        refresh_layer31(self, case)

    def _refresh_layer32(self, case: ASCENDCase) -> None:
        refresh_layer32(self, case)

    @staticmethod
    def _metric_endpoint_display(endpoint: dict[str, Any] | None) -> str:
        if not endpoint or endpoint.get("value") is None:
            return "—"
        value = endpoint.get("value")
        text = f"{float(value):.6f}".rstrip("0").rstrip(".") if isinstance(value, (int, float)) else str(value)
        return f"{text} {endpoint.get('units', '')}".strip()
