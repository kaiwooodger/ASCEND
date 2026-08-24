from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.response.mlq import lea_catcheside_factor, mlq_survival, solve_survival_eud
from ascend.validation.provenance import canonical_hash

from .helpers import synthetic_case


def parameter_set(identifier: str, *, alpha: float = 0.3, beta: float = 0.03) -> dict:
    return {
        "parameter_set_id": identifier,
        "parameter_source": "synthetic independent reference",
        "model_source": "specified test equation",
        "alpha_per_gy": alpha,
        "beta_per_gy2": beta,
        "delta_per_gy": 0.02,
        "repair_half_time": 0.5,
        "treatment_delivery_time": 0.2,
        "time_unit": "hours",
    }


def direct_survival(dose: np.ndarray, parameters: dict) -> np.ndarray:
    repair_rate = math.log(2.0) / parameters["repair_half_time"]
    z = repair_rate * parameters["treatment_delivery_time"] + parameters["delta_per_gy"] * dose
    g = 2.0 * (z + np.exp(-z) - 1.0) / (z * z)
    return np.exp(-parameters["alpha_per_gy"] * dose - parameters["beta_per_gy2"] * g * dose * dose)


class Layer31ResponseMathematicsTests(unittest.TestCase):
    def test_g_is_stable_at_and_near_zero(self) -> None:
        values = lea_catcheside_factor(np.asarray([0.0, 1.0e-12, 1.0e-8, 1.0e-5]))
        self.assertEqual(values[0], 1.0)
        self.assertTrue(np.isfinite(values).all())
        self.assertTrue(np.allclose(values, 1.0, atol=4.0e-6))

    def test_g_small_x_has_required_negative_one_third_slope(self) -> None:
        x = 1.0e-7
        slope = float((lea_catcheside_factor(np.asarray([x]))[0] - 1.0) / x)
        self.assertAlmostEqual(slope, -1.0 / 3.0, delta=1.0e-7)

    def test_g_large_x_has_required_asymptote(self) -> None:
        x = 1.0e6
        self.assertAlmostEqual(float(x * lea_catcheside_factor(np.asarray([x]))[0]), 2.0, delta=3.0e-6)

    def test_survival_matches_independent_equation_and_is_bounded(self) -> None:
        parameters = parameter_set("tumour-v1")
        dose = np.asarray([0.0, 1.5, 7.0, 20.0])
        calculated = mlq_survival(dose, parameters)
        self.assertTrue(np.allclose(calculated, direct_survival(dose, parameters), rtol=1.0e-12, atol=1.0e-14))
        self.assertTrue(np.all((calculated >= 0.0) & (calculated <= 1.0)))

    def test_uniform_distribution_returns_uniform_eud(self) -> None:
        solved = solve_survival_eud(np.full(1000, 13.5), parameter_set("uniform-v1"))
        self.assertEqual(solved["solver_status"], "converged_uniform")
        self.assertAlmostEqual(solved["eud_gy"], 13.5, places=12)
        self.assertLessEqual(solved["residual"], solved["solver_tolerance"])

    def test_heterogeneous_reference_case_and_root_residual(self) -> None:
        parameters = parameter_set("heterogeneous-v1")
        dose = np.asarray([2.0, 5.0, 10.0, 20.0, 20.0])
        solved = solve_survival_eud(dose, parameters)
        independent_mean = float(np.mean(direct_survival(dose, parameters)))
        self.assertAlmostEqual(solved["mean_survival_fraction"], independent_mean, places=13)
        independent_at_root = float(direct_survival(np.asarray([solved["eud_gy"]]), parameters)[0])
        self.assertLessEqual(abs(independent_at_root - independent_mean), solved["solver_tolerance"])


class Layer31ResponseServiceTests(unittest.TestCase):
    def test_layer2_summary_mutation_cannot_change_voxel_eud(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.configuration.layer31_mlq_tumour_parameters = parameter_set("tumour-v1")
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            first = Layer31Service().run(case).result["layer3_1b_high_dose_sfrt_response"]
            case.layer2_1.result = {"harmonised_metrics": [{"metric_id": "mean_peak_dose", "value": 999999.0}]}
            second = Layer31Service().run(case).result["layer3_1b_high_dose_sfrt_response"]
            self.assertEqual(first["input_hash"], second["input_hash"])
            self.assertEqual(first["tumour_eud_gy"], second["tumour_eud_gy"])

    def test_missing_parameters_are_not_guessed_and_repeated_fractions_are_reconstructed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            missing = Layer31Service().run(case).result["layer3_1b_high_dose_sfrt_response"]
            self.assertEqual(missing["applicability_status"], "NOT_ASSESSED")
            self.assertEqual(missing["reason"], "MISSING_TUMOUR_PARAMETER_SET")
            case.configuration.layer31_mlq_tumour_parameters = parameter_set("tumour-v1")
            case.configuration.fractionation = {"fractions": 5}
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            repeated = Layer31Service().run(case).result["layer3_1b_high_dose_sfrt_response"]
            self.assertEqual(repeated["applicability_status"], "APPLICABLE")
            self.assertEqual(repeated["fraction_history"]["number_of_biological_fraction_events"], 5)
            self.assertTrue(all(
                item["repeated_fraction_information"]["source_methods"] == ["identical_fractions"]
                for item in repeated["fraction_history"]["events"]
            ))

    def test_uniform_exposure_gives_modelled_therapeutic_ratio_one(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            tumour = parameter_set("tumour-v1")
            case.configuration.layer31_mlq_tumour_parameters = tumour
            case.configuration.layer31_mlq_normal_parameters = dict(tumour, parameter_set_id="normal-v1")
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            result = Layer31Service().run(case).result
            ratio = result["layer3_1c_modelled_therapeutic_ratio"]
            self.assertEqual(ratio["applicability_status"], "APPLICABLE")
            self.assertAlmostEqual(ratio["modelled_therapeutic_ratio"], 1.0, places=12)
            self.assertNotIn("clinical_status", ratio)
            self.assertNotIn("oar_compliance", ratio)
            self.assertIn("no_pass_fail", ratio["limitations"])

    def test_missing_normal_parameters_blocks_only_therapeutic_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.configuration.layer31_mlq_tumour_parameters = parameter_set("tumour-v1")
            case.configuration_hash = canonical_hash(case.configuration.to_dict())
            result = Layer31Service().run(case).result
            self.assertEqual(result["layer3_1b_high_dose_sfrt_response"]["applicability_status"], "APPLICABLE")
            self.assertEqual(result["layer3_1c_modelled_therapeutic_ratio"]["applicability_status"], "NOT_ASSESSED")
            self.assertEqual(result["layer3_1c_modelled_therapeutic_ratio"]["reason"], "MISSING_NORMAL_TISSUE_PARAMETER_SET")


if __name__ == "__main__":
    unittest.main()
