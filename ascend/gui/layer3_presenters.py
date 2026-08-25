"""Layer-specific presenters for stored Layer 3 results.

These presenters own biological result interpretation for the Qt workstation.
They only consume immutable/stored service results and never evaluate BED,
EQD2, MLQ, TCP, clonogen, or non-local scientific models.
"""

from __future__ import annotations

import json
from typing import Any

from ascend.gui.theme import canonical_state
from ascend.gui.workstation_widgets import set_table as _set_table
from ascend.layer3.nonlocal_effect.models import resolved_parameters
from ascend.models.case import ASCENDCase


def _probability_display(endpoint: dict[str, Any]) -> Any:
    value = endpoint.get("tcp")
    if endpoint.get("numerical_status") == "UNDERFLOW_REPORTED_IN_LOG_DOMAIN":
        return f"≈0 (log10 TCP {float(endpoint['log10_tcp']):.6g})"
    return value


def refresh_layer31(self, case: ASCENDCase) -> None:
    record = case.layer3_1
    result = record.result or {}
    self.layer31_status_pill.set_status(record.calculation_status)
    self.layer31_interpretation_pill.set_status(record.interpretation_status)
    history = result.get("fraction_history") or {}
    self.layer31_status_text.setText(
        f"Run {record.run_id or '—'}  ·  {history.get('number_of_biological_fraction_events', 0)} biological fraction event(s)  ·  "
        f"{len(result.get('roi_results', []))} spatial ROI result(s)"
        + (f"  ·  {record.error}" if record.error else "")
    )
    gate_rows: list[list[Any]] = []
    for gate in history.get("gate_results", []):
        gate_rows.append([gate.get("gate_id"), gate.get("status"), gate.get("reason_code") or gate.get("detail") or gate.get("evidence")])
    branch_specs = (
        ("3.1A Spatial BED/EQD2", result.get("layer3_1a_conventional_lq") or {}),
        ("3.1B Tumour survival/EUD", result.get("layer3_1b_high_dose_sfrt_response") or {}),
        ("3.1C Therapeutic ratio", result.get("layer3_1c_modelled_therapeutic_ratio") or {}),
        ("3.1D Spatial MLQ-Poisson TCP", result.get("layer3_1d_tumour_control_probability") or {}),
    )
    for label, branch in branch_specs:
        gate_rows.append([label, branch.get("status") or branch.get("calculation_status") or "NOT RUN", branch.get("reason") or ", ".join(branch.get("warnings", [])) or "No blocking reason recorded"])
    _set_table(self.layer31_gate_table, gate_rows, "Run Layer 3.1 to evaluate prerequisite gates.")
    _set_table(self.layer31_history_table, [[
        event.get("temporal_order"), event.get("event_id"), ", ".join(event.get("physical_components", [])),
        event.get("biological_fraction_index"), event.get("geometry_reference"),
        f"{event.get('delivery_time')} {event.get('delivery_time_unit') or ''}" if event.get("delivery_time") is not None else "Parameter-set fallback required for MLQ",
        "Dose: " + ", ".join(event.get("source_dose_identifiers", [])) + " | Plan: " + ", ".join(event.get("source_plan_identifiers", [])),
    ] for event in history.get("events", [])], "No reconstructed fraction history is stored.")

    branch_a = result.get("layer3_1a_conventional_lq") or {}
    warning = branch_a.get("high_dose_warning") or {}
    warning_by_identity = {
        self._layer31_identity_key(item.get("roi_identity", {})): item
        for item in warning.get("roi_summary", [])
    }
    a_rows = []
    for item in result.get("roi_results", []):
        assignment = item.get("assignment", {}); metrics = item.get("metrics", {})
        flag = warning_by_identity.get(self._layer31_identity_key(assignment.get("roi_identity", {})), {})
        a_rows.append([
            assignment.get("roi_name"), assignment.get("alpha_beta_gy"), metrics.get("bed_mean"), metrics.get("bed_d95"),
            metrics.get("bed_d50"), metrics.get("eqd2_mean"), metrics.get("eqd2_d95"), flag.get("flagged_volume_percent"),
        ])
    _set_table(self.layer31a_table, a_rows, "No 3.1A ROI results are stored.")
    a_messages = list(branch_a.get("warnings", []))
    if warning.get("configured"):
        a_messages.insert(0, warning.get("explanation") or warning.get("message"))
    if branch_a.get("reason"):
        a_messages.insert(0, str(branch_a["reason"]))
    self.layer31a_warning.set_messages(a_messages, blocked=str(branch_a.get("status") or "").upper() == "BLOCKED")

    branch_b = result.get("layer3_1b_high_dose_sfrt_response") or {}
    _set_table(self.layer31b_summary, [
        ["Tumour scenario", branch_b.get("scenario_id"), "Sensitivity scenario", branch_b.get("scenario_scope")],
        ["Mean direct surviving fraction", branch_b.get("mean_tumour_survival_fraction"), "dimensionless", "Model-derived mean; not TCP"],
        ["Equivalent log-survival effect Kᵀ,eq", branch_b.get("equivalent_log_survival_effect"), "dimensionless", branch_b.get("equivalent_log_survival_effect_definition")],
        ["Survival-equivalent EUD", branch_b.get("tumour_eud_gy"), "Gy", branch_b.get("eud_applicability") or branch_b.get("reason")],
        ["Reference schedule", (branch_b.get("reference_schedule") or {}).get("schedule_type"), branch_b.get("applicability_status"), branch_b.get("reference_schedule")],
        ["Calculation state", branch_b.get("status") or branch_b.get("calculation_status"), branch_b.get("interpretation_status"), branch_b.get("reason") or ", ".join(branch_b.get("warnings", []))],
    ] if branch_b else [], "No 3.1B tumour response result is stored.")
    comparison = result.get("layer3_1b_paired_course_comparison") or {}
    differences = comparison.get("comparison") or {}
    _set_table(self.layer31b_comparison, [
        ["Comparison state", comparison.get("status"), comparison.get("applicability_status"), comparison.get("reason") or comparison.get("comparison_scope")],
        ["SF difference: LRT+cERT − LRT", differences.get("sf_difference_lrt_plus_cert_minus_lrt"), "dimensionless", comparison.get("arms")],
        ["Equivalent-effect difference", differences.get("equivalent_log_survival_effect_difference"), "dimensionless", "Positive means greater modelled log-survival effect for LRT+cERT"],
        ["EUD difference: LRT+cERT − LRT", differences.get("eud_difference_gy_lrt_plus_cert_minus_lrt"), "Gy", "Research comparison; not a clinical outcome"],
    ] if comparison else [], "No paired-course comparison is configured.")
    regional = (branch_b.get("regional_survival") or {}).get("records", [])
    region_names = {"H": "High-dose vertices", "V": "Validated valley", "O": "Remaining tumour"}
    _set_table(self.layer31b_regional, [[
        region_names.get(item.get("region_id"), item.get("region_id")), item.get("voxel_count"),
        item.get("tumour_volume_fraction"), item.get("mean_surviving_fraction"), item.get("survivor_contribution_fraction"),
    ] for item in regional], "No regional survival decomposition is stored.")
    reconciliation = (branch_b.get("regional_survival") or {}).get("high_dose_fraction_reconciliation") or {}
    _set_table(self.layer31b_hf_reconciliation, [
        ["Layer 2.1 reported HF", reconciliation.get("layer2_1_reported_value_pct"), reconciliation.get("layer2_1_reported_basis"), None],
        ["Layer 2.1 dose-sampled HF", reconciliation.get("layer2_1_dose_sampled_value_pct"), "RTDOSE-sampled mask voxels", reconciliation.get("dose_sampled_difference_percentage_points_layer31b_minus_layer21")],
        ["Layer 3.1B regional fH", reconciliation.get("layer3_1b_value_pct"), reconciliation.get("layer3_1b_basis"), reconciliation.get("reported_difference_percentage_points_layer31b_minus_layer21")],
    ] if reconciliation else [], "No high-dose fraction reconciliation is available.")

    branch_c = result.get("layer3_1c_modelled_therapeutic_ratio") or {}
    _set_table(self.layer31c_summary, [
        ["Calculation state", branch_c.get("status") or branch_c.get("calculation_status"), branch_c.get("applicability_status"), branch_c.get("reason") or branch_c.get("numerical_status")],
        ["Modelled therapeutic ratio", branch_c.get("modelled_therapeutic_ratio"), branch_c.get("applicability_status"), branch_c.get("reference_schedule") or branch_c.get("reason")],
        ["ln(modelled therapeutic ratio)", branch_c.get("log_modelled_therapeutic_ratio"), branch_c.get("numerical_status"), "Primary numerical endpoint if the ratio is outside floating-point range"],
        ["Actual heterogeneous normal-cell SF", branch_c.get("normal_mean_survival_lrt"), branch_c.get("applicability_status"), "Research comparator only"],
        ["Reference normal-cell SF", branch_c.get("normal_survival_at_tumour_eud"), branch_c.get("applicability_status"), "Uniform tumour-isoeffective schedule"],
    ] if branch_c else [], "No 3.1C result is stored.")
    matrix = result.get("layer3_1c_sensitivity_scenario_matrix") or {}
    _set_table(self.layer31c_matrix, [[
        item.get("tumour_scenario"), item.get("normal_scenario"), item.get("therapeutic_ratio"),
        item.get("tumour_eud_gy"), item.get("normal_mean_survival_actual"), item.get("normal_survival_reference"),
        item.get("applicability_status") or item.get("reason"),
    ] for item in matrix.get("records", [])], "The C1–C3 × N1–N3 scenario matrix has not been calculated.")
    branch_d = result.get("layer3_1d_tumour_control_probability") or {}
    endpoints = branch_d.get("endpoints") or {}
    radiation = endpoints.get("radiation_only") or {}
    corrected = endpoints.get("repopulation_corrected") or {}
    source_context = branch_d.get("source_context") or {}
    spatial = endpoints.get("spatial_decomposition") or {}
    valley_record = next((item for item in spatial.get("records", []) if item.get("region_id") == "VALLEY"), {})
    active = corrected if branch_d.get("active_tcp_endpoint") == "TCP_MLQ_POISSON_REPOPULATION_CORRECTED" else radiation
    _set_table(self.layer31d_summary, [
        [branch_d.get("active_tcp_endpoint") or "Qualified TCP", _probability_display(active), "probability", "Poisson probability of zero expected surviving clonogens under the configured direct-kill model"],
        ["Active TCP percentage", (100.0 * active["tcp"]) if active.get("tcp") is not None and active.get("numerical_status") != "UNDERFLOW_REPORTED_IN_LOG_DOMAIN" else "≈0", "%", active.get("numerical_status") or branch_d.get("interpretation_status")],
        ["log10(TCP)", active.get("log10_tcp"), "log10 probability", "Retained when direct TCP numerically underflows"],
        ["Expected residual clonogens", active.get("expected_surviving_clonogens"), "clonogens", "Primary endpoint retained when TCP saturates"],
        ["Initial clonogens", endpoints.get("initial_clonogens"), "clonogens", "Density multiplied by validated physical tumour volume"],
        ["Mean tumour MLQ survival", source_context.get("mean_tumour_survival_fraction"), "dimensionless", "Consumed from Layer 3.1B"],
        ["Tumour EUD", source_context.get("tumour_eud_gy"), "Gy", "Consumed from Layer 3.1B"],
        ["Valley residual fraction", valley_record.get("residual_fraction"), "fraction", spatial.get("status")],
    ] if branch_d else [], "TCP unavailable: configure parameters and provide a valid Layer 3.1B tumour survival result.")
    _set_table(self.layer31d_comparison, [
        ["TCP_MLQ_POISSON_RADIATION_ONLY", _probability_display(radiation), radiation.get("ln_tcp"), radiation.get("expected_surviving_clonogens")],
        ["TCP_MLQ_POISSON_REPOPULATION_CORRECTED", _probability_display(corrected), corrected.get("ln_tcp"), corrected.get("expected_surviving_clonogens")],
    ] if endpoints else [], "No qualified TCP comparison is available.")
    _set_table(self.layer31d_spatial, [[
        item.get("region_id"), item.get("volume_cm3"), item.get("mean_radiation_survival_fraction"),
        item.get("expected_residual_clonogens"), item.get("residual_fraction"), item.get("p0"),
    ] for item in spatial.get("records", [])], "Whole-tumour TCP may be valid; vertex/valley decomposition is unavailable.")
    sensitivity = branch_d.get("sensitivity_analysis") or {}
    _set_table(self.layer31d_sensitivity, [[
        item.get("parameter"), item.get("value"), item.get("tcp_radiation_only"), item.get("expected_surviving_clonogens"),
    ] for item in sensitivity.get("records", [])], "Layer 3.1D sensitivity is disabled or unavailable.")
    d_messages = list(branch_d.get("warnings", []))
    if branch_d.get("reason"): d_messages.insert(0, str(branch_d["reason"]))
    self.layer31d_warning.set_messages(d_messages, blocked=str(branch_d.get("status") or "").upper() == "BLOCKED")
    self.layer31d_provenance.setPlainText(json.dumps({
        "model": branch_d.get("tcp_model"), "clonogen_model": branch_d.get("clonogen_model"),
        "repopulation": branch_d.get("repopulation"), "validation_status": branch_d.get("validation_status"),
        "assumptions": branch_d.get("assumptions"), "gates": branch_d.get("gate_results"),
        "provenance": branch_d.get("provenance"),
    }, indent=2, default=str) if branch_d else "No Layer 3.1D provenance is stored.")
    self.layer31_provenance.setPlainText(json.dumps({
        "scientific_position": result.get("scientific_position"), "scope_exclusions": result.get("scope_exclusions"),
        "fraction_history": history, "treatment_context": result.get("treatment_context"),
        "model_3_1a": branch_a, "model_3_1b": branch_b, "model_3_1c": branch_c,
        "paired_course_comparison": comparison,
        "model_3_1d": branch_d,
        "visualisation": result.get("visualisation"), "provenance": result.get("provenance"),
    }, indent=2, default=str) if result else "No Layer 3.1 provenance is stored.")
    if self.layer31_viewer_run_id and self.layer31_viewer_run_id != record.run_id:
        self.layer31_viewer_status.setText("STALE — Layer 3.1 changed. Rebuild the biological viewer.")
        if self.layer31_viewer is not None:
            self.layer31_viewer.setEnabled(False)


def refresh_layer32(self, case: ASCENDCase) -> None:
    """Present current Layer 3.2 records without recalculating any field or endpoint."""
    record = case.layer3_2
    enabled = case.configuration.layer32_enabled
    self.layer32_enabled.blockSignals(True)
    self.layer32_enabled.setChecked(enabled)
    self.layer32_enabled.blockSignals(False)
    self._update_layer32_enabled_controls(enabled)
    if not enabled:
        self.layer32_status_pill.set_status("not_applicable")
        self.layer32_interpretation_pill.set_status("not_applicable")
        self.layer32_status_text.setText(
            "NOT ASSESSED — DISABLED. Layer 3.2 is excluded from calculation and interpretation for this case."
        )
        self.layer32_warnings.badge.set_status("not_applicable")
        self.layer32_warnings.detail.setText(
            "Enable Layer 3.2 to include the optional non-local research model. Layers 1, 2.1, 2.2, and 3.1 are unaffected."
        )
        disabled_text = "Layer 3.2 is disabled; no result is being considered."
        for table in (
            self.layer32_parameter_table, self.layer32_scenario_table,
            self.layer32_graph_summary, self.layer32_edge_table, self.layer32_gtv_table,
            self.layer32_shell_table, self.layer32_oar_table, self.layer32_assay_table,
            self.layer32_regional_table,
        ):
            _set_table(table, [], disabled_text)
        _set_table(self.layer32_configuration_summary, [[
            "Layer 3.2 inclusion", "Disabled", "not assessed",
        ]])
        self.layer32_provenance.setPlainText(
            "Layer 3.2 is disabled in the current case configuration. Stored historical evidence, if any, is not presented or exported as current."
        )
        self.layer32_viewer_status.setText("Layer 3.2 is disabled; the biological field viewer is unavailable.")
        return
    dependencies_current = (
        case.layer1.calculation_status in {"completed", "completed_with_warnings"}
        and case.layer2_2.calculation_status in {"completed", "completed_with_warnings"}
        and case.layer3_1.calculation_status in {"completed", "completed_with_warnings"}
    )
    current = record.calculation_status in {"completed", "completed_with_warnings"} and dependencies_current
    result = (record.result or {}) if current else {}
    self.layer32_status_pill.set_status(record.calculation_status)
    self.layer32_interpretation_pill.set_status(record.interpretation_status)
    self.layer32_status_text.setText(
        f"Run {record.run_id or '—'}  ·  {len(result.get('edge_metrics', []))} edge(s)  ·  "
        f"{len(result.get('oar_biological_spill', []))} OAR result(s)"
    )
    warnings = list(result.get("warnings", record.warnings))
    if record.calculation_status == "stale":
        warnings.insert(0, record.stale_reason or "Layer 3.2 result is stale.")
    if not dependencies_current:
        warnings.insert(0, "Current Layer 1, Layer 2.2, and Layer 3.1 results are required; stored Layer 3.2 values are hidden.")
    if record.error:
        warnings.insert(0, record.error)
    self.layer32_warnings.set_messages(
        warnings, blocked=canonical_state(record.calculation_status) in {"BLOCKED", "INVALID"},
    )
    model = result.get("model", {})
    parameters = model.get("parameters") or resolved_parameters(case.configuration.layer32_parameters)
    rows = model.get("parameter_rows") or [
        {"parameter": key, "value": value, "units": "configured", "source": "current case configuration"}
        for key, value in parameters.items()
    ]
    parameter_display_names = {
        "hazard_weight_ros": "ROS-like mediator weight",
        "hazard_weight_cytokine": "Cytokine-like mediator weight",
        "nonlocal_scaling": "Non-local exposure scaling s",
    }
    _set_table(self.layer32_parameter_table, [[
        parameter_display_names.get(str(item.get("parameter")), item.get("parameter")),
        item.get("value"), item.get("units"), item.get("source"),
    ] for item in rows])
    _set_table(self.layer32_configuration_summary, [
        ["Non-local scaling s", parameters.get("nonlocal_scaling"), "dimensionless"],
        ["ROS-like weight", parameters.get("hazard_weight_ros"), "dimensionless"],
        ["Cytokine-like weight", parameters.get("hazard_weight_cytokine"), "dimensionless"],
        ["ROS diffusion", parameters.get("diffusion_ros_mm2_per_time"), "mm²/model-time"],
        ["Cytokine diffusion", parameters.get("diffusion_cytokine_mm2_per_time"), "mm²/model-time"],
        ["Vascular sink", "Disabled", "no vessel geometry or uptake model"],
        ["Parameter-set version", model.get("parameter_set_version", "not recorded"), "versioned"],
        ["Calculation status", record.calculation_status, record.interpretation_status],
    ])
    _set_table(self.layer32_scenario_table, [[
        item.get("label"), item.get("status"), item.get("definition") or item.get("reason"),
    ] for item in result.get("comparison_scenarios", [])], "No stored comparison-scenario record is available.")
    summary = result.get("graph_summary", {})
    _set_table(self.layer32_graph_summary, [[
        "Physical plan iPVDR median", summary.get("physical_plan_ipvdr_median"), "Stored Layer 2.2 absorbed-dose graph endpoint",
    ], [
        "Baseline LQ effect-equivalent iPVDR median", summary.get("baseline_lq_effect_equivalent_ipvdr_median"), "Fraction-history-aware alpha P + beta Q baseline",
    ], [
        "Biological effect-equivalent iPVDR median", summary.get("biological_effect_equivalent_ipvdr_median"), "Same graph and 3 mm valley spheres on final effect-equivalent field",
    ], [
        "Biological iPVDR shift", summary.get("biological_ipvdr_shift"), "Signed biological minus physical value",
    ], [
        "Non-local-only iPVDR shift", summary.get("nonlocal_only_ipvdr_shift"), "Final biological minus baseline LQ effect-equivalent value",
    ]] if summary else [], "Run Layer 3.2 to calculate the biological graph reinterpretation.")
    _set_table(self.layer32_edge_table, [[
        item.get("edge_id"), " — ".join(item.get("nodes", [])), item.get("physical_ipvdr"),
        item.get("baseline_lq_effect_equivalent_ipvdr"), item.get("biological_effect_equivalent_ipvdr"),
        item.get("biological_ipvdr_shift"), item.get("nonlocal_only_ipvdr_shift"),
        item.get("valley_effect_shift_gy_equivalent"),
    ] for item in result.get("edge_metrics", [])], "No Layer 3.2 edge metrics are available.")
    gtv = result.get("gtv_biological_context", {})
    _set_table(self.layer32_gtv_table, [[
        key.replace("_", " "), endpoint.get("mean"), endpoint.get("d95"), endpoint.get("d50"),
        endpoint.get("d2"), endpoint.get("units"),
    ] for key, endpoint in gtv.items() if key != "cumulative_nonlocal_hazard"],
        "No whole-GTV Layer 3.2 context is available.")
    _set_table(self.layer32_shell_table, [[
        f"{item.get('shell_mm', [None, None])[0]:g}–{item.get('shell_mm', [None, None])[1]:g}",
        item.get("voxel_count"), item.get("physical_absorbed_dose", {}).get("mean"),
        item.get("biological_effect_equivalent_dose", {}).get("mean"),
        item.get("additional_model_derived_effect_equivalent_dose", {}).get("mean"),
        item.get("final_survival_fraction", {}).get("mean"),
    ] for item in result.get("peri_gtv_spill_shells", [])], "No peri-GTV spill-shell results are available.")
    _set_table(self.layer32_oar_table, [[
        item.get("oar_name"), item.get("classification"), item.get("nearest_vertex_id"),
        item.get("nearest_vertex_distance_mm"), item.get("physical_absorbed_dose", {}).get("mean"),
        item.get("biological_effect_equivalent_dose", {}).get("mean"),
        item.get("biological_effect_equivalent_dose", {}).get("d2"),
        item.get("additional_model_derived_effect_equivalent_dose", {}).get("mean"),
        item.get("compliance_assessment"),
    ] for item in result.get("oar_biological_spill", [])], "No configured rasterised OAR spill results are available.")
    assay = result.get("assay_observables", {})
    _set_table(self.layer32_assay_table, [[
        key.replace("_", " "), item.get("mean"), item.get("maximum"), item.get("units"), assay.get("scope"),
    ] for key, item in assay.items() if isinstance(item, dict)], "No stored model observables are available.")
    _set_table(self.layer32_regional_table, [[
        item.get("display_name"),
        item.get("mean_cumulative_mediator_exposure_h"),
        item.get("p95_cumulative_mediator_exposure_h"),
        item.get("mean_additional_modelled_survival_reduction_percent"),
        item.get("maximum_additional_modelled_survival_reduction_percent"),
        item.get("volume_at_least_5pct_reduction_cc"),
        item.get("mean_final_survival_change_absolute"),
    ] for item in result.get("modelled_regional_exposure_and_consequence", [])],
        "No regional exposure and consequence records are available.")
    self.layer32_provenance.setPlainText(self._json_or_state({
        "scientific_position": result.get("scientific_position"),
        "physical_dose_mutated": result.get("physical_dose_mutated"),
        "model": model, "geometry": result.get("geometry"), "artifacts": result.get("artifacts"),
        "provenance": result.get("provenance"),
    } if result else None, "No Layer 3.2 provenance is available."))
    self._update_layer32_enabled_controls(enabled)
    if self.layer32_viewer_run_id and self.layer32_viewer_run_id != record.run_id:
        self.layer32_viewer_status.setText("STALE — Layer 3.2 changed. Rebuild the stored-field viewer.")
        if self.layer32_viewer is not None:
            self.layer32_viewer.setEnabled(False)
