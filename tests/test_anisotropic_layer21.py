from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ascend.validation.anisotropic import ANISOTROPIC_GRIDS, run_anisotropic_validation, write_anisotropic_report


class AnisotropicLayer21ValidationTests(unittest.TestCase):
    def test_uniform_and_physical_coordinate_dose_fields(self) -> None:
        result = run_anisotropic_validation()
        self.assertEqual(result["status"], "PASS")
        for item in result["results"]:
            with self.subTest(grid=item["grid_id"]):
                self.assertEqual(item["uniform_dose_validation"]["status"], "PASS")
                self.assertEqual(item["physical_gradient_validation"]["status"], "PASS")
                self.assertEqual(item["layer2_1"]["status"], "PASS")
                self.assertTrue(all(metric["status"] == "PASS" for metric in item["layer2_1"]["metrics"]))

    def test_validation_evidence_artifacts_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            artifacts = write_anisotropic_report(run_anisotropic_validation(), Path(folder))
            self.assertEqual(set(artifacts), {
                "report", "json", "csv", "resolution_sensitivity_csv",
            })
            self.assertTrue(all(Path(path).is_file() for path in artifacts.values()))


if __name__ == "__main__":
    unittest.main()
