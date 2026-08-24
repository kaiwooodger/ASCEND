"""Analytic physical-dose fields used for independent anisotropic-grid verification."""

from __future__ import annotations

import numpy as np

from .analytic_geometry import GridSpec


def uniform_field(grid: GridSpec, dose_gy: float = 10.0) -> np.ndarray:
    """Handle uniform field for the enclosing ASCEND workflow."""
    return np.full(grid.shape_zyx, dose_gy, dtype=np.float64)


def physical_gradient_field(
    grid: GridSpec,
    intercept_gy: float = 5.0,
    coefficients_gy_per_mm: tuple[float, float, float] = (0.1, 0.0, 0.0),
) -> np.ndarray:
    """Handle physical gradient field for the enclosing ASCEND workflow."""
    x, y, z = grid.coordinate_arrays()
    a, b, c = coefficients_gy_per_mm
    return np.asarray(intercept_gy + a * x + b * y + c * z, dtype=np.float64)


def lrt_field(
    grid: GridSpec,
    high_mask: np.ndarray,
    valley_mask: np.ndarray,
    background_gy: float = 10.0,
    peak_gy: float = 20.0,
    valley_gy: float = 5.0,
) -> np.ndarray:
    """Handle lrt field for the enclosing ASCEND workflow."""
    dose = np.full(grid.shape_zyx, background_gy, dtype=np.float64)
    dose[valley_mask] = valley_gy
    dose[high_mask] = peak_gy
    return dose


def dose_statistics(dose_gy: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    """Handle dose statistics for the enclosing ASCEND workflow."""
    values = np.asarray(dose_gy[mask], dtype=float)
    if not values.size:
        raise ValueError("Validation mask is empty.")
    return {
        "dmean_gy": float(values.mean()),
        "d50_gy": float(np.percentile(values, 50)),
        "d90_gy": float(np.percentile(values, 10)),
        "d95_gy": float(np.percentile(values, 5)),
        "dmax_gy": float(values.max()),
    }
