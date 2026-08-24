"""Pure high-dose survival/EUD mathematics for Layer 3.1B and 3.1C.

The configured survival form is evaluated on the complete ROI dose vector. It
does not consume Layer 2.1 summary metrics. Parameters have no defaults.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import brentq

from ascend.validation.provenance import canonical_hash


MLQ_FORMALISM_ID = "MLQ_EUD_LRT_COMPONENT"
MLQ_FORMALISM_VERSION = "ASCEND-L3.1B-fraction-event-MLQ-EUD-v2.0"
TR_FORMALISM_ID = "MODELLED_THERAPEUTIC_RATIO"
TR_FORMALISM_VERSION = "ASCEND-L3.1C-fraction-event-MLQ-TR-v2.0"
MLQ_SOURCE = {
    "implementation_equation": "ASCEND Layer 3.1 Guerrero–Li MLQ fraction-event specification",
    "published_equation_reference": {
        "citation": "Guerrero M, Li XA. Phys Med Biol. 2004;49(20):4825-4835",
        "doi": "10.1088/0031-9155/49/20/012",
        "pmid": "15566178",
        "scope": "Modified LQ model for large fraction doses; ASCEND fraction-event and EUD implementation remains provisional",
    },
    "methodology_lineage": [
        {"role": "modified_lq_model", "citation": "Guerrero M, Li XA. Phys Med Biol. 2004;49(20):4825-4835", "doi": "10.1088/0031-9155/49/20/012"},
        {"role": "sfrt_survival_and_eud_method", "citation": "Murphy et al. J Appl Clin Med Phys. 2020", "doi": "10.1002/acm2.13040"},
        {"role": "grid_radiobiology_method", "citation": "Zhang et al. Radiat Res. 2020", "doi": "10.1667/RADE-20-00047.1"},
        {"role": "grid_parameterisation", "citation": "Zhang et al. Cancers. 2022;14:1037", "doi": "10.3390/cancers14041037"},
        {"role": "sfrt_biological_method_context", "citation": "Moghaddasi et al. Int J Mol Sci. 2022;23:3366", "doi": "10.3390/ijms23063366"},
    ],
    "provenance_scope": "Structured methodological lineage; citations do not constitute clinical validation.",
}

TUMOUR_SCENARIOS = {
    "C1": {"sf2": 0.3, "alpha_beta_gy": 10.0, "alpha_per_gy": 0.5017, "beta_per_gy2": 0.05017},
    "C2": {"sf2": 0.5, "alpha_beta_gy": 10.0, "alpha_per_gy": 0.2888, "beta_per_gy2": 0.02888},
    "C3": {"sf2": 0.7, "alpha_beta_gy": 10.0, "alpha_per_gy": 0.1486, "beta_per_gy2": 0.01486},
}
NORMAL_SCENARIOS = {
    "N1": {"sf2": 0.3, "alpha_beta_gy": 3.1, "alpha_per_gy": 0.3659, "beta_per_gy2": 0.1180},
    "N2": {"sf2": 0.5, "alpha_beta_gy": 3.1, "alpha_per_gy": 0.2106, "beta_per_gy2": 0.06795},
    "N3": {"sf2": 0.7, "alpha_beta_gy": 3.1, "alpha_per_gy": 0.1084, "beta_per_gy2": 0.03497},
}

# Named parameter provenance used by configuration adapters.  Scenario choice
# fixes alpha, beta, alpha/beta and SF2 only.  Kinetic inputs remain a separate
# choice so an N1–N3 radiosensitivity scenario cannot silently inherit tumour
# repair assumptions.
SCENARIO_SOURCE = {
    "tumour": {
        "citation": "Zhang H et al. Cancers (Basel). 2022;14(4):1037",
        "doi": "10.3390/cancers14041037",
        "parameter_set_prefix": "zhang-grid-2022",
    },
    "normal_cell": {
        "citation": "Zhang H et al. Front Oncol. 2025;15:1648847",
        "doi": "10.3389/fonc.2025.1648847",
        "parameter_set_prefix": "zhang-lattice-2025",
    },
}

TUMOUR_KINETIC_PRESETS = {
    "zhang_grid_2022": {
        "label": "Zhang 2022 GRID reproduction",
        "delta_per_gy": 0.15,
        "repair_half_time": 60.0,
        "time_unit": "minutes",
        "parameter_source": (
            "Zhang H et al. Cancers (Basel). 2022;14(4):1037; "
            "Guerrero M, Li XA. Phys Med Biol. 2004;49:4825-4835"
        ),
        "parameter_set_id": "zhang-grid-2022-mlq-kinetics-v1",
    },
}

NORMAL_KINETIC_PRESETS = {
    "zhang_grid_2022": {
        "label": "Zhang 2022 GRID normal-cell reproduction",
        "delta_per_gy": 0.15,
        "repair_half_time": 60.0,
        "time_unit": "minutes",
        "parameter_source": "Zhang H et al. Cancers (Basel). 2022;14(4):1037",
        "parameter_set_id": "zhang-grid-2022-normal-kinetics-v1",
    },
}


def with_scenario(value: dict[str, Any], scenario_id: str | None, *, tissue: str) -> dict[str, Any]:
    """Bind a named sensitivity scenario while retaining explicit kinetic inputs."""
    if scenario_id is None:
        return dict(value)
    scenarios = TUMOUR_SCENARIOS if tissue == "tumour" else NORMAL_SCENARIOS
    if scenario_id not in scenarios:
        raise ValueError(f"Unsupported {tissue} sensitivity scenario: {scenario_id}")
    merged = dict(value)
    expected = scenarios[scenario_id]
    for key in ("alpha_per_gy", "beta_per_gy2"):
        if key in merged and not math.isclose(float(merged[key]), expected[key], rel_tol=1.0e-6, abs_tol=1.0e-9):
            raise ValueError(f"Configured {key} conflicts with scenario {scenario_id}.")
        merged[key] = expected[key]
    merged.update({
        "scenario_id": scenario_id,
        "scenario_sf2": expected["sf2"],
        "alpha_beta_gy": expected["alpha_beta_gy"],
        "scenario_scope": "standardised_sensitivity_scenario_not_patient_specific",
    })
    return merged


def validate_mlq_parameter_set(value: dict[str, Any], label: str = "MLQ") -> dict[str, Any]:
    """Validate one explicit parameter set without supplying literature defaults."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} MLQ parameter set must be a structured record.")
    required = (
        "parameter_set_id", "parameter_source", "model_source", "alpha_per_gy",
        "beta_per_gy2", "delta_per_gy", "repair_half_time", "treatment_delivery_time", "time_unit",
    )
    missing = [key for key in required if value.get(key) in (None, "")]
    if missing:
        raise ValueError(f"{label} MLQ parameter set is missing: {', '.join(missing)}")
    output = dict(value)
    for key in ("alpha_per_gy", "beta_per_gy2", "repair_half_time"):
        number = float(output[key])
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{label} MLQ {key} must be finite and positive.")
        output[key] = number
    for key in ("delta_per_gy", "treatment_delivery_time"):
        number = float(output[key])
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{label} MLQ {key} must be finite and non-negative.")
        output[key] = number
    if output["time_unit"] not in {"seconds", "minutes", "hours"}:
        raise ValueError(f"{label} MLQ time_unit must be seconds, minutes, or hours.")
    output["parameter_set_id"] = str(output["parameter_set_id"]).strip()
    output["parameter_source"] = str(output["parameter_source"]).strip()
    output["model_source"] = str(output["model_source"]).strip()
    output["delivery_time_source"] = str(output.get("delivery_time_source") or "explicit_parameter_set").strip()
    output["parameter_hash"] = canonical_hash({key: output[key] for key in sorted(output) if key != "parameter_hash"})
    return output


def lea_catcheside_factor(z: Any) -> np.ndarray:
    """Evaluate G(z)=2(z+exp(-z)-1)/z^2 stably as z approaches zero."""
    values = np.asarray(z, dtype=np.float64)
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("MLQ G(z) requires finite non-negative z.")
    result = np.empty_like(values)
    small = np.abs(values) < 1.0e-4
    x = values[small]
    result[small] = 1.0 - x / 3.0 + x * x / 12.0 - x * x * x / 60.0 + x**4 / 360.0
    regular = ~small
    x = values[regular]
    result[regular] = 2.0 * (x + np.expm1(-x)) / (x * x)
    return result


def mlq_survival(dose_gy: Any, parameters: dict[str, Any]) -> np.ndarray:
    """Evaluate the configured voxel/bin MLQ survival function."""
    parameter = validate_mlq_parameter_set(parameters)
    dose = np.asarray(dose_gy, dtype=np.float64)
    if not np.isfinite(dose).all() or np.any(dose < 0):
        raise ValueError("MLQ dose values must be finite and non-negative.")
    repair_rate = math.log(2.0) / parameter["repair_half_time"]
    z = repair_rate * parameter["treatment_delivery_time"] + parameter["delta_per_gy"] * dose
    exponent = (
        -parameter["alpha_per_gy"] * dose
        -parameter["beta_per_gy2"] * lea_catcheside_factor(z) * dose * dose
    )
    return np.clip(np.exp(exponent), 0.0, 1.0)


def mlq_effect(
    dose_gy: Any,
    parameters: dict[str, Any],
    *,
    delivery_time: float | None = None,
) -> np.ndarray:
    """Return Guerrero–Li effect K without exponentiation."""
    parameter = validate_mlq_parameter_set(parameters)
    dose = np.asarray(dose_gy, dtype=np.float64)
    if not np.isfinite(dose).all() or np.any(dose < 0):
        raise ValueError("MLQ dose values must be finite and non-negative.")
    tau = parameter["treatment_delivery_time"] if delivery_time is None else float(delivery_time)
    if not math.isfinite(tau) or tau < 0:
        raise ValueError("MLQ delivery time must be finite and non-negative.")
    repair_rate = math.log(2.0) / parameter["repair_half_time"]
    z = repair_rate * tau + parameter["delta_per_gy"] * dose
    effect = parameter["alpha_per_gy"] * dose + parameter["beta_per_gy2"] * lea_catcheside_factor(z) * dose * dose
    if not np.isfinite(effect).all() or np.any(effect < 0):
        raise ValueError("MLQ effect evaluation produced invalid values.")
    return effect


def solve_effect_eud(
    target_effect: float,
    parameters: dict[str, Any],
    reference_delivery_times: list[float],
    *,
    tolerance: float = 1.0e-10,
    maximum_iterations: int = 200,
) -> dict[str, Any]:
    """Invert course effect for a uniform total dose under an explicit reference schedule."""
    parameter = validate_mlq_parameter_set(parameters)
    if not math.isfinite(target_effect) or target_effect < 0:
        raise ValueError("EUD inversion target effect must be finite and non-negative.")
    times = [float(item) for item in reference_delivery_times]
    if not times or any(not math.isfinite(item) or item < 0 for item in times):
        raise ValueError("EUD reference schedule requires finite non-negative delivery times.")
    count = len(times)

    def uniform_effect(total_dose: float) -> float:
        dose_per_event = float(total_dose) / count
        return float(sum(mlq_effect(np.asarray([dose_per_event]), parameter, delivery_time=tau)[0] for tau in times))

    if target_effect == 0:
        return {"eud_gy": 0.0, "solver_status": "converged_zero", "solver_iterations": 0, "residual": 0.0,
                "solver_tolerance": tolerance, "root_solver_algorithm": "bounded_brentq"}
    upper = max(1.0, target_effect / max(parameter["alpha_per_gy"], 1.0e-12))
    for _ in range(40):
        if uniform_effect(upper) >= target_effect:
            break
        upper *= 2.0
    else:
        raise RuntimeError("EUD_ROOT_BRACKET_INVALID")
    iterations = 0

    def residual_function(value: float) -> float:
        nonlocal iterations
        iterations += 1
        return uniform_effect(value) - target_effect

    eud = float(brentq(residual_function, 0.0, upper, xtol=tolerance, rtol=1.0e-14, maxiter=maximum_iterations))
    residual = abs(uniform_effect(eud) - target_effect)
    if eud < 0 or residual > tolerance:
        raise RuntimeError("EUD_ROOT_CONVERGENCE_FAILED")
    return {
        "eud_gy": eud, "solver_status": "converged", "solver_iterations": iterations,
        "residual": residual, "solver_tolerance": tolerance,
        "root_solver_algorithm": "bounded_brentq", "reference_fraction_count": count,
        "reference_delivery_times": times,
    }


def solve_survival_eud(
    dose_values_gy: Any,
    parameters: dict[str, Any],
    *,
    tolerance: float = 1.0e-10,
    maximum_iterations: int = 200,
) -> dict[str, Any]:
    """Solve SF_MLQ(EUD)=volume-weighted mean voxel survival."""
    dose = np.asarray(dose_values_gy, dtype=np.float64).reshape(-1)
    if not dose.size or not np.isfinite(dose).all() or np.any(dose < 0):
        raise ValueError("MLQ EUD requires a non-empty finite non-negative dose vector.")
    parameter = validate_mlq_parameter_set(parameters)
    survival = mlq_survival(dose, parameter)
    mean_survival = float(np.mean(survival))
    upper = float(np.max(dose))
    iterations = 0
    if upper == 0.0 or np.all(dose == dose[0]):
        eud = float(dose[0])
        status = "converged_uniform"
    else:
        def residual_function(value: float) -> float:
            nonlocal iterations
            iterations += 1
            return float(mlq_survival(np.asarray([value]), parameter)[0] - mean_survival)

        eud = float(brentq(residual_function, 0.0, upper, xtol=tolerance, rtol=1.0e-14, maxiter=maximum_iterations))
        status = "converged"
    residual = abs(float(mlq_survival(np.asarray([eud]), parameter)[0]) - mean_survival)
    if residual > tolerance:
        raise RuntimeError(f"MLQ EUD residual {residual:g} exceeds tolerance {tolerance:g}.")
    return {
        "mean_survival_fraction": mean_survival,
        "eud_gy": eud,
        "solver_status": status,
        "solver_iterations": iterations,
        "residual": residual,
        "solver_tolerance": tolerance,
        "voxel_count": int(dose.size),
        "dose_min_gy": float(np.min(dose)),
        "dose_mean_gy": float(np.mean(dose)),
        "dose_max_gy": upper,
        "dose_distribution_method": "direct_equal-volume_dose_voxels",
    }
