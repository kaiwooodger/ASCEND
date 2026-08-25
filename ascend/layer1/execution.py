"""Execution boundary for the byte-locked Layer 1 validator.

Input selection and geometry are resolved before this module is called. The
validator receives an identity-filtered RTSTRUCT and normalized RTDOSE frame
offset representation without changing its locked scientific implementation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pydicom

from ascend.dicom.geometry import DoseGeometryError
from ascend.dicom.roi import identity_key
from ascend.layer1.incremental_raster import incremental_rasterisation
from ascend.layer1.preparation import PreparedLayer1Inputs
from ascend.layer1.selection import filtered_rtstruct
from ascend.models.case import ASCENDCase
from ascend.scientific.legacy import layer1_validated as validated


def execute_locked_validator(
    case: ASCENDCase,
    prepared: PreparedLayer1Inputs,
    legacy_reference: Path | None,
) -> Any:
    """Run the authoritative validator against prepared, equivalent inputs."""
    with (
        tempfile.TemporaryDirectory(prefix="ascend-l1-normalized-", dir=case.root) as temporary_folder,
        tempfile.TemporaryDirectory(prefix="ascend-l1-masks-", dir=case.root) as mask_folder,
    ):
        temporary = Path(temporary_folder)
        calculation_dose_path = prepared.rtdose
        if prepared.geometry["grid_frame_offset_vector_convention"] != "relative_from_image_position_patient":
            normalized_dose = pydicom.dcmread(prepared.rtdose)
            normalized_dose.GridFrameOffsetVector = [float(value) for value in prepared.geometry["offsets"]]
            normalized_dose_path = temporary / "rtdose_normalized.dcm"
            normalized_dose.save_as(normalized_dose_path, write_like_original=False)
            calculation_dose_path = normalized_dose_path

        selected_struct = filtered_rtstruct(
            prepared.structure_dataset,
            set(prepared.selected_roi_reasons),
        )
        selected_struct_path = temporary / "rtstruct_selected.dcm"
        selected_struct.save_as(selected_struct_path, write_like_original=False)
        calculation_paths: dict[str, Any] = {
            "rtdose": calculation_dose_path,
            "rtstruct": selected_struct_path,
            "image_series": list(prepared.images),
        }
        if prepared.rtplan:
            calculation_paths["rtplan"] = prepared.rtplan
        gtv_binding = case.configuration.structure_bindings.get("GTV")
        if not isinstance(gtv_binding, dict):
            raise ValueError("GTV must bind to exactly one RTSTRUCT ROI identity.")
        gtv_name = prepared.roi_names_by_number[identity_key(gtv_binding)[1]]
        with incremental_rasterisation(Path(mask_folder)):
            result = validated.validate(
                calculation_paths,
                legacy_reference,
                "",
                "",
                case.configuration.treatment_delivery_mode,
                gtv_name,
            )

    if tuple(result.dose_array_gy.shape) != tuple(prepared.geometry["shape"]):
        raise DoseGeometryError(
            "BLOCK_RTDOSE_GEOMETRY: decoded dose array dimensions differ from "
            "Rows, Columns and NumberOfFrames."
        )
    return result
