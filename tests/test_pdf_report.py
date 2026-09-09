from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.metrics.service import Layer21Service
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
