"""Authoritative axial, coronal and sagittal array views."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import BiologicalVolume


@dataclass(frozen=True)
class BiologicalSlice:
    orientation: str
    index: int
    values: np.ndarray
    valid_mask: np.ndarray


def biological_slice(volume: BiologicalVolume, orientation: str, index: int, mask: np.ndarray | None = None) -> BiologicalSlice:
    axis = {"axial": 0, "coronal": 1, "sagittal": 2}.get(orientation.lower())
    if axis is None:
        raise ValueError("BIOLOGICAL_SLICE_ORIENTATION_INVALID")
    if not 0 <= int(index) < volume.values.shape[axis]:
        raise ValueError("BIOLOGICAL_SLICE_INDEX_INVALID")
    selected = volume.valid_mask if mask is None else volume.valid_mask & np.asarray(mask, dtype=bool)
    if selected.shape != volume.values.shape:
        raise ValueError("BIOLOGICAL_MASK_SHAPE_MISMATCH")
    selectors = [slice(None)] * 3
    selectors[axis] = int(index)
    key = tuple(selectors)
    values = np.array(volume.values[key], copy=True)
    valid = np.array(selected[key], copy=True)
    values[~valid] = np.nan
    values.setflags(write=False); valid.setflags(write=False)
    return BiologicalSlice(orientation.lower(), int(index), values, valid)
