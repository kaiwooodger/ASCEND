from __future__ import annotations

import unittest

from ascend.validation.anisotropic.comparison import ANISOTROPIC_GRIDS, validate_grid


class AnisotropicLayer22ScopeTests(unittest.TestCase):
    def test_valid_anisotropic_input_is_outside_scope_not_invalid_geometry(self) -> None:
        for grid in ANISOTROPIC_GRIDS[1:]:
            with self.subTest(grid=grid.name):
                result = validate_grid(grid)
                self.assertEqual(result["layer1"]["calculation_status"], "completed")
                self.assertEqual(result["layer2_1"]["status"], "PASS")
                self.assertEqual(result["layer2_2"]["calculation_status"], "outside_validated_scope")
                self.assertEqual(result["layer2_2"]["reason"], "anisotropic_grid_outside_validated_scope")


if __name__ == "__main__":
    unittest.main()
