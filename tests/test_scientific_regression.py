from __future__ import annotations

import math
import unittest

import numpy as np

from ascend.scientific.legacy import layer1_validated as l1
from ascend.scientific.legacy import layer21_validated as l21
from ascend.scientific.legacy import layer22_reference_validated as l22_reference
from ascend.scientific.legacy import layer22_validated as l22


class ScientificRegressionTests(unittest.TestCase):
    def test_layer21_locked_suite(self) -> None:
        self.assertEqual(l21.synthetic_tests()["status"], "PASS")

    def test_layer22_exact_ground_truth_suite(self) -> None:
        result = l22_reference.synthetic_ground_truth_tests()
        self.assertTrue(all(item["status"] == "PASS" for item in result["tests"]))

    def test_nearest_neighbour_retains_all_ties_without_duplicates(self) -> None:
        points = np.asarray([[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0]], float)
        edges, _ = l22.nearest_neighbour_edges(points, 0.001)
        self.assertEqual(edges, [(0, 1), (0, 2), (1, 3), (2, 3)])

    def test_half_open_even_odd_polygon_fill(self) -> None:
        mask = l1.polygon_fill(np.asarray([1, 1, 4, 4]), np.asarray([1, 4, 4, 1]), 6, 6)
        self.assertEqual(int(mask.sum()), 9)
        self.assertTrue(mask[2, 2])
        self.assertFalse(mask[0, 0])

    def test_contour_stack_volume_is_gap_safe(self) -> None:
        positions = [0.0, 1.0, 10.0, 11.0]
        areas = {position: 100.0 for position in positions}
        self.assertTrue(math.isclose(l1._contour_stack_volume_cc(positions, areas, 1.0), 0.4))

    def test_missing_is_not_encoded_as_zero(self) -> None:
        record = l21.metric("x", value=None, units="Gy", applicability="not_applicable", warnings=[])
        self.assertIsNone(record["value"])


if __name__ == "__main__":
    unittest.main()

