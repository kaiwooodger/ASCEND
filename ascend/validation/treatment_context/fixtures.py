"""Deterministic synthetic fixtures used by independent validation workstreams."""

from __future__ import annotations

from dataclasses import dataclass

from ascend.treatment.models import TreatmentComponent, TreatmentContext


STRUCTURES = {"GTV": "GTV", "T_L": "PTVLOW", "VTV_H": "VTVH", "VTV_L": "VTVL"}


@dataclass(frozen=True)
class TreatmentScenario:
    """Represent treatment scenario state and behavior."""
    scenario_id: str
    context: TreatmentContext
    peak_gy: float
    valley_gy: float
    expected: dict[str, tuple[str, str | None]]

    @property
    def dose_ratio(self) -> float:
        """Handle dose ratio for the enclosing ASCEND workflow."""
        return self.peak_gy / self.valley_gy


def _rx(low: float | None, high: float | None, source: str = "protocol_configuration") -> dict[str, dict[str, object]]:
    return {
        "Rx_L": {"gy": low, "fractions": None, "source": source if low is not None else "unavailable"},
        "Rx_H": {"gy": high, "fractions": None, "source": source if high is not None else "unavailable"},
    }


def scenarios() -> tuple[TreatmentScenario, ...]:
    """Handle scenarios for the enclosing ASCEND workflow."""
    sib_component = TreatmentComponent(
        "sib-plan", "integrated_plan", "dose-sib", "plan-sib", 20, 66.7,
        20.0, 66.7, "synthetic_validation",
    )
    lrt_component = TreatmentComponent(
        "lrt-boost", "lrt", "dose-lrt", "plan-lrt", 1, 15.0,
        None, 15.0, "synthetic_validation",
    )
    cert_component = TreatmentComponent(
        "cert", "conventional_rt", "dose-cert", "plan-cert", 10, 30.0,
        None, None, "synthetic_validation",
    )
    integrated_component = TreatmentComponent(
        "integrated", "integrated_plan", "dose-integrated", "plan-integrated", 10, 40.0,
        20.0, 40.0, "synthetic_validation",
    )
    composite_component = TreatmentComponent(
        "course", "composite_course", "dose-composite", None, None, 45.0,
        20.0, 40.0, "derived_composite",
    )
    normal = {metric: ("valid", None) for metric in (
        "peripheral_coverage_v95_rxl", "high_dose_coverage_v95_rxh",
        "high_dose_volume_fraction", "mean_peak_dose", "mean_valley_dose",
        "structure_based_dose_ratio",
    )}
    sequential = dict(normal)
    sequential["peripheral_coverage_v95_rxl"] = (
        "not_applicable", "peripheral_prescription_not_defined_for_lrt_component",
    )
    composite = dict(normal)
    composite["structure_based_dose_ratio"] = (
        "valid", "course_level_dr_not_comparable_to_lrt_component_dr",
    )
    return (
        TreatmentScenario(
            "sib_lrt",
            TreatmentContext(
                "simultaneous_integrated_lrt", "complete_single_plan", "complete_plan",
                "dose-sib", "plan-sib", sib_component, (sib_component,), _rx(20.0, 66.7), False,
            ),
            66.7, 20.0, normal,
        ),
        TreatmentScenario(
            "sequential_lrt_boost",
            TreatmentContext(
                "sequential_lrt_boost", "lrt_component", "component_specific",
                "dose-lrt", "plan-lrt", lrt_component, (lrt_component, cert_component), _rx(None, 15.0), False,
            ),
            20.0, 5.0, sequential,
        ),
        TreatmentScenario(
            "integrated_lrt_cert",
            TreatmentContext(
                "integrated_lrt_cert", "complete_single_plan", "complete_plan",
                "dose-integrated", "plan-integrated", integrated_component, (integrated_component,), _rx(20.0, 40.0), True,
            ),
            40.0, 25.0, normal,
        ),
        TreatmentScenario(
            "composite_course",
            TreatmentContext(
                "composite_course", "composite_course", "composite_course",
                "dose-composite", None, composite_component,
                (lrt_component, cert_component, composite_component), _rx(20.0, 40.0), True,
            ),
            40.0, 25.0, composite,
        ),
    )


def mismatch_scenarios() -> tuple[TreatmentScenario, ...]:
    """Handle mismatch scenarios for the enclosing ASCEND workflow."""
    component = TreatmentComponent(
        "lrt-only", "lrt", "dose-lrt", "plan-lrt", 1, 15.0,
        10.0, 20.0, "synthetic_validation",
    )
    composite = TreatmentComponent(
        "course", "composite_course", "dose-course", None, None, 45.0,
        10.0, 20.0, "derived_composite",
    )
    base = {metric: ("valid", None) for metric in (
        "peripheral_coverage_v95_rxl", "high_dose_coverage_v95_rxh",
        "high_dose_volume_fraction", "mean_peak_dose", "mean_valley_dose",
        "structure_based_dose_ratio",
    )}
    wrong_composite = dict(base)
    wrong_composite["peripheral_coverage_v95_rxl"] = ("invalid", "prescription_dose_context_mismatch")
    wrong_composite["high_dose_coverage_v95_rxh"] = ("invalid", "prescription_dose_context_mismatch")
    wrong_lrt = dict(base)
    wrong_lrt["peripheral_coverage_v95_rxl"] = ("invalid", "prescription_dose_context_mismatch")
    wrong_lrt["high_dose_coverage_v95_rxh"] = ("invalid", "prescription_dose_context_mismatch")
    missing_high = dict(base)
    missing_high["high_dose_coverage_v95_rxh"] = ("invalid", "missing_prescription")
    missing_low = dict(base)
    missing_low["peripheral_coverage_v95_rxl"] = ("invalid", "missing_prescription")
    manual = dict(base)
    manual["peripheral_coverage_v95_rxl"] = ("valid", "manual_prescription_input")
    manual["high_dose_coverage_v95_rxh"] = ("valid", "manual_prescription_input")
    equal = dict(base)
    equal["peripheral_coverage_v95_rxl"] = ("valid", "equal_peak_and_peripheral_prescriptions")
    equal["high_dose_coverage_v95_rxh"] = ("valid", "equal_peak_and_peripheral_prescriptions")
    identity_conflict = {
        metric: ("invalid", "dose_uid_not_associated_with_selected_treatment_component")
        for metric in base
    }
    plan_conflict = {
        metric: ("invalid", "plan_uid_not_associated_with_selected_treatment_component")
        for metric in base
    }
    return (
        TreatmentScenario(
            "composite_dose_with_boost_rx",
            TreatmentContext(
                "composite_course", "composite_course", "component_specific",
                "dose-course", None, composite, (component, composite), _rx(10.0, 20.0), True,
            ), 40.0, 25.0, wrong_composite,
        ),
        TreatmentScenario(
            "lrt_component_with_total_course_rx",
            TreatmentContext(
                "sequential_lrt_boost", "lrt_component", "complete_plan",
                "dose-lrt", "plan-lrt", component, (component,), _rx(10.0, 20.0), False,
            ), 20.0, 5.0, wrong_lrt,
        ),
        TreatmentScenario(
            "missing_rx_h",
            TreatmentContext(
                "simultaneous_integrated_lrt", "complete_single_plan", "complete_plan",
                "dose-lrt", "plan-lrt", component, (component,), _rx(10.0, None), False,
            ), 20.0, 5.0, missing_high,
        ),
        TreatmentScenario(
            "missing_rx_l",
            TreatmentContext(
                "simultaneous_integrated_lrt", "complete_single_plan", "complete_plan",
                "dose-lrt", "plan-lrt", component, (component,), _rx(None, 20.0), False,
            ), 20.0, 5.0, missing_low,
        ),
        TreatmentScenario(
            "manual_prescriptions",
            TreatmentContext(
                "simultaneous_integrated_lrt", "complete_single_plan", "manual",
                "dose-lrt", "plan-lrt", component, (component,), _rx(10.0, 20.0, "user_supplied"), False,
            ), 20.0, 5.0, manual,
        ),
        TreatmentScenario(
            "equal_peak_and_peripheral_rx",
            TreatmentContext(
                "simultaneous_integrated_lrt", "complete_single_plan", "complete_plan",
                "dose-lrt", "plan-lrt", component, (component,), _rx(20.0, 20.0), False,
            ), 20.0, 5.0, equal,
        ),
        TreatmentScenario(
            "dose_uid_identity_conflict",
            TreatmentContext(
                "sequential_lrt_boost", "lrt_component", "component_specific",
                "different-dose", "plan-lrt", component, (component,), _rx(10.0, 20.0), False,
            ), 20.0, 5.0, identity_conflict,
        ),
        TreatmentScenario(
            "rtplan_component_identity_conflict",
            TreatmentContext(
                "sequential_lrt_boost", "lrt_component", "component_specific",
                "dose-lrt", "different-plan", component, (component,), _rx(10.0, 20.0), False,
            ), 20.0, 5.0, plan_conflict,
        ),
    )
