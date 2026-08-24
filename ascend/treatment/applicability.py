"""Treatment-context rules that separate calculability from clinical interpretability."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ascend.models.status import CalculationStatus, InterpretationStatus

from .models import TreatmentContext


COVERAGE_REQUIREMENTS = {
    "peripheral_coverage_v95_rxl": ("Rx_L", "T_L"),
    "high_dose_coverage_v95_rxh": ("Rx_H", "VTV_H"),
}
REQUIRED_STRUCTURES = {
    "high_dose_volume_fraction": ("GTV", "VTV_H"),
    "mean_peak_dose": ("VTV_H",),
    "mean_valley_dose": ("VTV_L",),
    "structure_based_dose_ratio": ("VTV_H", "VTV_L"),
}


@dataclass(frozen=True)
class ApplicabilityDecision:
    """Represent applicability decision state and behavior."""
    metric_id: str
    applicable: bool
    applicability: str
    calculation_status: str
    interpretation_status: str
    reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    required_prescription: str | None = None
    required_dose_component: str | None = None
    required_inputs: tuple[str, ...] = field(default_factory=tuple)
    missing_inputs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        value["required_inputs"] = list(self.required_inputs)
        value["missing_inputs"] = list(self.missing_inputs)
        value["applicability_status"] = (
            "BLOCKED" if self.calculation_status == CalculationStatus.BLOCKED.value
            else "APPLICABLE" if self.applicable else "NOT_APPLICABLE"
        )
        value["reason_code"] = self.reason
        value["explanation"] = {
            "peripheral_prescription_not_defined_for_lrt_component": (
                "No protocol-defined peripheral LRT prescription exists for the selected sequential LRT component."
            ),
            "missing_prescription": "The required prescription is not explicitly available.",
            "missing_structure": "A required canonical structure is not available in the validated case.",
            "dose_uid_not_associated_with_selected_treatment_component": "The selected dose is not bound to the analysis component.",
            "plan_uid_not_associated_with_selected_treatment_component": "The selected plan is not bound to the analysis component.",
        }.get(self.reason, self.reason)
        return value


def _prescription(context: TreatmentContext, key: str) -> tuple[float | None, str]:
    item = context.prescriptions.get(key, {})
    value = item.get("gy")
    return (float(value) if value is not None else None, str(item.get("source", "unavailable")))


def resolve_metric_applicability(
    metric_id: str,
    treatment_context: TreatmentContext,
    structures: dict[str, str | list[str]],
    protocol_config: dict[str, Any] | None = None,
) -> ApplicabilityDecision:
    """Resolve metric applicability without silently guessing ambiguous meaning."""
    context = treatment_context
    warnings: list[str] = []
    interpretation = InterpretationStatus.PROTOCOL_INTERPRETABLE.value
    selected = context.selected_component
    if selected and selected.dose_object_uid and context.dose_object_uid and selected.dose_object_uid != context.dose_object_uid:
        return ApplicabilityDecision(
            metric_id, False, "invalid", CalculationStatus.BLOCKED.value,
            InterpretationStatus.NOT_INTERPRETABLE.value,
            "dose_uid_not_associated_with_selected_treatment_component",
            required_dose_component=selected.component_id,
        )
    if selected and selected.plan_uid and context.plan_uid and selected.plan_uid != context.plan_uid:
        return ApplicabilityDecision(
            metric_id, False, "invalid", CalculationStatus.BLOCKED.value,
            InterpretationStatus.NOT_INTERPRETABLE.value,
            "plan_uid_not_associated_with_selected_treatment_component",
            required_dose_component=selected.component_id,
        )
    required_structures = REQUIRED_STRUCTURES.get(metric_id, ())
    if metric_id in COVERAGE_REQUIREMENTS:
        required_structures = (COVERAGE_REQUIREMENTS[metric_id][1],)
    missing = [role for role in required_structures if not structures.get(role)]
    if missing:
        return ApplicabilityDecision(
            metric_id, False, "invalid", CalculationStatus.BLOCKED.value,
            InterpretationStatus.NOT_INTERPRETABLE.value,
            "missing_structure", tuple(f"missing_{role.lower()}" for role in missing),
            required_inputs=tuple(required_structures), missing_inputs=tuple(missing),
        )
    if metric_id in COVERAGE_REQUIREMENTS:
        rx_key, _role = COVERAGE_REQUIREMENTS[metric_id]
        rx_value, source = _prescription(context, rx_key)
        if (
            metric_id == "peripheral_coverage_v95_rxl"
            and context.treatment_approach == "LRT_SEQUENTIAL_CERT"
            and context.dose_context == "lrt_component"
        ):
            if selected is None or selected.rx_low_gy is None:
                return ApplicabilityDecision(
                    metric_id, False, "not_applicable", CalculationStatus.NOT_RUN.value,
                    InterpretationStatus.PROTOCOL_INTERPRETABLE.value,
                    "peripheral_prescription_not_defined_for_lrt_component",
                    required_prescription=rx_key,
                    required_dose_component=selected.component_id if selected else "lrt_component",
                    required_inputs=(rx_key, "T_L"), missing_inputs=(rx_key,),
                )
            rx_value = float(selected.rx_low_gy)
            source = str(selected.prescription_source or selected.source)
        if rx_value is None:
            return ApplicabilityDecision(
                metric_id, False, "invalid", CalculationStatus.BLOCKED.value,
                InterpretationStatus.NOT_INTERPRETABLE.value, "missing_prescription",
                required_prescription=rx_key,
                required_inputs=(rx_key, _role), missing_inputs=(rx_key,),
            )
        if source == "user_supplied" or context.prescription_context == "manual":
            warnings.append("manual_prescription_input")
            interpretation = InterpretationStatus.PROVISIONAL.value
        rx_low, _rx_low_source = _prescription(context, "Rx_L")
        rx_high, _rx_high_source = _prescription(context, "Rx_H")
        if rx_low is not None and rx_high is not None and abs(rx_low - rx_high) <= 1.0e-12:
            warnings.append("equal_peak_and_peripheral_prescriptions")
            interpretation = InterpretationStatus.PROVISIONAL.value
        mismatch = (
            context.dose_context == "composite_course" and context.prescription_context == "component_specific"
        ) or (
            context.dose_context == "lrt_component"
            and context.prescription_context in {"complete_plan", "composite_course"}
        )
        if mismatch:
            return ApplicabilityDecision(
                metric_id, False, "invalid", CalculationStatus.COMPLETED.value,
                InterpretationStatus.NOT_INTERPRETABLE.value,
                "prescription_dose_context_mismatch",
                ("protocol_coverage_suppressed_for_mismatched_context",),
                required_prescription=rx_key,
                required_dose_component=selected.component_id if selected else context.dose_context,
            )
    if context.dose_context == "composite_course" and metric_id == "structure_based_dose_ratio":
        warnings.append("course_level_dr_not_comparable_to_lrt_component_dr")
        interpretation = InterpretationStatus.PROVISIONAL.value
    if context.treatment_delivery_mode == "integrated_lrt_cert" and metric_id in {
        "mean_valley_dose", "structure_based_dose_ratio",
    }:
        warnings.append("valley_includes_cert_background")
    if context.prescription_context == "unknown" or context.dose_context == "unknown":
        warnings.append("treatment_context_unconfirmed")
        interpretation = InterpretationStatus.PROVISIONAL.value
    return ApplicabilityDecision(
        metric_id, True, "valid", CalculationStatus.COMPLETED.value, interpretation,
        warnings=tuple(warnings),
        required_prescription=COVERAGE_REQUIREMENTS.get(metric_id, (None,))[0],
        required_dose_component=selected.component_id if selected else context.dose_context,
    )


def resolve_all_metric_applicability(
    treatment_context: TreatmentContext,
    structures: dict[str, str | list[str]],
    metric_ids: tuple[str, ...],
    protocol_config: dict[str, Any] | None = None,
) -> list[ApplicabilityDecision]:
    """Resolve all metric applicability without silently guessing ambiguous meaning."""
    return [resolve_metric_applicability(item, treatment_context, structures, protocol_config) for item in metric_ids]


def apply_context_decisions(
    metrics: list[dict[str, Any]],
    decisions: list[ApplicabilityDecision],
) -> None:
    """Apply semantic eligibility after calculation without changing numerical formulas."""
    indexed = {item.metric_id: item for item in decisions}
    for metric in metrics:
        decision = indexed.get(str(metric.get("metric_id")))
        if decision is None:
            continue
        metric["treatment_context_applicability"] = decision.to_dict()
        metric["warnings"] = sorted(set(metric.get("warnings", [])) | set(decision.warnings))
        if not decision.applicable:
            metric["value"] = None
            metric["applicability"] = decision.applicability
            metric["calculation_status"] = decision.calculation_status
            metric["applicability_status"] = decision.to_dict()["applicability_status"]
            metric["reason"] = decision.reason
            metric["warnings"] = sorted(set(metric["warnings"]) | ({decision.reason} if decision.reason else set()))
