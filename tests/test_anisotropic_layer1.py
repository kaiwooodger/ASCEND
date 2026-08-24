from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

import numpy as np

from ascend.dicom.geometry import DoseGeometryError, normalise_rtdose_geometry
from ascend.app.controller import ApplicationController
from ascend.models.config import CaseConfiguration
from ascend.validation.anisotropic.comparison import ANISOTROPIC_GRIDS, validate_grid
from ascend.validation.anisotropic.fixtures import build_fixture, malformed_fixture, write_dicom_fixture


class AnisotropicLayer1ValidationTests(unittest.TestCase):
    def test_regular_anisotropic_geometry_and_patient_coordinates(self) -> None:
        for grid in ANISOTROPIC_GRIDS:
            with self.subTest(grid=grid.name):
                result = validate_grid(grid)
                self.assertEqual(result["geometry"]["status"], "PASS")
                self.assertTrue(result["geometry"]["uniform_frame_spacing"])
                expected_anisotropic = len(set(grid.spacing_xyz_mm)) > 1
                self.assertEqual(result["geometry"]["anisotropic"], expected_anisotropic)
                self.assertEqual(result["geometry"]["isotropic"], not expected_anisotropic)

    def test_volume_evidence_records_all_representations_and_resolution_error(self) -> None:
        for grid in ANISOTROPIC_GRIDS:
            with self.subTest(grid=grid.name):
                result = validate_grid(grid)
                self.assertEqual({item["structure"] for item in result["volume_validation"]}, {
                    "GTV", "PTVLOW", "VTVH", "VTVL",
                })
                for item in result["volume_validation"]:
                    self.assertGreater(item["analytic_volume_cc"], 0)
                    self.assertGreater(item["ct_voxel_count"], 0)
                    self.assertTrue(np.isfinite(item["ct_error_pct"]))
                    self.assertEqual(item["ct_volume_cc"], item["dose_sampled_volume_cc"])

    def test_descending_offsets_preserve_frame_markers(self) -> None:
        fixture = build_fixture(ANISOTROPIC_GRIDS[2])
        dataset = fixture.rtdose
        source = np.asarray(dataset.pixel_array).copy()
        for frame in range(source.shape[0]):
            source[frame] = frame + 1
        dataset.PixelData = source.astype(np.uint16).tobytes()
        spacing = float(dataset.GridFrameOffsetVector[1])
        dataset.GridFrameOffsetVector = [-spacing * index for index in range(source.shape[0])]
        geometry = normalise_rtdose_geometry(dataset)
        decoded = np.asarray(dataset.pixel_array)
        self.assertEqual(geometry["canonical_frame_permutation"], list(range(source.shape[0])))
        self.assertTrue(np.array_equal(decoded[:, 0, 0], np.arange(1, source.shape[0] + 1)))
        self.assertEqual([item["source_frame_index"] for item in geometry["frames"]], list(range(source.shape[0])))

    def test_malformed_geometries_are_explicitly_blocked(self) -> None:
        kinds = (
            "nonuniform_frame_spacing", "duplicate_frame_position", "zero_pixel_spacing",
            "negative_pixel_spacing", "nonfinite_pixel_spacing", "malformed_orientation",
            "nonorthogonal_orientation", "mismatched_number_of_frames",
            "nonmonotonic_frame_offsets",
        )
        for kind in kinds:
            with self.subTest(kind=kind):
                with self.assertRaises(DoseGeometryError) as captured:
                    normalise_rtdose_geometry(malformed_fixture(kind))
                self.assertTrue(str(captured.exception).startswith("BLOCK_"))

    def test_complete_synthetic_dicom_chain_runs_layer1_and_layer21(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = write_dicom_fixture(root / "dicom", ANISOTROPIC_GRIDS[2])
            controller = ApplicationController()
            case = controller.import_case(source, root / "case")
            configuration = CaseConfiguration.from_dict(json.loads((source / "validation_config.json").read_text()))
            controller.configure(configuration)
            layer1 = controller.run_layer1()
            self.assertIn(layer1.calculation_status, {"completed", "completed_with_warnings"})
            layer21 = controller.run_layer21()
            self.assertIn(layer21.calculation_status, {"completed", "completed_with_warnings"})
            layer22 = controller.run_layer22()
            self.assertEqual(layer22.calculation_status, "outside_validated_scope")


if __name__ == "__main__":
    unittest.main()
