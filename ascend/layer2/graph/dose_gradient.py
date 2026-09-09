"""ICRU 91-context Paddick gradient-index evidence on the native RTDOSE grid."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from .result_models import (
    ASCEND_LAYER22_DOSE_GRADIENT_SCHEMA,
    DOSE_GRADIENT_ALGORITHM_VERSION,
    IsodoseVolumePoint,
)
from .spatial_sampling import GridGeometry, validate_native_inputs


DEFAULT_PROFILE_LEVELS_PERCENT = tuple(float(value) for value in range(25, 126, 5))


def _volume_at_or_above(dose_gy: np.ndarray, threshold_gy: float, geometry: GridGeometry) -> tuple[int, float]:
    """Return exact native-grid voxel count and physical volume at a dose threshold."""
    counts = np.count_nonzero(dose_gy >= threshold_gy, axis=(1, 2))
    frame_voxel_cc = geometry.frame_thicknesses_mm * geometry.row_spacing_mm * geometry.column_spacing_mm / 1000.0
    return int(np.sum(counts)), float(np.sum(counts * frame_voxel_cc))


def _touches_grid_boundary(mask: np.ndarray) -> bool:
    """Identify an isodose volume that may be clipped by the stored RTDOSE extent."""
    return bool(
        np.any(mask[0]) or np.any(mask[-1])
        or np.any(mask[:, 0, :]) or np.any(mask[:, -1, :])
        or np.any(mask[:, :, 0]) or np.any(mask[:, :, -1])
    )


def analyse_icru91_dose_gradient(
    *,
    case_id: str,
    dose_gy: np.ndarray,
    geometry: GridGeometry,
    prescription_dose_gy: float | None,
    prescription_source: str,
    high_dose_target_volume_cc: float | None,
    number_of_targets: int,
    target_volumes_cc: Sequence[float] = (),
    profile_levels_percent: Sequence[float] = DEFAULT_PROFILE_LEVELS_PERCENT,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate Paddick GI = V(50% Rx) / V(100% Rx) and its isodose-volume profile.

    This is a whole-dose-distribution SRS plan-quality descriptor. It is not
    calculated separately for individual vertices because that would require a
    non-standard allocation of overlapping low-dose wash to individual targets.
    """
    dose = np.asarray(dose_gy, dtype=float)
    validate_native_inputs(dose, geometry, [])
    source = str(prescription_source or "unavailable")
    resolved_target_volumes = [float(value) for value in target_volumes_cc if math.isfinite(float(value)) and float(value) > 0]
    base = {
        "schema_version": ASCEND_LAYER22_DOSE_GRADIENT_SCHEMA,
        "algorithm_version": DOSE_GRADIENT_ALGORITHM_VERSION,
        "case_id": case_id,
        "metric_name": "Paddick gradient index",
        "reference_context": "ICRU Report 91 stereotactic-treatment reporting context",
        "formula": "GI = PIV_half / PIV = V(D >= 0.5 * prescription dose) / V(D >= prescription dose)",
        "scope": "Whole selected RTDOSE distribution; not a per-vertex radial profile or clinical pass/fail constraint.",
        "comparison_boundary": (
            "Lower GI indicates steeper dose fall-off. Compare plans only with matched target volume and similar conformity; "
            "small targets and multiple-target low-dose overlap can increase GI."
        ),
        "prescription": {"dose_gy": prescription_dose_gy, "source": source, "role": "Rx_H"},
        "target_context": {
            "high_dose_target_volume_cc": high_dose_target_volume_cc,
            "number_of_targets": int(number_of_targets),
            "individual_target_volumes_cc": resolved_target_volumes,
            "minimum_target_volume_cc": min(resolved_target_volumes) if resolved_target_volumes else None,
        },
        "dose_volume_basis": (
            "All finite native RTDOSE voxels at or above each threshold, using physical voxel volumes; "
            "no interpolation, radial shells, smoothing, or inferred external contour."
        ),
        "provenance": {**(provenance or {}), "algorithm_version": DOSE_GRADIENT_ALGORITHM_VERSION},
    }
    if prescription_dose_gy is None or not math.isfinite(float(prescription_dose_gy)) or float(prescription_dose_gy) <= 0:
        return {
            **base,
            "calculation_status": "NOT_CALCULATED_MISSING_PRESCRIPTION",
            "piv_half_cc": None,
            "piv_cc": None,
            "gradient_index": None,
            "isodose_volume_profile": [],
            "warnings": ["MISSING_OR_INVALID_RX_H"],
        }

    prescription = float(prescription_dose_gy)
    levels = sorted({float(value) for value in profile_levels_percent} | {50.0, 100.0})
    if any(not math.isfinite(value) or value <= 0 for value in levels):
        raise ValueError("Relative isodose profile levels must be finite and greater than zero.")
    profile: list[dict[str, Any]] = []
    by_level: dict[float, IsodoseVolumePoint] = {}
    for level in levels:
        threshold = prescription * level / 100.0
        count, volume_cc = _volume_at_or_above(dose, threshold, geometry)
        point = IsodoseVolumePoint(level, threshold, volume_cc, count)
        by_level[level] = point
        profile.append(point.to_dict())

    piv_half = by_level[50.0]
    piv = by_level[100.0]
    warnings: list[str] = []
    if piv.voxel_count == 0 or piv.isodose_volume_cc <= 0:
        return {
            **base,
            "calculation_status": "NOT_CALCULATED_EMPTY_PIV",
            "piv_half_cc": piv_half.isodose_volume_cc,
            "piv_cc": 0.0,
            "gradient_index": None,
            "isodose_volume_profile": profile,
            "warnings": ["NO_VOXELS_AT_OR_ABOVE_RX_H"],
        }

    if _touches_grid_boundary(dose >= 0.5 * prescription):
        warnings.append("HALF_PRESCRIPTION_ISODOSE_TOUCHES_DOSE_GRID_BOUNDARY")
    if _touches_grid_boundary(dose >= prescription):
        warnings.append("PRESCRIPTION_ISODOSE_TOUCHES_DOSE_GRID_BOUNDARY")
    if int(number_of_targets) > 1:
        warnings.append("MULTIPLE_TARGETS_GI_INCLUDES_COMBINED_LOW_DOSE_WASH")
    if resolved_target_volumes and min(resolved_target_volumes) < 1.0:
        warnings.append("SUB_CC_TARGET_CONTEXT_INTERPRET_GI_CAUTIOUSLY")
    gradient_index = float(piv_half.isodose_volume_cc / piv.isodose_volume_cc)
    return {
        **base,
        "calculation_status": "CALCULATED_WITH_WARNINGS" if warnings else "CALCULATED",
        "piv_half_cc": piv_half.isodose_volume_cc,
        "piv_cc": piv.isodose_volume_cc,
        "gradient_index": gradient_index,
        "isodose_volume_profile": profile,
        "warnings": warnings,
    }
