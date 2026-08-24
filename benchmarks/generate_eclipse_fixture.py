"""Generate deterministic non-clinical DICOM-RT fixtures for performance tests.

The generated identifiers derive from stable UUID seeds and the patient fields
are synthetic.  These files test ingestion scale; they are not clinical plans.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, RTDoseStorage, RTPlanStorage, RTStructureSetStorage


def uid(seed: str) -> str:
    """Return a deterministic numeric DICOM UID for a synthetic object."""
    return "2.25." + str(uuid.uuid5(uuid.NAMESPACE_URL, "ascend-benchmark:" + seed).int)


def file_dataset(path: Path, sop_class: str, sop_uid: str) -> FileDataset:
    """Create a minimal explicit-VR synthetic DICOM file dataset."""
    meta = FileMetaDataset(); meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = sop_class; meta.MediaStorageSOPInstanceUID = sop_uid
    dataset = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    dataset.is_little_endian = True; dataset.is_implicit_VR = False
    dataset.SOPClassUID = sop_class; dataset.SOPInstanceUID = sop_uid
    dataset.PatientID = "ASCEND_BENCHMARK"; dataset.PatientName = "SYNTHETIC^NONCLINICAL"
    return dataset


def generate(destination: Path, rows: int, columns: int, frames: int, roi_count: int, selected_count: int) -> Path:
    """Write one linked CT, RTSTRUCT, RTPLAN, and RTDOSE benchmark chain."""
    destination.mkdir(parents=True, exist_ok=True)
    study_uid, frame_uid, series_uid = uid("study"), uid("frame"), uid("ct-series")
    ct_uids: list[str] = []
    zero_slice = np.zeros((rows, columns), dtype=np.uint16).tobytes()
    for index in range(frames):
        sop_uid = uid(f"ct-{index}"); ct_uids.append(sop_uid)
        path = destination / f"CT_{index:04d}.dcm"
        ds = file_dataset(path, CTImageStorage, sop_uid)
        ds.Modality = "CT"; ds.StudyInstanceUID = study_uid; ds.SeriesInstanceUID = series_uid; ds.FrameOfReferenceUID = frame_uid
        ds.Rows = rows; ds.Columns = columns; ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0, 0, float(index)]; ds.PixelSpacing = [1, 1]; ds.SliceThickness = 1
        ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"; ds.BitsAllocated = 16
        ds.BitsStored = 16; ds.HighBit = 15; ds.PixelRepresentation = 0; ds.RescaleIntercept = 0; ds.RescaleSlope = 1
        ds.PixelData = zero_slice; ds.save_as(path, write_like_original=False)

    structure_uid = uid("rtstruct")
    struct_path = destination / "RTSTRUCT.dcm"
    struct = file_dataset(struct_path, RTStructureSetStorage, structure_uid)
    struct.Modality = "RTSTRUCT"; struct.StudyInstanceUID = study_uid; struct.SeriesInstanceUID = uid("struct-series")
    structure_names = ["GTV", "PTV", "VTVH", "VTVL"] + [f"VTVH_{index:02d}" for index in range(1, max(0, selected_count - 4) + 1)]
    structure_names += [f"INVENTORY_{index:03d}" for index in range(len(structure_names) + 1, roi_count + 1)]
    struct.StructureSetROISequence = Sequence([]); struct.ROIContourSequence = Sequence([])
    reference_frame = Dataset(); reference_frame.FrameOfReferenceUID = frame_uid
    reference_study = Dataset(); reference_series = Dataset(); reference_series.SeriesInstanceUID = series_uid
    reference_series.ContourImageSequence = Sequence([])
    for sop_uid in ct_uids:
        image = Dataset(); image.ReferencedSOPClassUID = CTImageStorage; image.ReferencedSOPInstanceUID = sop_uid
        reference_series.ContourImageSequence.append(image)
    reference_study.RTReferencedSeriesSequence = Sequence([reference_series])
    reference_frame.RTReferencedStudySequence = Sequence([reference_study])
    struct.ReferencedFrameOfReferenceSequence = Sequence([reference_frame])
    for number, name in enumerate(structure_names, 1):
        roi = Dataset(); roi.ROINumber = number; roi.ReferencedFrameOfReferenceUID = frame_uid
        roi.ROIName = name; roi.ROIGenerationAlgorithm = "MANUAL"
        struct.StructureSetROISequence.append(roi)
        if number <= selected_count:
            z = float(2 + (number * max(1, frames - 4) // max(1, selected_count + 1)))
            margin = max(4, min(rows, columns) // 8)
            shift = number % max(1, min(rows, columns) // 4)
            low, high = float(margin + shift), float(min(rows, columns) - margin + shift)
            high = min(high, float(min(rows, columns) - 2))
            contour = Dataset(); contour.ContourGeometricType = "CLOSED_PLANAR"; contour.NumberOfContourPoints = 4
            contour.ContourData = [low, low, z, high, low, z, high, high, z, low, high, z]
            nearest = min(range(frames), key=lambda item: abs(item - z))
            image = Dataset(); image.ReferencedSOPClassUID = CTImageStorage; image.ReferencedSOPInstanceUID = ct_uids[nearest]
            contour.ContourImageSequence = Sequence([image])
            roi_contour = Dataset(); roi_contour.ReferencedROINumber = number; roi_contour.ContourSequence = Sequence([contour])
            struct.ROIContourSequence.append(roi_contour)
    struct.save_as(struct_path, write_like_original=False)

    plan_uid = uid("rtplan"); plan_path = destination / "RTPLAN.dcm"
    plan = file_dataset(plan_path, RTPlanStorage, plan_uid)
    plan.Modality = "RTPLAN"; plan.StudyInstanceUID = study_uid; plan.SeriesInstanceUID = uid("plan-series")
    plan.FrameOfReferenceUID = frame_uid; plan.RTPlanLabel = "ASCEND_BENCHMARK"; plan.ApprovalStatus = "UNAPPROVED"
    reference = Dataset(); reference.ReferencedSOPClassUID = RTStructureSetStorage; reference.ReferencedSOPInstanceUID = structure_uid
    plan.ReferencedStructureSetSequence = Sequence([reference]); plan.save_as(plan_path, write_like_original=False)

    dose_uid = uid("rtdose"); dose_path = destination / "RTDOSE.dcm"
    dose = file_dataset(dose_path, RTDoseStorage, dose_uid)
    dose.Modality = "RTDOSE"; dose.StudyInstanceUID = study_uid; dose.SeriesInstanceUID = uid("dose-series"); dose.FrameOfReferenceUID = frame_uid
    dose.Rows = rows; dose.Columns = columns; dose.NumberOfFrames = frames
    dose.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]; dose.ImagePositionPatient = [0, 0, 0]
    dose.PixelSpacing = [1, 1]; dose.GridFrameOffsetVector = [float(index) for index in range(frames)]
    dose.DoseUnits = "GY"; dose.DoseType = "PHYSICAL"; dose.DoseSummationType = "PLAN"; dose.DoseGridScaling = 0.01
    dose.SamplesPerPixel = 1; dose.PhotometricInterpretation = "MONOCHROME2"; dose.BitsAllocated = 16
    dose.BitsStored = 16; dose.HighBit = 15; dose.PixelRepresentation = 0
    dose.PixelData = np.full((frames, rows, columns), 1000, dtype=np.uint16).tobytes()
    plan_reference = Dataset(); plan_reference.ReferencedSOPClassUID = RTPlanStorage; plan_reference.ReferencedSOPInstanceUID = plan_uid
    dose.ReferencedRTPlanSequence = Sequence([plan_reference]); dose.save_as(dose_path, write_like_original=False)

    individual_count = max(0, selected_count - 4)
    config = {
        "treatment_delivery_mode": "simultaneous_integrated_lrt", "dose_context": "complete_single_plan",
        "prescriptions": {"Rx_L": {"gy": None, "fractions": None, "source": "unavailable"}, "Rx_H": {"gy": None, "fractions": None, "source": "unavailable"}},
        "fractionation": {}, "structure_roles": {
            "GTV": "GTV", "T_L": "PTV", "VTV_H": "VTVH", "VTV_L": "VTVL",
            **({"VTV_H_individual": [f"VTVH_{index:02d}" for index in range(1, individual_count + 1)]} if individual_count else {}),
        },
        "structure_bindings": {}, "validation_structures": [], "protocol_native_endpoints": [], "oar_structures": [],
    }
    (destination / "benchmark_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return destination


def main() -> None:
    """Parse a benchmark profile and generate its synthetic DICOM chain."""
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--profile", choices=("small", "very-large"), required=True)
    args = parser.parse_args()
    values = {"small": (64, 64, 32, 12, 4), "very-large": (512, 512, 400, 100, 16)}[args.profile]
    generate(args.destination, *values)


if __name__ == "__main__":
    main()
