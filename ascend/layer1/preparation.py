"""Layer 1 DICOM, identity, and geometry preparation.

This module resolves selected source objects and validates the geometry
contract required by the locked Layer 1 implementation. It owns no dose,
mask, DVH, or downstream scientific calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydicom

from ascend.dicom.geometry import normalise_rtdose_geometry, validate_classic_image_series
from ascend.dicom.roi import resolve_name, rtstruct_roi_lookup
from ascend.layer1.cache import cache_key
from ascend.layer1.selection import selected_roi_reasons
from ascend.models.case import ASCENDCase
from ascend.treatment.models import TreatmentContext
from ascend.validation.provenance import file_hash


@dataclass(frozen=True)
class PreparedLayer1Inputs:
    """Validated source identities and geometry for one Layer 1 run."""

    rtdose: Path
    rtstruct: Path
    rtplan: Path | None
    images: tuple[Path, ...]
    structure_dataset: Any
    roi_names_by_number: dict[int, str]
    selected_roi_reasons: dict[int, list[str]]
    geometry: dict[str, Any]
    dose_scaling: float
    planning_image_geometry: dict[str, Any]
    input_hashes: dict[str, str]
    eclipse_reference: Path | None
    cache_key: str


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


def _cache_payload(
    case: ASCENDCase,
    hashes: dict[str, str],
    reference: Path | None,
    versions: dict[str, str],
) -> dict[str, Any]:
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
            "rtdose_uid": next((
                item.get("sop_instance_uid") for item in case.dicom_objects.get("RTDOSE", [])
                if item.get("path") == case.selected_objects.get("rtdose")
            ), None),
            "rtplan_uid": next((
                item.get("sop_instance_uid") for item in case.dicom_objects.get("RTPLAN", [])
                if item.get("path") == case.selected_objects.get("rtplan")
            ), None),
        }).to_dict(),
        "eclipse_reference_hashes": _reference_hashes(reference),
        "versions": versions,
    }


def _ensure_bindings(case: ASCENDCase, structure: Any) -> None:
    """Migrate legacy names once, then retain ROI identities as authority."""
    if not case.configuration.structure_bindings and case.configuration.structure_roles:
        case.configuration.structure_bindings = {
            role: (
                [resolve_name(structure, name) for name in value]
                if isinstance(value, list)
                else resolve_name(structure, value)
            )
            for role, value in case.configuration.structure_roles.items()
        }
    for item in case.configuration.oar_structures:
        if item.get("roi_identity") is None:
            item["roi_identity"] = resolve_name(
                structure, str(item.get("name") or item.get("display_name"))
            )
    case.configuration.validate()


def prepare_layer1_inputs(case: ASCENDCase, versions: dict[str, str]) -> PreparedLayer1Inputs:
    """Resolve identity-bound inputs and strict geometry before calculation."""
    if case.dicom_chains and not case.selected_chain_id:
        raise ValueError("DICOM chain selection is unresolved; select one candidate before Layer 1.")
    rtdose, rtstruct, rtplan = (_selected(case, key) for key in ("rtdose", "rtstruct", "rtplan"))
    images = tuple(Path(item) for item in case.selected_objects.get("image_series", []) or [])
    if not rtdose or not rtstruct:
        raise ValueError("RTDOSE and RTSTRUCT selections are required.")
    if len(images) < 2:
        raise ValueError("The complete referenced planning image series is required by validated Layer 1.")

    structure_dataset = pydicom.dcmread(rtstruct, stop_before_pixels=True)
    _ensure_bindings(case, structure_dataset)
    rtstruct_uid = str(getattr(structure_dataset, "SOPInstanceUID", ""))
    reasons = selected_roi_reasons(case.configuration, rtstruct_uid)
    if not reasons:
        raise ValueError("No ROI identities are configured for Layer 1 rasterisation.")
    by_number, _by_name = rtstruct_roi_lookup(structure_dataset)
    missing_numbers = sorted(set(reasons) - set(by_number))
    if missing_numbers:
        raise ValueError(f"Configured ROI identities are absent from the selected RTSTRUCT: {missing_numbers}")

    dose_dataset = pydicom.dcmread(rtdose, stop_before_pixels=True)
    geometry = normalise_rtdose_geometry(dose_dataset, validate_pixels=False)
    dose_scaling = float(dose_dataset.DoseGridScaling)
    image_headers = [pydicom.dcmread(path, stop_before_pixels=True) for path in images]
    image_geometry = validate_classic_image_series(image_headers)
    original_paths: dict[str, Any] = {
        "rtdose": rtdose,
        "rtstruct": rtstruct,
        "image_series": list(images),
    }
    if rtplan:
        original_paths["rtplan"] = rtplan
    hashes = _source_hashes(original_paths)
    reference = Path(case.configuration.tps_metrics_csv) if case.configuration.tps_metrics_csv else None
    return PreparedLayer1Inputs(
        rtdose=rtdose,
        rtstruct=rtstruct,
        rtplan=rtplan,
        images=images,
        structure_dataset=structure_dataset,
        roi_names_by_number=by_number,
        selected_roi_reasons=reasons,
        geometry=geometry,
        dose_scaling=dose_scaling,
        planning_image_geometry=image_geometry,
        input_hashes=hashes,
        eclipse_reference=reference,
        cache_key=cache_key(_cache_payload(case, hashes, reference, versions)),
    )
