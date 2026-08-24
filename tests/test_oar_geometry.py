from __future__ import annotations

import unittest

import numpy as np

from ascend.oar.geometry import OARClassification, OARGeometryService


class OARGeometryTests(unittest.TestCase):
    def test_anisotropic_descriptive_geometry_and_nearest_vertex(self) -> None:
        shape = (12, 12, 12)
        oar = np.zeros(shape, dtype=bool)
        oar[4:7, 4:7, 4:7] = True
        first = np.zeros(shape, dtype=bool)
        first[6, 5, 5] = True
        second = np.zeros(shape, dtype=bool)
        second[9, 9, 9] = True
        aggregate = first | second
        result = OARGeometryService().analyse(
            "Heart",
            oar,
            OARClassification.CONTAINING_ORGAN,
            {"VTVH_01": first, "VTVH_02": second},
            (2.0, 1.0, 1.0),
            aggregate,
            0.002,
        )
        self.assertEqual(result["nearest_vertex_id"], "VTVH_01")
        self.assertEqual(result["nearest_vertex_distance_mm"], 0.0)
        self.assertEqual(result["aggregate_vtvh_minimum_surface_distance_mm"], 0.0)
        self.assertAlmostEqual(result["overlap_volume_cc"], 0.002)
        self.assertEqual(result["compliance_interpretation"], "not_performed")
        self.assertEqual(result["aggregate_vtvh_spatial_relationship"], "overlap")
        self.assertEqual(result["nearest_vertex_spatial_relationship"], "overlap")
        self.assertEqual(result["nearest_vertex_zero_distance_reason"], "mask_overlap")
        self.assertFalse(result["geometry_audit"]["vertex_diameter_calculated"])
        self.assertEqual(result["geometry_audit"]["overlapping_vertex_count"], 1)
        self.assertIn(
            "zero_distance_due_to_mask_overlap",
            {item["code"] for item in result["geometry_audit"]["findings"]},
        )
        self.assertNotIn(
            "configured_separate_oar_overlaps_vtvh",
            {item["code"] for item in result["geometry_audit"]["findings"]},
        )

    def test_positive_distance_is_separation_and_not_a_diameter(self) -> None:
        shape = (8, 8, 8)
        oar = np.zeros(shape, dtype=bool); oar[1:3, 1:3, 1:3] = True
        vertex = np.zeros(shape, dtype=bool); vertex[5:7, 1:3, 1:3] = True
        result = OARGeometryService().analyse(
            "Heart", oar, OARClassification.SEPARATE_CRITICAL_OAR,
            {"VTVH_01": vertex}, (2.0, 1.0, 1.0), vertex, 0.002,
        )
        self.assertGreater(result["nearest_vertex_distance_mm"], 0.0)
        self.assertEqual(result["nearest_vertex_spatial_relationship"], "separated")
        self.assertIsNone(result["nearest_vertex_zero_distance_reason"])
        self.assertEqual(result["geometry_audit"]["overlapping_vertex_count"], 0)
        self.assertEqual(result["geometry_audit"]["separated_vertex_count"], 1)
        self.assertFalse(result["geometry_audit"]["vertex_diameter_calculated"])

    def test_empty_oar_is_rejected(self) -> None:
        empty = np.zeros((3, 3, 3), dtype=bool)
        with self.assertRaisesRegex(ValueError, "explicitly supplied"):
            OARGeometryService().analyse(
                "Heart", empty, OARClassification.SEPARATE_CRITICAL_OAR,
                {}, (1.0, 1.0, 1.0), empty, 0.001,
            )


if __name__ == "__main__":
    unittest.main()
