from __future__ import annotations

import unittest

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from ascend.dicom.roi import identity, resolve_name
from ascend.layer1.selection import build_roi_inventory
from ascend.models.config import CaseConfiguration


def rtstruct_fixture() -> Dataset:
    dataset = Dataset()
    dataset.SOPInstanceUID = "1.2.3"
    rois = []
    contours = []
    for number, name, has_contour in ((1, "GTV", True), (2, "Heart", True), (3, "Unused", False)):
        roi = Dataset(); roi.ROINumber = number; roi.ROIName = name; roi.ROIGenerationAlgorithm = "MANUAL"
        rois.append(roi)
        if has_contour:
            roi_contour = Dataset(); roi_contour.ReferencedROINumber = number
            contour = Dataset(); contour.ContourGeometricType = "CLOSED_PLANAR"; contour.ContourData = [0, 0, 0, 1, 0, 0, 1, 1, 0]
            image = Dataset(); image.ReferencedSOPInstanceUID = f"9.8.{number}"
            contour.ContourImageSequence = Sequence([image])
            roi_contour.ContourSequence = Sequence([contour]); contours.append(roi_contour)
    dataset.StructureSetROISequence = Sequence(rois)
    dataset.ROIContourSequence = Sequence(contours)
    return dataset


class RoiInventoryTests(unittest.TestCase):
    def test_inventory_separates_not_rasterised_from_failed(self) -> None:
        dataset = rtstruct_fixture()
        configuration = CaseConfiguration(
            structure_bindings={
                "GTV": identity("1.2.3", 1, "GTV"),
                "T_L": identity("1.2.3", 2, "Heart"),
            }
        )
        inventory = build_roi_inventory(
            dataset,
            configuration,
            [{"roi_number": "1", "original_name": "GTV", "standard_name": "GTV", "mapping_status": "EXACT"}],
            {"GTV": {}},
        )
        by_number = {item["roi_number"]: item for item in inventory}
        self.assertEqual(by_number[1]["rasterisation_status"], "rasterised")
        self.assertEqual(by_number[2]["rasterisation_status"], "rasterisation_failed")
        self.assertEqual(by_number[3]["rasterisation_status"], "not_rasterised")
        self.assertEqual(by_number[3]["selection_reason"], ["not_selected"])
        self.assertEqual(by_number[1]["referenced_contour_image_sop_uids"], ["9.8.1"])

    def test_roi_identity_survives_display_name_change(self) -> None:
        dataset = rtstruct_fixture()
        bound = identity("1.2.3", 1, "Old GTV name")
        dataset.StructureSetROISequence[0].ROIName = "Renamed GTV"
        configuration = CaseConfiguration(structure_bindings={"GTV": bound})
        inventory = build_roi_inventory(dataset, configuration, [], {})
        self.assertEqual(inventory[0]["roi_identity"]["roi_number"], 1)
        self.assertEqual(inventory[0]["original_name"], "Renamed GTV")
        self.assertNotEqual(inventory[0]["rasterisation_status"], "not_rasterised")

    def test_ambiguous_legacy_name_migration_is_rejected(self) -> None:
        dataset = rtstruct_fixture()
        duplicate = Dataset(); duplicate.ROINumber = 4; duplicate.ROIName = "GTV"
        dataset.StructureSetROISequence.append(duplicate)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_name(dataset, "GTV")


if __name__ == "__main__":
    unittest.main()
