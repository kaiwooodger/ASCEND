"""Current-result checks shared by case and branch export adapters."""

from __future__ import annotations

from ascend.models.case import ASCENDCase, LayerRun


COMPLETED_STATES = frozenset({"completed", "completed_with_warnings"})


def require_current_result(case: ASCENDCase, record: LayerRun) -> None:
    """Reject obsolete evidence while allowing current diagnostic gate results."""
    if not record.result:
        raise ValueError(f"No stored {record.layer} result is available.")
    if record.calculation_status in {"stale", "running", "not_run", "not_implemented"}:
        raise ValueError(f"Cannot export {record.layer}: result is {record.calculation_status}; rerun the layer first.")
    if record.layer != "layer1":
        if case.layer1.calculation_status not in COMPLETED_STATES or case.layer1_status not in {"PASS", "WARN"}:
            raise ValueError(f"Cannot export {record.layer}: a current validated Layer 1 result is required.")
        if record.parent_layer1_run_id != case.layer1.run_id:
            raise ValueError(f"Cannot export {record.layer}: its parent Layer 1 run has changed; rerun the layer first.")

