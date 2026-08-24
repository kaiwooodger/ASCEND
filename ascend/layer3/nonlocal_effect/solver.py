"""No-vascular two-species reaction-diffusion and cumulative-hazard solver.

Adapted under BSD-3-Clause from SFRT-MODEL1's finite-difference solvers.  The
ASCEND implementation uses native ``(z, y, x)`` array order and intentionally
removes the source model's optional uptake term:

    dC_k/dt = D_k Laplacian(C_k) - lambda_k C_k + E_k(x)

No vessel mask, synthetic cylinder, uptake tensor, or uptake coefficient is
accepted by this API.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage


def cfl_stability_limit(spacing_zyx_mm: tuple[float, float, float], maximum_diffusion: float) -> float:
    """Return the explicit-Euler 3-D diffusion stability limit."""
    spacing = np.asarray(spacing_zyx_mm, dtype=float)
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError("Layer 3.2 model spacing must contain three positive finite values.")
    if not np.isfinite(maximum_diffusion) or maximum_diffusion <= 0:
        raise ValueError("Layer 3.2 maximum diffusion coefficient must be positive.")
    return float(1.0 / (2.0 * maximum_diffusion * np.sum(1.0 / np.square(spacing))))


def anisotropic_laplacian(field: np.ndarray, spacing_zyx_mm: tuple[float, float, float]) -> np.ndarray:
    """Calculate the nearest-boundary finite-difference Laplacian."""
    kernel = np.asarray([1.0, -2.0, 1.0], dtype=np.float32)
    result = np.zeros_like(field, dtype=np.float32)
    for axis, spacing in enumerate(spacing_zyx_mm):
        result += ndimage.convolve1d(field, kernel, axis=axis, mode="nearest") / float(spacing) ** 2
    return result


def solve_no_uptake(
    dose_gy: np.ndarray,
    spacing_zyx_mm: tuple[float, float, float],
    gtv_mask: np.ndarray,
    parameters: dict[str, Any],
    history_masks: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Integrate ROS-like and cytokine-like fields with exactly zero uptake."""
    dose = np.asarray(dose_gy, dtype=np.float32)
    gtv = np.asarray(gtv_mask, dtype=bool)
    if dose.ndim != 3 or gtv.shape != dose.shape:
        raise ValueError("Layer 3.2 dose and GTV model masks must share one three-dimensional grid.")
    if not np.isfinite(dose).all() or np.any(dose < 0):
        raise ValueError("Layer 3.2 model dose must be finite and non-negative.")
    diffusion = np.asarray([
        parameters["diffusion_ros_mm2_per_time"],
        parameters["diffusion_cytokine_mm2_per_time"],
    ], dtype=np.float32)
    decay = np.asarray([
        parameters["decay_ros_per_time"],
        parameters["decay_cytokine_per_time"],
    ], dtype=np.float32)
    emax = np.asarray([
        parameters["emission_max_ros"], parameters["emission_max_cytokine"],
    ], dtype=np.float32)
    weights = np.asarray([
        parameters["hazard_weight_ros"], parameters["hazard_weight_cytokine"],
    ], dtype=np.float32)
    dt = float(parameters["pde_dt"])
    dt_limit = cfl_stability_limit(spacing_zyx_mm, float(diffusion.max()))
    if dt > dt_limit + 1.0e-12:
        raise ValueError(
            f"Layer 3.2 pde_dt {dt:g} exceeds the explicit diffusion stability limit {dt_limit:g}."
        )
    base_emission = 1.0 - np.exp(-float(parameters["emission_gamma_per_gy"]) * dose)
    source = emax[:, None, None, None] * base_emission[None, ...]
    # Patient-specific GTV replaces the source repository's synthetic spherical
    # tumour modifier.  Only the cytokine-like channel is amplified.
    source[1, gtv] *= float(parameters["gtv_cytokine_multiplier"])
    concentration = np.zeros((2, *dose.shape), dtype=np.float32)
    peak_concentration = np.zeros_like(concentration)
    hazard = np.zeros(dose.shape, dtype=np.float32)
    masks = {
        name: np.asarray(mask, dtype=bool)
        for name, mask in (history_masks or {}).items()
        if np.asarray(mask).shape == dose.shape and np.asarray(mask, dtype=bool).any()
    }
    history: list[dict[str, Any]] = []
    interval = int(parameters["history_interval_steps"])
    steps = int(parameters["pde_steps"])
    for step in range(steps):
        for species in range(2):
            derivative = (
                diffusion[species] * anisotropic_laplacian(concentration[species], spacing_zyx_mm)
                - decay[species] * concentration[species]
                + source[species]
            )
            concentration[species] += derivative * dt
            np.maximum(concentration[species], 0.0, out=concentration[species])
            np.maximum(peak_concentration[species], concentration[species], out=peak_concentration[species])
        hazard += (weights[0] * concentration[0] + weights[1] * concentration[1]) * dt
        if masks and ((step + 1) % interval == 0 or step + 1 == steps):
            for region, mask in masks.items():
                history.append({
                    "step": step + 1,
                    "model_time": float((step + 1) * dt),
                    "region": region,
                    "ros_mean": float(concentration[0][mask].mean()),
                    "cytokine_mean": float(concentration[1][mask].mean()),
                    "hazard_mean": float(hazard[mask].mean()),
                })
    return {
        "concentration": concentration,
        "peak_concentration": peak_concentration,
        "hazard": hazard,
        "history": history,
        "cfl_limit": dt_limit,
        "uptake_model": "none",
        "uptake_coefficient": 0.0,
    }
