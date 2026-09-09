"""Synthetic regressions for the cross-dataset DICOM ingestion audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset

from ascend.app.controller import ApplicationController
from ascend.dicom.contours import validate_selected_contours
from ascend.dicom.geometry import DoseGeometryError, normalise_rtdose_geometry, validate_classic_image_series
from ascend.dicom.roi import identity, rtstruct_roi_lookup
from ascend.layer1.preparation import prepare_layer1_inputs
from ascend.layer1.selection import map_selected_rois
from ascend.layer1.service import VERSIONS
from ascend.models.config import CaseConfiguration
from ascend.scientific.legacy.layer1_validated import CaseResult
from benchmarks.generate_eclipse_fixture import generate


@pytest.fixture
def dicom_source(tmp_path: Path):
    source = generate(tmp_path / "source", 24, 24, 8, 8, 4)
    structure = pydicom.dcmread(source / "RTSTRUCT.dcm")
    dose = pydicom.dcmread(source / "RTDOSE.dcm")
    images = [pydicom.dcmread(path, stop_before_pixels=True) for path in sorted(source.glob("CT_*.dcm"))]
    return source, structure, dose, images


def test_optional_contour_image_references_and_unselected_point_roi_are_supported(dicom_source):
    _, structure, dose, images = dicom_source
    del structure.ROIContourSequence[0].ContourSequence[0].ContourImageSequence
    point = copy.deepcopy(structure.ROIContourSequence[0])
    point.ReferencedROINumber = 5
    point.ContourSequence[0].ContourGeometricType = "POINT"
    point.ContourSequence[0].NumberOfContourPoints = 1
    point.ContourSequence[0].ContourData = [1, 1, 1]
    structure.ROIContourSequence.append(point)
    validate_selected_contours(structure, {1}, images, dose)


@pytest.mark.parametrize("mutation, message", [
    ("nonplanar", "not planar"),
    ("oblique", "not planar"),
    ("open", "unsupported contour types"),
    ("nonfinite", "finite xyz"),
    ("point_count", "NumberOfContourPoints"),
    ("missing_image", "image absent"),
    ("wrong_image_plane", "referenced image plane"),
    ("roi_frame", "ROI 1 does not reference"),
    ("image_frame", "same Frame of Reference"),
    ("mixed_xor", "mixes CLOSEDPLANAR_XOR"),
])
def test_invalid_selected_contours_are_rejected_before_rasterisation(dicom_source, mutation, message):
    _, structure, dose, images = dicom_source
    roi = structure.ROIContourSequence[0]
    contour = roi.ContourSequence[0]
    if mutation == "nonplanar":
        contour.ContourData[2] = float(contour.ContourData[2]) + 1.0
    elif mutation == "oblique":
        for index in range(0, len(contour.ContourData), 3):
            contour.ContourData[index + 2] = float(contour.ContourData[index])
    elif mutation == "open":
        contour.ContourGeometricType = "OPEN_PLANAR"
    elif mutation == "nonfinite":
        contour.ContourData[0] = "NaN"
    elif mutation == "point_count":
        contour.NumberOfContourPoints = 3
    elif mutation == "missing_image":
        contour.ContourImageSequence[0].ReferencedSOPInstanceUID = "1.2.3.999"
    elif mutation == "wrong_image_plane":
        contour.ContourImageSequence[0].ReferencedSOPInstanceUID = images[0].SOPInstanceUID
    elif mutation == "roi_frame":
        structure.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = "1.2.3.999"
    elif mutation == "image_frame":
        images[0].FrameOfReferenceUID = "1.2.3.999"
    elif mutation == "mixed_xor":
        xor = copy.deepcopy(contour)
        xor.ContourGeometricType = "CLOSEDPLANAR_XOR"
        roi.ContourSequence.append(xor)
    with pytest.raises(DoseGeometryError, match=message):
        validate_selected_contours(structure, {1}, images, dose)


def test_duplicate_roi_number_is_rejected(dicom_source):
    _, structure, _, _ = dicom_source
    structure.StructureSetROISequence.append(copy.deepcopy(structure.StructureSetROISequence[0]))
    with pytest.raises(ValueError, match="duplicate ROI number"):
        rtstruct_roi_lookup(structure)


@pytest.mark.parametrize("spacing", [[0, 1], [-1, 1]])
def test_classic_series_rejects_nonpositive_pixel_spacing(dicom_source, spacing):
    _, _, _, images = dicom_source
    for image in images:
        image.PixelSpacing = spacing
    with pytest.raises(DoseGeometryError, match="PixelSpacing must be positive"):
        validate_classic_image_series(images)


def test_enhanced_images_and_single_frame_dose_have_explicit_scope_errors(dicom_source):
    _, _, dose, images = dicom_source
    images[0].PerFrameFunctionalGroupsSequence = [Dataset()]
    del images[0].ImageOrientationPatient
    with pytest.raises(DoseGeometryError, match="enhanced or multi-frame"):
        validate_classic_image_series(images)
    dose.NumberOfFrames = 1
    with pytest.raises(DoseGeometryError, match="single-frame RTDOSE"):
        normalise_rtdose_geometry(dose, validate_pixels=False)


@pytest.mark.parametrize("dose_type", ["EFFECTIVE", "ERROR", ""])
def test_nonphysical_dose_cannot_enter_physical_analysis(dicom_source, tmp_path, dose_type):
    source, _, dose, _ = dicom_source
    dose.DoseType = dose_type
    dose.save_as(source / "RTDOSE.dcm", write_like_original=False)
    controller = ApplicationController()
    case = controller.import_case(source, tmp_path / "case")
    controller.configure(CaseConfiguration.from_dict(json.loads((source / "benchmark_config.json").read_text())))
    with pytest.raises(DoseGeometryError, match="DoseType PHYSICAL"):
        prepare_layer1_inputs(case, VERSIONS)


def test_alias_collisions_preserve_independent_roi_identities(dicom_source):
    _, structure, _, _ = dicom_source
    structure.StructureSetROISequence[4].ROIName = "PTVLOW"
    result = CaseResult()
    mapping = map_selected_rois(structure, result, 1)
    assert mapping[1] == "GTV"
    assert mapping[2] == "ROI_2_PTV"
    assert mapping[5] == "ROI_5_PTVLOW"
    assert len(set(mapping.values())) == len(mapping)


@pytest.mark.parametrize("duplicate_name", ["GTV", "gtv", "CTV"])
def test_selected_gtv_identity_survives_duplicate_names_and_aliases(dicom_source, tmp_path, duplicate_name):
    source, structure, _, _ = dicom_source
    structure.StructureSetROISequence[4].ROIName = duplicate_name
    duplicate = copy.deepcopy(structure.ROIContourSequence[0])
    duplicate.ReferencedROINumber = 5
    structure.ROIContourSequence.append(duplicate)
    structure.save_as(source / "RTSTRUCT.dcm", write_like_original=False)
    config = CaseConfiguration.from_dict(json.loads((source / "benchmark_config.json").read_text()))
    dvh_path = Path(config.tps_metrics_csv)
    dvh_path.write_text(
        dvh_path.read_text(encoding="utf-8")
        + f"ASCEND_BENCHMARK,{structure.SOPInstanceUID},5,{duplicate_name},D2,10,Gy\n"
        + f"ASCEND_BENCHMARK,{structure.SOPInstanceUID},5,{duplicate_name},D95,10,Gy\n",
        encoding="utf-8",
    )
    config.structure_bindings = {
        role: identity(str(structure.SOPInstanceUID), number)
        for role, number in (("GTV", 1), ("T_L", 2), ("VTV_H", 3), ("VTV_L", 4))
    }
    config.layer1_rasterisation_rois = [identity(str(structure.SOPInstanceUID), 5)]
    controller = ApplicationController()
    controller.import_case(source, tmp_path / "case")
    controller.configure(config)
    run = controller.run_layer1()
    assert run.calculation_status in {"completed", "completed_with_warnings"}, run.error
    assert run.result["manifest"]["versions"]["roi_identity_mapping_version"] == "ASCEND-ROI-identity-mapping-v2"
    by_number = {int(item["roi_number"]): item["standard_name"] for item in run.result["structure_mapping"]}
    assert by_number[1] == "GTV"
    assert by_number[5] != "GTV"
    with np.load(run.result["manifest"]["mask_export"]["path"]) as masks:
        assert masks["GTV"].any()
        np.testing.assert_array_equal(masks["GTV"], masks[by_number[5]])
