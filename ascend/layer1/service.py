"""Layer 1 orchestration around the locked validated scientific implementation.

This module owns workflow concerns—identity binding, strict DICOM geometry,
selective input preparation, Eclipse audit import, provenance, cache reuse, and
atomic publication.  It does not reimplement the locked dose/mask calculations.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pydicom

from ascend import __version__
from ascend.dicom.discovery import INVENTORY_SCHEMA_VERSION
from ascend.dicom.geometry import (
    DoseGeometryError,
    GEOMETRY_NORMALISATION_VERSION,
    normalise_rtdose_geometry,
    serialise_geometry,
    validate_classic_image_series,
)
from ascend.dicom.roi import identity_key, resolve_name, rtstruct_roi_lookup
from ascend.layer1.artifacts import deterministic_npz, streamed_scaled_float64_npy
from ascend.layer1.cache import (
    CACHE_SCHEMA_VERSION,
    LAYER1_RESULT_SCHEMA_VERSION,
    RASTERISATION_ALGORITHM_VERSION,
    Layer1Cache,
    atomic_publish_directory,
    cache_key,
    cleanup_abandoned,
)
from ascend.layer1.selection import (
    build_roi_inventory,
    effective_roles_from_bindings,
    filtered_rtstruct,
    selected_roi_reasons,
)
from ascend.layer1.incremental_raster import incremental_rasterisation
from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.status import CalculationStatus, InterpretationStatus
from ascend.scientific.legacy import layer1_validated as validated
from ascend.treatment.models import TreatmentContext
from ascend.validation.eclipse_dvh import (
    compare_eclipse_to_layer1,
    normalise_eclipse_dvh_source,
    write_import_artifacts,
    write_legacy_gtv_csv,
)
from ascend.validation.provenance import file_hash, run_id, software_identity


VERSIONS = {
    # These contracts version independently.  Combining them would allow a
    # schema-only or geometry-only change to masquerade as the same algorithm.
    "software_version": __version__,
    "dicom_inventory_schema_version": INVENTORY_SCHEMA_VERSION,
    "layer1_result_schema_version": LAYER1_RESULT_SCHEMA_VERSION,
    "layer1_algorithm_version": validated.VERSION,
    "geometry_normalisation_version": GEOMETRY_NORMALISATION_VERSION,
    "rasterisation_algorithm_version": RASTERISATION_ALGORITHM_VERSION,
    "cache_schema_version": CACHE_SCHEMA_VERSION,
}


def _selected(case: ASCENDCase, key: str) -> Path | None:
    value = case.selected_objects.get(key)
    return Path(value) if isinstance(value, str) and value else None


def _source_hashes(paths: dict[str, Any]) -> dict[str, str]:
    hashes = {
        key: file_hash(value)
        for key, value in paths.items()
        if isinstance(value, Path) and value.is_file()
    }
    for index, path in enumerate(paths.get("image_series", []), 1):
        hashes[f"planning_image_{index}"] = file_hash(path)
    return hashes


def _reference_hashes(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    return sorted(file_hash(item) for item in files)


def _identity_only(value: Any) -> Any:
    if isinstance(value, list):
        return [_identity_only(item) for item in value]
    if isinstance(value, dict) and "roi_number" in value:
        return {
            "rtstruct_sop_instance_uid": str(value["rtstruct_sop_instance_uid"]),
            "roi_number": int(value["roi_number"]),
        }
    if isinstance(value, dict):
        return {key: _identity_only(item) for key, item in value.items() if key not in {"name", "display_name"}}
    return value


def _role_display_names(configuration: Any, structure: Any) -> dict[str, str | list[str]]:
    """Derive presentation/reference names from authoritative ROI identities."""
    by_number, _by_name = rtstruct_roi_lookup(structure)
    output: dict[str, str | list[str]] = {}
    for role, binding in configuration.structure_bindings.items():
        if isinstance(binding, list):
            output[role] = [by_number[identity_key(item)[1]] for item in binding]
        else:
            output[role] = by_number[identity_key(binding)[1]]
    return output


def _cache_payload(case: ASCENDCase, hashes: dict[str, str], reference: Path | None) -> dict[str, Any]:
    return {
        "input_hashes": hashes,
        "selected_chain_id": case.selected_chain_id,
        "selected_chain_uids": next((
            item.get("uids") for item in case.dicom_chains if item.get("chain_id") == case.selected_chain_id
        ), None),
        "structure_bindings": _identity_only(case.configuration.structure_bindings),
        "validation_structures": _identity_only(case.configuration.validation_structures),
        "oar_structures": _identity_only(case.configuration.oar_structures),
        "treatment_context": TreatmentContext.from_case(case.configuration, {
            "rtdose_uid": next((item.get("sop_instance_uid") for item in case.dicom_objects.get("RTDOSE", []) if item.get("path") == case.selected_objects.get("rtdose")), None),
            "rtplan_uid": next((item.get("sop_instance_uid") for item in case.dicom_objects.get("RTPLAN", []) if item.get("path") == case.selected_objects.get("rtplan")), None),
        }).to_dict(),
        "eclipse_reference_hashes": _reference_hashes(reference),
        "versions": VERSIONS,
    }


def _status_from_payload(payload: dict[str, Any]) -> tuple[str, str, str | None]:
    layer1_status = payload.get("eligibility", {}).get("layer_1_status", "BLOCK")
    calculation = (
        CalculationStatus.FAILED.value if layer1_status == "BLOCK" else
        CalculationStatus.COMPLETED_WITH_WARNINGS.value if layer1_status == "WARN" else
        CalculationStatus.COMPLETED.value
    )
    interpretation = (
        InterpretationStatus.NOT_INTERPRETABLE.value if layer1_status == "BLOCK"
        else InterpretationStatus.PROVISIONAL.value
    )
    error = "Layer 1 validation produced blocking findings." if layer1_status == "BLOCK" else None
    return calculation, interpretation, error


def _relocate_paths(value: Any, old: Path, new: Path) -> Any:
    old_text, new_text = str(old), str(new)
    if isinstance(value, str) and (value == old_text or value.startswith(old_text + "/")):
        return new_text + value[len(old_text):]
    if isinstance(value, list):
        return [_relocate_paths(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _relocate_paths(item, old, new) for key, item in value.items()}
    return value


class Layer1Service:
    """Coordinate the layer1 workflow without GUI-side calculation."""
    algorithm_version = validated.VERSION
    raster_standard = RASTERISATION_ALGORITHM_VERSION

    def _ensure_bindings(self, case: ASCENDCase, structure: Any) -> None:
        """Migrate legacy names once, then retain ROI identities as authority."""
        if not case.configuration.structure_bindings and case.configuration.structure_roles:
            case.configuration.structure_bindings = {
                role: ([resolve_name(structure, name) for name in value] if isinstance(value, list) else resolve_name(structure, value))
                for role, value in case.configuration.structure_roles.items()
            }
        for item in case.configuration.oar_structures:
            if item.get("roi_identity") is None:
                item["roi_identity"] = resolve_name(structure, str(item.get("name") or item.get("display_name")))
        case.configuration.validate()

    def _cache_hit(
        self,
        case: ASCENDCase,
        cache: Layer1Cache,
        key: str,
        identifier: str,
        destination: Path,
    ) -> LayerRun | None:
        """Materialise a verified cache hit as a new atomic formal run."""
        source = cache.path(key)
        if not source.exists():
            return None
        staging = destination.parent / f".tmp-formal-{identifier}-{uuid.uuid4().hex}"
        try:
            method = cache.materialise(key, staging)
        except ValueError:
            return None
        payload_path = staging / "layer1_result.json"
        payload_path.chmod(0o600)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload = _relocate_paths(payload, Path(payload.get("manifest", {}).get("mask_export", {}).get("path", "")).parent, destination)
        payload["manifest"]["ascend_run_id"] = identifier
        payload["manifest"]["provenance"] = {
            **dict(payload["manifest"].get("provenance") or {}),
            **software_identity(),
            "configuration_hash": case.configuration_hash,
        }
        payload["manifest"]["cache"] = {
            "cache_key": key, "cache_hit": True, "materialisation_method": method,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
        }
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        atomic_publish_directory(staging, destination)
        case.effective_structure_roles = payload["manifest"].get("effective_structure_roles", {})
        calculation, interpretation, error = _status_from_payload(payload)
        return LayerRun(
            "layer1", calculation, interpretation, identifier,
            result_path=str(destination / "layer1_result.json"), result=payload,
            warnings=[f"{item['check']}: {item['detail']}" for item in payload.get("findings", []) if item.get("level") == "WARN"],
            error=error,
        )

    def run(self, case: ASCENDCase) -> LayerRun:
        """Run strict Layer 1 preparation and the locked validator.

        Chain resolution and ROI identity selection must be complete before any
        scientific work starts.  Every calculated artifact is staged, hashed,
        and atomically published; blocking runs are never placed in the cache.
        """
        if case.dicom_chains and not case.selected_chain_id:
            raise ValueError("DICOM chain selection is unresolved; select one candidate before Layer 1.")
        rtdose, rtstruct, rtplan = (_selected(case, key) for key in ("rtdose", "rtstruct", "rtplan"))
        images = [Path(item) for item in case.selected_objects.get("image_series", []) or []]
        if not rtdose or not rtstruct:
            raise ValueError("RTDOSE and RTSTRUCT selections are required.")
        if len(images) < 2:
            raise ValueError("The complete referenced planning image series is required by validated Layer 1.")
        structure_dataset = pydicom.dcmread(rtstruct, stop_before_pixels=True)
        self._ensure_bindings(case, structure_dataset)
        rtstruct_uid = str(getattr(structure_dataset, "SOPInstanceUID", ""))
        # Rasterise only explicitly bound roles, vertices, OARs, and validation
        # structures.  Unselected RTSTRUCT inventory records remain auditable
        # but intentionally produce no masks, volumes, DVHs, or metric rows.
        selected_reasons = selected_roi_reasons(case.configuration, rtstruct_uid)
        if not selected_reasons:
            raise ValueError("No ROI identities are configured for Layer 1 rasterisation.")
        by_number, _by_name = rtstruct_roi_lookup(structure_dataset)
        missing_numbers = sorted(set(selected_reasons) - set(by_number))
        if missing_numbers:
            raise ValueError(f"Configured ROI identities are absent from the selected RTSTRUCT: {missing_numbers}")
        dose_dataset = pydicom.dcmread(rtdose, stop_before_pixels=True)
        # Pixel decoding occurs once inside the locked validator. Its returned
        # dose array is then checked explicitly against the strict dimensions,
        # avoiding a second full decoded array at peak memory.
        geometry = normalise_rtdose_geometry(dose_dataset, validate_pixels=False)
        dose_scaling = float(dose_dataset.DoseGridScaling)
        # Strict pixel decoding has completed. Release both encoded and decoded
        # copies before the locked validator reaches its own peak allocation.
        del dose_dataset
        image_headers = [pydicom.dcmread(path, stop_before_pixels=True) for path in images]
        image_geometry = validate_classic_image_series(image_headers)
        original_paths: dict[str, Any] = {"rtdose": rtdose, "rtstruct": rtstruct, "image_series": images}
        if rtplan:
            original_paths["rtplan"] = rtplan
        hashes = _source_hashes(original_paths)
        reference = Path(case.configuration.tps_metrics_csv) if case.configuration.tps_metrics_csv else None
        key = cache_key(_cache_payload(case, hashes, reference))
        identifier = run_id("L1")
        safe_case = re.sub(r"[^A-Za-z0-9._-]+", "_", case.case_id) or "unknown"
        validated_root = case.root / "validated"
        validated_root.mkdir(parents=True, exist_ok=True)
        cleanup_abandoned(validated_root)
        destination = validated_root / f"layer1_{safe_case}_{identifier}"
        cache = Layer1Cache(case.root)
        hit = self._cache_hit(case, cache, key, identifier, destination)
        if hit is not None:
            return hit

        eclipse_import = None
        legacy_reference = reference
        if reference and (reference.is_dir() or reference.suffix.lower() == ".txt"):
            plan_header = pydicom.dcmread(rtplan, stop_before_pixels=True) if rtplan else None
            eclipse_import = normalise_eclipse_dvh_source(
                reference, _role_display_names(case.configuration, structure_dataset), expected_patient_id=case.case_id,
                expected_plan=str(getattr(plan_header, "RTPlanLabel", "")) or None,
            )
            legacy_reference = write_legacy_gtv_csv(eclipse_import, case.root / "raw" / "eclipse_dvh_layer1_reference.csv")

        with tempfile.TemporaryDirectory(prefix="ascend-l1-normalized-", dir=case.root) as temporary_folder, tempfile.TemporaryDirectory(prefix="ascend-l1-masks-", dir=case.root) as mask_folder:
            temporary = Path(temporary_folder)
            calculation_dose_path = rtdose
            if geometry["grid_frame_offset_vector_convention"] != "relative_from_image_position_patient":
                normalized_dose = pydicom.dcmread(rtdose)
                normalized_dose.GridFrameOffsetVector = [float(value) for value in geometry["offsets"]]
                normalized_dose_path = temporary / "rtdose_normalized.dcm"
                normalized_dose.save_as(normalized_dose_path, write_like_original=False)
                calculation_dose_path = normalized_dose_path
                del normalized_dose
            # The locked validator sees an identity-filtered RTSTRUCT, preserving
            # its validated behavior while avoiding work on unrelated ROIs.
            selected_struct = filtered_rtstruct(structure_dataset, set(selected_reasons))
            selected_struct_path = temporary / "rtstruct_selected.dcm"
            selected_struct.save_as(selected_struct_path, write_like_original=False)
            calculation_paths: dict[str, Any] = {
                "rtdose": calculation_dose_path, "rtstruct": selected_struct_path, "image_series": images,
            }
            if rtplan:
                calculation_paths["rtplan"] = rtplan
            gtv_binding = case.configuration.structure_bindings.get("GTV")
            if not isinstance(gtv_binding, dict):
                raise ValueError("GTV must bind to exactly one RTSTRUCT ROI identity.")
            gtv_name = by_number[identity_key(gtv_binding)[1]]
            with incremental_rasterisation(Path(mask_folder)):
                result = validated.validate(
                    calculation_paths, legacy_reference, "", "",
                    case.configuration.treatment_delivery_mode, gtv_name,
                )

        if tuple(result.dose_array_gy.shape) != tuple(geometry["shape"]):
            raise DoseGeometryError(
                "BLOCK_RTDOSE_GEOMETRY: decoded dose array dimensions differ from Rows, Columns and NumberOfFrames."
            )

        result.manifest["input_file_hashes"] = hashes
        result.manifest["ascend_run_id"] = identifier
        result.manifest["framework"] = "ASCEND"
        result.manifest["validated_geometry"] = serialise_geometry(geometry)
        result.manifest["planning_image_geometry"] = image_geometry
        result.manifest["configured_structure_roles"] = case.configuration.structure_roles
        result.manifest["configured_structure_bindings"] = case.configuration.structure_bindings
        result.manifest["versions"] = dict(VERSIONS)
        result.manifest.update(VERSIONS)
        result.manifest["provenance"] = {
            **software_identity(),
            "configuration_hash": case.configuration_hash,
        }
        result.manifest["rasterisation"]["standard_id"] = RASTERISATION_ALGORITHM_VERSION
        result.manifest["rasterisation"]["selected_roi_numbers"] = sorted(selected_reasons)
        case.effective_structure_roles = effective_roles_from_bindings(case.configuration, result.mappings)
        result.manifest["effective_structure_roles"] = case.effective_structure_roles
        result.manifest["roi_inventory"] = build_roi_inventory(
            structure_dataset, case.configuration, result.mappings,
            {name: {} for name in result.mask_arrays if result.mask_arrays[name].any()},
        )
        result.manifest["treatment_context"] = TreatmentContext.from_case(
            case.configuration, result.manifest,
        ).to_dict()
        failed = [item for item in result.manifest["roi_inventory"] if item["rasterisation_status"] == "rasterisation_failed"]
        for item in failed:
            result.add("BLOCK", "Selective rasterisation", f"ROI {item['roi_number']} failed rasterisation: {item['rasterisation_failure']}")
        if eclipse_import is not None:
            for issue in eclipse_import["issues"]:
                if issue["severity"] == "WARN":
                    result.add("WARN", "Eclipse DVH import", f"{issue['code']}: {issue['detail']}")
        result.eligibility["layer_1_status"] = result.status
        result.eligibility["layer_2_eligible"] = result.status != "BLOCK"
        eclipse_audit = compare_eclipse_to_layer1(eclipse_import, result, geometry, validated) if eclipse_import is not None else []

        # Build the complete formal run below a hidden sibling directory.  Only
        # the final rename makes it discoverable to the case manifest.
        staging_parent = validated_root / f".tmp-publish-{identifier}-{uuid.uuid4().hex}"
        staging_parent.mkdir()
        generated = validated.save_result(result, staging_parent)
        try:
            archive = generated / "layer1_native_dose_masks.npz"
            deterministic_npz(archive, {
                "dose_gy": result.dose_array_gy,
                **result.mask_arrays,
            })
            payload = json.loads((generated / "layer1_result.json").read_text(encoding="utf-8"))
            payload["schema_version"] = LAYER1_RESULT_SCHEMA_VERSION
            payload["manifest"] = result.manifest
            payload["manifest"]["mask_export"]["path"] = str(destination / archive.name)
            payload["manifest"]["mask_export"]["sha256"] = file_hash(archive)
            if eclipse_import is not None:
                artifacts = write_import_artifacts(eclipse_import, eclipse_audit, generated)
                payload["eclipse_dvh_audit"] = eclipse_audit
                payload["eclipse_dvh_import"] = {
                    "format": eclipse_import["format"], "patient_id": eclipse_import["patient_id"],
                    "plan": eclipse_import["plan"], "courses": eclipse_import["courses"],
                    "total_dose_gy": eclipse_import["total_dose_gy"], "summary": eclipse_import["summary"],
                    "issues": eclipse_import["issues"], "source_files": eclipse_import["source_files"],
                    "artifacts": artifacts,
                }
            native_dose_path = generated / "validated_native_rtdose_float64.npy"
            native_source = pydicom.dcmread(rtdose)
            source_dose_pixels = native_source.pixel_array
            streamed_scaled_float64_npy(native_dose_path, source_dose_pixels, dose_scaling)
            del source_dose_pixels, native_source
            payload["manifest"]["validated_native_dose"] = {
                "path": str(destination / native_dose_path.name), "format": "numpy_npy_float64",
                "dtype": "float64", "array_order": "z,y,x", "sha256": file_hash(native_dose_path),
                "derivation": "source-frame-order pixel_array.astype(float) * DoseGridScaling",
            }
            payload["manifest"]["cache"] = {
                "cache_key": key, "cache_hit": False, "cache_schema_version": CACHE_SCHEMA_VERSION,
            }
            payload = _relocate_paths(payload, generated, destination)
            (generated / "layer1_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            atomic_publish_directory(generated, destination)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
        if result.status != "BLOCK":
            cache.publish(key, destination, VERSIONS)
        calculation, interpretation, error = _status_from_payload(payload)
        return LayerRun(
            "layer1", calculation, interpretation, identifier,
            result_path=str(destination / "layer1_result.json"), result=payload,
            warnings=[f"{item.check}: {item.detail}" for item in result.findings if item.level == "WARN"],
            error=error,
        )
