from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from ascend.dicom.discovery import discover_case
from ascend.dicom.geometry import DoseGeometryError, normalise_rtdose_geometry
from ascend.dicom.relationships import resolve_dicom_chains, select_chain
from ascend.dicom.rtplan_config import (
    apply_unambiguous_rtplan_prefill,
    extract_rtplan_configuration,
    extract_rtplan_delivery_metadata,
)
from ascend.models.config import CaseConfiguration
from ascend.app.controller import ApplicationController
from ascend.scientific.legacy.layer1_validated import dose_geometry


def write_dose(path: Path) -> None:
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = pydicom.uid.RTDoseStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True; ds.is_implicit_VR = False
    ds.SOPClassUID = meta.MediaStorageSOPClassUID; ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.PatientID = "TEST"; ds.StudyInstanceUID = generate_uid(); ds.SeriesInstanceUID = generate_uid()
    ds.FrameOfReferenceUID = generate_uid(); ds.Modality = "RTDOSE"; ds.DoseUnits = "GY"
    ds.Rows = 2; ds.Columns = 2; ds.NumberOfFrames = 2
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]; ds.ImagePositionPatient = [0, 0, 0]
    ds.PixelSpacing = [1, 1]; ds.GridFrameOffsetVector = [0, 1]
    ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15; ds.PixelRepresentation = 0
    ds.DoseGridScaling = 0.01
    ds.PixelData = np.arange(8, dtype=np.uint16).reshape(2, 2, 2).tobytes()
    ds.save_as(path, write_like_original=False)


class DicomTests(unittest.TestCase):
    def test_rtplan_delivery_metadata_extracts_vmat_mu_efficiency_and_beam_on_time(self) -> None:
        plan = pydicom.dataset.Dataset()
        plan.RTPlanLabel = "VMAT_PLAN"
        plan.SOPInstanceUID = generate_uid()
        beam = pydicom.dataset.Dataset()
        beam.BeamNumber = 1
        beam.BeamName = "ARC_1"
        beam.BeamType = "DYNAMIC"
        beam.RadiationType = "PHOTON"
        beam.TreatmentDeliveryType = "TREATMENT"
        beam.TreatmentMachineName = "LINAC_A"
        beam.NumberOfControlPoints = 3
        beam.FinalCumulativeMetersetWeight = 1.0
        beam.ControlPointSequence = []
        for index, (weight, angle, leaves) in enumerate(
            ((0.0, 181.0, [-10.0, 10.0]), (0.5, 0.0, [-8.0, 8.0]), (1.0, 179.0, [-6.0, 6.0]))
        ):
            point = pydicom.dataset.Dataset()
            point.ControlPointIndex = index
            point.CumulativeMetersetWeight = weight
            point.GantryAngle = angle
            point.GantryRotationDirection = "CC" if index < 2 else "NONE"
            point.NominalBeamEnergy = 6.0
            point.DoseRateSet = 600.0
            point.BeamLimitingDeviceAngle = 30.0
            point.PatientSupportAngle = 0.0
            positions = pydicom.dataset.Dataset()
            positions.RTBeamLimitingDeviceType = "MLCX"
            positions.LeafJawPositions = leaves
            point.BeamLimitingDevicePositionSequence = [positions]
            beam.ControlPointSequence.append(point)
        plan.BeamSequence = [beam]
        group = pydicom.dataset.Dataset()
        group.FractionGroupNumber = 1
        group.NumberOfFractionsPlanned = 5
        reference = pydicom.dataset.Dataset()
        reference.ReferencedBeamNumber = 1
        reference.BeamMeterset = 200.0
        reference.BeamDose = 2.0
        group.ReferencedBeamSequence = [reference]
        plan.FractionGroupSequence = [group]

        metadata = extract_rtplan_delivery_metadata(plan)

        self.assertEqual(metadata["vmat_arc_count"], 1)
        self.assertEqual(metadata["total_mu_per_fraction"], 200.0)
        self.assertEqual(metadata["total_planned_mu"], 1000.0)
        self.assertEqual(metadata["estimated_beam_on_time_seconds_per_fraction"], 20.0)
        self.assertEqual(metadata["beams"][0]["mu_per_gy"], 100.0)
        self.assertEqual(metadata["beams"][0]["gantry_rotation_deg"], 358.0)
        self.assertEqual(metadata["beams"][0]["delivery_technique"], "VMAT")

    def test_rtplan_prefill_handles_multiple_beams_and_fraction_groups_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plan_path = Path(folder) / "plan.dcm"
            dose_path = Path(folder) / "dose.dcm"
            write_dose(dose_path)
            meta = FileMetaDataset(); meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.MediaStorageSOPClassUID = pydicom.uid.RTPlanStorage; meta.MediaStorageSOPInstanceUID = generate_uid()
            plan = FileDataset(str(plan_path), {}, file_meta=meta, preamble=b"\0" * 128)
            plan.is_little_endian = True; plan.is_implicit_VR = False
            plan.SOPClassUID = meta.MediaStorageSOPClassUID; plan.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
            plan.Modality = "RTPLAN"; plan.RTPlanLabel = "MULTI_BEAM"; plan.BeamSequence = [pydicom.dataset.Dataset() for _ in range(6)]
            first = pydicom.dataset.Dataset(); first.FractionGroupNumber = 1; first.NumberOfFractionsPlanned = 5
            second = pydicom.dataset.Dataset(); second.FractionGroupNumber = 2; second.NumberOfFractionsPlanned = 10
            plan.FractionGroupSequence = [first, second]
            low = pydicom.dataset.Dataset(); low.DoseReferenceNumber = 1; low.TargetPrescriptionDose = 20.0; low.DoseReferenceDescription = "Peripheral Rx_L"
            high = pydicom.dataset.Dataset(); high.DoseReferenceNumber = 2; high.TargetPrescriptionDose = 60.0; high.DoseReferenceDescription = "Vertex Rx_H"
            plan.DoseReferenceSequence = [low, high]; plan.save_as(plan_path, write_like_original=False)
            evidence = extract_rtplan_configuration(str(plan_path), str(dose_path))
            self.assertEqual(evidence["beam_count"], 6)
            self.assertEqual(len(evidence["fraction_candidates"]), 2)
            self.assertIn("multiple_rtplan_fraction_groups_require_explicit_fraction_selection", evidence["warnings"])
            config = CaseConfiguration(); apply_unambiguous_rtplan_prefill(config, evidence)
            self.assertEqual(config.prescriptions["Rx_L"].gy, 20.0)
            self.assertEqual(config.prescriptions["Rx_H"].gy, 60.0)
            self.assertEqual(config.fractionation, {})
    def test_ambiguous_case_directory_requires_selection_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            write_dose(Path(folder) / "dose1.dcm")
            write_dose(Path(folder) / "dose2.dcm")
            case = ApplicationController().import_case(folder, Path(folder) / "case")
            self.assertIsNone(case.selected_chain_id)
            self.assertTrue(case.dicom_chains)
            self.assertTrue(all(item["selection_status"] == "selection_required" for item in case.dicom_chains))

    def test_discovery_scaling_and_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dose.dcm"
            write_dose(path)
            inventory = discover_case(folder)
            self.assertEqual(inventory["counts"]["RTDOSE"], 1)
            dataset = pydicom.dcmread(path)
            dose = dataset.pixel_array.astype(float) * float(dataset.DoseGridScaling)
            self.assertTrue(np.allclose(dose.ravel(), np.arange(8) * 0.01))
            geometry = dose_geometry(dataset)
            self.assertEqual(geometry["shape"], (2, 2, 2))
            self.assertTrue(np.array_equal(geometry["spacing"], [1, 1]))

    def test_relative_absolute_and_descending_frame_offsets_preserve_array_association(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dose.dcm"
            write_dose(path)
            dataset = pydicom.dcmread(path)
            dataset.ImagePositionPatient = [0, 0, 30]
            dataset.GridFrameOffsetVector = [30, 29]
            geometry = normalise_rtdose_geometry(dataset)
            self.assertEqual(geometry["grid_frame_offset_vector_convention"], "absolute_patient_z_axial")
            self.assertEqual(geometry["offsets"].tolist(), [0.0, -1.0])
            self.assertEqual([item["source_frame_index"] for item in geometry["frames"]], [0, 1])
            self.assertEqual(dataset.pixel_array[0, 0, 0], 0)
            self.assertEqual(dataset.pixel_array[1, 0, 0], 4)

    def test_nonuniform_dose_spacing_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dose.dcm"
            write_dose(path)
            dataset = pydicom.dcmread(path)
            dataset.NumberOfFrames = 3
            dataset.GridFrameOffsetVector = [0, 1, 3]
            dataset.PixelData = np.arange(12, dtype=np.uint16).reshape(3, 2, 2).tobytes()
            with self.assertRaisesRegex(DoseGeometryError, "NONUNIFORM"):
                normalise_rtdose_geometry(dataset)

    def test_anisotropic_uniform_geometry_is_valid_and_tolerances_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dose.dcm"; write_dose(path)
            dataset = pydicom.dcmread(path); dataset.PixelSpacing = [1, 2]
            geometry = normalise_rtdose_geometry(dataset)
            self.assertEqual(geometry["spacing_zyx_mm"].tolist(), [1.0, 1.0, 2.0])
            dataset.ImageOrientationPatient = [1.01, 0, 0, 0, 1, 0]
            with self.assertRaisesRegex(DoseGeometryError, "unit length"):
                normalise_rtdose_geometry(dataset)
            dataset = pydicom.dcmread(path); del dataset.ImageOrientationPatient
            with self.assertRaisesRegex(DoseGeometryError, "ImageOrientationPatient"):
                normalise_rtdose_geometry(dataset)

    def test_inconsistent_pixel_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dose.dcm"; write_dose(path)
            dataset = pydicom.dcmread(path)
            dataset.BitsStored = 12; dataset.HighBit = 15
            with self.assertRaisesRegex(DoseGeometryError, "HighBit"):
                normalise_rtdose_geometry(dataset)
            dataset = pydicom.dcmread(path); dataset.SamplesPerPixel = 2
            with self.assertRaisesRegex(DoseGeometryError, "SamplesPerPixel"):
                normalise_rtdose_geometry(dataset)

    def test_chain_validity_and_selection_are_independent(self) -> None:
        objects = {
            "RTDOSE": [{"path": "dose", "sop_instance_uid": "d", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_rtplan_uids": ["p"]}],
            "RTPLAN": [{"path": "plan", "sop_instance_uid": "p", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_rtstruct_uids": ["s"]}],
            "RTSTRUCT": [{"path": "struct", "sop_instance_uid": "s", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_image_series_uids": ["ct"]}],
            "CT": [{"path": "ct1", "sop_instance_uid": "i", "series_instance_uid": "ct", "patient_id": "P", "frame_of_reference_uid": "F"}],
        }
        chains = resolve_dicom_chains(objects)
        self.assertEqual(chains[0]["validity_status"], "complete")
        self.assertEqual(chains[0]["selection_status"], "selected")
        evidence = select_chain(chains, chains[0]["chain_id"])
        self.assertFalse(evidence["override_confirmed"])

    def test_incomplete_chain_requires_audited_override(self) -> None:
        objects = {
            "RTDOSE": [{"path": "dose", "sop_instance_uid": "d", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_rtplan_uids": []}],
            "RTSTRUCT": [{"path": "struct", "sop_instance_uid": "s", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_image_series_uids": ["ct"]}],
            "CT": [{"path": "ct1", "sop_instance_uid": "i", "series_instance_uid": "ct", "patient_id": "P", "frame_of_reference_uid": "F"}],
        }
        chains = resolve_dicom_chains(objects)
        self.assertEqual(chains[0]["validity_status"], "override_eligible")
        self.assertEqual(chains[0]["selection_status"], "selection_required")
        with self.assertRaisesRegex(ValueError, "explicit"):
            select_chain(chains, chains[0]["chain_id"])
        evidence = select_chain(chains, chains[0]["chain_id"], True, "Eclipse export omitted RTPLAN")
        self.assertTrue(evidence["override_confirmed"])
        self.assertEqual(evidence["override_reason"], "Eclipse export omitted RTPLAN")

    def test_identity_conflict_makes_incomplete_chain_ineligible_for_override(self) -> None:
        objects = {
            "RTDOSE": [{"path": "dose", "sop_instance_uid": "d", "patient_id": "P1", "frame_of_reference_uid": "F", "referenced_rtplan_uids": []}],
            "RTSTRUCT": [{"path": "struct", "sop_instance_uid": "s", "patient_id": "P2", "frame_of_reference_uid": "F", "referenced_image_series_uids": ["ct"]}],
            "CT": [{"path": "ct1", "sop_instance_uid": "i", "series_instance_uid": "ct", "patient_id": "P2", "frame_of_reference_uid": "F"}],
        }
        chains = resolve_dicom_chains(objects)
        self.assertEqual(chains[0]["validity_status"], "invalid")
        with self.assertRaisesRegex(ValueError, "conflicting patient"):
            select_chain(chains, chains[0]["chain_id"], True, "override attempted")

    def test_multiple_complete_chains_are_valid_but_selection_required(self) -> None:
        objects = {"RTDOSE": [], "RTPLAN": [], "RTSTRUCT": [], "CT": []}
        for suffix in ("1", "2"):
            objects["RTDOSE"].append({"path": f"dose{suffix}", "sop_instance_uid": f"d{suffix}", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_rtplan_uids": [f"p{suffix}"]})
            objects["RTPLAN"].append({"path": f"plan{suffix}", "sop_instance_uid": f"p{suffix}", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_rtstruct_uids": [f"s{suffix}"]})
            objects["RTSTRUCT"].append({"path": f"struct{suffix}", "sop_instance_uid": f"s{suffix}", "patient_id": "P", "frame_of_reference_uid": "F", "referenced_image_series_uids": [f"ct{suffix}"]})
            objects["CT"].append({"path": f"ct{suffix}", "sop_instance_uid": f"i{suffix}", "series_instance_uid": f"ct{suffix}", "patient_id": "P", "frame_of_reference_uid": "F"})
        chains = resolve_dicom_chains(objects)
        self.assertEqual(len(chains), 2)
        self.assertTrue(all(item["validity_status"] == "complete" for item in chains))
        self.assertTrue(all(item["selection_status"] == "selection_required" for item in chains))


if __name__ == "__main__":
    unittest.main()
