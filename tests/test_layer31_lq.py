from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from ascend.layer3.lq.basis import build_basis
from ascend.layer3.lq.metrics import bed_values, full_bed_map, roi_metrics
from ascend.layer3.lq.parameters import validate_alpha_beta
from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.lq.validation import validate_direct_equivalence
from ascend.layer2.metrics.service import Layer21Service
from ascend.validation.provenance import canonical_hash
from ascend.scientific.legacy import layer21_validated

from .helpers import synthetic_case


def _set_uniform_dose(case, dose_gy: float, fractions: int) -> None:
    result_path = Path(case.layer1.result_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    archive_path = Path(payload["manifest"]["mask_export"]["path"])
    with np.load(archive_path, allow_pickle=False) as source:
        arrays = {key: np.asarray(source[key]) for key in source.files if key != "dose_gy"}
        shape = source["dose_gy"].shape
    np.savez_compressed(archive_path, dose_gy=np.full(shape, dose_gy, np.float32), **arrays)
    payload["manifest"]["mask_export"]["sha256"] = layer21_validated.sha256(archive_path)
    payload["manifest"]["fractionation"] = {
        "number_of_fractions": fractions,
        "prescription_dose_gy": dose_gy,
        "fractionation_source": "synthetic_test",
    }
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    case.layer1.result = payload
    case.configuration.fractionation = {"fractions": fractions}


def _set_fractionation(case, dose_gy: float, fractions: int) -> None:
    result_path = Path(case.layer1.result_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["manifest"]["fractionation"] = {
        "number_of_fractions": fractions,
        "prescription_dose_gy": dose_gy,
        "fractionation_source": "synthetic_test",
    }
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    case.layer1.result = payload
    case.configuration.fractionation = {"fractions": fractions}


def _assign_all_lrt_roles(case, alpha_beta: float = 10.0) -> None:
    roles = {"GTV", "T_L", "VTV_H", "VTV_L"}
    role_by_standard = {standard: role for role, standard in case.effective_structure_roles.items() if role in roles}
    case.configuration.layer31_roi_parameters = [
        {
            "roi_identity": item["roi_identity"],
            "alpha_beta_gy": alpha_beta,
            "parameter_source": "synthetic validation",
            "parameter_source_type": "configured_reference",
            "parameter_set_version": "synthetic-v1",
            "assignment_method": "test",
        }
        for item in case.layer1.result["manifest"]["roi_inventory"]
        if item.get("canonical_mapping") in role_by_standard
    ]


class Layer31LQTests(unittest.TestCase):
    def test_unrasterised_oar_cannot_produce_bed_or_eqd2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory), include_oar=True)
            heart = next(
                item for item in case.layer1.result["manifest"]["roi_inventory"]
                if item["original_name"] == "Heart"
            )
            heart["rasterisation_status"] = "not_rasterised"
            case.configuration.layer31_roi_parameters = [{
                "roi_identity": heart["roi_identity"],
                "alpha_beta_gy": 3.0,
                "parameter_source": "synthetic validation",
                "parameter_source_type": "configured_reference",
                "parameter_set_version": "synthetic-v1",
                "assignment_method": "test",
            }]
            Path(case.layer1.result_path).write_text(
                json.dumps(case.layer1.result, indent=2), encoding="utf-8"
            )
            record = Layer31Service().run(case)
            self.assertEqual(record.calculation_status, "blocked")
            self.assertIn("not a rasterised Layer 1 ROI", record.error)
            self.assertEqual(record.result["roi_results"], [])

    def test_stale_layer1_blocks_layer31_service_and_sensitivity_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            case.layer1.mark_stale("OAR rasterisation configuration changed")
            case.layer1_status = "STALE"
            service = Layer31Service()
            record = service.run(case)
            self.assertEqual(record.calculation_status, "blocked")
            self.assertIn("current validated Layer 1", record.error)
            identity = case.layer1.result["manifest"]["roi_inventory"][0]["roi_identity"]
            with self.assertRaisesRegex(ValueError, "current validated Layer 1"):
                service.parameter_sweep(case, identity, [2, 3, 5])

    def test_lrt_only_biological_counterparts_and_contextual_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            _set_fractionation(case, 20.0, 1)
            _assign_all_lrt_roles(case)
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            record = Layer31Service().run(case)
            suite = record.result["biological_six_metrics"]
            metrics = {item["metric_id"]: item for item in suite["records"]}
            self.assertEqual(set(metrics), {
                "peripheral_coverage_v95_rxl", "high_dose_coverage_v95_rxh",
                "high_dose_volume_fraction", "mean_peak_dose", "mean_valley_dose",
                "structure_based_dose_ratio",
            })
            self.assertAlmostEqual(metrics["peripheral_coverage_v95_rxl"]["bed"]["value"], 100.0)
            self.assertAlmostEqual(metrics["high_dose_coverage_v95_rxh"]["eqd2"]["value"], 100.0)
            self.assertAlmostEqual(metrics["mean_peak_dose"]["bed"]["value"], 60.0)
            self.assertAlmostEqual(metrics["mean_peak_dose"]["eqd2"]["value"], 50.0)
            self.assertAlmostEqual(metrics["mean_valley_dose"]["bed"]["value"], 7.5)
            self.assertAlmostEqual(metrics["mean_valley_dose"]["eqd2"]["value"], 6.25)
            self.assertAlmostEqual(metrics["structure_based_dose_ratio"]["bed"]["value"], 8.0)
            self.assertAlmostEqual(metrics["structure_based_dose_ratio"]["eqd2"]["value"], 8.0)
            self.assertEqual(metrics["peripheral_coverage_v95_rxl"]["mapping_type"], "biological_coverage_analogue")
            self.assertEqual(metrics["mean_peak_dose"]["mapping_type"], "biological_transformation")
            self.assertEqual(metrics["structure_based_dose_ratio"]["mapping_type"], "derived_biological_contrast")
            self.assertTrue(
                metrics["structure_based_dose_ratio"]["mathematical_redundancy"]
                ["bed_and_eqd2_contrasts_redundant"]
            )
            fraction = metrics["high_dose_volume_fraction"]
            self.assertEqual(fraction["mapping_type"], "geometry_carried_forward")
            self.assertIsNotNone(fraction["geometry"]["value"])
            self.assertIsNone(fraction["bed"]["value"])
            self.assertIsNone(fraction["eqd2"]["value"])
            high_rx = metrics["high_dose_coverage_v95_rxh"]["biological_prescription"]
            self.assertAlmostEqual(high_rx["bed_prescription_gy"], 60.0)
            self.assertAlmostEqual(high_rx["bed_threshold_gy"], 57.0)
            self.assertNotAlmostEqual(high_rx["bed_threshold_gy"], 19.0 + 19.0**2 / 10.0)
            gtv_context = suite["whole_gtv_biological_context"]
            self.assertEqual(gtv_context["applicability"], "valid")
            self.assertIn("bed_mean", gtv_context["endpoints"])
            self.assertIn("bed_d95", gtv_context["endpoints"])

    def test_lrt_plus_cert_component_history_produces_analytic_biological_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lrt = synthetic_case(root / "lrt")
            cert = synthetic_case(root / "cert")
            _set_fractionation(lrt, 20.0, 1)
            _set_uniform_dose(cert, 20.0, 10)
            _assign_all_lrt_roles(lrt)
            lrt.configuration.treatment_approach = "LRT_SEQUENTIAL_CERT"
            lrt.configuration.treatment_delivery_mode = "integrated_lrt_cert"
            lrt.configuration.dose_context = "composite_course"
            lrt.configuration.prescription_context = "composite_course"
            lrt.configuration.treatment_components = [
                {
                    "component_id": "LRT", "component_type": "lrt", "fraction_count": 1,
                    "prescription_gy": 20.0, "rx_low_gy": 5.0, "rx_high_gy": 20.0,
                    "source": "synthetic_validation",
                },
                {
                    "component_id": "cERT", "component_type": "conventional_rt", "fraction_count": 10,
                    "prescription_gy": 20.0, "source": "synthetic_validation",
                },
            ]
            lrt.configuration.layer31_component_sources = [
                {"component_id": "LRT", "layer1_result_path": lrt.layer1.result_path, "fraction_dose_model": "identical_fractions"},
                {"component_id": "cERT", "layer1_result_path": cert.layer1.result_path, "fraction_dose_model": "identical_fractions"},
            ]
            lrt.configuration_hash = canonical_hash(lrt.configuration.to_dict())
            record = Layer31Service().run(lrt)
            self.assertEqual(len(record.result["components"]), 2)
            metrics = {item["metric_id"]: item for item in record.result["biological_six_metrics"]["records"]}
            self.assertAlmostEqual(metrics["peripheral_coverage_v95_rxl"]["bed"]["value"], 100.0)
            self.assertAlmostEqual(metrics["high_dose_coverage_v95_rxh"]["eqd2"]["value"], 100.0)
            self.assertAlmostEqual(metrics["mean_peak_dose"]["bed"]["value"], 84.0)
            self.assertAlmostEqual(metrics["mean_peak_dose"]["eqd2"]["value"], 70.0)
            self.assertAlmostEqual(metrics["mean_valley_dose"]["bed"]["value"], 31.5)
            self.assertAlmostEqual(metrics["mean_valley_dose"]["eqd2"]["value"], 26.25)
            self.assertAlmostEqual(metrics["structure_based_dose_ratio"]["bed"]["value"], 84.0 / 31.5)
            self.assertIn("cert_background_included_in_biological_valley", metrics["mean_valley_dose"]["warnings"])
            threshold_components = metrics["high_dose_coverage_v95_rxh"]["biological_prescription"]["components"]
            self.assertEqual([item["component_id"] for item in threshold_components], ["LRT", "cERT"])
            high_rx = metrics["high_dose_coverage_v95_rxh"]["biological_prescription"]
            self.assertAlmostEqual(high_rx["prescription_p_gy"], 40.0)
            self.assertAlmostEqual(high_rx["prescription_q_gy2"], 440.0)
            self.assertAlmostEqual(high_rx["bed_prescription_gy"], 84.0)
            self.assertAlmostEqual(high_rx["bed_threshold_gy"], 79.8)

    def test_same_physical_dose_with_different_fractionation_changes_layer31_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            single = synthetic_case(root / "single")
            fractionated = synthetic_case(root / "fractionated")
            _set_uniform_dose(single, 20.0, 1)
            _set_uniform_dose(fractionated, 20.0, 10)
            _assign_all_lrt_roles(single)
            _assign_all_lrt_roles(fractionated)
            single.configuration_hash = canonical_hash(single.configuration.to_dict())
            fractionated.configuration_hash = canonical_hash(fractionated.configuration.to_dict())
            single_layer21 = Layer21Service().run(single).result
            fractionated_layer21 = Layer21Service().run(fractionated).result
            physical_single = [
                (item["metric_id"], item.get("value"), item.get("units"), item.get("applicability"))
                for item in single_layer21["harmonised_metrics"]
            ]
            physical_fractionated = [
                (item["metric_id"], item.get("value"), item.get("units"), item.get("applicability"))
                for item in fractionated_layer21["harmonised_metrics"]
            ]
            self.assertEqual(physical_single, physical_fractionated)
            single_result = Layer31Service().run(single).result
            fractionated_result = Layer31Service().run(fractionated).result
            single_metrics = {item["metric_id"]: item for item in single_result["biological_six_metrics"]["records"]}
            fractionated_metrics = {
                item["metric_id"]: item for item in fractionated_result["biological_six_metrics"]["records"]
            }
            self.assertAlmostEqual(single_metrics["mean_peak_dose"]["bed"]["value"], 60.0)
            self.assertAlmostEqual(fractionated_metrics["mean_peak_dose"]["bed"]["value"], 24.0)
            self.assertNotEqual(
                single_metrics["mean_peak_dose"]["bed"]["value"],
                fractionated_metrics["mean_peak_dose"]["bed"]["value"],
            )

    def test_configured_high_dose_warning_does_not_change_lq_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            _assign_all_lrt_roles(case)
            service = Layer31Service()
            baseline = service.run(case).result
            baseline_metrics = [item["metrics"] for item in baseline["roi_results"]]
            case.configuration.layer31_lq_high_dose_warning_gy_per_fraction = 1.0
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            warned = service.run(case).result
            self.assertEqual([item["metrics"] for item in warned["roi_results"]], baseline_metrics)
            self.assertTrue(warned["layer3_1a_conventional_lq"]["high_dose_warning"]["threshold_triggered"])
            self.assertIn("configured_high_dose_sensitivity_flag", warned["layer3_1a_conventional_lq"]["warnings"])

    def test_composite_coverage_is_unassessed_when_component_prescription_history_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lrt = synthetic_case(root / "lrt")
            cert = synthetic_case(root / "cert")
            _set_fractionation(lrt, 20.0, 1)
            _set_uniform_dose(cert, 20.0, 10)
            _assign_all_lrt_roles(lrt)
            lrt.configuration.treatment_approach = "LRT_SEQUENTIAL_CERT"
            lrt.configuration.treatment_components = [
                {"component_id": "LRT", "component_type": "lrt", "fraction_count": 1, "source": "synthetic_validation"},
                {"component_id": "cERT", "component_type": "conventional_rt", "fraction_count": 10, "prescription_gy": 20.0, "source": "synthetic_validation"},
            ]
            lrt.configuration.layer31_component_sources = [
                {"component_id": "LRT", "layer1_result_path": lrt.layer1.result_path, "fraction_dose_model": "identical_fractions"},
                {"component_id": "cERT", "layer1_result_path": cert.layer1.result_path, "fraction_dose_model": "identical_fractions"},
            ]
            lrt.configuration_hash = canonical_hash(lrt.configuration.to_dict())
            result = Layer31Service().run(lrt).result
            metrics = {item["metric_id"]: item for item in result["biological_six_metrics"]["records"]}
            self.assertEqual(metrics["peripheral_coverage_v95_rxl"]["applicability"], "not_assessed")
            self.assertIn(
                "incomplete_component_prescription_history_for_t_l",
                metrics["peripheral_coverage_v95_rxl"]["warnings"],
            )
            self.assertIsNone(metrics["peripheral_coverage_v95_rxl"]["bed"]["value"])
    def test_single_component_uniform_analytic_result(self) -> None:
        p = np.full((3, 4, 5), 10.0)
        q = np.full_like(p, 20.0)
        mask = np.ones_like(p, dtype=bool)
        metrics, _bed_hist, _eqd2_hist = roi_metrics(p, q, mask, 10.0)
        for endpoint in ("bed_mean", "bed_d50", "bed_d95"):
            self.assertAlmostEqual(metrics[endpoint], 12.0)
        for endpoint in ("eqd2_mean", "eqd2_d50", "eqd2_d95"):
            self.assertAlmostEqual(metrics[endpoint], 10.0)

    def test_multi_component_basis_uses_component_fractionation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = synthetic_case(root / "first")
            second = synthetic_case(root / "second")
            _set_uniform_dose(first, 15.0, 1)
            _set_uniform_dose(second, 30.0, 10)
            result = build_basis(root / "course", [
                {"component_id": "lrt", "layer1_result_path": first.layer1.result_path, "fraction_count": 1},
                {"component_id": "cert", "layer1_result_path": second.layer1.result_path, "fraction_count": 10},
            ], "test-configuration")
            self.assertIsNotNone(result.basis)
            self.assertTrue(np.all(result.basis.p_map == 45.0))
            self.assertTrue(np.all(result.basis.q_map == 315.0))
            self.assertTrue(np.allclose(bed_values(result.basis.p_map, result.basis.q_map, 10.0), 76.5))
            naive = 45.0 * (1.0 + (45.0 / 11.0) / 10.0)
            self.assertNotAlmostEqual(float(bed_values(result.basis.p_map[:1, :1, :1], result.basis.q_map[:1, :1, :1], 10.0)[0, 0, 0]), naive)

    def test_explicit_fraction_dose_history_uses_fundamental_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = synthetic_case(root / "fraction1")
            second = synthetic_case(root / "fraction2")
            _set_uniform_dose(first, 2.0, 1)
            _set_uniform_dose(second, 3.0, 1)
            result = build_basis(root / "course", [{
                "component_id": "adaptive",
                "fraction_layer1_result_paths": [first.layer1.result_path, second.layer1.result_path],
                "fraction_dose_model": "explicit_fraction_doses",
                "fraction_count": 2,
            }], "explicit-history")
            self.assertIsNotNone(result.basis)
            self.assertTrue(np.all(result.basis.p_map == 5.0))
            self.assertTrue(np.all(result.basis.q_map == 13.0))
            self.assertEqual(result.basis.components[0].accumulation_method, "explicit_validated_fraction_doses")
            self.assertEqual(result.basis.provenance["implicit_dose_warping"], False)

    def test_gradient_direct_and_pq_equivalence(self) -> None:
        rng = np.random.default_rng(310)
        doses = [rng.uniform(0, 20, (8, 7, 6)), rng.uniform(0, 30, (8, 7, 6))]
        result = validate_direct_equivalence(doses, [3, 12], 3.0)
        self.assertEqual(result["status"], "PASS")

    def test_roi_only_matches_full_map_slice(self) -> None:
        rng = np.random.default_rng(31)
        p = rng.uniform(0, 30, (9, 8, 7)).astype(np.float32)
        q = rng.uniform(0, 500, p.shape).astype(np.float32)
        mask = rng.random(p.shape) > 0.7
        metrics, _bed_hist, _eqd2_hist = roi_metrics(p, q, mask, 5.0)
        self.assertAlmostEqual(metrics["bed_mean"], float(full_bed_map(p, q, 5.0)[mask].mean()), places=5)

    def test_invalid_alpha_beta_is_rejected(self) -> None:
        for value in (0, -1, float("nan"), float("inf"), None, "not-a-number"):
            with self.assertRaises(ValueError):
                validate_alpha_beta(value)

    def test_invalid_fractionation_and_incompatible_geometry_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = synthetic_case(root / "first")
            second = synthetic_case(root / "second")
            missing = build_basis(root / "course1", [{
                "component_id": "missing", "layer1_result_path": first.layer1.result_path,
            }], "missing")
            self.assertEqual(missing.reason, "missing_component_fractionation")
            payload = json.loads(Path(second.layer1.result_path).read_text(encoding="utf-8"))
            payload["manifest"]["validated_geometry"]["origin"] = [1.0, 0.0, 0.0]
            Path(second.layer1.result_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            mismatch = build_basis(root / "course2", [
                {"component_id": "a", "layer1_result_path": first.layer1.result_path, "fraction_count": 1},
                {"component_id": "b", "layer1_result_path": second.layer1.result_path, "fraction_count": 1},
            ], "mismatch")
            self.assertEqual(mismatch.reason, "incompatible_component_dose_geometry")

    def test_service_roi_results_sweep_cache_and_roi_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            identities = [item["roi_identity"] for item in case.layer1.result["manifest"]["roi_inventory"][:2]]
            case.configuration.layer31_roi_parameters = [
                {
                    "roi_identity": identity, "alpha_beta_gy": alpha_beta,
                    "parameter_source": "synthetic fixture", "parameter_source_type": source,
                    "parameter_set_version": "fixture-v1", "assignment_method": "test",
                }
                for identity, alpha_beta, source in zip(identities, (10.0, 3.0), ("configured_reference", "user_selected"))
            ]
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            service = Layer31Service()
            record = service.run(case)
            self.assertEqual(record.calculation_status, "completed_with_warnings")
            self.assertEqual(len(record.result["roi_results"]), 2)
            self.assertEqual(len(record.result["roi_history"]), 4)
            self.assertTrue(record.result["roi_overlap_audit"])
            first_hash = record.result["basis"]["basis_hash"]
            sweep = service.parameter_sweep(case, identities[0], [2, 3, 5, 8, 10])
            self.assertEqual(len(sweep["records"]), 5)
            self.assertEqual(sweep["basis_hash"], first_hash)
            self.assertTrue(sweep["basis_cache_hit"])


if __name__ == "__main__":
    unittest.main()
