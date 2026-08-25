"""Stable endpoint-aware colour ranges and opacity transfer functions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import BiologicalEndpoint, BiologicalVolume, ColourMappingMode


@dataclass(frozen=True)
class BiologicalColourScale:
    endpoint: BiologicalEndpoint
    minimum: float
    maximum: float
    true_minimum: float
    true_maximum: float
    mapping_mode: ColourMappingMode
    colormap: str
    opacity_function: tuple[float, ...]
    locked: bool = False


def endpoint_opacity(endpoint: BiologicalEndpoint, samples: int = 256, preset: str = "biological_effect") -> tuple[float, ...]:
    if samples < 2:
        raise ValueError("BIOLOGICAL_OPACITY_SAMPLE_COUNT_INVALID")
    x = np.linspace(0.0, 1.0, samples)
    if endpoint is BiologicalEndpoint.MLQ_SF:
        # SF uses a reversed map: low survival is the visually intense end.
        opacity = np.power(1.0 - x, 1.35)
    elif preset == "linear":
        opacity = x
    elif preset == "high_effect":
        opacity = np.power(x, 2.4)
    else:
        opacity = np.power(x, 1.45)
    opacity[0] = 0.0
    return tuple(map(float, opacity))


class BiologicalColourScaleManager:
    def __init__(self) -> None:
        self._scales: dict[BiologicalEndpoint, BiologicalColourScale] = {}

    def resolve(
        self,
        volume: BiologicalVolume,
        *,
        mask: np.ndarray | None = None,
        mode: ColourMappingMode = ColourMappingMode.PERCENTILE,
        absolute: tuple[float, float] | None = None,
        percentiles: tuple[float, float] = (2.0, 98.0),
        lock: bool = False,
    ) -> BiologicalColourScale:
        existing = self._scales.get(volume.endpoint)
        if existing is not None and existing.locked:
            return existing
        valid = np.asarray(volume.valid_mask, dtype=bool).copy()
        if mask is not None:
            if np.asarray(mask).shape != valid.shape:
                raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
            valid &= np.asarray(mask, dtype=bool)
        values = np.asarray(volume.values, dtype=float)[valid]
        values = values[np.isfinite(values)]
        if not values.size:
            raise ValueError("BIOLOGICAL_VOLUME_MISSING")
        true_min, true_max = float(np.min(values)), float(np.max(values))
        if absolute is not None or mode in {ColourMappingMode.ABSOLUTE, ColourMappingMode.LOCKED_COMPARISON}:
            if absolute is None or not np.isfinite(absolute).all() or absolute[1] <= absolute[0]:
                raise ValueError("BIOLOGICAL_DISPLAY_RANGE_INVALID")
            low, high = map(float, absolute)
        else:
            if not 0.0 <= percentiles[0] < percentiles[1] <= 100.0:
                raise ValueError("BIOLOGICAL_DISPLAY_PERCENTILE_INVALID")
            low, high = map(float, np.percentile(values, percentiles))
            if high <= low:
                delta = max(abs(low) * 1.0e-6, 1.0e-6)
                low, high = low - delta, high + delta
        colormap = "magma_r" if volume.endpoint is BiologicalEndpoint.MLQ_SF else (
            "turbo" if volume.endpoint is BiologicalEndpoint.PHYSICAL_DOSE else "magma" if volume.endpoint is BiologicalEndpoint.MLQ_EFFECT else "viridis"
        )
        result = BiologicalColourScale(
            volume.endpoint, low, high, true_min, true_max, mode, colormap,
            endpoint_opacity(volume.endpoint), lock or mode is ColourMappingMode.LOCKED_COMPARISON,
        )
        self._scales[volume.endpoint] = result
        return result

    def unlock(self, endpoint: BiologicalEndpoint | None = None) -> None:
        targets = list(self._scales) if endpoint is None else [endpoint]
        for target in targets:
            if target in self._scales:
                current = self._scales[target]
                self._scales[target] = BiologicalColourScale(
                    current.endpoint, current.minimum, current.maximum,
                    current.true_minimum, current.true_maximum,
                    current.mapping_mode, current.colormap,
                    current.opacity_function, False,
                )
