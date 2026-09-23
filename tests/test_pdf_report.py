from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.metrics.service import Layer21Service
from ascend.models.case import LayerRun
from ascend.models.config import CaseConfiguration
from ascend.report_options import DEFAULT_PDF_REPORT_OPTIONS, PDF_REPORT_OPTIONS
from ascend.reporting.pdf_report import _Report, export_pdf_report

from .helpers import synthetic_case


class PdfReportTests(unittest.TestCase):
    def test_tumour_pdf_includes_enabled_alpha_beta_sensitivity_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            case.layer3_1 = LayerRun(
                "layer3_1", "completed", "provisional", "SYNTHETIC_L31",
                parent_layer1_run_id=case.layer1.run_id,
                result={
                    "layer3_1b_high_dose_sfrt_response": {},
                    "layer3_1b_tumour_alpha_beta_sensitivity": {
                        "enabled": True, "tumour_site": "Sarcoma", "parameter_scaling": "hold_alpha",
                        "records": [
                            {"alpha_beta_gy": 2.0, "alpha_per_gy": 0.3, "beta_per_gy2": 0.15,
                             "sf2": 0.301, "mean_tumour_survival_fraction": 0.02, "tumour_eud_gy": 3.1},
                            {"alpha_beta_gy": 10.0, "alpha_per_gy": 0.3, "beta_per_gy2": 0.03,
                             "sf2": 0.487, "mean_tumour_survival_fraction": 0.15, "tumour_eud_gy": 4.5},
                        ],
                    },
                },
            )
            report = _Report(case, {"layer31_tumour"})
            report.section_layer31()
            tables = [item for item in report.story if hasattr(item, "_cellvalues")]
            rows = [[cell.getPlainText() for cell in row] for row in tables[-1]._cellvalues]
            self.assertEqual(rows[0][0:3], ["Alpha/beta (Gy)", "Alpha (Gy-1)", "Beta (Gy-2)"])
            self.assertEqual(rows[1], ["2", "0.3", "0.15", "0.301", "0.02", "3.1"])
            self.assertEqual(rows[2], ["10", "0.3", "0.03", "0.487", "0.15", "4.5"])

    def test_tumour_pdf_table_reports_all_three_regional_survival_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            records = [
                {"region_id": "H", "voxel_count": 10, "tumour_volume_fraction": 0.1,
                 "mean_surviving_fraction": 0.01, "survivor_contribution_fraction": 0.02},
                {"region_id": "V", "voxel_count": 30, "tumour_volume_fraction": 0.3,
                 "mean_surviving_fraction": 0.2, "survivor_contribution_fraction": 0.36},
                {"region_id": "O", "voxel_count": 60, "tumour_volume_fraction": 0.6,
                 "mean_surviving_fraction": 0.172222, "survivor_contribution_fraction": 0.62},
            ]
            case.layer3_1 = LayerRun(
                "layer3_1", "completed", "provisional", "SYNTHETIC_L31",
                parent_layer1_run_id=case.layer1.run_id,
                result={"layer3_1b_high_dose_sfrt_response": {
                    "regional_survival": {
                        "records": records, "contribution_sum": 1.0, "sum_residual": 0.0,
                    },
                }},
            )
            report = _Report(case, {"layer31_tumour"})
            report.section_layer31()
            tables = [item for item in report.story if hasattr(item, "_cellvalues")]
            rows = [[cell.getPlainText() for cell in row] for row in tables[-1]._cellvalues]
            self.assertEqual(rows[0], [
                "Region", "Voxels", "Tumour volume (%)", "Mean SF", "Survivor contribution (%)",
            ])
            self.assertEqual([row[0] for row in rows[1:]], [
                "Vertex (H)", "Valley (V)", "Remaining tumour (O)",
            ])
            self.assertEqual(rows[1][2:], ["10", "0.01", "2"])
            self.assertEqual(rows[2][2:], ["30", "0.2", "36"])
            self.assertEqual(rows[3][2:], ["60", "0.172222", "62"])
            text = " ".join(item.getPlainText() for item in report.story if hasattr(item, "getPlainText"))
            self.assertIn("Contribution sum: 100%; sum residual: 0 percentage points.", text)
            target = export_pdf_report(case, Path(directory) / "regional-survival.pdf", ["layer31_tumour"])
            self.assertTrue(target.read_bytes().startswith(b"%PDF-"))

    def test_oar_pdf_table_includes_every_stored_eud_and_sf_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = synthetic_case(root / "case")
            records = [{
                "oar_name": f"OAR-{index:02}", "classification": "separate_critical_oar",
                "dose_sampled_volume_cc": 12.5,
                "mean_normal_tissue_survival_fraction": 0.0 if index == 0 else 0.125,
                "normal_tissue_eud_gy": None if index == 0 else 4.75,
                "solver": {"solver_status": "not_assessed" if index == 0 else "converged"},
            } for index in range(20)]
            case.layer3_1 = LayerRun(
                "layer3_1", "completed", "provisional", "SYNTHETIC_L31",
                parent_layer1_run_id=case.layer1.run_id,
                result={"layer3_1c_modelled_therapeutic_ratio": {
                    "oar_eud_summary": {"applicability_status": "APPLICABLE", "records": records},
                }},
            )
            report = _Report(case, {"layer31_oar"})
            report.section_layer31()
            tables = [item for item in report.story if hasattr(item, "_cellvalues")]
            rows = [[cell.getPlainText() for cell in row] for row in tables[-1]._cellvalues]
            self.assertEqual(rows[0][3:5], ["Mean OAR SF", "OAR EUD (Gy)"])
            self.assertEqual(len(rows), 21)
            self.assertEqual(rows[1][3:6], ["0", "Not available", "not assessed"])
            self.assertEqual(rows[-1][0], "OAR-19")
            self.assertEqual(rows[-1][3:6], ["0.125", "4.75", "converged"])
            target = export_pdf_report(case, root / "all-oars.pdf", ["layer31_oar"])
            self.assertTrue(target.read_bytes().startswith(b"%PDF-"))

    def test_oar_pdf_explains_missing_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory))
            case.layer3_1.result = {"layer3_1c_modelled_therapeutic_ratio": {
                "oar_eud_summary": {"records": [], "reason": "NO_VERIFIED_OARS"},
            }}
            report = _Report(case, {"layer31_oar"})
            report.section_layer31()
            text = " ".join(item.getPlainText() for item in report.story if hasattr(item, "getPlainText"))
            self.assertIn("NO_VERIFIED_OARS", text)
            self.assertIn("No stored records", text)

    def test_selected_current_results_export_as_one_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = synthetic_case(root / "case")
            case.layer2_1 = Layer21Service().run(case)
            case.layer2_2 = Layer22Service().run(case)
            target = export_pdf_report(
                case,
                root / "selected-report",
                ["overview", "metric:mean_peak_dose", "layer22_graph", "warnings_provenance"],
            )
            self.assertEqual(target.suffix, ".pdf")
            self.assertTrue(target.read_bytes().startswith(b"%PDF-"))
            self.assertGreater(target.stat().st_size, 5_000)

    def test_report_selection_contract_is_validated(self) -> None:
        configuration = CaseConfiguration()
        self.assertEqual(configuration.pdf_report_options, list(DEFAULT_PDF_REPORT_OPTIONS))
        self.assertIn("metric:structure_based_dose_ratio", PDF_REPORT_OPTIONS)
        configuration.pdf_report_options = ["unknown-section"]
        with self.assertRaisesRegex(ValueError, "Unsupported PDF report selections"):
            configuration.validate()

    def test_empty_and_unknown_export_selections_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = synthetic_case(Path(directory) / "case")
            with self.assertRaisesRegex(ValueError, "Select at least one"):
                export_pdf_report(case, Path(directory) / "empty.pdf", [])
            with self.assertRaisesRegex(ValueError, "Unsupported PDF report selections"):
                export_pdf_report(case, Path(directory) / "unknown.pdf", ["unknown-section"])

    def test_large_histograms_and_nested_values_paginate_without_layout_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = synthetic_case(root / "case")
            bins = [float(index) / 10.0 for index in range(201)]
            volumes = [100.0 - float(index) / 2.0 for index in range(201)]
            histogram = {
                "bin_values": bins,
                "volume_pct": volumes,
                "curve_type": "cumulative",
                "histogram_type": "BED-volume histogram",
                "units": "Gy BED",
            }
            case.layer3_1 = LayerRun(
                "layer3_1", "completed", "provisional", "SYNTHETIC_L31",
                parent_layer1_run_id=case.layer1.run_id,
                result={
                    "roi_results": [{
                        "assignment": {
                            "roi_name": "GTV", "roi_identity": {"roi_number": 1}, "alpha_beta_gy": 10.0,
                        },
                        "metrics": {"bed_mean": 24.0, "eqd2_mean": 20.0},
                        "bed_volume_histogram": histogram,
                        "eqd2_volume_histogram": {**histogram, "histogram_type": "EQD2-volume histogram", "units": "Gy EQD2"},
                    }],
                    "layer3_1c_modelled_therapeutic_ratio": {
                        "applicability_status": "APPLICABLE",
                        "large_nested_result": {"values": list(range(10_000))},
                    },
                },
            )
            case.provenance["large_nested_record"] = {"values": list(range(10_000))}

            target = export_pdf_report(
                case,
                root / "large-report.pdf",
                ["layer31_roi", "layer31_oar", "warnings_provenance"],
            )

            self.assertTrue(target.read_bytes().startswith(b"%PDF-"))
            self.assertGreater(target.stat().st_size, 20_000)
