"""Pure conventional-LQ BED/EQD2 transformations and ROI summary functions."""

from __future__ import annotations

from typing import Any

import numpy as np

from .parameters import validate_alpha_beta


DOSE_PERCENTILES = (2, 5, 50, 90, 95, 98)


def bed_values(p_values: np.ndarray, q_values: np.ndarray, alpha_beta_gy: float) -> np.ndarray:
    """Handle bed values for the enclosing ASCEND workflow."""
    alpha_beta = validate_alpha_beta(alpha_beta_gy)
    return np.asarray(p_values, dtype=np.float64) + np.asarray(q_values, dtype=np.float64) / alpha_beta


def eqd2_values_from_bed(bed: np.ndarray, alpha_beta_gy: float) -> np.ndarray:
    """Handle eqd2 values from bed for the enclosing ASCEND workflow."""
    alpha_beta = validate_alpha_beta(alpha_beta_gy)
    return np.asarray(bed, dtype=np.float64) / (1.0 + 2.0 / alpha_beta)


def full_bed_map(p_map: np.ndarray, q_map: np.ndarray, alpha_beta_gy: float, dtype: Any = np.float32) -> np.ndarray:
    """Handle full bed map for the enclosing ASCEND workflow."""
    return np.asarray(bed_values(p_map, q_map, alpha_beta_gy), dtype=dtype)


def full_eqd2_map(p_map: np.ndarray, q_map: np.ndarray, alpha_beta_gy: float, dtype: Any = np.float32) -> np.ndarray:
    """Handle full eqd2 map for the enclosing ASCEND workflow."""
    return np.asarray(eqd2_values_from_bed(bed_values(p_map, q_map, alpha_beta_gy), alpha_beta_gy), dtype=dtype)


def scalar_endpoints(values: np.ndarray, prefix: str) -> dict[str, float]:
    """Handle scalar endpoints for the enclosing ASCEND workflow."""
    selected = np.asarray(values, dtype=np.float64)
    if selected.ndim != 1 or not selected.size or not np.isfinite(selected).all():
        raise ValueError("ROI biological values must be a non-empty finite one-dimensional array.")
    endpoints = {
        f"{prefix}_mean": float(selected.mean()),
        f"{prefix}_min": float(selected.min()),
        f"{prefix}_max": float(selected.max()),
    }
    for percent in DOSE_PERCENTILES:
        endpoints[f"{prefix}_d{percent}"] = float(np.percentile(selected, 100.0 - percent))
    return endpoints


def cumulative_volume_histogram(values: np.ndarray, bins: int = 200) -> dict[str, Any]:
    """Handle cumulative volume histogram for the enclosing ASCEND workflow."""
    selected = np.asarray(values, dtype=np.float64)
    if not selected.size:
        return {"bin_values": [], "volume_pct": []}
    maximum = float(selected.max())
    if maximum == 0:
        return {"bin_values": [0.0], "volume_pct": [100.0]}
    thresholds = np.linspace(0.0, maximum, bins + 1, dtype=np.float64)
    ordered = np.sort(selected)
    counts = selected.size - np.searchsorted(ordered, thresholds, side="left")
    return {
        "bin_values": thresholds.tolist(),
        "volume_pct": (100.0 * counts / selected.size).tolist(),
        "curve_type": "cumulative",
    }


def roi_metrics(
    p_map: np.ndarray,
    q_map: np.ndarray,
    mask: np.ndarray,
    alpha_beta_gy: float,
) -> tuple[dict[str, float], dict[str, Any], dict[str, Any]]:
    """Handle roi metrics for the enclosing ASCEND workflow."""
    if mask.shape != p_map.shape or mask.shape != q_map.shape:
        raise ValueError("ROI mask geometry differs from the P/Q basis.")
    if not mask.any():
        raise ValueError("ROI mask is empty.")
    bed, eqd2, metrics = roi_summary_values(p_map, q_map, mask, alpha_beta_gy)
    return metrics, cumulative_volume_histogram(bed), cumulative_volume_histogram(eqd2)


def roi_summary_values(
    p_map: np.ndarray,
    q_map: np.ndarray,
    mask: np.ndarray,
    alpha_beta_gy: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Calculate ROI vectors and endpoints without histogram allocation or sorting."""
    if mask.shape != p_map.shape or mask.shape != q_map.shape:
        raise ValueError("ROI mask geometry differs from the P/Q basis.")
    if not mask.any():
        raise ValueError("ROI mask is empty.")
    bed = bed_values(p_map[mask], q_map[mask], alpha_beta_gy)
    eqd2 = eqd2_values_from_bed(bed, alpha_beta_gy)
    return bed, eqd2, {**scalar_endpoints(bed, "bed"), **scalar_endpoints(eqd2, "eqd2")}


def roi_summary_metrics(
    p_map: np.ndarray,
    q_map: np.ndarray,
    mask: np.ndarray,
    alpha_beta_gy: float,
) -> dict[str, float]:
    """Handle roi summary metrics for the enclosing ASCEND workflow."""
    return roi_summary_values(p_map, q_map, mask, alpha_beta_gy)[2]

