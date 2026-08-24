from __future__ import annotations

from dataclasses import FrozenInstanceError
import tempfile
import unittest
from pathlib import Path

from ascend.models.config import CaseConfiguration
from ascend.treatment.applicability import resolve_metric_applicability
from ascend.treatment.models import TreatmentComponent, TreatmentContext
from ascend.validation.treatment_context import run_treatment_context_validation, write_treatment_context_report
from ascend.validation.treatment_context.fixtures import STRUCTURES, scenarios


class TreatmentContextValidationTests(unittest.TestCase):
    def test_four_primary_semantic_scenarios_and_negative_matrix(self) -> None:
        result = run_treatment_context_validation()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([item["scenario_id"] for item in result["cases"]], [
            "sib_lrt", "sequential_lrt_boost", "integrated_lrt_cert", "composite_course",
        ])
        self.assertEqual(result["lrt_vs_course_contrast"], {
            "lrt_component_dr": 4.0, "composite_course_dr": 1.6, "status": "PASS",
        })

    def test_sequential_missing_rxl_is_not_applicable(self) -> None:
        context = scenarios()[1].context
        decision = resolve_metric_applicability("peripheral_coverage_v95_rxl", context, STRUCTURES)
        self.assertEqual(decision.applicability, "not_applicable")
        self.assertEqual(decision.reason, "peripheral_prescription_not_defined_for_lrt_component")

    def test_composite_ratio_is_valid_but_context_warned(self) -> None:
        context = scenarios()[3].context
        decision = resolve_metric_applicability("structure_based_dose_ratio", context, STRUCTURES)
        self.assertTrue(decision.applicable)
        self.assertIn("course_level_dr_not_comparable_to_lrt_component_dr", decision.warnings)

    def test_dose_uid_identity_conflict_blocks(self) -> None:
        component = TreatmentComponent("lrt", "lrt", "expected-dose", "plan", 1, 15.0, None, 15.0, "synthetic_validation")
        context = TreatmentContext(
            "sequential_lrt_boost", "lrt_component", "component_specific",
            "wrong-dose", "plan", component, (component,),
            {"Rx_L": {"gy": None, "source": "unavailable"}, "Rx_H": {"gy": 15.0, "source": "protocol_configuration"}},
        )
        decision = resolve_metric_applicability("mean_peak_dose", context, STRUCTURES)
        self.assertEqual(decision.calculation_status, "blocked")
        self.assertEqual(decision.reason, "dose_uid_not_associated_with_selected_treatment_component")

    def test_fractionation_is_component_specific_and_configuration_validates(self) -> None:
        first = TreatmentComponent("lrt", "lrt", "d1", "p1", 1, 15.0, None, 15.0, "synthetic_validation")
        second = TreatmentComponent("cert", "conventional_rt", "d2", "p2", 10, 30.0, None, None, "synthetic_validation")
        self.assertEqual(first.dose_per_fraction_gy, 15.0)
        self.assertEqual(second.dose_per_fraction_gy, 3.0)
        config = CaseConfiguration(
            treatment_delivery_mode="sequential_lrt_boost",
            dose_context="lrt_component",
            prescription_context="component_specific",
            treatment_components=[first.to_dict(), second.to_dict()],
            selected_treatment_component_id="lrt",
        )
        config.validate()

    def test_all_treatment_approaches_timing_hash_and_immutability(self) -> None:
        component = TreatmentComponent(
            "lrt", "LRT", "dose", "plan", 1, 15.0, 5.0, 15.0, "user_supplied",
            start_time="2026-08-01", end_time="2026-08-01", preceding_gap_days=7.0,
            geometry_hash="geometry", prescription_source="protocol_configuration",
        )
        for approach in ("LRT_ALONE", "LRT_SEQUENTIAL_CERT", "LRT_INTEGRATED", "UNKNOWN"):
            context = TreatmentContext(
                "unknown", "lrt_component", "component_specific", "dose", "plan", component,
                (component,), {}, treatment_approach=approach,
            )
            self.assertEqual(context.treatment_approach, approach)
            self.assertEqual(context.components[0].preceding_gap_days, 7.0)
            self.assertEqual(len(context.context_hash), 64)
            self.assertFalse(context.provenance.get("implicit_dose_warping", False))
            with self.assertRaises(FrozenInstanceError):
                context.dose_context = "composite_course"
            with self.assertRaises(TypeError):
                context.prescriptions["Rx_L"] = {"gy": 5.0}
            with self.assertRaises(TypeError):
                context.provenance["implicit_dose_warping"] = True

    def test_missing_component_prescription_and_fractionation_are_explicit(self) -> None:
        component = TreatmentComponent("lrt", "LRT", source="user_supplied")
        self.assertIsNone(component.prescription_gy)
        self.assertIsNone(component.fraction_count)
        self.assertIsNone(component.dose_per_fraction_gy)

    def test_report_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            artifacts = write_treatment_context_report(run_treatment_context_validation(), Path(folder))
            self.assertEqual(len(artifacts), 5)
            self.assertTrue(all(Path(path).is_file() for path in artifacts.values()))


if __name__ == "__main__":
    unittest.main()
