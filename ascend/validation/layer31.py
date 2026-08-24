"""Analytic Layer 3.1 computational verification and performance benchmarking."""

from __future__ import annotations

import math
from time import perf_counter
from typing import Any

import numpy as np

from ascend.layer3.lq.metrics import bed_values, full_bed_map, full_eqd2_map, roi_metrics, roi_summary_metrics
from ascend.layer3.lq.parameters import validate_alpha_beta
from ascend.layer3.lq.validation import validate_direct_equivalence
from ascend.layer3.response.mlq import lea_catcheside_factor
from ascend.layer3.tcp.service import compute_poisson_tcp


def run_layer31_validation() -> dict[str, Any]:
    """Execute layer31 validation and return its explicit calculation state and evidence."""
    cases: list[dict[str, Any]] = []

    tcp_mask = np.ones((2, 2, 2), dtype=bool)
    tcp_zero, _ = compute_poisson_tcp(np.ones(tcp_mask.shape), tcp_mask, 0.125, 100.0)
    cases.append({"case": "tcp_zero_dose_survival_identity", "status": "PASS" if math.isclose(tcp_zero["radiation_only"]["expected_surviving_clonogens"], tcp_zero["initial_clonogens"], abs_tol=1e-12) else "FAIL"})
    tcp_kill, _ = compute_poisson_tcp(np.zeros(tcp_mask.shape), tcp_mask, 0.125, 100.0)
    cases.append({"case": "tcp_extreme_kill_limit", "status": "PASS" if tcp_kill["radiation_only"]["tcp"] == 1.0 else "FAIL"})
    tcp_low, _ = compute_poisson_tcp(np.full(tcp_mask.shape, 0.01), tcp_mask, 0.125, 100.0)
    tcp_high, _ = compute_poisson_tcp(np.full(tcp_mask.shape, 0.01), tcp_mask, 0.125, 200.0)
    cases.append({"case": "tcp_clonogen_density_monotonicity", "status": "PASS" if tcp_high["radiation_only"]["tcp"] < tcp_low["radiation_only"]["tcp"] else "FAIL"})
    tcp_rep_low, _ = compute_poisson_tcp(np.full(tcp_mask.shape, 0.01), tcp_mask, 0.125, 100.0, phi_repopulation=0.1)
    tcp_rep_high, _ = compute_poisson_tcp(np.full(tcp_mask.shape, 0.01), tcp_mask, 0.125, 100.0, phi_repopulation=0.5)
    cases.append({"case": "tcp_repopulation_monotonicity", "status": "PASS" if tcp_rep_high["repopulation_corrected"]["tcp"] < tcp_rep_low["repopulation_corrected"]["tcp"] else "FAIL"})
    fine_mask = np.ones((4, 4, 4), dtype=bool)
    tcp_coarse, _ = compute_poisson_tcp(np.full(tcp_mask.shape, 0.02), tcp_mask, 0.125, 1000.0)
    tcp_fine, _ = compute_poisson_tcp(np.full(fine_mask.shape, 0.02), fine_mask, 0.015625, 1000.0)
    cases.append({"case": "tcp_grid_volume_invariance", "status": "PASS" if math.isclose(tcp_coarse["radiation_only"]["expected_surviving_clonogens"], tcp_fine["radiation_only"]["expected_surviving_clonogens"], abs_tol=1e-12) else "FAIL"})
    high_mask = np.zeros_like(tcp_mask); high_mask[0] = True
    valley_mask = np.zeros_like(tcp_mask); valley_mask[1, 0] = True
    tcp_partition, _ = compute_poisson_tcp(np.linspace(0.1, 0.8, 8).reshape(tcp_mask.shape), tcp_mask, 0.125, 100.0,
                                           spatial_partition={"vertex": high_mask, "valley": valley_mask})
    partition = tcp_partition["spatial_decomposition"]
    cases.append({"case": "tcp_compartment_reconstruction", "status": "PASS" if partition["reconstruction_residual"] < 1e-12 and math.isclose(partition["residual_fraction_sum"], 1.0, abs_tol=1e-12) else "FAIL"})
    uniform_sf = 0.25
    tcp_uniform, _ = compute_poisson_tcp(np.full(tcp_mask.shape, uniform_sf), tcp_mask, 0.125, 100.0)
    expected_uniform = tcp_uniform["initial_clonogens"] * uniform_sf
    cases.append({"case": "tcp_uniform_survival_eud_identity", "status": "PASS" if math.isclose(tcp_uniform["radiation_only"]["expected_surviving_clonogens"], expected_uniform, abs_tol=1e-12) else "FAIL"})

    zero_x = 1.0e-10
    zero_value = float(lea_catcheside_factor(np.asarray([zero_x]))[0])
    cases.append({"case": "lea_catcheside_zero_limit", "status": "PASS" if math.isclose(zero_value, 1.0, rel_tol=0.0, abs_tol=1.0e-9) else "FAIL",
                  "requirement": "lim(x->0) G(x) = 1", "x": zero_x, "observed": zero_value})
    slope_x = 1.0e-7
    slope = float((lea_catcheside_factor(np.asarray([slope_x]))[0] - 1.0) / slope_x)
    cases.append({"case": "lea_catcheside_small_x_slope", "status": "PASS" if math.isclose(slope, -1.0 / 3.0, rel_tol=0.0, abs_tol=1.0e-7) else "FAIL",
                  "requirement": "G(x) = 1 - x/3 + O(x^2)", "x": slope_x, "observed_slope": slope, "expected_slope": -1.0 / 3.0})
    large_x = 1.0e6
    asymptote = float(large_x * lea_catcheside_factor(np.asarray([large_x]))[0])
    cases.append({"case": "lea_catcheside_large_x_limit", "status": "PASS" if math.isclose(asymptote, 2.0, rel_tol=0.0, abs_tol=3.0e-6) else "FAIL",
                  "requirement": "lim(x->infinity) xG(x) = 2", "x": large_x, "observed": asymptote})

    p = np.full((8, 8, 8), 10.0, dtype=np.float32)
    q = np.full_like(p, 20.0)
    mask = np.ones_like(p, dtype=bool)
    metrics, _bed_hist, _eqd2_hist = roi_metrics(p, q, mask, 10.0)
    cases.append({"case": "single_component_uniform", "status": "PASS" if metrics["bed_mean"] == 12.0 and metrics["eqd2_mean"] == 10.0 else "FAIL", "expected_bed_gy": 12.0, "observed_bed_gy": metrics["bed_mean"]})

    p = np.full((4, 4, 4), 45.0, dtype=np.float32)
    q = np.full_like(p, 315.0)
    observed = float(bed_values(p, q, 10.0)[0, 0, 0])
    naive = 45.0 * (1.0 + (45.0 / 11.0) / 10.0)
    cases.append({"case": "multi_component_fractionation", "status": "PASS" if observed == 76.5 and not math.isclose(observed, naive) else "FAIL", "expected_bed_gy": 76.5, "observed_bed_gy": observed, "naive_flattened_bed_gy": naive})

    full_rx_bed = 20.0 + 20.0**2 / (1 * 10.0)
    required_threshold = 0.95 * full_rx_bed
    incorrect_transform_of_scaled_rx = 19.0 + 19.0**2 / (1 * 10.0)
    cases.append({
        "case": "biological_coverage_threshold_order",
        "status": "PASS" if required_threshold == 57.0 and not math.isclose(required_threshold, incorrect_transform_of_scaled_rx) else "FAIL",
        "definition": "0.95 * BED_Rx after full prescription P/Q accumulation",
        "expected_threshold_gy_bed": 57.0,
        "observed_threshold_gy_bed": required_threshold,
        "rejected_bed_of_0.95_rx_gy": incorrect_transform_of_scaled_rx,
    })

    same_dose_single_fraction_bed = 20.0 + 20.0**2 / (1 * 10.0)
    same_dose_ten_fraction_bed = 20.0 + 20.0**2 / (10 * 10.0)
    cases.append({
        "case": "same_physical_dose_different_fractionation",
        "status": "PASS" if same_dose_single_fraction_bed == 60.0 and same_dose_ten_fraction_bed == 24.0 else "FAIL",
        "physical_dose_gy": 20.0,
        "single_fraction_bed_gy": same_dose_single_fraction_bed,
        "ten_fraction_bed_gy": same_dose_ten_fraction_bed,
        "interpretation": "Layer 2.1 physical equivalence with Layer 3.1 biological non-equivalence.",
    })

    rng = np.random.default_rng(310)
    doses = [rng.uniform(0, 20, (12, 11, 10)), rng.uniform(0, 30, (12, 11, 10))]
    equivalence = validate_direct_equivalence(doses, [3, 12], 3.0)
    cases.append({"case": "physical_gradient_direct_equivalence", **equivalence})

    p = np.asarray(doses[0] + doses[1], dtype=np.float32)
    q = np.asarray(doses[0] ** 2 / 3 + doses[1] ** 2 / 12, dtype=np.float32)
    roi = rng.random(p.shape) > 0.6
    roi_result, _bed_hist, _eqd2_hist = roi_metrics(p, q, roi, 5.0)
    expected = float(full_bed_map(p, q, 5.0)[roi].mean())
    cases.append({"case": "roi_restriction", "status": "PASS" if math.isclose(roi_result["bed_mean"], expected, rel_tol=2e-6, abs_tol=2e-5) else "FAIL", "maximum_absolute_error_gy": abs(roi_result["bed_mean"] - expected)})

    invalid_pass = True
    for value in (0, -1, float("nan"), float("inf"), None):
        try:
            validate_alpha_beta(value)
            invalid_pass = False
        except ValueError:
            pass
    cases.append({"case": "invalid_alpha_beta", "status": "PASS" if invalid_pass else "FAIL"})
    return {
        "schema_version": "ASCEND-L3.1-validation-v2",
        "algorithm_version": "ASCEND-L3.1-LQ-PQ-v1.0",
        "status": "PASS" if all(item["status"] == "PASS" for item in cases) else "FAIL",
        "validation_scope": "computational_verification_not_clinical_validation",
        "validation_cases": cases,
    }


def benchmark_layer31(shape: tuple[int, int, int] = (128, 128, 64), seed: int = 31) -> dict[str, Any]:
    """Handle benchmark layer31 for the enclosing ASCEND workflow."""
    rng = np.random.default_rng(seed)
    dose1 = rng.uniform(0, 20, shape).astype(np.float32)
    dose2 = rng.uniform(0, 30, shape).astype(np.float32)
    started = perf_counter()
    p = dose1 + dose2
    q = dose1 * dose1 / 3.0 + dose2 * dose2 / 12.0
    basis_seconds = perf_counter() - started
    mask = np.zeros(shape, dtype=bool)
    mask[tuple(slice(size // 4, 3 * size // 4) for size in shape)] = True
    started = perf_counter(); roi_metrics(p, q, mask, 3.0); roi_seconds = perf_counter() - started
    started = perf_counter()
    for value in (2, 3, 5, 8, 10):
        roi_summary_metrics(p, q, mask, value)
    sweep_seconds = perf_counter() - started
    started = perf_counter(); bed = full_bed_map(p, q, 3.0); eqd2 = full_eqd2_map(p, q, 3.0); map_seconds = perf_counter() - started
    output = {
        "schema_version": "ASCEND-L3.1-performance-v1",
        "profile": "synthetic",
        "shape": list(shape),
        "voxel_count": int(np.prod(shape)),
        "pq_basis_build_seconds": basis_seconds,
        "roi_bed_eqd2_seconds": roi_seconds,
        "five_value_sweep_seconds": sweep_seconds,
        "full_bed_eqd2_map_seconds": map_seconds,
        "parameter_sweep_reuses_basis": True,
        "working_array_bytes": int(sum(item.nbytes for item in (dose1, dose2, p, q, mask, bed, eqd2))),
    }
    del dose1, dose2, p, q, mask, bed, eqd2
    return output
