from __future__ import annotations

import unittest

from ascend.validation.anisotropic.comparison import ANISOTROPIC_GRIDS, validate_grid


class AnisotropicLayer22ScopeTests(unittest.TestCase):
    def test_anisotropic_input_at_or_below_2mm_runs_with_scope_warning(self) -> None:
        result = validate_grid(ANISOTROPIC_GRIDS[1])
        self.assertEqual(result["layer1"]["calculation_status"], "completed")
        self.assertEqual(result["layer2_1"]["status"], "PASS")
        self.assertEqual(result["layer2_2"]["calculation_status"], "completed_with_warnings")
        self.assertEqual(result["layer2_2"]["reason"], "anisotropic_grid_outside_original_validation_scope")

    def test_anisotropic_input_above_2mm_is_outside_scope(self) -> None:
        for grid in ANISOTROPIC_GRIDS[2:]:
            with self.subTest(grid=grid.name):
                result = validate_grid(grid)
                self.assertEqual(result["layer1"]["calculation_status"], "completed")
                self.assertEqual(result["layer2_1"]["status"], "PASS")
                self.assertEqual(result["layer2_2"]["calculation_status"], "outside_validated_scope")
                self.assertEqual(result["layer2_2"]["reason"], "axis_spacing_exceeds_2mm_extension")


if __name__ == "__main__":
    unittest.main()
