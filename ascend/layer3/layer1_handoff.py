"""Memory-bounded Layer 1 readers for Layer 3 services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ascend.scientific.legacy.layer21_validated import mask_hash, sha256


def load_result(directory: Path) -> dict[str, Any]:
    result_path = directory / "layer1_result.json"
    if not result_path.is_file():
        raise ValueError("BLOCK_LAYER1_INPUT: layer1_result.json is missing.")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("eligibility", {}).get("layer_2_eligible"):
        raise ValueError("BLOCK_LAYER1_INPUT: Layer 1 did not open Layer 2 eligibility.")
    return result


def load_native_dose(directory: Path) -> tuple[dict[str, Any], np.ndarray]:
    """Load only the verified float32 dose field from the Layer 1 archive."""
    result = load_result(directory)
    export = result.get("manifest", {}).get("mask_export", {})
    path = Path(str(export.get("path") or ""))
    if not path.is_file() or sha256(path) != export.get("sha256"):
        raise ValueError("BLOCK_LAYER1_INPUT: native Layer 1 archive missing or hash mismatch.")
    with np.load(path, allow_pickle=False) as archive:
        if "dose_gy" not in archive:
            raise ValueError("BLOCK_LAYER1_INPUT: archive lacks dose_gy; rerun Layer 1 with the current exporter.")
        dose = np.asarray(archive["dose_gy"], dtype=np.float32)
    if dose.ndim != 3 or not np.isfinite(dose).all() or np.any(dose < 0):
        raise ValueError("BLOCK_LAYER1_INPUT: validated native dose must be finite, non-negative, and three-dimensional.")
    return result, dose


def load_masks(directory: Path) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Load verified masks without decoding or converting the dose array."""
    result = load_result(directory)
    export = result.get("manifest", {}).get("mask_export", {})
    archive_path = Path(str(export.get("path") or ""))
    if not archive_path.is_file() or sha256(archive_path) != export.get("sha256"):
        raise ValueError("BLOCK_LAYER1_INPUT: native Layer 1 archive missing or hash mismatch.")
    with np.load(archive_path, allow_pickle=False) as archive:
        if "dose_gy" not in archive:
            raise ValueError("BLOCK_LAYER1_INPUT: archive lacks dose_gy; rerun Layer 1 with the current exporter.")
        masks = {
            name: np.asarray(archive[name], dtype=bool)
            for name in archive.files
            if name != "dose_gy"
        }
    for name, mask in masks.items():
        expected = export.get("structures", {}).get(name, {}).get("mask_sha256")
        if expected and expected != mask_hash(mask):
            raise ValueError(f"BLOCK_LAYER1_INPUT: mask hash mismatch for {name!r}.")
    return result, masks
