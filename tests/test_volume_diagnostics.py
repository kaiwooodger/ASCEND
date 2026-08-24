from __future__ import annotations

import unittest

import numpy as np

from ascend.validation.volume_diagnostics import (
    DiagnosticConclusion,
    aggregate_component_comparison,
    contour_slice_groups,
    mask_comparison,
    overlap_metrics,
    parse_eclipse_volume_precision,
    three_volume_comparison,
)


class VolumeDiagnosticInfrastructureTests(unittest.TestCase):
    def test_three_volume_table_creation(self) -> None:
        result = three_volume_comparison("all_vertices", 12.0, 12.7, 12.6, 12.5)
        self.assertEqual(result["formal_harness_comparator"], "anatomical_volume_contour_cc")
        self.assertAlmostEqual(result["differences"]["eclipse_minus_contour"]["absolute_cc"], -0.7)
        self.assertAlmostEqual(result["differences"]["dose_minus_ct"]["absolute_cc"], -0.1)

    def test_exact_aggregate_union_agreement(self) -> None:
        first = np.zeros((2, 2, 3), dtype=bool); first[0, 0, 0] = True
        second = np.zeros_like(first); second[1, 1, 2] = True
        result = aggregate_component_comparison(first | second, [first, second], 0.001)
        self.assertTrue(result["bitwise_equal"])
        self.assertEqual(result["symmetric_difference_voxels"], 0)
        self.assertEqual(result["overlap_voxel_count"], 0)

    def test_aggregate_union_disagreement(self) -> None:
        explicit = np.zeros((1, 2, 2), dtype=bool); explicit[0, 0, 0] = True
        component = np.zeros_like(explicit); component[0, 1, 1] = True
        result = aggregate_component_comparison(explicit, [component], 0.01)
        self.assertFalse(result["bitwise_equal"])
        self.assertEqual(result["symmetric_difference_voxels"], 2)
        self.assertEqual(result["dice_coefficient"], 0.0)

    def test_overlapping_individual_components_count_overlap_once_in_union(self) -> None:
        first = np.zeros((1, 1, 3), dtype=bool); first[0, 0, :2] = True
        second = np.zeros_like(first); second[0, 0, 1:] = True
        result = aggregate_component_comparison(first | second, [first, second], 0.1)
        self.assertEqual(result["sum_individual_voxel_counts"], 4)
        self.assertEqual(result["union_voxel_count"], 3)
        self.assertEqual(result["overlap_voxel_count"], 1)

    def test_symmetric_difference_calculation(self) -> None:
        first = np.array([True, True, False, False])
        second = np.array([True, False, True, False])
        result = mask_comparison(first, second, 0.2)
        self.assertEqual(result["symmetric_difference_voxels"], 2)
        self.assertAlmostEqual(result["symmetric_difference_volume_cc"], 0.4)

    def test_dice_calculation(self) -> None:
        first = np.array([True, True, False])
        second = np.array([True, False, True])
        self.assertAlmostEqual(mask_comparison(first, second, 1.0)["dice_coefficient"], 0.5)

    def test_no_individual_components_available(self) -> None:
        result = aggregate_component_comparison(np.ones((1, 1, 1), dtype=bool), [], 1.0)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no_individual_component_rois_available")

    def test_contour_slice_grouping(self) -> None:
        rows = contour_slice_groups([-2.0, 0.0, 2.0])
        self.assertEqual([item["physical_slice_coordinate_mm"] for item in rows], [-2.0, 0.0, 2.0])
        self.assertEqual(rows[1]["spacing_to_previous_mm"], 2.0)

    def test_repeated_physical_slice_positions(self) -> None:
        rows = contour_slice_groups([2.00001, 2.00002, 4.0])
        self.assertTrue(rows[0]["repeated_physical_slice_position"])
        self.assertGreater(rows[0]["raw_position_spread_mm"], 0)

    def test_source_precision_extraction(self) -> None:
        source = "Structure: all_vertices\nVolume [cm³]: 12.0\nMean Dose [Gy]: 13.8\n"
        result = parse_eclipse_volume_precision(source, "all_vertices")
        self.assertEqual(result["reported_text"], "12.0")
        self.assertEqual(result["displayed_decimal_places"], 1)
        self.assertEqual(result["reported_resolution_cc"], 0.1)
        self.assertFalse(result["higher_precision_volume_present_in_same_structure_section"])

    def test_overlap_and_containment(self) -> None:
        first = np.array([True, True, False])
        second = np.array([True, True, True])
        result = overlap_metrics("A", first, "B", second, 0.5)
        self.assertEqual(result["intersection_volume_cc"], 1.0)
        self.assertTrue(result["a_fully_contained_in_b"])
        self.assertFalse(result["b_fully_contained_in_a"])

    def test_diagnostic_classification_serialization(self) -> None:
        result = DiagnosticConclusion(
            "contour_representation_difference", "moderate", "Evidence", True
        ).to_dict()
        self.assertEqual(result["classification"], "contour_representation_difference")
        self.assertTrue(result["discrepancy_unresolved"])

    def test_existing_failure_status_is_preserved_by_diagnostic_record(self) -> None:
        formal = {"comparison_status": "valid_comparison", "pass_fail": "fail", "delta": 0.7}
        diagnostic = {"formal_volume_finding": dict(formal), "diagnostic_status": "investigated"}
        self.assertEqual(diagnostic["formal_volume_finding"], formal)
        self.assertEqual(diagnostic["formal_volume_finding"]["pass_fail"], "fail")


if __name__ == "__main__":
    unittest.main()
