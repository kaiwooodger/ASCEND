from __future__ import annotations

import csv
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ascend.app.controller import ApplicationController
from ascend.cli import main as cli_main
from ascend.models.case import ASCENDCase
from ascend.models.config import Prescription
from ascend.validation.eclipse_harness import AcceptanceCriteria, EclipseDvhValidationService, ReferenceImportError, ReferenceRecord
from ascend.validation.eclipse_harness.comparison import compare_reference
from ascend.validation.eclipse_harness.matching import match_reference
from ascend.validation.eclipse_harness.reference_import import import_canonical_csv
from ascend.validation.eclipse_harness.statistics import bland_altman, build_summary, summarize_records


HEADERS = [
    "case_id", "rtstruct_uid", "rtdose_uid", "rtplan_uid", "roi_number", "roi_name",
    "endpoint", "value", "units", "rx_gy", "reference_volume_cc", "structure_role",
    "eclipse_software", "eclipse_version",
]


def synthetic_case(root: str) -> ASCENDCase:
    case = ASCENDCase(root, case_id="CASE1")
    case.configuration.prescriptions["Rx_L"] = Prescription(20.0, 1, "user_supplied")
    case.configuration.structure_roles = {"T_L": "PTV"}
    case.layer1.run_id = "L1_TEST"
    case.layer1.result_path = str(Path(root) / "validated" / "layer1_result.json")
    case.layer1.result = {
        "manifest": {
            "case_id": "CASE1",
            "rtstruct_uid": "1.2.3",
            "rtdose_uid": "1.2.4",
            "rtplan_uid": "1.2.5",
            "layer1_algorithm_version": "LOCKED-L1",
            "layer1_result_schema_version": "L1-v2",
            "effective_structure_roles": {"T_L": "PTVLOW"},
            "dose_grid": {"voxel_spacing_mm": [1.0, 1.0, 1.0], "dimensions": [4, 4, 4]},
            "mask_export": {"structures": {"PTVLOW": {"voxel_count": 1200}}},
            "roi_inventory": [{
                "roi_identity": {"rtstruct_sop_instance_uid": "1.2.3", "roi_number": 7},
                "roi_number": 7,
                "original_name": "PTV",
                "canonical_mapping": "PTVLOW",
                "mapping_status": "CONFIGURED_ALIAS",
                "rasterisation_status": "rasterised",
            }],
        },
        "dvh_summary": [{
            "Structure": "PTVLOW", "Volume_cc": 5.0, "DoseCover_D95_Gy": 20.2,
            "MeanDose_Gy": 21.0,
        }],
        "dvh_audit": [
            {"structure": "PTVLOW", "metric": "D2_Gy", "Layer1_calculated": 24.0, "unit": "Gy"},
            {"structure": "PTVLOW", "metric": "DoseSampledVolume_cc", "Layer1_calculated": 4.8, "unit": "cc"},
            {"structure": "PTVLOW", "metric": "AnatomicalVolumeContour_cc", "Layer1_calculated": 5.0, "unit": "cc"},
        ],
        "findings": [{"level": "WARN", "check": "Plan approval", "detail": "Unapproved"}],
    }
    case.layer2_1.result = {
        "harmonised_metrics": [{
            "metric_id": "peripheral_coverage_v95_rxl", "value": 96.2,
            "units": "%", "applicability": "valid", "warnings": [],
        }]
    }
    return case


def reference(**overrides: object) -> ReferenceRecord:
    values = {
        "case_id": "CASE1", "rtstruct_uid": "1.2.3", "rtdose_uid": "1.2.4", "rtplan_uid": "1.2.5",
        "roi_number": 7, "roi_name": "PTV", "endpoint": "D95", "endpoint_type": "dose_at_volume",
        "eclipse_value": 20.0, "units": "Gy", "source_file": "reference.csv", "source_content_hash": "abc",
        "import_timestamp_utc": "2026-08-11T00:00:00+00:00",
    }
    values.update(overrides)
    return ReferenceRecord(**values)


class ReferenceImportTests(unittest.TestCase):
    def _csv(self, directory: str, rows: list[dict[str, object]], headers: list[str] | None = None) -> Path:
        path = Path(directory) / "reference.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers or HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "case_id": "CASE1", "rtstruct_uid": "1.2.3", "rtdose_uid": "1.2.4", "rtplan_uid": "1.2.5",
            "roi_number": 7, "roi_name": "PTV", "endpoint": "D95", "value": 20.0, "units": "Gy",
            "rx_gy": "", "reference_volume_cc": 5.0, "structure_role": "T_L",
            "eclipse_software": "Eclipse", "eclipse_version": "18.0",
        }
        row.update(overrides)
        return row

    def test_valid_csv_preserves_identity_source_and_prescription(self) -> None:
        with TemporaryDirectory() as directory:
            imported = import_canonical_csv(self._csv(directory, [self._row(endpoint="V95%Rx", value=96.8, units="%", rx_gy=20)]))
            item = imported["records"][0]
            self.assertEqual(item.structure_identity, {"rtstruct_sop_instance_uid": "1.2.3", "roi_number": 7})
            self.assertEqual(item.endpoint, "V95%Rx")
            self.assertEqual(item.endpoint_type, "volume_at_prescription")
            self.assertEqual(item.rx_gy, 20.0)
            self.assertEqual(len(item.source_content_hash), 64)

    def test_missing_required_columns_fails_import(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ReferenceImportError, "missing required columns"):
                import_canonical_csv(self._csv(directory, [{"case_id": "CASE1"}], ["case_id"]))

    def test_malformed_numeric_value_fails_import(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ReferenceImportError, "must be numeric"):
                import_canonical_csv(self._csv(directory, [self._row(value="twenty")]))

    def test_unsupported_units_fail_import(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ReferenceImportError, "units must be Gy or cGy"):
                import_canonical_csv(self._csv(directory, [self._row(units="Sv")]))

    def test_duplicate_reference_rows_fail_import(self) -> None:
        with TemporaryDirectory() as directory:
            row = self._row()
            with self.assertRaisesRegex(ReferenceImportError, "Duplicate Eclipse reference"):
                import_canonical_csv(self._csv(directory, [row, dict(row)]))

    def test_missing_prescription_is_imported_for_explicit_not_comparable_status(self) -> None:
        with TemporaryDirectory() as directory:
            imported = import_canonical_csv(self._csv(directory, [self._row(endpoint="V95%Rx", value=96.8, units="%", rx_gy="")]))
            self.assertEqual(imported["records"][0].import_status, "valid")
            self.assertIsNone(imported["records"][0].rx_gy)

    def test_non_finite_and_negative_values_are_retained_as_invalid_references(self) -> None:
        with TemporaryDirectory() as directory:
            imported = import_canonical_csv(self._csv(directory, [
                self._row(endpoint="D95", value="nan", reference_volume_cc=""),
                self._row(endpoint="Dmean", value=-1, reference_volume_cc=""),
            ]))
            self.assertEqual([item.import_status for item in imported["records"]], ["invalid_reference", "invalid_reference"])

    def test_all_planned_endpoint_families_and_absolute_vx_are_canonicalised(self) -> None:
        with TemporaryDirectory() as directory:
            endpoints = ["D2", "D5", "D50", "D90", "D95", "D98", "Dmean", "V95%Rx", "V100%Rx", "V20Gy"]
            rows = []
            for index, endpoint in enumerate(endpoints, 1):
                is_volume_at_dose = endpoint.startswith("V")
                rows.append(self._row(
                    endpoint=endpoint, value=95.0 if is_volume_at_dose else 20.0,
                    units="%" if is_volume_at_dose else "Gy",
                    rx_gy=20 if "%Rx" in endpoint else "",
                    roi_number=index, roi_name=f"ROI_{index}", reference_volume_cc="",
                ))
            imported = import_canonical_csv(self._csv(directory, rows))
            self.assertEqual([item.endpoint for item in imported["records"]], endpoints)
            self.assertEqual(imported["records"][-1].endpoint_type, "volume_at_absolute_dose")

    def test_cgy_conversion_is_explicit_and_recorded(self) -> None:
        with TemporaryDirectory() as directory:
            imported = import_canonical_csv(self._csv(directory, [self._row(value=2020, units="cGy")]))
            item = imported["records"][0]
            self.assertAlmostEqual(item.eclipse_value, 20.2)
            self.assertEqual(item.provenance["unit_conversion"], "cgy_to_gy")


class MatchingTests(unittest.TestCase):
    def test_exact_uid_and_roi_match(self) -> None:
        with TemporaryDirectory() as directory:
            match = match_reference(synthetic_case(directory), reference())
            self.assertEqual(match.status, "matched_exact_identity")
            self.assertEqual(match.candidate["canonical_structure"], "PTVLOW")

    def test_unique_name_fallback_is_explicit(self) -> None:
        with TemporaryDirectory() as directory:
            match = match_reference(synthetic_case(directory), reference(rtstruct_uid=None, roi_number=None))
            self.assertEqual(match.status, "matched_unique_fallback")
            self.assertIn("unique_name_only_fallback", match.warnings)

    def test_duplicate_name_is_ambiguous(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            duplicate = dict(case.layer1.result["manifest"]["roi_inventory"][0])
            duplicate["roi_number"] = 8
            duplicate["roi_identity"] = {"rtstruct_sop_instance_uid": "1.2.3", "roi_number": 8}
            duplicate["canonical_mapping"] = "PTV_DUPLICATE"
            case.layer1.result["manifest"]["roi_inventory"].append(duplicate)
            match = match_reference(case, reference(rtstruct_uid=None, roi_number=None, structure_role=None))
            self.assertEqual(match.status, "ambiguous")

    def test_missing_roi_is_not_found(self) -> None:
        with TemporaryDirectory() as directory:
            match = match_reference(synthetic_case(directory), reference(roi_number=99))
            self.assertEqual(match.status, "not_found")

    def test_rtstruct_identity_conflict_blocks_matching(self) -> None:
        with TemporaryDirectory() as directory:
            match = match_reference(synthetic_case(directory), reference(rtstruct_uid="9.9.9"))
            self.assertEqual(match.status, "identity_conflict")


class ComparisonTests(unittest.TestCase):
    def test_dose_difference_and_relative_difference(self) -> None:
        with TemporaryDirectory() as directory:
            record = compare_reference(synthetic_case(directory), reference(), AcceptanceCriteria())
            self.assertAlmostEqual(record["delta"], 0.2)
            self.assertAlmostEqual(record["relative_delta_percent"], 1.0)
            self.assertAlmostEqual(record["acceptance_limit"], 0.4)
            self.assertEqual(record["pass_fail"], "pass")

    def test_volume_at_prescription_uses_percentage_points(self) -> None:
        with TemporaryDirectory() as directory:
            item = reference(
                endpoint="V95%Rx", endpoint_type="volume_at_prescription", eclipse_value=96.8,
                units="%", rx_gy=20.0,
            )
            record = compare_reference(synthetic_case(directory), item, AcceptanceCriteria())
            self.assertAlmostEqual(record["delta"], -0.6)
            self.assertEqual(record["delta_semantics"], "percentage-point difference")
            self.assertEqual(record["acceptance_limit"], 1.0)
            self.assertEqual(record["pass_fail"], "pass")

    def test_missing_prescription_is_not_comparable(self) -> None:
        with TemporaryDirectory() as directory:
            item = reference(endpoint="V95%Rx", endpoint_type="volume_at_prescription", eclipse_value=96.8, units="%", rx_gy=None)
            record = compare_reference(synthetic_case(directory), item, AcceptanceCriteria())
            self.assertEqual(record["comparison_status"], "not_comparable")
            self.assertEqual(record["reason"], "missing_prescription")

    def test_acceptance_boundary_and_immediately_above(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            item = reference(eclipse_value=1.0)
            at_limit = compare_reference(case, item, AcceptanceCriteria(), {("PTVLOW", "D95"): {"value": 1.2, "units": "Gy", "source": "test"}})
            above = compare_reference(case, item, AcceptanceCriteria(), {("PTVLOW", "D95"): {"value": 1.200001, "units": "Gy", "source": "test"}})
            self.assertEqual(at_limit["pass_fail"], "pass")
            self.assertEqual(above["pass_fail"], "fail")

    def test_zero_reference_does_not_divide_by_zero(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            item = reference(eclipse_value=0.0)
            record = compare_reference(case, item, AcceptanceCriteria(), {("PTVLOW", "D95"): {"value": 0.1, "units": "Gy", "source": "test"}})
            self.assertIsNone(record["relative_delta_percent"])
            self.assertEqual(record["pass_fail"], "pass")

    def test_very_small_volume_uses_absolute_floor(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            item = reference(endpoint="Volume", endpoint_type="structure_volume", eclipse_value=0.05, units="cc")
            record = compare_reference(case, item, AcceptanceCriteria(), {("PTVLOW", "Volume"): {"value": 0.15, "units": "cc", "source": "test"}})
            self.assertAlmostEqual(record["acceptance_limit"], 0.1)
            self.assertEqual(record["pass_fail"], "pass")

    def test_missing_stored_endpoint_is_explicit_and_not_recalculated(self) -> None:
        with TemporaryDirectory() as directory:
            item = reference(endpoint="D90", endpoint_type="dose_at_volume")
            record = compare_reference(synthetic_case(directory), item, AcceptanceCriteria())
            self.assertEqual(record["comparison_status"], "missing_ascend_endpoint")
            self.assertIn("no stored D90", record["reason"])

    def test_unit_mismatch_is_excluded(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            record = compare_reference(case, reference(), AcceptanceCriteria(), {("PTVLOW", "D95"): {"value": 20.2, "units": "%", "source": "test"}})
            self.assertEqual(record["comparison_status"], "unit_mismatch")

    def test_invalid_reference_is_excluded_with_reason(self) -> None:
        with TemporaryDirectory() as directory:
            item = reference(eclipse_value=-1.0, import_status="invalid_reference", import_reason="Eclipse reference value is negative")
            record = compare_reference(synthetic_case(directory), item, AcceptanceCriteria())
            self.assertEqual(record["comparison_status"], "invalid_reference")
            self.assertEqual(record["pass_fail"], "not_assessed")


class SummaryAndIntegrationTests(unittest.TestCase):
    def test_summary_counts_medians_maxima_and_bland_altman(self) -> None:
        records = [
            {"case_id": "A", "roi_number": 1, "roi_name": "R1", "endpoint": "D95", "units": "Gy", "comparison_status": "valid_comparison", "pass_fail": "pass", "delta": -0.1, "absolute_delta": 0.1, "relative_delta_percent": -1.0, "ascend_value": 9.9, "eclipse_value": 10.0},
            {"case_id": "B", "roi_number": 2, "roi_name": "R2", "endpoint": "D95", "units": "Gy", "comparison_status": "valid_comparison", "pass_fail": "fail", "delta": 0.3, "absolute_delta": 0.3, "relative_delta_percent": 3.0, "ascend_value": 10.3, "eclipse_value": 10.0},
            {"case_id": "C", "roi_number": 3, "roi_name": "R3", "endpoint": "D95", "units": "Gy", "comparison_status": "not_comparable", "pass_fail": "not_assessed", "delta": None, "absolute_delta": None, "relative_delta_percent": None, "ascend_value": None, "eclipse_value": 10.0},
        ]
        stats = summarize_records(records)
        self.assertEqual(stats["n_valid_comparisons"], 2)
        self.assertEqual(stats["n_excluded_or_not_comparable"], 1)
        self.assertEqual(stats["n_passing"], 1)
        self.assertEqual(stats["n_failing"], 1)
        self.assertAlmostEqual(stats["median_signed_difference"], 0.1)
        self.assertAlmostEqual(stats["maximum_absolute_difference"], 0.3)
        ba = bland_altman(records)
        self.assertEqual(len(ba["data"]), 2)
        self.assertAlmostEqual(ba["by_endpoint"][0]["mean_difference"], 0.1)

    def test_service_writes_all_required_outputs_from_canonical_records(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            case.initialise_directories()
            case.save()
            source = Path(directory) / "reference.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerow({
                    "case_id": "CASE1", "rtstruct_uid": "1.2.3", "rtdose_uid": "1.2.4", "rtplan_uid": "1.2.5",
                    "roi_number": 7, "roi_name": "PTV", "endpoint": "D95", "value": 20.0, "units": "Gy",
                    "rx_gy": "", "reference_volume_cc": 5.0, "structure_role": "T_L",
                    "eclipse_software": "Eclipse", "eclipse_version": "18.0",
                })
            output = Path(directory) / "validation-output"
            result = ApplicationController(case).validate_eclipse_dvh(source, output)
            required = {
                "eclipse_dvh_comparisons.json", "eclipse_dvh_comparisons.csv", "eclipse_dvh_summary.json",
                "eclipse_dvh_summary.csv", "eclipse_dvh_bland_altman.csv", "ECLIPSE_DVH_VALIDATION_REPORT.md",
            }
            self.assertTrue(required.issubset({path.name for path in output.iterdir()}))
            self.assertEqual(result["summary"]["overall_counts"]["n_passing"], 2)
            report = (output / "ECLIPSE_DVH_VALIDATION_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("software-agreement criteria", report)
            self.assertIn("not clinical treatment-plan tolerances", report)
            payload = json.loads((output / "eclipse_dvh_comparisons.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["records"][0]["comparison_status"], "valid_comparison")
            self.assertEqual(payload["records"][0]["provenance"]["reference_record"]["eclipse_version"], "18.0")
            self.assertEqual(payload["records"][0]["reference_volume_cc"], 5.0)

    def test_cli_runs_the_same_controller_validation_pathway(self) -> None:
        with TemporaryDirectory() as directory:
            case = synthetic_case(directory)
            case.initialise_directories()
            case_file = case.save()
            source = Path(directory) / "reference.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerow({
                    "case_id": "CASE1", "rtstruct_uid": "1.2.3", "rtdose_uid": "1.2.4", "rtplan_uid": "1.2.5",
                    "roi_number": 7, "roi_name": "PTV", "endpoint": "D95", "value": 20.0, "units": "Gy",
                    "rx_gy": "", "reference_volume_cc": 5.0, "structure_role": "T_L",
                    "eclipse_software": "Eclipse", "eclipse_version": "18.0",
                })
            output = Path(directory) / "cli-output"
            stream = io.StringIO()
            with redirect_stdout(stream):
                exit_code = cli_main([
                    "validate-eclipse-dvh", "--case", str(case_file), "--reference", str(source), "--output", str(output),
                ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stream.getvalue())["status"], "completed")
            self.assertTrue((output / "eclipse_dvh_summary.json").is_file())


if __name__ == "__main__":
    unittest.main()
