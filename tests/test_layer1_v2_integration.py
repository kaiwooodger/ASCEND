from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pydicom

from ascend.app.controller import ApplicationController
from ascend.layer1.artifacts import canonical_scientific_payload
from ascend.models.config import CaseConfiguration
from ascend.scientific.legacy import layer1_validated as locked
from ascend.validation.provenance import file_hash
from benchmarks.generate_eclipse_fixture import generate


class Layer1V2IntegrationTests(unittest.TestCase):
    def test_selective_raster_cache_and_locked_scientific_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = generate(root / "source", 24, 24, 8, 8, 4)
            paths = {
                "rtdose": source / "RTDOSE.dcm", "rtstruct": source / "RTSTRUCT.dcm",
                "rtplan": source / "RTPLAN.dcm",
                "image_series": sorted(source.glob("CT_*.dcm")),
            }
            locked_result = locked.validate(paths, None, "", "", "simultaneous_integrated_lrt", "GTV")
            controller = ApplicationController()
            case = controller.import_case(source, root / "case")
            configuration = CaseConfiguration.from_dict(json.loads((source / "benchmark_config.json").read_text()))
            controller.configure(configuration)
            bound_gtv = dict(case.configuration.structure_bindings["GTV"])
            identity_configuration = CaseConfiguration.from_dict(case.configuration.to_dict())
            identity_configuration.structure_roles["GTV"] = "stale-display-name"
            controller.configure(identity_configuration)
            self.assertEqual(case.configuration.structure_bindings["GTV"], bound_gtv)
            first = controller.run_layer1()
            self.assertFalse(first.result["manifest"]["cache"]["cache_hit"])
            treatment_context = first.result["manifest"]["treatment_context"]
            self.assertEqual(treatment_context["schema_version"], "ASCEND-TreatmentContext-v2")
            self.assertEqual(len(treatment_context["treatment_context_hash"]), 64)
            self.assertFalse(treatment_context["provenance"]["implicit_dose_warping"])
            inventory = first.result["manifest"]["roi_inventory"]
            self.assertEqual(len(inventory), 8)
            self.assertEqual(sum(item["rasterisation_status"] == "rasterised" for item in inventory), 4)
            self.assertTrue(all(
                item["rasterisation_status"] == "not_rasterised" for item in inventory if item["roi_number"] > 4
            ))
            self.assertEqual({row["Structure"] for row in first.result["dvh_summary"]}, {"GTV", "PTVLOW", "VTVH", "VTVL"})
            for name in ("GTV", "PTVLOW", "VTVH", "VTVL"):
                with np.load(first.result["manifest"]["mask_export"]["path"], allow_pickle=False) as archive:
                    self.assertTrue(np.array_equal(archive[name], locked_result.mask_arrays[name]))
            case.layer1 = first
            second = controller.run_layer1()
            self.assertTrue(second.result["manifest"]["cache"]["cache_hit"])
            self.assertEqual(
                file_hash(first.result["manifest"]["mask_export"]["path"]),
                file_hash(second.result["manifest"]["mask_export"]["path"]),
            )
            self.assertEqual(
                file_hash(first.result["manifest"]["validated_native_dose"]["path"]),
                file_hash(second.result["manifest"]["validated_native_dose"]["path"]),
            )
            self.assertEqual(canonical_scientific_payload(first.result), canonical_scientific_payload(second.result))
            self.assertFalse(any(path.name.startswith(".tmp-") for path in (root / "case" / "validated").iterdir()))

    def test_uniform_anisotropic_grid_completes_layer1_and_layer21_but_scopes_layer22(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = generate(root / "source", 24, 24, 8, 8, 4)
            for path in [source / "RTDOSE.dcm", *source.glob("CT_*.dcm")]:
                dataset = pydicom.dcmread(path)
                dataset.PixelSpacing = [1.0, 2.0]
                dataset.save_as(path, write_like_original=False)
            controller = ApplicationController()
            case = controller.import_case(source, root / "case")
            controller.configure(CaseConfiguration.from_dict(json.loads((source / "benchmark_config.json").read_text())))
            layer1 = controller.run_layer1()
            self.assertNotEqual(layer1.calculation_status, "failed")
            layer21 = controller.run_layer21()
            self.assertIn(layer21.calculation_status, {"completed", "completed_with_warnings"})
            layer22 = controller.run_layer22()
            self.assertEqual(layer22.calculation_status, "outside_validated_scope")


if __name__ == "__main__":
    unittest.main()
