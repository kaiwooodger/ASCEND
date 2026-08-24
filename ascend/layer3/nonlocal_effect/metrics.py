"""Pure numerical summaries for Layer 3.2 fields and graph profiles."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage

from ascend.scientific.legacy import layer22_validated as graph_validated


def baseline_survival(p_gy: np.ndarray, q_gy2: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    """Return fraction-history-aware LQ survival from the Layer 3.1 P/Q basis."""
    return np.exp(-float(alpha) * np.asarray(p_gy) - float(beta) * np.asarray(q_gy2)).astype(np.float32)


def final_survival(lq_survival: np.ndarray, hazard: np.ndarray, scaling: float) -> np.ndarray:
    """Apply cumulative non-local hazard without changing physical dose."""
    result = np.asarray(lq_survival, dtype=np.float32) * np.exp(
        -float(scaling) * np.asarray(hazard, dtype=np.float32)
    )
    return np.clip(result, 1.0e-10, 1.0).astype(np.float32)


def nonlocal_consequence_fields(hazard: np.ndarray, scaling: float) -> dict[str, np.ndarray]:
    """Return named no-sink consequence fields derived directly from stored H."""
    exposure = np.asarray(hazard, dtype=np.float32)
    scaled = (float(scaling) * exposure).astype(np.float32)
    multiplier = np.exp(-scaled).astype(np.float32)
    reduction_percent = (100.0 * (1.0 - multiplier)).astype(np.float32)
    return {
        "scaled_nonlocal_exposure": scaled,
        "nonlocal_survival_multiplier": multiplier,
        "additional_modelled_survival_reduction_percent": reduction_percent,
    }


def regional_exposure_consequence_summary(
    region_id: str,
    display_name: str,
    category: str,
    hazard_values: np.ndarray,
    baseline_survival_values: np.ndarray,
    final_survival_values: np.ndarray,
    voxel_volume_cc: float,
) -> dict[str, Any]:
    """Summarise modelled mediator exposure and consequence for one region."""
    exposure = np.asarray(hazard_values, dtype=float)
    baseline = np.asarray(baseline_survival_values, dtype=float)
    final = np.asarray(final_survival_values, dtype=float)
    valid = np.isfinite(exposure) & np.isfinite(baseline) & np.isfinite(final)
    exposure, baseline, final = exposure[valid], baseline[valid], final[valid]
    if not len(exposure):
        return {
            "region_id": region_id, "display_name": display_name, "category": category,
            "status": "not_assessed", "reason": "no_finite_samples",
        }
    multiplier = np.divide(final, baseline, out=np.ones_like(final), where=baseline > 0)
    reduction = 100.0 * (1.0 - np.clip(multiplier, 0.0, 1.0))
    return {
        "region_id": region_id,
        "display_name": display_name,
        "category": category,
        "status": "calculated",
        "sample_count": int(len(exposure)),
        "mean_cumulative_mediator_exposure_h": float(np.mean(exposure)),
        "p95_cumulative_mediator_exposure_h": float(np.percentile(exposure, 95.0)),
        "mean_additional_modelled_survival_reduction_percent": float(np.mean(reduction)),
        "maximum_additional_modelled_survival_reduction_percent": float(np.max(reduction)),
        "volume_at_least_5pct_reduction_cc": float(np.count_nonzero(reduction >= 5.0) * voxel_volume_cc),
        "mean_final_survival_change_absolute": float(np.mean(final - baseline)),
        "mean_baseline_lq_survival": float(np.mean(baseline)),
        "mean_final_survival": float(np.mean(final)),
        "units": {
            "cumulative_mediator_exposure_h": "dimensionless",
            "additional_modelled_survival_reduction": "% relative to LQ baseline",
            "volume": "cc",
            "survival_change": "absolute fraction",
        },
    }


def effect_equivalent_dose(survival: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    """Invert a single-exposure LQ curve for a model-derived equivalent field."""
    safe = np.clip(np.asarray(survival, dtype=np.float32), 1.0e-10, 1.0)
    discriminant = float(alpha) ** 2 - 4.0 * float(beta) * np.log(safe)
    return ((-float(alpha) + np.sqrt(discriminant)) / (2.0 * float(beta))).astype(np.float32)


def endpoint_summary(values: np.ndarray, units: str) -> dict[str, Any]:
    """Summarise one finite, non-empty field sample without clinical grading."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return {"status": "not_assessed", "reason": "no_finite_samples", "units": units}
    return {
        "status": "calculated",
        "units": units,
        "sample_count": int(finite.size),
        "mean": float(finite.mean()),
        "d95": graph_validated.percentile_dose(finite, 95.0),
        "d50": graph_validated.percentile_dose(finite, 50.0),
        "d2": graph_validated.percentile_dose(finite, 2.0),
        "minimum": float(finite.min()),
        "maximum": float(finite.max()),
    }


def resize_field(field: np.ndarray, target_shape: tuple[int, int, int], order: int) -> np.ndarray:
    """Deterministically resize a 3-D model field and force the exact shape."""
    source = np.asarray(field)
    if source.shape == target_shape:
        return source.copy()
    zoom = tuple(float(target) / float(current) for target, current in zip(target_shape, source.shape))
    resized = ndimage.zoom(source, zoom=zoom, order=order, mode="nearest", prefilter=order > 1)
    padding = tuple((0, max(target - current, 0)) for target, current in zip(target_shape, resized.shape))
    if any(after for _before, after in padding):
        resized = np.pad(resized, padding, mode="edge")
    return np.ascontiguousarray(resized[tuple(slice(0, size) for size in target_shape)])


def sample_line_lps(
    field: np.ndarray,
    geometry: dict[str, Any],
    first_lps: np.ndarray,
    second_lps: np.ndarray,
    samples: int = 101,
) -> tuple[np.ndarray, np.ndarray]:
    """Trilinearly sample a stored field along one patient-coordinate edge."""
    first, second = np.asarray(first_lps, dtype=float), np.asarray(second_lps, dtype=float)
    fractions = np.linspace(0.0, 1.0, max(int(samples), 2))
    points = first[None, :] + fractions[:, None] * (second - first)[None, :]
    relative = points - np.asarray(geometry["origin"], dtype=float)
    x = relative @ np.asarray(geometry["row_direction"], dtype=float) / float(geometry["spacing"][1])
    y = relative @ np.asarray(geometry["column_direction"], dtype=float) / float(geometry["spacing"][0])
    offsets = np.asarray(geometry["offsets"], dtype=float)
    physical_offsets = relative @ np.asarray(geometry["normal"], dtype=float)
    if offsets[0] <= offsets[-1]:
        z = np.interp(physical_offsets, offsets, np.arange(len(offsets), dtype=float))
    else:
        z = np.interp(physical_offsets, offsets[::-1], np.arange(len(offsets) - 1, -1, -1, dtype=float))
    values = ndimage.map_coordinates(
        np.asarray(field, dtype=np.float32), np.vstack((z, y, x)), order=1, mode="nearest",
    )
    distance = fractions * float(np.linalg.norm(second - first))
    return distance, np.asarray(values, dtype=float)
