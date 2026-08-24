"""Deterministic synthetic fixtures used by independent validation workstreams."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any
import uuid

import numpy as np
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    CTImageStorage, ExplicitVRLittleEndian, RTDoseStorage, RTPlanStorage,
    RTStructureSetStorage, generate_uid,
)

from .analytic_dose import lrt_field
from .analytic_geometry import (
    GridSpec,
    cuboid_mask,
    cuboid_volume_cc,
    sphere_mask,
    sphere_volume_cc,
)


@dataclass
class AnisotropicFixture:
    """Represent anisotropic fixture state and behavior."""
    grid: GridSpec
    rtdose: Dataset
    masks: dict[str, np.ndarray]
    dose_gy: np.ndarray
    analytic_volumes_cc: dict[str, float]


def build_rtdose_dataset(grid: GridSpec, dose_gy: np.ndarray) -> Dataset:
    """Build rtdose dataset from validated inputs."""
    if dose_gy.shape != grid.shape_zyx:
        raise ValueError("Dose shape does not match the validation grid.")
    scale = 0.001
    encoded = np.rint(dose_gy / scale).astype(np.uint16)
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = RTDoseStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    dataset = Dataset()
    dataset.file_meta = meta
    dataset.SOPClassUID = RTDoseStorage
    dataset.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    dataset.Modality = "RTDOSE"
    dataset.Rows = grid.shape_zyx[1]
    dataset.Columns = grid.shape_zyx[2]
    dataset.NumberOfFrames = grid.shape_zyx[0]
    dataset.ImagePositionPatient = list(grid.origin_xyz_mm)
    dataset.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    dx, dy, dz = grid.spacing_xyz_mm
    dataset.PixelSpacing = [dy, dx]
    dataset.GridFrameOffsetVector = [float(index * dz) for index in range(grid.shape_zyx[0])]
    dataset.DoseUnits = "GY"
    dataset.DoseType = "PHYSICAL"
    dataset.DoseSummationType = "PLAN"
    dataset.DoseGridScaling = scale
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated = 16
    dataset.BitsStored = 16
    dataset.HighBit = 15
    dataset.PixelRepresentation = 0
    dataset.PixelData = encoded.tobytes()
    dataset.is_little_endian = True
    dataset.is_implicit_VR = False
    return dataset


def build_fixture(grid: GridSpec) -> AnisotropicFixture:
    """Build fixture from validated inputs."""
    gtv = sphere_mask(grid, (0.0, 0.0, 0.0), 10.0)
    peripheral = cuboid_mask(grid, (-12.0, 0.0, 0.0), (6.0, 10.0, 10.0))
    vertex_centres = ((-4.5, -4.5, 0.0), (4.5, -4.5, 0.0), (-4.5, 4.5, 0.0), (4.5, 4.5, 0.0))
    vertices = [sphere_mask(grid, centre, 3.0) for centre in vertex_centres]
    high = np.logical_or.reduce(vertices)
    valley = cuboid_mask(grid, (0.0, 0.0, 0.0), (3.0, 3.0, 4.0)) & ~high
    masks = {
        "GTV": gtv,
        "PTVLOW": peripheral,
        "VTVH": high,
        "VTVL": valley,
        **{f"VTVH_{index:02d}": mask for index, mask in enumerate(vertices, 1)},
    }
    dose = lrt_field(grid, high, valley)
    analytic = {
        "GTV": sphere_volume_cc(10.0),
        "PTVLOW": cuboid_volume_cc((6.0, 10.0, 10.0)),
        "VTVH": 4.0 * sphere_volume_cc(3.0),
        "VTVL": cuboid_volume_cc((3.0, 3.0, 4.0)),
    }
    return AnisotropicFixture(grid, build_rtdose_dataset(grid, dose), masks, dose, analytic)


def malformed_fixture(kind: str) -> Dataset:
    """Handle malformed fixture for the enclosing ASCEND workflow."""
    fixture = build_fixture(GridSpec("negative", (1.0, 2.0, 2.5)))
    dataset = fixture.rtdose
    if kind == "nonuniform_frame_spacing":
        values = list(dataset.GridFrameOffsetVector)
        values[-1] = float(values[-1]) + 0.5
        dataset.GridFrameOffsetVector = values
    elif kind == "duplicate_frame_position":
        values = list(dataset.GridFrameOffsetVector)
        values[2] = values[1]
        dataset.GridFrameOffsetVector = values
    elif kind == "zero_pixel_spacing":
        dataset.PixelSpacing = [0.0, 1.0]
    elif kind == "negative_pixel_spacing":
        dataset.PixelSpacing = [-1.0, 1.0]
    elif kind == "nonfinite_pixel_spacing":
        dataset.PixelSpacing = [float("nan"), 1.0]
    elif kind == "malformed_orientation":
        dataset.ImageOrientationPatient = [2, 0, 0, 0, 1, 0]
    elif kind == "nonorthogonal_orientation":
        dataset.ImageOrientationPatient = [1, 0, 0, 1, 0, 0]
    elif kind == "mismatched_number_of_frames":
        dataset.NumberOfFrames = int(dataset.NumberOfFrames) + 1
    elif kind == "nonmonotonic_frame_offsets":
        values = list(dataset.GridFrameOffsetVector)
        values[3], values[4] = values[4], values[3]
        dataset.GridFrameOffsetVector = values
    else:
        raise ValueError(f"Unknown malformed fixture: {kind}")
    return dataset


def _uid(grid: GridSpec, label: str) -> str:
    return "2.25." + str(uuid.uuid5(uuid.NAMESPACE_URL, f"ascend-anisotropic:{grid.name}:{label}").int)


def _file_dataset(path: Path, sop_class_uid: str, sop_instance_uid: str) -> FileDataset:
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    dataset = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    dataset.is_little_endian = True
    dataset.is_implicit_VR = False
    dataset.SOPClassUID = sop_class_uid
    dataset.SOPInstanceUID = sop_instance_uid
    dataset.PatientID = "ASCEND_ANISOTROPIC_VALIDATION"
    dataset.PatientName = "SYNTHETIC^NONCLINICAL"
    return dataset


def _circle_points(centre_x: float, centre_y: float, radius: float, z: float, count: int = 72) -> list[float]:
    angles = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
    result: list[float] = []
    for angle in angles:
        result.extend([centre_x + radius * float(np.cos(angle)), centre_y + radius * float(np.sin(angle)), z])
    return result


def _rectangle_points(centre: tuple[float, float], lengths: tuple[float, float], z: float) -> list[float]:
    cx, cy = centre
    hx, hy = lengths[0] / 2.0, lengths[1] / 2.0
    return [cx - hx, cy - hy, z, cx + hx, cy - hy, z, cx + hx, cy + hy, z, cx - hx, cy + hy, z]


def write_dicom_fixture(destination: str | Path, grid: GridSpec) -> Path:
    """Write one complete classic CT/RTSTRUCT/RTPLAN/RTDOSE validation chain."""
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture(grid)
    study_uid = _uid(grid, "study")
    frame_uid = _uid(grid, "frame")
    ct_series_uid = _uid(grid, "ct-series")
    dx, dy, dz = grid.spacing_xyz_mm
    ox, oy, oz = grid.origin_xyz_mm
    nz, ny, nx = grid.shape_zyx
    ct_uids: list[str] = []
    for index in range(nz):
        sop_uid = _uid(grid, f"ct-{index}")
        ct_uids.append(sop_uid)
        path = output / f"CT_{index:04d}.dcm"
        dataset = _file_dataset(path, CTImageStorage, sop_uid)
        dataset.Modality = "CT"
        dataset.StudyInstanceUID = study_uid
        dataset.SeriesInstanceUID = ct_series_uid
        dataset.FrameOfReferenceUID = frame_uid
        dataset.Rows = ny
        dataset.Columns = nx
        dataset.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        dataset.ImagePositionPatient = [ox, oy, oz + index * dz]
        dataset.PixelSpacing = [dy, dx]
        dataset.SliceThickness = dz
        dataset.SamplesPerPixel = 1
        dataset.PhotometricInterpretation = "MONOCHROME2"
        dataset.BitsAllocated = 16
        dataset.BitsStored = 16
        dataset.HighBit = 15
        dataset.PixelRepresentation = 1
        dataset.RescaleIntercept = 0
        dataset.RescaleSlope = 1
        dataset.PixelData = np.zeros((ny, nx), dtype=np.int16).tobytes()
        dataset.save_as(path, write_like_original=False)

    structure_uid = _uid(grid, "rtstruct")
    structure_path = output / "RTSTRUCT.dcm"
    structure = _file_dataset(structure_path, RTStructureSetStorage, structure_uid)
    structure.Modality = "RTSTRUCT"
    structure.StudyInstanceUID = study_uid
    structure.SeriesInstanceUID = _uid(grid, "struct-series")
    structure.StructureSetLabel = "ANISO_VALIDATION"
    frame_reference = Dataset()
    frame_reference.FrameOfReferenceUID = frame_uid
    study_reference = Dataset()
    series_reference = Dataset()
    series_reference.SeriesInstanceUID = ct_series_uid
    series_reference.ContourImageSequence = Sequence([])
    for sop_uid in ct_uids:
        reference = Dataset()
        reference.ReferencedSOPClassUID = CTImageStorage
        reference.ReferencedSOPInstanceUID = sop_uid
        series_reference.ContourImageSequence.append(reference)
    study_reference.RTReferencedSeriesSequence = Sequence([series_reference])
    frame_reference.RTReferencedStudySequence = Sequence([study_reference])
    structure.ReferencedFrameOfReferenceSequence = Sequence([frame_reference])
    names = ["GTV", "PTVLOW", "VTVH", "VTVL", "VTVH_01", "VTVH_02", "VTVH_03", "VTVH_04"]
    structure.StructureSetROISequence = Sequence([])
    structure.ROIContourSequence = Sequence([])
    vertex_centres = ((-4.5, -4.5), (4.5, -4.5), (-4.5, 4.5), (4.5, 4.5))

    def contour_data(name: str, z: float) -> list[list[float]]:
        if name == "GTV" and abs(z) < 10.0:
            return [_circle_points(0.0, 0.0, math.sqrt(10.0 ** 2 - z ** 2), z)]
        if name == "PTVLOW" and abs(z) <= 5.0:
            return [_rectangle_points((-12.0, 0.0), (6.0, 10.0), z)]
        if name == "VTVL" and abs(z) <= 2.0:
            return [_rectangle_points((0.0, 0.0), (3.0, 3.0), z)]
        if name == "VTVH" and abs(z) < 3.0:
            radius = math.sqrt(3.0 ** 2 - z ** 2)
            return [_circle_points(cx, cy, radius, z) for cx, cy in vertex_centres]
        if name.startswith("VTVH_") and abs(z) < 3.0:
            centre = vertex_centres[int(name[-2:]) - 1]
            return [_circle_points(*centre, math.sqrt(3.0 ** 2 - z ** 2), z)]
        return []

    for number, name in enumerate(names, 1):
        roi = Dataset()
        roi.ROINumber = number
        roi.ReferencedFrameOfReferenceUID = frame_uid
        roi.ROIName = name
        roi.ROIGenerationAlgorithm = "MANUAL"
        structure.StructureSetROISequence.append(roi)
        roi_contour = Dataset()
        roi_contour.ReferencedROINumber = number
        roi_contour.ContourSequence = Sequence([])
        for index in range(nz):
            z = oz + index * dz
            for points in contour_data(name, z):
                contour = Dataset()
                contour.ContourGeometricType = "CLOSED_PLANAR"
                contour.NumberOfContourPoints = len(points) // 3
                contour.ContourData = points
                image = Dataset()
                image.ReferencedSOPClassUID = CTImageStorage
                image.ReferencedSOPInstanceUID = ct_uids[index]
                contour.ContourImageSequence = Sequence([image])
                roi_contour.ContourSequence.append(contour)
        structure.ROIContourSequence.append(roi_contour)
    structure.save_as(structure_path, write_like_original=False)

    plan_uid = _uid(grid, "rtplan")
    plan_path = output / "RTPLAN.dcm"
    plan = _file_dataset(plan_path, RTPlanStorage, plan_uid)
    plan.Modality = "RTPLAN"
    plan.StudyInstanceUID = study_uid
    plan.SeriesInstanceUID = _uid(grid, "plan-series")
    plan.FrameOfReferenceUID = frame_uid
    plan.RTPlanLabel = "ANISO_VALIDATION"
    plan.ApprovalStatus = "UNAPPROVED"
    structure_reference = Dataset()
    structure_reference.ReferencedSOPClassUID = RTStructureSetStorage
    structure_reference.ReferencedSOPInstanceUID = structure_uid
    plan.ReferencedStructureSetSequence = Sequence([structure_reference])
    plan.save_as(plan_path, write_like_original=False)

    dose_uid = _uid(grid, "rtdose")
    dose_path = output / "RTDOSE.dcm"
    dose = _file_dataset(dose_path, RTDoseStorage, dose_uid)
    for element in fixture.rtdose:
        dose.add(element)
    dose.SOPClassUID = RTDoseStorage
    dose.SOPInstanceUID = dose_uid
    dose.file_meta.MediaStorageSOPInstanceUID = dose_uid
    dose.Modality = "RTDOSE"
    dose.StudyInstanceUID = study_uid
    dose.SeriesInstanceUID = _uid(grid, "dose-series")
    dose.FrameOfReferenceUID = frame_uid
    plan_reference = Dataset()
    plan_reference.ReferencedSOPClassUID = RTPlanStorage
    plan_reference.ReferencedSOPInstanceUID = plan_uid
    dose.ReferencedRTPlanSequence = Sequence([plan_reference])
    dose.save_as(dose_path, write_like_original=False)
    configuration = {
        "treatment_delivery_mode": "simultaneous_integrated_lrt",
        "dose_context": "complete_single_plan",
        "prescription_context": "complete_plan",
        "prescriptions": {
            "Rx_L": {"gy": 10.0, "fractions": 1, "source": "protocol_configuration"},
            "Rx_H": {"gy": 20.0, "fractions": 1, "source": "protocol_configuration"},
        },
        "structure_roles": {
            "GTV": "GTV", "T_L": "PTVLOW", "VTV_H": "VTVH", "VTV_L": "VTVL",
            "VTV_H_individual": ["VTVH_01", "VTVH_02", "VTVH_03", "VTVH_04"],
        },
        "protocol_context": {
            "prescriptions_confirmed": True, "roles_confirmed": True,
            "dose_object_confirmed": True, "valley_confirmed": True,
        },
    }
    (output / "validation_config.json").write_text(json.dumps(configuration, indent=2), encoding="utf-8")
    return output
