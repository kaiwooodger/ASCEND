"""Layer 1 orchestration around the locked validated scientific implementation.

This module owns workflow concerns—identity binding, strict DICOM geometry,
selective input preparation, Eclipse audit import, provenance, cache reuse, and
atomic publication.  It does not reimplement the locked dose/mask calculations.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import pydicom

from ascend import __version__
from ascend.dicom.discovery import INVENTORY_SCHEMA_VERSION
from ascend.dicom.geometry import GEOMETRY_NORMALISATION_VERSION, serialise_geometry
from ascend.dicom.roi import identity_key, rtstruct_roi_lookup
from ascend.dicom.rtplan_config import RTPLAN_DELIVERY_METADATA_VERSION, extract_rtplan_delivery_metadata
from ascend.layer1.artifacts import deterministic_npz, streamed_scaled_float64_npy
from ascend.layer1.cache import (
    CACHE_SCHEMA_VERSION,
    LAYER1_RESULT_SCHEMA_VERSION,
    RASTERISATION_ALGORITHM_VERSION,
    Layer1Cache,
    atomic_publish_directory,
    cleanup_abandoned,
)
from ascend.layer1.selection import (
    build_roi_inventory,
    effective_roles_from_bindings,
)
from ascend.layer1.execution import execute_locked_validator
from ascend.layer1.preparation import prepare_layer1_inputs
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
    "rtplan_delivery_metadata_version": RTPLAN_DELIVERY_METADATA_VERSION,
}










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
        prepared = prepare_layer1_inputs(case, VERSIONS)
        rtdose, rtplan = prepared.rtdose, prepared.rtplan
        structure_dataset = prepared.structure_dataset
        selected_reasons = prepared.selected_roi_reasons
        geometry = prepared.geometry
        dose_scaling = prepared.dose_scaling
        image_geometry = prepared.planning_image_geometry
        hashes = prepared.input_hashes
        reference = prepared.eclipse_reference
        key = prepared.cache_key
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

        result = execute_locked_validator(case, prepared, legacy_reference)

        result.manifest["input_file_hashes"] = hashes
        result.manifest["ascend_run_id"] = identifier
        result.manifest["framework"] = "ASCEND"
        result.manifest["validated_geometry"] = serialise_geometry(geometry)
        result.manifest["planning_image_geometry"] = image_geometry
        plan_dataset = pydicom.dcmread(rtplan, stop_before_pixels=True) if rtplan else None
        result.manifest["rtplan_delivery"] = extract_rtplan_delivery_metadata(plan_dataset)
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
