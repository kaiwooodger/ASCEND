from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.config import CaseConfiguration, Prescription


def mask_hash(mask: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(mask, dtype=np.uint8).tobytes()).hexdigest()


def synthetic_case(root: Path, explicit_vertices: bool = False, include_oar: bool = False) -> ASCENDCase:
    case = ASCENDCase(str(root), "SYNTHETIC")
    case.initialise_directories()
    shape = (21, 21, 21)
    dose = np.full(shape, 5.0, dtype=np.float32)
    high = np.zeros(shape, bool)
    for point in ((10, 6, 6), (10, 6, 14), (10, 14, 6), (10, 14, 14)):
        high[point] = True
    dose[high] = 20.0
    gtv = np.ones(shape, bool)
    peripheral = np.ones(shape, bool)
    valley = gtv & ~high
    masks = {"GTV": gtv, "PTVLOW": peripheral, "VTVH": high, "VTVL": valley}
    if explicit_vertices:
        for index, point in enumerate(((10, 6, 6), (10, 6, 14), (10, 14, 6), (10, 14, 14)), 1):
            vertex = np.zeros(shape, bool)
            vertex[point] = True
            masks[f"VTVH_{index:02d}"] = vertex
    if include_oar:
        oar = np.zeros(shape, bool)
        oar[9:12, 5:8, 5:8] = True
        masks["ROI_9_Heart"] = oar
    l1dir = root / "validated" / "layer1_SYNTHETIC"
    l1dir.mkdir(parents=True)
    archive = l1dir / "layer1_native_dose_masks.npz"
    np.savez_compressed(archive, dose_gy=dose, **{key: value.astype(np.uint8) for key, value in masks.items()})
    native_dose = l1dir / "validated_native_rtdose_float64.npy"
    np.save(native_dose, dose.astype(float), allow_pickle=False)
    import ascend.scientific.legacy.layer21_validated as l21
    manifest = {
        "case_id": "SYNTHETIC", "rtdose_uid": "1.2.3", "rtstruct_uid": "1.2.4", "rtplan_uid": "1.2.5",
        "treatment_component": "LRT_ONLY", "dose_grid": {"voxel_spacing_mm": [1.0, 1.0, 1.0]},
        "input_file_hashes": {"rtdose": "synthetic"},
        "validated_native_dose": {"path": str(native_dose), "sha256": l21.sha256(native_dose)},
        "validated_geometry": {
            "origin": [0.0, 0.0, 0.0], "row_dir": [1.0, 0.0, 0.0],
            "col_dir": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0],
            "offsets": list(map(float, range(shape[0]))), "spacing": [1.0, 1.0], "shape": list(shape),
        },
        "rasterisation": {"volume_definitions": {
            "GTV": {"anatomical_volume_contour_cc": float(gtv.sum() / 1000), "anatomical_volume_ct_cc": float(gtv.sum() / 1000), "dose_sampled_volume_cc": float(gtv.sum() / 1000)},
            "VTVH": {"anatomical_volume_contour_cc": float(high.sum() / 1000), "anatomical_volume_ct_cc": float(high.sum() / 1000), "dose_sampled_volume_cc": float(high.sum() / 1000)},
            **({"ROI_9_Heart": {"anatomical_volume_contour_cc": float(oar.sum() / 1000), "anatomical_volume_ct_cc": float(oar.sum() / 1000), "dose_sampled_volume_cc": float(oar.sum() / 1000)}} if include_oar else {}),
        }},
        "mask_export": {
            "path": str(archive), "sha256": l21.sha256(archive),
            "structures": {key: {"voxel_count": int(value.sum()), "mask_sha256": mask_hash(value)} for key, value in masks.items()},
        },
    }
    manifest["roi_inventory"] = [
        {
            "roi_identity": {"rtstruct_sop_instance_uid": "1.2.4", "roi_number": index},
            "roi_number": index,
            "original_name": original,
            "canonical_mapping": standard,
            "mapping_status": "EXACT",
            "selection_reason": ["synthetic_fixture"],
            "rasterisation_status": "rasterised",
        }
        for index, (original, standard) in enumerate([
            ("GTV", "GTV"), ("PTVLOW", "PTVLOW"), ("VTVH", "VTVH"), ("VTVL", "VTVL"),
            *([("VTVH_01", "VTVH_01"), ("VTVH_02", "VTVH_02"), ("VTVH_03", "VTVH_03"), ("VTVH_04", "VTVH_04")] if explicit_vertices else []),
            *([("Heart", "ROI_9_Heart")] if include_oar else []),
        ], 1)
    ]
    structure_mapping = [
        {"original_name": "GTV", "standard_name": "GTV"},
        {"original_name": "PTVLOW", "standard_name": "PTVLOW"},
        {"original_name": "VTVH", "standard_name": "VTVH"},
        {"original_name": "VTVL", "standard_name": "VTVL"},
        *([{"original_name": "Heart", "standard_name": "ROI_9_Heart"}] if include_oar else []),
    ]
    payload = {"manifest": manifest, "findings": [], "structure_mapping": structure_mapping, "eligibility": {"layer_1_status": "PASS", "layer_2_eligible": True}}
    result = l1dir / "layer1_result.json"
    result.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    case.layer1_status = "PASS"
    case.layer1 = LayerRun("layer1", "completed", "provisional", "SYNTHETIC_L1", result_path=str(result), result=payload)
    case.configuration = CaseConfiguration(
        treatment_delivery_mode="simultaneous_integrated_lrt", dose_context="complete_single_plan",
        prescriptions={"Rx_L": Prescription(5.0, 1, "protocol_configuration"), "Rx_H": Prescription(20.0, 1, "protocol_configuration")},
        fractionation={"fractions": 1}, structure_roles={
            "GTV": "GTV", "T_L": "PTVLOW", "VTV_H": "VTVH", "VTV_L": "VTVL",
            **({"VTV_H_individual": ["VTVH_01", "VTVH_02", "VTVH_03", "VTVH_04"]} if explicit_vertices else {}),
        },
        protocol_context={"prescriptions_confirmed": True, "roles_confirmed": True, "dose_object_confirmed": True, "valley_confirmed": True},
        oar_structures=([{"name": "Heart", "classification": "containing_organ"}] if include_oar else []),
    )
    case.effective_structure_roles = dict(case.configuration.structure_roles)
    case.configuration_hash = "synthetic-configuration"
    case.save()
    return case
