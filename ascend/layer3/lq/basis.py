"""Construction and caching of the validated voxelwise Layer 3.1 P/Q biological basis."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from ascend.layer2.metrics.service import validated as layer21_handoff
from ascend.validation.provenance import canonical_hash, file_hash

from .models import (
    FUNDAMENTAL_PQ_MODEL,
    LQ_ALGORITHM_VERSION,
    LQ_BASIS_SCHEMA_VERSION,
    LQ_CACHE_SCHEMA_VERSION,
    BasisBuildResult,
    ComponentEvidence,
    LQBiologicalBasis,
    ROIInstance,
)


class LQBasisError(ValueError):
    """Signal a controlled failure to construct a validated P/Q basis."""
    pass


def _geometry_payload(manifest: dict[str, Any], shape: tuple[int, ...]) -> dict[str, Any]:
    geometry = manifest.get("validated_geometry") or {}
    spacing = manifest.get("dose_grid", {}).get("voxel_spacing_mm")
    if not isinstance(spacing, list) or len(spacing) != 3:
        raise LQBasisError("missing_validated_component_geometry")
    required = ("origin", "normal", "offsets", "spacing", "shape")
    if any(key not in geometry for key in required):
        raise LQBasisError("missing_validated_component_geometry")
    geometry_shape = tuple(map(int, geometry["shape"]))
    if geometry_shape != tuple(shape):
        raise LQBasisError("incompatible_component_dose_geometry")
    return {
        "origin": geometry["origin"],
        "row_dir": geometry.get("row_dir", geometry.get("row_direction")),
        "col_dir": geometry.get("col_dir", geometry.get("column_direction")),
        "normal": geometry["normal"],
        "offsets": geometry["offsets"],
        "spacing": geometry["spacing"],
        "shape": list(geometry_shape),
        "spacing_zyx_mm": list(map(float, spacing)),
        "frame_of_reference_uid": manifest.get("frame_of_reference_uid"),
    }


def _positive_integer(value: Any, missing_reason: str, invalid_reason: str) -> int:
    if value is None:
        raise LQBasisError(missing_reason)
    try:
        numeric = float(value)
        result = int(numeric)
    except (TypeError, ValueError) as exc:
        raise LQBasisError(missing_reason) from exc
    if not math.isfinite(numeric) or result <= 0 or numeric != result:
        raise LQBasisError(invalid_reason)
    return result


def _component_fraction(component: dict[str, Any], manifest: dict[str, Any]) -> int:
    configured = component.get("fraction_count")
    manifest_value = manifest.get("fractionation", {}).get("number_of_fractions")
    if configured is not None and manifest_value is not None:
        configured_value = _positive_integer(configured, "missing_component_fractionation", "invalid_component_fractionation")
        manifest_fraction = _positive_integer(manifest_value, "missing_component_fractionation", "invalid_component_fractionation")
        if configured_value != manifest_fraction:
            raise LQBasisError("conflicting_fractionation_metadata")
    raw = configured if configured is not None else manifest_value
    return _positive_integer(raw, "missing_component_fractionation", "invalid_component_fractionation")


def _result_path(value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.name == "layer1_result.json" else path / "layer1_result.json"


def _load_layer1(path_value: Any) -> tuple[Path, dict[str, Any], np.ndarray, dict[str, Any]]:
    path = _result_path(path_value)
    if not path.is_file():
        raise LQBasisError("missing_validated_component_dose")
    layer1, dose, _masks = layer21_handoff.load_handoff(path.parent)
    manifest = layer1.get("manifest", {})
    geometry = _geometry_payload(manifest, dose.shape)
    return path, manifest, dose, geometry


def _load_layer1_metadata(path_value: Any) -> tuple[Path, dict[str, Any], tuple[int, ...], dict[str, Any]]:
    """Load provenance/geometry without decoding the large dose array."""
    path = _result_path(path_value)
    if not path.is_file():
        raise LQBasisError("missing_validated_component_dose")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LQBasisError("missing_validated_component_dose") from exc
    manifest = result.get("manifest", {})
    raw_shape = (manifest.get("validated_geometry") or {}).get("shape")
    if not isinstance(raw_shape, list) or len(raw_shape) != 3:
        raise LQBasisError("missing_validated_component_geometry")
    shape = tuple(map(int, raw_shape))
    return path, manifest, shape, _geometry_payload(manifest, shape)


def _basis_hash(p_map: np.ndarray, q_map: np.ndarray, metadata_hash: str) -> str:
    digest = hashlib.sha256(metadata_hash.encode("ascii"))
    digest.update(np.ascontiguousarray(p_map, dtype=np.float32).tobytes())
    digest.update(np.ascontiguousarray(q_map, dtype=np.float32).tobytes())
    return digest.hexdigest()


def _cache_directory(case_root: Path, key: str) -> Path:
    return case_root / "cache" / "layer3_1" / key


def _deterministic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """Write stable NPY members in fixed ZIP order with normalized metadata."""
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for key in sorted(arrays):
            temporary = path.parent / f".{key}.npy"
            try:
                np.save(temporary, np.asarray(arrays[key]), allow_pickle=False)
                info = ZipInfo(f"{key}.npy", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, temporary.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=6)
            finally:
                temporary.unlink(missing_ok=True)


def _load_cache(case_root: Path, key: str, expected: dict[str, Any]) -> LQBiologicalBasis | None:
    directory = _cache_directory(case_root, key)
    metadata_path, archive_path = directory / "basis.json", directory / "pq_basis.npz"
    if not metadata_path.is_file() or not archive_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("cache_schema_version") != LQ_CACHE_SCHEMA_VERSION:
            return None
        if metadata.get("cache_key_payload") != expected:
            return None
        if file_hash(archive_path) != metadata.get("archive_sha256"):
            return None
        with np.load(archive_path, allow_pickle=False) as archive:
            p_map = np.asarray(archive["P_gy"], dtype=np.float32)
            q_map = np.asarray(archive["Q_gy2"], dtype=np.float32)
        components = tuple(ComponentEvidence(
            **{**item,
               "fraction_dose_uids": tuple(item.get("fraction_dose_uids", [])),
               "fraction_dose_sha256": tuple(item.get("fraction_dose_sha256", []))}
        ) for item in metadata["components"])
        roi_history = tuple(ROIInstance(**item) for item in metadata.get("roi_history", []))
        basis = LQBiologicalBasis(
            geometry_identity=metadata["geometry_identity"],
            dose_grid_shape=tuple(metadata["dose_grid_shape"]),
            dose_grid_spacing_mm=tuple(metadata["dose_grid_spacing_mm"]),
            frame_of_reference_uid=metadata["frame_of_reference_uid"],
            components=components,
            roi_history=roi_history,
            p_map=p_map,
            q_map=q_map,
            dtype="float32",
            algorithm_version=str(metadata.get("algorithm_version") or LQ_ALGORITHM_VERSION),
            configuration_hash=metadata["configuration_hash"],
            source_hashes=metadata["source_hashes"],
            warnings=tuple(metadata["warnings"]),
            provenance=metadata["provenance"],
            basis_hash=metadata["basis_hash"],
            cache_key=key,
            cache_hit=True,
            cache_path=str(directory),
        )
        if basis.basis_hash != _basis_hash(p_map, q_map, metadata["basis_metadata_hash"]):
            return None
        return basis
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _publish_cache(
    case_root: Path,
    key: str,
    basis: LQBiologicalBasis,
    key_payload: dict[str, Any],
    metadata_hash: str,
) -> Path:
    root = case_root / "cache" / "layer3_1"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    if destination.exists():
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=f".tmp-{key[:12]}-", dir=root))
    try:
        archive = temporary / "pq_basis.npz"
        _deterministic_npz(archive, {
            "P_gy": np.asarray(basis.p_map, dtype=np.float32),
            "Q_gy2": np.asarray(basis.q_map, dtype=np.float32),
        })
        metadata = {
            "schema_version": LQ_BASIS_SCHEMA_VERSION,
            "cache_schema_version": LQ_CACHE_SCHEMA_VERSION,
            "cache_key_payload": key_payload,
            "archive_sha256": file_hash(archive),
            "basis_metadata_hash": metadata_hash,
            **basis.metadata(),
        }
        (temporary / "basis.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _roi_history(
    manifest: dict[str, Any],
    component_id: str,
    timepoint: str,
    geometry_identity: str,
    layer1_sha256: str,
) -> list[ROIInstance]:
    structures = manifest.get("mask_export", {}).get("structures", {})
    volumes = manifest.get("rasterisation", {}).get("volume_definitions", {})
    output: list[ROIInstance] = []
    for item in manifest.get("roi_inventory", []):
        if item.get("rasterisation_status") != "rasterised" or not item.get("roi_identity"):
            continue
        mask_key = str(item.get("canonical_mapping") or "")
        mask_record = structures.get(mask_key, {})
        volume_record = volumes.get(mask_key, {})
        voxel_count = int(mask_record.get("voxel_count") or 0)
        spacing = manifest.get("dose_grid", {}).get("voxel_spacing_mm") or [0.0, 0.0, 0.0]
        fallback_volume = voxel_count * float(np.prod(spacing)) / 1000.0
        volume = volume_record.get("anatomical_volume_contour_cc")
        if volume is None:
            volume = volume_record.get("dose_sampled_volume_cc", fallback_volume)
        output.append(ROIInstance(
            roi_identity=dict(item["roi_identity"]),
            roi_name=str(item.get("original_name") or mask_key),
            canonical_role=mask_key or None,
            reference_geometry=geometry_identity,
            treatment_component=component_id,
            timepoint=timepoint,
            volume_cc=float(volume),
            mask={
                "representation": "validated_layer1_native_mask_reference",
                "archive_sha256": str(manifest.get("mask_export", {}).get("sha256") or ""),
                "array_key": mask_key,
                "mask_sha256": str(mask_record.get("mask_sha256") or ""),
            },
            mask_sha256=str(mask_record.get("mask_sha256") or ""),
            mask_key=mask_key,
            source_layer1_result_sha256=layer1_sha256,
        ))
    return output


def build_basis(
    case_root: Path,
    components: list[dict[str, Any]],
    configuration_hash: str,
    fraction_history: Any | None = None,
) -> BasisBuildResult:
    """Build or reuse voxelwise P/Q from validated treatment components.

    Every contribution must already share one physical geometry.  Layer 3.1
    deliberately performs no implicit rigid registration, deformable
    registration, dose warping, or ROI propagation.
    """
    if not components:
        return BasisBuildResult("blocked", "not_interpretable", "missing_treatment_components", (), None)
    warnings = ("conventional_lq_high_dose_caution",)
    try:
        prepared: list[dict[str, Any]] = []
        reference_geometry: dict[str, Any] | None = None
        geometry_identity = ""
        source_hashes: dict[str, str] = {}
        evidence: list[ComponentEvidence] = []
        history: list[ROIInstance] = []
        key_components: list[dict[str, Any]] = []

        for component in components:
            component_id = str(component.get("component_id") or "").strip()
            if not component_id:
                raise LQBasisError("missing_treatment_component_identity")
            fraction_paths = list(component.get("fraction_layer1_result_paths") or [])
            method = str(component.get("fraction_dose_model") or (
                "explicit_fraction_doses" if fraction_paths else "identical_fractions"
            ))
            path_values = fraction_paths or [component.get("layer1_result_path")]
            if method not in {"identical_fractions", "explicit_fraction_doses"}:
                raise LQBasisError("unsupported_fraction_dose_model")
            if method == "identical_fractions" and len(path_values) != 1:
                raise LQBasisError("ambiguous_component_dose_history")
            source_records: list[dict[str, Any]] = []
            for source_index, path_value in enumerate(path_values, 1):
                if fraction_history is None:
                    path, manifest, dose, geometry = _load_layer1(path_value)
                    source_shape = tuple(dose.shape)
                else:
                    path, manifest, source_shape, geometry = _load_layer1_metadata(path_value)
                this_identity = canonical_hash(geometry)
                if reference_geometry is None:
                    reference_geometry = geometry
                    geometry_identity = this_identity
                elif this_identity != geometry_identity:
                    # Hash equality covers origin, directions, offsets, spacing,
                    # shape, and Frame of Reference provenance as one contract.
                    raise LQBasisError("incompatible_component_dose_geometry")
                result_sha = file_hash(path)
                dose_record = manifest.get("validated_native_dose", {})
                dose_sha = str(dose_record.get("sha256") or manifest.get("input_file_hashes", {}).get("rtdose") or "")
                source_records.append({
                    "path": path,
                    "manifest": manifest,
                    "shape": source_shape,
                    "result_sha": result_sha,
                    "dose_sha": dose_sha,
                    "dose_uid": str(manifest.get("rtdose_uid") or ""),
                })
                timepoint = str(component.get("timepoint") or (
                    f"fraction_{source_index:03d}" if method == "explicit_fraction_doses" else "component_reference"
                ))
                history.extend(_roi_history(manifest, component_id, timepoint, geometry_identity, result_sha))
                if fraction_history is None:
                    del dose

            if method == "explicit_fraction_doses":
                fraction_count = len(source_records)
                if component.get("fraction_count") is not None:
                    configured_count = _positive_integer(
                        component.get("fraction_count"), "missing_component_fractionation", "invalid_component_fractionation"
                    )
                    if configured_count != fraction_count:
                        raise LQBasisError("conflicting_fractionation_metadata")
                accumulation_method = "explicit_validated_fraction_doses"
            else:
                fraction_count = _component_fraction(component, source_records[0]["manifest"])
                accumulation_method = "identical_fraction_component_total_shortcut"

            primary = source_records[0]
            combined_dose_sha = canonical_hash([item["dose_sha"] for item in source_records])
            combined_result_sha = canonical_hash([item["result_sha"] for item in source_records])
            prescription = component.get(
                "prescription_gy", primary["manifest"].get("fractionation", {}).get("prescription_dose_gy")
            )
            item = ComponentEvidence(
                component_id=component_id,
                dose_uid=primary["dose_uid"],
                plan_uid=primary["manifest"].get("rtplan_uid"),
                fraction_count=fraction_count,
                prescription_gy=float(prescription) if prescription is not None else None,
                prescription_source=str(component.get("source") or primary["manifest"].get("fractionation", {}).get("fractionation_source") or "unknown"),
                dose_sha256=combined_dose_sha,
                layer1_result_sha256=combined_result_sha,
                layer1_result_path=str(primary["path"]),
                geometry_hash=geometry_identity,
                treatment_component_type=str(component.get("component_type") or primary["manifest"].get("treatment_component") or "unknown"),
                accumulation_method=accumulation_method,
                fraction_dose_uids=tuple(record["dose_uid"] for record in source_records),
                fraction_dose_sha256=tuple(record["dose_sha"] for record in source_records),
                timepoint=str(component.get("timepoint") or "unspecified"),
            )
            evidence.append(item)
            for index, record in enumerate(source_records, 1):
                source_hashes[f"{component_id}:dose:{index}"] = record["dose_sha"]
                source_hashes[f"{component_id}:layer1:{index}"] = record["result_sha"]
            prepared.append({"component": component, "method": method, "sources": source_records, "fraction_count": fraction_count})
            key_components.append({
                "component_id": item.component_id,
                "dose_uids": list(item.fraction_dose_uids),
                "dose_hashes": list(item.fraction_dose_sha256),
                "layer1_result_hashes": [record["result_sha"] for record in source_records],
                "fraction_count": item.fraction_count,
                "accumulation_method": accumulation_method,
                "geometry_hash": item.geometry_hash,
            })

        if reference_geometry is None:
            raise LQBasisError("missing_validated_component_geometry")
        key_payload = {
            "cache_schema_version": LQ_CACHE_SCHEMA_VERSION,
            "algorithm_version": LQ_ALGORITHM_VERSION,
            "fundamental_model": FUNDAMENTAL_PQ_MODEL,
            "geometry_identity": geometry_identity,
            "components": key_components,
            "configuration_hash": configuration_hash,
            "authoritative_fraction_history_hash": (
                fraction_history.history_hash if fraction_history is not None else None
            ),
            "accumulation_engine": (
                "shared_fraction_event_engine" if fraction_history is not None else
                "component_shortcut_compatibility_engine"
            ),
        }
        key = canonical_hash(key_payload)
        cached = _load_cache(case_root, key, key_payload)
        if cached is not None:
            return BasisBuildResult("completed_with_warnings", "provisional", None, warnings, cached)

        shape = tuple(reference_geometry["shape"])
        p_map = np.zeros(shape, dtype=np.float32)
        q_map = np.zeros(shape, dtype=np.float32)
        # Fundamental model: P(x)=sum_f d_f(x), Q(x)=sum_f d_f(x)^2.
        # For n identical fractions supplied as total dose D, the exact shortcut
        # is P_k=D and Q_k=D^2/n; explicit fraction doses use the direct sum.
        if fraction_history is not None:
            for event in fraction_history.events:
                dose = np.asarray(event.combined_fraction_dose_field, dtype=np.float32)
                np.add(p_map, dose, out=p_map)
                np.add(q_map, np.square(dose, dtype=np.float32), out=q_map)
        else:
            for prepared_component in prepared:
                if prepared_component["method"] == "explicit_fraction_doses":
                    for source in prepared_component["sources"]:
                        _path, _manifest, dose, _geometry = _load_layer1(source["path"])
                        np.add(p_map, dose, out=p_map, casting="unsafe")
                        np.add(q_map, np.square(dose, dtype=np.float32), out=q_map)
                        del dose
                else:
                    source = prepared_component["sources"][0]
                    _path, _manifest, dose, _geometry = _load_layer1(source["path"])
                    np.add(p_map, dose, out=p_map, casting="unsafe")
                    np.add(
                        q_map,
                        np.square(dose, dtype=np.float32) / float(prepared_component["fraction_count"]),
                        out=q_map,
                    )
                    del dose

        metadata_hash = canonical_hash({
            "key_payload": key_payload,
            "components": [item.to_dict() for item in evidence],
            "roi_history": [item.to_dict() for item in history],
        })
        basis_hash = _basis_hash(p_map, q_map, metadata_hash)
        basis = LQBiologicalBasis(
            geometry_identity=geometry_identity,
            dose_grid_shape=shape,
            dose_grid_spacing_mm=tuple(reference_geometry["spacing_zyx_mm"]),
            frame_of_reference_uid=str(reference_geometry.get("frame_of_reference_uid") or ""),
            components=tuple(evidence),
            roi_history=tuple(history),
            p_map=p_map,
            q_map=q_map,
            dtype="float32",
            algorithm_version=(
                "ASCEND-L3.1-fraction-event-PQ-v2.0"
                if fraction_history is not None else LQ_ALGORITHM_VERSION
            ),
            configuration_hash=configuration_hash,
            source_hashes=source_hashes,
            warnings=warnings,
            provenance={
                "basis_schema_version": LQ_BASIS_SCHEMA_VERSION,
                "fundamental_model": FUNDAMENTAL_PQ_MODEL,
                "accumulation_rule": "Course P/Q permitted only on one validated physical geometry.",
                "implicit_registration": False,
                "implicit_rigid_registration": False,
                "implicit_deformable_registration": False,
                "implicit_dose_warping": False,
                "fraction_history_hash": (
                    fraction_history.history_hash if fraction_history is not None else None
                ),
                "authoritative_accumulation": (
                    "shared_fraction_event_engine" if fraction_history is not None else
                    "component_shortcut_compatibility_engine"
                ),
                "same_fraction_physical_sum_before_quadratic_transform": fraction_history is not None,
            },
            basis_hash=basis_hash,
            cache_key=key,
            cache_hit=False,
        )
        cache_path = _publish_cache(case_root, key, basis, key_payload, metadata_hash)
        basis.cache_path = str(cache_path)
        return BasisBuildResult("completed_with_warnings", "provisional", None, warnings, basis)
    except LQBasisError as exc:
        return BasisBuildResult("blocked", "not_interpretable", str(exc), warnings, None)
