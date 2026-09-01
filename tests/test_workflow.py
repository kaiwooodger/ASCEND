from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ascend.app.controller import ApplicationController
from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.metrics.service import Layer21Service
from ascend.models.config import CaseConfiguration, Prescription
from ascend.reporting.export import export_case

from .helpers import synthetic_case


class WorkflowTests(unittest.TestCase):
    def test_optional_supporting_calculations_are_skipped_before_layer21(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory), explicit_vertices=True, include_oar=True)
            case.configuration.supporting_outputs_enabled = True
            case.configuration.supporting_output_categories = ["coverage", "peak_valley", "integrity"]
            case.configuration.oar_structures = [{
                "name": "Heart", "classification": "separate_critical_oar",
                "roi_identity": {"rtstruct_sop_instance_uid": "1.2.4", "roi_number": 9},
            }]
            result = Layer21Service().run(case).result
            selection = result["supporting_output_selection"]
            self.assertTrue(selection["selection_applied_before_calculation"])
            self.assertEqual(selection["skipped_optional_calculations"], ["oar_geometry", "per_vertex", "protocol_native"])
            self.assertNotIn("per_vertex_qa", result["supporting_outputs"])
            self.assertNotIn("oar_vertex_geometry", result["supporting_outputs"])
            self.assertEqual(result["oar_vertex_geometry"]["status"], "not_selected")
    def test_browser_workstation_assets_are_present(self) -> None:
        static = Path(__file__).resolve().parents[1] / "ascend" / "web" / "static"
        browser_source = (static / "app.js").read_text(encoding="utf-8")
        self.assertIn("ASCEND 1.5.0", (static / "index.html").read_text(encoding="utf-8"))
        self.assertIn("127.0.0.1", __import__("inspect").getsource(__import__("ascend.web.server", fromlist=["launch"]).launch))
        self.assertTrue((static / "app.js").is_file())
        self.assertTrue((static / "styles.css").is_file())
        self.assertIn("/api/run/layer3_1", browser_source)
        self.assertIn("structure_bindings:previous.structure_bindings||{}", browser_source)

    def test_layer21_and_layer22_use_one_layer1_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer2_1 = Layer21Service().run(case)
            case.layer2_2 = Layer22Service().run(case)
            metrics = {item["metric_id"]: item for item in case.layer2_1.result["harmonised_metrics"]}
            self.assertEqual(metrics["structure_based_dose_ratio"]["formula"], "Dmean(VTV_H) / Dmean(VTV_L)")
            self.assertAlmostEqual(metrics["mean_peak_dose"]["value"], 20.0)
            self.assertAlmostEqual(case.layer2_2.result["plan_ipvdr"]["primary_median"], 4.0)
            self.assertEqual(case.layer2_2.parent_layer1_run_id, "SYNTHETIC_L1")
            self.assertEqual(case.layer2_2.result["vertex_source"], "connected_components_derived")
            self.assertEqual(case.layer2_2.result["frozen_definitions"]["vertex_source"], "connected_components_derived")
            supporting = case.layer2_1.result["supporting_outputs"]
            self.assertEqual(supporting["vertex_analysis"]["source"], "connected_components_derived_from_aggregate_vtv_h")
            self.assertEqual(supporting["vertex_analysis"]["status"], "available")
            self.assertEqual(supporting["vertex_analysis"]["stored_record_count"], 4)
            self.assertEqual(supporting["protocol_native_endpoint_status"]["status"], "not_configured")
            self.assertEqual(len(supporting["per_vertex_qa"]), 4)
            self.assertTrue(all(float(item["volume_cc"]) > 0 for item in supporting["per_vertex_qa"]))
            self.assertEqual(supporting["high_dose_coverage_context"]["number_of_vertices"], 4)
            self.assertEqual(supporting["high_dose_volume_fraction_context"]["common_volume_basis"], "contour_stack")
            self.assertFalse(supporting["peak_valley_dose_context"]["peak_valley_overlap_warning"])
            self.assertFalse(supporting["peak_valley_dose_context"]["valley_outside_gtv_warning"])
            self.assertEqual(supporting["ratio_context"]["formula"], "Dmean(VTV_H) / Dmean(VTV_L)")
            self.assertEqual(len(supporting["integrity_and_interpretability_qa"]["individual_vertex_mask_hashes"]), 4)

    def test_dependency_invalidation_is_selective(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer2_1 = Layer21Service().run(case)
            case.layer2_2 = Layer22Service().run(case)
            controller = ApplicationController(case)
            config = CaseConfiguration.from_dict(case.configuration.to_dict())
            config.prescriptions["Rx_H"] = Prescription(21.0, 1, "protocol_configuration")
            controller.configure(config)
            self.assertEqual(case.layer2_1.calculation_status, "stale")
            self.assertNotEqual(case.layer2_2.calculation_status, "stale")
            self.assertEqual(case.layer1_status, "PASS")

    def test_prescription_change_invalidates_biological_coverage_suite(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer3_1.calculation_status = "completed"
            controller = ApplicationController(case)
            config = CaseConfiguration.from_dict(case.configuration.to_dict())
            config.prescriptions["Rx_H"] = Prescription(21.0, 1, "protocol_configuration")
            controller.configure(config)
            self.assertEqual(case.layer3_1.calculation_status, "stale")

    def test_changing_tps_dvh_reference_invalidates_layer1(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            controller = ApplicationController(case)
            config = CaseConfiguration.from_dict(case.configuration.to_dict())
            config.tps_metrics_csv = "/references/eclipse_dvh"
            controller.configure(config)
            self.assertEqual(case.layer1.calculation_status, "stale")
            self.assertEqual(case.layer1_status, "STALE")

    def test_changing_oar_geometry_invalidates_layer1_rasterisation_and_dependants(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer2_1 = Layer21Service().run(case)
            case.layer2_2 = Layer22Service().run(case)
            case.layer3_1.calculation_status = "completed_with_warnings"
            case.layer3_1.result = {"roi_results": [{"metrics": {"bed_mean": 12.0}}]}
            config = CaseConfiguration.from_dict(case.configuration.to_dict())
            config.oar_structures = [{"name": "Heart", "classification": "containing_organ"}]
            controller = ApplicationController(case)
            controller.configure(config)
            self.assertEqual(case.layer1.calculation_status, "stale")
            self.assertEqual(case.layer2_1.calculation_status, "stale")
            self.assertEqual(case.layer2_2.calculation_status, "stale")
            self.assertEqual(case.layer3_1.calculation_status, "stale")
            self.assertEqual(case.layer1_status, "STALE")
            blocked = controller.run_layer31()
            self.assertEqual(blocked.calculation_status, "blocked")
            self.assertIn("current validated Layer 1", blocked.error)
            self.assertIsNone(blocked.result)

    def test_explicit_rtstruct_vertex_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder), explicit_vertices=True)
            case.layer2_1 = Layer21Service().run(case)
            case.layer2_2 = Layer22Service().run(case)
            self.assertEqual(case.layer2_2.result["vertex_source"], "explicit_rtstruct_vertices")
            self.assertNotIn("individual_vertices_unavailable_components_used", case.layer2_2.warnings)
            supporting = case.layer2_1.result["supporting_outputs"]
            self.assertEqual(supporting["vertex_analysis"]["source"], "explicit_rtstruct_vertices")
            self.assertEqual(supporting["vertex_analysis"]["stored_record_count"], 4)
            self.assertTrue(all(float(item["volume_cc"]) > 0 for item in supporting["per_vertex_qa"]))
            self.assertEqual(
                [item["vertex_id"] for item in case.layer2_1.result["per_vertex_quality_control"]],
                ["VTVH_01", "VTVH_02", "VTVH_03", "VTVH_04"],
            )
            self.assertEqual(
                supporting["vertex_analysis"]["aggregate_individual_mask_consistency"]["status"], "PASS"
            )

    def test_optional_oar_vertex_geometry_is_descriptive_and_exported(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = synthetic_case(root / "case", include_oar=True)
            case.layer2_1 = Layer21Service().run(case)
            geometry = case.layer2_1.result["oar_vertex_geometry"]
            self.assertEqual(geometry["status"], "available")
            record = geometry["records"][0]
            self.assertEqual(record["oar_name"], "Heart")
            self.assertEqual(record["classification"], "containing_organ")
            self.assertAlmostEqual(record["oar_volume_cc"], 0.027)
            self.assertAlmostEqual(record["overlap_volume_cc"], 0.001)
            self.assertEqual(record["aggregate_vtvh_minimum_surface_distance_mm"], 0.0)
            self.assertEqual(record["nearest_vertex_id"], "VTVH_CC_01")
            self.assertEqual(record["nearest_vertex_distance_mm"], 0.0)
            self.assertEqual(record["compliance_interpretation"], "not_performed")
            paths = export_case(case, root / "exports")
            self.assertIn(root / "exports" / "layer2_1_supporting_outputs.json", paths)
            self.assertIn(root / "exports" / "layer2_1_oar_vertex_geometry.csv", paths)

    def test_supporting_outputs_explain_missing_vertex_context_and_protocol_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.configuration.prescriptions["Rx_H"] = Prescription(None, 1, "unavailable")
            case.layer2_1 = Layer21Service().run(case)
            supporting = case.layer2_1.result["supporting_outputs"]
            self.assertEqual(supporting["vertex_analysis"]["status"], "available_with_unassessed_v95")
            self.assertIn("Rx_H is unresolved", supporting["vertex_analysis"]["reason"])
            self.assertEqual(case.layer2_1.result["per_vertex_quality_control"], [])
            self.assertEqual(len(supporting["per_vertex_qa"]), 4)
            self.assertIsNone(supporting["per_vertex_qa"][0]["v95_rxh_pct"])
            self.assertEqual(supporting["per_vertex_qa"][0]["v95_rxh_applicability"], "not_assessed")
            self.assertAlmostEqual(supporting["per_vertex_qa"][0]["dmean_gy"], 20.0)
            case.configuration.prescriptions["Rx_H"] = Prescription(20.0, 1, "protocol_configuration")
            case.configuration.protocol_native_endpoints = [{
                "id": "gtv_v10gy", "role": "GTV", "kind": "coverage_absolute_gy", "value": 10.0,
            }]
            case.layer2_1 = Layer21Service().run(case)
            supporting = case.layer2_1.result["supporting_outputs"]
            self.assertEqual(supporting["protocol_native_endpoint_status"]["status"], "available")
            self.assertEqual(supporting["protocol_native_endpoint_status"]["stored_record_count"], 1)
            self.assertEqual(supporting["protocol_native_metrics"][0]["id"], "gtv_v10gy")

    def test_invalid_protocol_native_endpoint_is_rejected_before_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            config = CaseConfiguration.from_dict(case.configuration.to_dict())
            config.protocol_native_endpoints = [{
                "id": "invalid", "role": "GTV", "kind": "invented_metric", "value": 1.0,
            }]
            with self.assertRaisesRegex(ValueError, "unsupported kind"):
                ApplicationController(case).configure(config)

    def test_invalid_oar_classification_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            config = CaseConfiguration.from_dict(case.configuration.to_dict())
            config.oar_structures = [{"name": "Heart", "classification": "clinical_failure"}]
            with self.assertRaisesRegex(ValueError, "unsupported classification"):
                ApplicationController(case).configure(config)

    def test_result_serialization_and_csv_do_not_run_calculations(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer2_1 = Layer21Service().run(case)
            case.layer2_2 = Layer22Service().run(case)
            paths = export_case(case, Path(folder) / "exports")
            self.assertTrue(all(path.exists() for path in paths))
            payload = json.loads((Path(folder) / "exports" / "ascend_result.json").read_text())
            self.assertEqual(payload["case"]["layer3_1"]["calculation_status"], "not_run")
            self.assertEqual(payload["case"]["layer3_2"]["calculation_status"], "not_run")

    def test_anisotropic_layer22_is_outside_validated_scope(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            manifest = case.layer1.result["manifest"]
            manifest["validated_geometry"]["offsets"] = [float(index * 2) for index in range(21)]
            Path(case.layer1.result_path).write_text(json.dumps(case.layer1.result, indent=2), encoding="utf-8")
            controller = ApplicationController(case)
            record = controller.run_layer22()
            self.assertEqual(record.calculation_status, "outside_validated_scope")
            self.assertIsNone(record.error)
            self.assertIn("outside the validated Layer 2.2 scope", record.warnings[0])


if __name__ == "__main__":
    unittest.main()
