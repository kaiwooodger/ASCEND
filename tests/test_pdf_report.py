from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.metrics.service import Layer21Service
from ascend.models.case import LayerRun
from ascend.models.config import CaseConfiguration
from ascend.report_options import DEFAULT_PDF_REPORT_OPTIONS, PDF_REPORT_OPTIONS
from ascend.reporting.pdf_report import export_pdf_report

from .helpers import synthetic_case


class PdfReportTests(unittest.TestCase):
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
