from __future__ import annotations

import math
from pathlib import Path
import tempfile

import numpy as np

from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.tcp.service import compute_poisson_tcp
from ascend.validation.provenance import canonical_hash

from .helpers import synthetic_case
from .test_layer31_fraction_event_upgrade import _kinetic_parameters


def test_zero_effect_extreme_kill_and_density_monotonicity() -> None:
    mask = np.ones((2, 2, 2), dtype=bool)
    zero, _ = compute_poisson_tcp(np.ones(mask.shape), mask, 0.125, 100.0)
    assert zero["initial_clonogens"] == 100.0
    assert math.isclose(zero["radiation_only"]["expected_surviving_clonogens"], 100.0, abs_tol=1.0e-12)
    assert math.isclose(zero["radiation_only"]["tcp"], math.exp(-100.0))
    killed, _ = compute_poisson_tcp(np.zeros(mask.shape), mask, 0.125, 100.0)
    assert killed["radiation_only"]["expected_surviving_clonogens"] == 0.0
    assert killed["radiation_only"]["tcp"] == 1.0
    denser, _ = compute_poisson_tcp(np.full(mask.shape, 0.01), mask, 0.125, 200.0)
    sparse, _ = compute_poisson_tcp(np.full(mask.shape, 0.01), mask, 0.125, 100.0)
    assert denser["radiation_only"]["tcp"] < sparse["radiation_only"]["tcp"]


def test_repopulation_is_monotonic_and_distinct_from_radiation_only() -> None:
    mask = np.ones((3, 3, 3), dtype=bool)
    first, _ = compute_poisson_tcp(np.full(mask.shape, 0.001), mask, 0.001, 1.0e4, phi_repopulation=0.1)
    second, _ = compute_poisson_tcp(np.full(mask.shape, 0.001), mask, 0.001, 1.0e4, phi_repopulation=0.5)
    assert first["radiation_only"] == second["radiation_only"]
    assert second["repopulation_corrected"]["expected_surviving_clonogens"] > first["repopulation_corrected"]["expected_surviving_clonogens"]
    assert second["repopulation_corrected"]["tcp"] < first["repopulation_corrected"]["tcp"]


def test_compartment_reconstruction_and_residual_fractions() -> None:
    mask = np.ones((2, 2, 2), dtype=bool)
    high = np.zeros_like(mask); high[0] = True
    valley = np.zeros_like(mask); valley[1, 0] = True
    result, _ = compute_poisson_tcp(np.linspace(0.1, 0.8, 8).reshape(mask.shape), mask, 0.01, 100.0,
                                    spatial_partition={"vertex": high, "valley": valley})
    spatial = result["spatial_decomposition"]
    assert spatial["status"] == "VALID"
    assert spatial["reconstruction_residual"] < 1.0e-12
    assert math.isclose(spatial["residual_fraction_sum"], 1.0, abs_tol=1.0e-12)


def test_grid_resolution_invariance_for_preserved_uniform_field_and_volume() -> None:
    coarse_mask = np.ones((2, 2, 2), dtype=bool)
    fine_mask = np.ones((4, 4, 4), dtype=bool)
    coarse, _ = compute_poisson_tcp(np.full(coarse_mask.shape, 0.02), coarse_mask, 0.125, 1000.0)
    fine, _ = compute_poisson_tcp(np.full(fine_mask.shape, 0.02), fine_mask, 0.015625, 1000.0)
    assert math.isclose(coarse["initial_clonogens"], fine["initial_clonogens"], rel_tol=0, abs_tol=1e-12)
    assert math.isclose(coarse["radiation_only"]["expected_surviving_clonogens"], fine["radiation_only"]["expected_surviving_clonogens"], rel_tol=0, abs_tol=1e-12)
    assert math.isclose(coarse["radiation_only"]["tcp"], fine["radiation_only"]["tcp"], rel_tol=0, abs_tol=1e-12)


def test_uniform_survival_eud_identity_weighting_contract() -> None:
    mask = np.ones((5, 4, 3), dtype=bool)
    survival_value = 0.25
    result, _ = compute_poisson_tcp(np.full(mask.shape, survival_value), mask, 0.002, 2.0e5)
    expected = 2.0e5 * mask.sum() * 0.002 * survival_value
    assert math.isclose(result["radiation_only"]["expected_surviving_clonogens"], expected)
    assert math.isclose(result["radiation_only"]["tcp"], math.exp(-expected) if expected < 745 else 0.0)


def test_tcp_underflow_retains_log_domain_probability() -> None:
    mask = np.ones((2, 2, 2), dtype=bool)
    result, _ = compute_poisson_tcp(np.ones(mask.shape), mask, 0.125, 1000.0)
    endpoint = result["radiation_only"]
    assert endpoint["tcp"] == 0.0
    assert endpoint["numerical_status"] == "UNDERFLOW_REPORTED_IN_LOG_DOMAIN"
    assert math.isclose(endpoint["log10_tcp"], endpoint["ln_tcp"] / math.log(10.0))


def test_layer31d_service_consumes_layer31b_and_reports_provenance() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder))
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration.layer31_tcp_parameters = {
            "clonogen_density_per_cm3": 1000.0, "units": "clonogens/cm3",
            "source": "synthetic software test", "parameter_set_id": "tcp-test-v1",
            "repopulation_enabled": True, "overall_treatment_time_days": 10.0,
            "kickoff_time_days": 5.0, "potential_doubling_time_days": 3.0,
        }
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result["layer3_1d_tumour_control_probability"]
        assert result["applicability_status"] == "APPLICABLE"
        assert result["repopulation"]["status"] == "APPLIED"
        assert result["endpoints"]["radiation_only"]["tcp"] >= result["endpoints"]["repopulation_corrected"]["tcp"]
        assert result["provenance"]["source_survival_model"] == "MLQ_EUD_LRT_COMPONENT"
        assert result["validation_status"][-1] == "BIOLOGICALLY_UNVALIDATED"


def test_complete_normal_model_materialises_oar_mlq_volume_and_tr_logs() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder))
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration.layer31_mlq_normal_parameters = _kinetic_parameters("normal")
        case.configuration.layer31_materialise_full_maps_on_run = True
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        branch = Layer31Service().run(case).result
        tr = branch["layer3_1c_modelled_therapeutic_ratio"]
        assert tr["applicability_status"] == "APPLICABLE"
        assert tr["numerical_status"] == "FINITE"
        assert math.isclose(
            tr["log_modelled_therapeutic_ratio"],
            math.log(tr["modelled_therapeutic_ratio"]),
            rel_tol=0,
            abs_tol=1.0e-12,
        )
        artifact = branch["layer3_1b_high_dose_sfrt_response"]["artifacts"]
        with np.load(artifact["survival_fields_path"], allow_pickle=False) as archive:
            assert "voxel_survival_MLQ_normal_tissue" in archive.files


def test_layer31d_missing_density_blocks_only_tcp_branch() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder))
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result
        assert result["layer3_1b_high_dose_sfrt_response"]["applicability_status"] == "APPLICABLE"
        assert result["layer3_1d_tumour_control_probability"]["reason"] == "VALID_CLONOGEN_DENSITY_FAILED"


def test_layer31d_rejects_placeholder_clonogen_provenance() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder))
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration.layer31_tcp_parameters = {
            "clonogen_density_per_cm3": 10.0,
            "units": "clonogens/cm3",
            "source": "N/A",
            "parameter_set_id": "N/A",
        }
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result["layer3_1d_tumour_control_probability"]
        assert result["applicability_status"] == "BLOCKED"
        assert result["reason"] == "VALID_CLONOGEN_DENSITY_FAILED"
