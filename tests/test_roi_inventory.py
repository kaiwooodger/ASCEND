from __future__ import annotations

import unittest

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from ascend.dicom.roi import identity, resolve_name
from ascend.layer1.selection import build_roi_inventory, filtered_rtstruct, selected_roi_reasons
from ascend.models.config import CaseConfiguration
from ascend.validation.dvh_eligibility import bind_imported_dvh_rois


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
    def test_only_eight_imported_dvhs_bind_from_ten_rtstruct_rois(self) -> None:
        dataset = Dataset()
        dataset.SOPInstanceUID = "1.2.840.180"
        rois = []
        for number in range(1, 11):
            roi = Dataset(); roi.ROINumber = number; roi.ROIName = f"Structure {number}"
            rois.append(roi)
        dataset.StructureSetROISequence = Sequence(rois)
        records = []
        for number in range(1, 9):
            for endpoint in ("D2", "D95"):
                records.append({
                    "case_id": "CASE", "rtstruct_uid": "1.2.840.180", "roi_number": number,
                    "roi_name": f"Structure {number}", "endpoint": endpoint,
                    "import_status": "valid", "source_content_hash": str(number) * 64,
                })
        verified = bind_imported_dvh_rois({"records": records}, dataset)
        self.assertEqual([item["roi_number"] for item in verified], list(range(1, 9)))
        self.assertNotIn(9, {item["roi_number"] for item in verified})
        self.assertNotIn(10, {item["roi_number"] for item in verified})
        configuration = CaseConfiguration(
            tps_metrics_csv="imported-dvh.csv",
            dvh_verified_rois=verified,
            layer1_rasterisation_rois=[
                {
                    "rtstruct_sop_instance_uid": item["rtstruct_sop_instance_uid"],
                    "roi_number": item["roi_number"],
                }
                for item in verified
            ],
        )
        selected = selected_roi_reasons(configuration, str(dataset.SOPInstanceUID))
        self.assertEqual(sorted(selected), list(range(1, 9)))
        filtered = filtered_rtstruct(dataset, set(selected))
        self.assertEqual(
            [int(item.ROINumber) for item in filtered.StructureSetROISequence],
            list(range(1, 9)),
        )

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
