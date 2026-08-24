"""Deterministic Layer 1 artifact serialization and hashing helpers."""

from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

import numpy as np


_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _npy_stage(directory: Path, key: str, array: np.ndarray) -> Path:
    path = directory / f"{key}.npy"
    np.save(path, array, allow_pickle=False)
    return path


def deterministic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """Write a stable compressed NPZ without retaining encoded arrays in memory."""
    staging = path.parent / f".tmp-npz-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        npy_files = {}
        for key in sorted(arrays):
            value = np.asarray(arrays[key], dtype=np.float32 if key == "dose_gy" else np.uint8)
            npy_files[key] = _npy_stage(staging, key, value)
            del value
        temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for key in sorted(npy_files):
                info = zipfile.ZipInfo(f"{key}.npy", _ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                with npy_files[key].open("rb") as reader, archive.open(info, "w") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
        os.replace(temporary, path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def streamed_scaled_float64_npy(path: Path, pixels: np.ndarray, scaling: float) -> None:
    """Write native physical dose without allocating a second full dose array."""
    source = np.asarray(pixels)
    header = {
        "descr": np.dtype(np.float64).str,
        "fortran_order": False,
        "shape": source.shape,
    }
    with path.open("wb") as output:
        np.lib.format.write_array_header_1_0(output, header)
        for index in range(source.shape[0]):
            frame = np.multiply(source[index], scaling, dtype=np.float64)
            output.write(np.ascontiguousarray(frame).tobytes(order="C"))
            del frame
        output.flush()
        os.fsync(output.fileno())


def canonical_scientific_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove run/cache publication metadata for equivalence assertions."""
    value = json.loads(json.dumps(payload))
    value.pop("created_utc", None)
    value.pop("run_id", None)
    provenance = value.get("provenance", {})
    for key in ("calculation_timestamp_utc", "cache", "configuration_hash"):
        provenance.pop(key, None)
    manifest = value.get("manifest", {})
    manifest.pop("ascend_run_id", None)
    manifest.get("run", {}).pop("timestamp_utc", None)
    manifest.pop("cache", None)
    for section in ("mask_export", "validated_native_dose"):
        if section in manifest:
            manifest[section].pop("path", None)
    return value
