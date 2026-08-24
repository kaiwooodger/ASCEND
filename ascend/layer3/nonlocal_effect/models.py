"""Versioned parameters and validation for the ASCEND Layer 3.2 model.

The parameter defaults are adapted from SFRT-MODEL1 commit
``b894eeef2cb1b3359bdbe4eff8881a43a690de9b``.  ASCEND deliberately omits
all vessel geometry and uptake parameters.  The implemented PDE therefore
contains diffusion, decay, and emission only.
"""

from __future__ import annotations

import math
from typing import Any


LAYER32_SCHEMA_VERSION = "ASCEND-L3.2-nonlocal-effect-v2"
LAYER32_ALGORITHM_VERSION = "ASCEND-L3.2-SFRT-MODEL1-no-vascular-v1.0"
LAYER32_ARTIFACT_SCHEMA_VERSION = "ASCEND-L3.2-fields-v2"
LAYER32_PARAMETER_SET_VERSION = "SFRT-MODEL1-reference-no-uptake-v1"

SOURCE_MODEL = {
    "repository": "https://github.com/kaiwooodger/SFRT-MODEL1",
    "commit": "b894eeef2cb1b3359bdbe4eff8881a43a690de9b",
    "license": "BSD-3-Clause",
    "source_files": {
        "bystander_multispecies_pde_solver.py": "a01748118f705178d5fb1ff48a97de6c83d002ff6aa66ef0d8eab3207bf0ad7e",
        "bystander_pde_solver.py": "0dd5c2e80f7c34e56b2cd7216b14bcf892a416009bbb56a7bd4b8301c80e1eeb",
        "phase37_bio_model_params.py": "2025225ab677e910ee7a2e70e5315945107715202c3bbaa83441736bf5ae0290",
        "LICENSE": "306854219513be39f4fc34b0c644abbbe47661517deeb203c54122ee01b3138d",
    },
}

# No vascular or uptake parameter exists in this contract.  This is a positive
# allow-list so loading arbitrary JSON cannot silently introduce a sink term.
DEFAULT_PARAMETERS: dict[str, Any] = {
    "alpha_per_gy": 0.03,
    "beta_per_gy2": 0.003,
    "diffusion_ros_mm2_per_time": 0.8,
    "diffusion_cytokine_mm2_per_time": 1.2,
    "decay_ros_per_time": 0.2,
    "decay_cytokine_per_time": 0.001,
    "emission_max_ros": 1.5,
    "emission_max_cytokine": 0.8,
    "emission_gamma_per_gy": 0.35,
    "nonlocal_scaling": 0.0029365813,
    "hazard_weight_ros": 0.4,
    "hazard_weight_cytokine": 0.4,
    "gtv_cytokine_multiplier": 2.0,
    "pde_steps": 400,
    "pde_dt": 0.12,
    "model_grid_target_spacing_mm": 2.0,
    "model_domain_margin_mm": 30.0,
    "history_interval_steps": 20,
}

_POSITIVE_FLOATS = {
    "alpha_per_gy", "beta_per_gy2",
    "diffusion_ros_mm2_per_time", "diffusion_cytokine_mm2_per_time",
    "emission_gamma_per_gy", "model_grid_target_spacing_mm",
    "model_domain_margin_mm", "pde_dt",
}
_NONNEGATIVE_FLOATS = {
    "decay_ros_per_time", "decay_cytokine_per_time",
    "emission_max_ros", "emission_max_cytokine", "nonlocal_scaling",
    "hazard_weight_ros", "hazard_weight_cytokine", "gtv_cytokine_multiplier",
}
_POSITIVE_INTEGERS = {"pde_steps", "history_interval_steps"}


def resolved_parameters(value: dict[str, Any] | None) -> dict[str, Any]:
    """Merge and validate only the explicit no-uptake Layer 3.2 contract."""
    supplied = dict(value or {})
    unknown = sorted(set(supplied) - set(DEFAULT_PARAMETERS))
    if unknown:
        raise ValueError(f"Unsupported Layer 3.2 parameter(s): {', '.join(unknown)}")
    result = {**DEFAULT_PARAMETERS, **supplied}
    for name in _POSITIVE_FLOATS | _NONNEGATIVE_FLOATS:
        try:
            number = float(result[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Layer 3.2 parameter {name} must be numeric.") from exc
        minimum_ok = number > 0.0 if name in _POSITIVE_FLOATS else number >= 0.0
        if not math.isfinite(number) or not minimum_ok:
            comparator = "greater than zero" if name in _POSITIVE_FLOATS else "non-negative"
            raise ValueError(f"Layer 3.2 parameter {name} must be finite and {comparator}.")
        result[name] = number
    for name in _POSITIVE_INTEGERS:
        try:
            number = int(result[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Layer 3.2 parameter {name} must be a positive integer.") from exc
        if number < 1 or float(result[name]) != number:
            raise ValueError(f"Layer 3.2 parameter {name} must be a positive integer.")
        result[name] = number
    if result["history_interval_steps"] > result["pde_steps"]:
        result["history_interval_steps"] = result["pde_steps"]
    if result["hazard_weight_ros"] == 0.0 and result["hazard_weight_cytokine"] == 0.0:
        raise ValueError("At least one Layer 3.2 hazard weight must be greater than zero.")
    return result


def parameter_rows(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Return presentation-neutral parameter records for GUI and export."""
    units = {
        "alpha_per_gy": "Gy^-1", "beta_per_gy2": "Gy^-2",
        "diffusion_ros_mm2_per_time": "mm^2/model-time",
        "diffusion_cytokine_mm2_per_time": "mm^2/model-time",
        "decay_ros_per_time": "model-time^-1", "decay_cytokine_per_time": "model-time^-1",
        "emission_gamma_per_gy": "Gy^-1", "pde_dt": "model-time",
        "model_grid_target_spacing_mm": "mm", "model_domain_margin_mm": "mm",
    }
    display_names = {
        "hazard_weight_ros": "ROS-like mediator weight",
        "hazard_weight_cytokine": "Cytokine-like mediator weight",
        "nonlocal_scaling": "Non-local exposure scaling s",
    }
    return [
        {
            "parameter": display_names.get(name, name), "parameter_key": name,
            "value": value, "units": units.get(name, "dimensionless"),
            "source": "SFRT-MODEL1 reference preset",
        }
        for name, value in parameters.items()
    ]
