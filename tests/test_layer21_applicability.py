from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ascend.layer2.metrics.service import Layer21Service
from ascend.validation.provenance import canonical_hash

from .helpers import synthetic_case


def metrics_by_id(payload: dict) -> dict[str, dict]:
    return {item["metric_id"]: item for item in payload["harmonised_metrics"]}


class Layer21ApplicabilityTests(unittest.TestCase):
    def _sequential(self, case, rx_low_gy):
        case.configuration.treatment_approach = "LRT_SEQUENTIAL_CERT"
        case.configuration.treatment_delivery_mode = "sequential_lrt_boost"
        case.configuration.dose_context = "lrt_component"
        case.configuration.prescription_context = "component_specific"
        case.configuration.treatment_components = [{
            "component_id": "LRT", "component_type": "LRT",
            "dose_object_uid": "1.2.3", "plan_uid": "1.2.5",
            "fraction_count": 1, "prescription_gy": 20.0,
            "rx_low_gy": rx_low_gy, "rx_high_gy": 20.0,
            "source": "synthetic_validation", "prescription_source": "protocol_configuration",
        }]
        case.configuration.selected_treatment_component_id = "LRT"
        case.configuration_hash = canonical_hash(case.configuration.to_dict())

    def test_lrt_alone_peripheral_coverage_is_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.configuration.treatment_approach = "LRT_ALONE"
            result = Layer21Service().run(case).result
            metric = metrics_by_id(result)["peripheral_coverage_v95_rxl"]
            self.assertEqual(metric["treatment_context_applicability"]["applicability_status"], "APPLICABLE")
            self.assertIsNotNone(metric["value"])

    def test_sequential_missing_component_rxl_is_not_applicable_without_false_fail(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            baseline = Layer21Service().run(case).result
            self._sequential(case, None)
            result = Layer21Service().run(case).result
            baseline_metrics = metrics_by_id(baseline)
            metrics = metrics_by_id(result)
            peripheral = metrics["peripheral_coverage_v95_rxl"]
            self.assertEqual(peripheral["applicability"], "not_applicable")
            self.assertEqual(peripheral["applicability_status"], "NOT_APPLICABLE")
            self.assertEqual(peripheral["calculation_status"], "not_run")
            self.assertIsNone(peripheral["value"])
            self.assertNotIn("FAIL", str(peripheral).upper())
            for metric_id in set(metrics) - {"peripheral_coverage_v95_rxl"}:
                self.assertEqual(metrics[metric_id].get("value"), baseline_metrics[metric_id].get("value"))

    def test_sequential_explicit_component_rxl_is_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            self._sequential(case, 5.0)
            metric = metrics_by_id(Layer21Service().run(case).result)["peripheral_coverage_v95_rxl"]
            self.assertEqual(metric["treatment_context_applicability"]["applicability_status"], "APPLICABLE")
            self.assertIsNotNone(metric["value"])

    def test_vertices_qa_stores_local_and_global_fwhm_with_distances(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder), explicit_vertices=True)
            supporting = Layer21Service().run(case).result["supporting_outputs"]
            records = supporting["per_vertex_qa"]
            self.assertEqual(len(records), 4)
            self.assertTrue(all(item["centroid_lps_mm"] is not None for item in records))
            self.assertTrue(all(abs(item["local_fwhm_mm"] - 1.333333) < 1.0e-6 for item in records))
            self.assertTrue(all(item["nearest_vertex_distance_mm"] == 8.0 for item in records))
            self.assertEqual(len(supporting["vertex_connections"]), 3)
            summary = supporting["global_fwhm_summary"]
            self.assertEqual(summary["status"], "available")
            self.assertEqual(summary["vertex_count"], 4)
            self.assertAlmostEqual(summary["average_fwhm_mm"], 1.333333, places=6)
            self.assertAlmostEqual(summary["median_fwhm_mm"], 1.333333, places=6)


if __name__ == "__main__":
    unittest.main()
