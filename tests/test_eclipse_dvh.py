from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from ascend.validation.eclipse_dvh import (
    normalise_eclipse_dvh_source,
    write_import_artifacts,
    write_legacy_gtv_csv,
)
from ascend.workflow.preferences import eclipse_endpoint_suggestions


def _eclipse_export(patient: str, mean_value: float, mean_unit: str, dose_first: bool) -> str:
    if dose_first:
        curve_header = "Dose [Gy] Relative dose [%] Ratio of Total Structure Volume [%]"
        curve_rows = "0.000 0.0 100.0\n15.000 100.0 0.0"
    else:
        curve_header = "Relative dose [%] Dose [Gy] Ratio of Total Structure Volume [%]"
        curve_rows = "0.0 0.000 100.0\n100.0 15.000 0.0"
    return f"""Patient Name: Research^Case
Patient ID: {patient}
Course: Phantom
Plan: Lattice_LLU2
Total dose [Gy]: 15.0

Structure: CTV
Volume [cm³]: 922.5
Min Dose [{mean_unit}]: {mean_value / 2}
Max Dose [{mean_unit}]: {mean_value * 2}
Mean Dose [{mean_unit}]: {mean_value}
D2.0% [Gy]: 11.934
D95.0% [%]: 3.1
{curve_header}
{curve_rows}
"""


class EclipseDvhImportTests(unittest.TestCase):
    def test_supported_eclipse_endpoints_auto_fill_without_mapping_summary_statistics(self) -> None:
        text = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
D95% [Gy]: 18
D2% [Gy]: 21
V95%Rx [%]: 87
V10Gy [%]: 93
Mean Dose [Gy]: 19
Dose [Gy] Volume [%]
0 100
21 0
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dvh.txt"
            path.write_text(text, encoding="utf-8")
            endpoints, summary = eclipse_endpoint_suggestions(path, {"GTV": "Target"}, "GENERAL003", "Plan-C")
            self.assertEqual(
                [item["id"] for item in endpoints],
                ["gtv_v10gy", "gtv_v95rx", "gtv_d2", "gtv_d95"],
            )
            self.assertTrue(all(item["source"] == "eclipse_reference_auto_fill" for item in endpoints))
            self.assertEqual(summary["supplied_record_count"], 6)
            self.assertEqual(summary["auto_filled_endpoint_count"], 4)

    def test_utf16_decimal_comma_cgy_and_header_aliases(self) -> None:
        text = """Patient Identifier: GENERAL001
Plan Name: Plan-A
Normalization dose [cGy]: 2000,0

Structure: TARGET-ALPHA
Structure Volume [mL]: 10,5
Minimum Dose [cGy]: 100,0
Maximum Dose [cGy]: 2100,0
Average Dose [cGy]: 1500,0
D2% [cGy]: 1900,0
D20.0% [cGy]: 1700,0
D95,0% [cGy]: 1000,0
Dose [cGy] Relative dose [%] Relative Volume [%]
0,0 0,0 100,0
2000,0 100,0 0,0
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "EXPORT.TXT"
            path.write_text(text, encoding="utf-16")
            imported = normalise_eclipse_dvh_source(path, {"GTV": "TARGET-ALPHA"}, "GENERAL001", "Plan-A")
            metrics = {item["metric"]: item["value"] for item in imported["metrics"]}
            self.assertEqual(set(metrics), {"Volume", "D95", "D20", "D2", "Dmin", "Dmax", "Dmean"})
            self.assertAlmostEqual(imported["total_dose_gy"], 20.0)
            self.assertAlmostEqual(metrics["Volume"], 10.5)
            self.assertAlmostEqual(metrics["Dmean"], 15.0)
            self.assertAlmostEqual(metrics["D2"], 19.0)
            self.assertAlmostEqual(imported["curves"][0]["points"][-1]["dose_gy"], 20.0)

    def test_missing_normalization_keeps_absolute_metrics_and_marks_relative_metric_unassessed(self) -> None:
        text = """Patient Name: Étude^Générale
Patient ID: GENERAL002
Plan: Plan-B

Structure: Arbitrary Target Name
Volume [cc]: 12.0
Min Dose [Gy]: 1.0
Max Dose [Gy]: 18.0
Mean Dose [Gy]: 8.0
D2.0% [Gy]: 17.0
D95.0% [%]: 25.0
Dose [Gy] Volume [%]
0.0 100.0
18.0 0.0
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "single_structure.txt"
            path.write_text(text, encoding="cp1252")
            imported = normalise_eclipse_dvh_source(path, {"GTV": "Arbitrary Target Name"}, "GENERAL002", "Plan-B")
            self.assertIsNone(imported["total_dose_gy"])
            self.assertEqual({item["metric"] for item in imported["metrics"]}, {"Volume", "D2", "Dmin", "Dmax", "Dmean"})
            self.assertIn("relative_metric_without_normalization", {item["code"] for item in imported["issues"]})
            self.assertIsNone(imported["curves"][0]["points"][0]["relative_dose_pct"])

    def test_redundant_absolute_and_relative_exports_normalise_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "absolute.txt").write_text(_eclipse_export("PHPROLRT01", 3.141, "Gy", True), encoding="utf-8-sig")
            (root / "relative.txt").write_text(_eclipse_export("PHPROLRT01", 20.94, "%", False), encoding="utf-8-sig")
            imported = normalise_eclipse_dvh_source(root, {"GTV": "CTV"}, "PHPROLRT01")
            metrics = {item["metric"]: item for item in imported["metrics"]}
            self.assertEqual(imported["summary"]["files_read"], 2)
            self.assertEqual(imported["summary"]["normalized_metrics"], 6)
            self.assertAlmostEqual(metrics["Dmean"]["value"], 3.141)
            self.assertEqual(metrics["Dmean"]["preferred_conversion"], "direct_gy")
            self.assertAlmostEqual(metrics["D95"]["value"], 0.465)
            self.assertEqual(metrics["D95"]["preferred_conversion"], "relative_percent_of_total_dose")
            self.assertEqual(imported["curves"][0]["display_order"], "dose_first")

    def test_patient_identity_mismatch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dvh.txt"
            path.write_text(_eclipse_export("OTHER", 3.141, "Gy", True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ECLIPSE_DVH_IDENTITY"):
                normalise_eclipse_dvh_source(path, {"GTV": "CTV"}, "PHPROLRT01")

    def test_plan_identity_mismatch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dvh.txt"
            path.write_text(_eclipse_export("PHPROLRT01", 3.141, "Gy", True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ECLIPSE_DVH_IDENTITY"):
                normalise_eclipse_dvh_source(path, {"GTV": "CTV"}, "PHPROLRT01", "OtherPlan")

    def test_conflicting_direct_exports_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "one.txt").write_text(_eclipse_export("PHPROLRT01", 3.1, "Gy", True), encoding="utf-8")
            (root / "two.txt").write_text(_eclipse_export("PHPROLRT01", 4.1, "Gy", True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ECLIPSE_DVH_AMBIGUOUS"):
                normalise_eclipse_dvh_source(root, {"GTV": "CTV"}, "PHPROLRT01")

    def test_conflicting_role_assignment_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dvh.txt"
            path.write_text(_eclipse_export("PHPROLRT01", 3.141, "Gy", True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ECLIPSE_DVH_MAPPING"):
                normalise_eclipse_dvh_source(path, {"GTV": "CTV", "T_L": "CTV"}, "PHPROLRT01")

    def test_legacy_bridge_and_provenance_artifacts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "dvh.txt"
            source.write_text(_eclipse_export("PHPROLRT01", 3.141, "Gy", True), encoding="utf-8")
            imported = normalise_eclipse_dvh_source(source, {"GTV": "CTV"}, "PHPROLRT01")
            bridge = write_legacy_gtv_csv(imported, root / "bridge.csv")
            with bridge.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["metric_name"] for row in rows}, {"Volume", "D95", "D2", "Dmin", "Dmax", "Dmean"})
            audit = [{
                "original_structure": "CTV", "ascend_role": "GTV", "validated_structure": "GTV",
                "metric": "Dmean", "eclipse_value": 3.141, "ascend_value": 3.14,
                "difference": -0.001, "tolerance": 0.2, "unit": "Gy", "status": "PASS", "reason": None,
            }]
            artifacts = write_import_artifacts(imported, audit, root / "artifacts")
            self.assertEqual(set(artifacts), {"normalized_metrics", "normalized_curves", "comparison_audit", "import_manifest"})
            self.assertTrue(all(Path(item["path"]).is_file() and len(item["sha256"]) == 64 for item in artifacts.values()))


if __name__ == "__main__":
    unittest.main()
