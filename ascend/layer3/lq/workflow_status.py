"""Summarise requested biological branches without changing their calculations."""

from __future__ import annotations

from ascend.models.config import CaseConfiguration


def summarise_workflow(payload: dict, configuration: CaseConfiguration) -> None:
    """Keep usable branch results, but explicitly report an incomplete workflow."""
    branches = {
        "3.1A": "layer3_1a_conventional_lq",
        "3.1B": "layer3_1b_high_dose_sfrt_response",
        "3.1C": "layer3_1c_modelled_therapeutic_ratio",
        "3.1D": "layer3_1d_tumour_control_probability",
    }
    required = {"3.1A", "3.1B"}
    if configuration.layer31c_oar_rois:
        required.add("3.1C")
    if configuration.layer31_tcp_parameters:
        required.add("3.1D")
    if configuration.layer31_sensitivity_sweep_enabled:
        branches["sensitivity"] = "layer3_1c_sensitivity_scenario_matrix"
        required.add("sensitivity")
    rows = []
    reasons = []
    for label, key in branches.items():
        branch = payload.get(key) or {}
        state = str(branch.get("status") or branch.get("calculation_status") or "not_run").upper()
        complete = state in {"PASS", "WARN", "COMPLETED", "COMPLETED_WITH_WARNINGS"}
        reason = branch.get("reason") or "; ".join(branch.get("blocking_reasons") or [])
        if label in required and not complete:
            reasons.append(f"{label}: {reason or state}")
        rows.append({"branch": label, "required": label in required, "status": state, "complete": complete, "reason": reason})
    any_complete = any(row["complete"] for row in rows if row["required"])
    workflow_status = "complete" if not reasons else ("partial" if any_complete else "blocked")
    payload["workflow_status"] = workflow_status
    payload["workflow_branches"] = rows
    payload["blocking_reasons"] = sorted(set(payload.get("blocking_reasons", [])) | set(reasons))
    if reasons:
        # A usable branch remains inspectable; it does not imply completion of the course workflow.
        payload["interpretation_status"] = "provisional" if any_complete else "not_interpretable"
        payload["calculation_status"] = "completed_with_warnings" if any_complete else "blocked"
        payload["warnings"] = sorted(set(payload.get("warnings", [])) | {"layer31_workflow_incomplete"})
        payload["status"] = "WARN" if any_complete else "BLOCKED"

