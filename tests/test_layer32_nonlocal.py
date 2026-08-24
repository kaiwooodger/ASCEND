from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest
import zipfile

import numpy as np

from ascend.app.controller import ApplicationController
from ascend.gui.layer32_viewer import DEFAULT_FIELD, FIELD_LABELS, FIELD_METADATA, _colour_map, prepare_layer32_viewer_data
from ascend.layer2.graph.service import Layer22Service
from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.nonlocal_effect.metrics import baseline_survival, final_survival, nonlocal_consequence_fields
from ascend.layer3.nonlocal_effect.models import DEFAULT_PARAMETERS, resolved_parameters
from ascend.layer3.nonlocal_effect.service import Layer32Service
from ascend.layer3.nonlocal_effect.spatial import (
    equivalent_exposure_h, export_spatial_package, indices_to_lps, multilevel_surfaces,
)
from ascend.layer3.nonlocal_effect.solver import solve_no_uptake
from ascend.reporting.export import export_case
from ascend.validation.provenance import file_hash

from .helpers import synthetic_case


def prepared_case(root: Path, include_oar: bool = True):
    case = synthetic_case(root, include_oar=include_oar)
    if include_oar:
        heart = next(item for item in case.layer1.result["manifest"]["roi_inventory"] if item["original_name"] == "Heart")
        case.configuration.oar_structures[0]["roi_identity"] = heart["roi_identity"]
    case.layer2_2 = Layer22Service().run(case)
    gtv = next(item for item in case.layer1.result["manifest"]["roi_inventory"] if item["canonical_mapping"] == "GTV")
    case.configuration.layer31_roi_parameters = [{
        "roi_identity": gtv["roi_identity"], "alpha_beta_gy": 10.0,
        "parameter_source": "synthetic reference", "parameter_source_type": "configured_reference",
        "parameter_set_version": "test-v1", "assignment_method": "test",
    }]
    case.configuration.layer32_parameters = {
        "pde_steps": 12, "history_interval_steps": 4,
        "model_grid_target_spacing_mm": 2.0, "model_domain_margin_mm": 30.0,
    }
    case.configuration.layer32_enabled = True
    case.layer3_1 = Layer31Service().run(case)
    return case


class Layer32NonlocalEffectTests(unittest.TestCase):
    def test_layer32_requires_explicit_enable_without_mutating_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = prepared_case(Path(directory))
            case.configuration.layer32_enabled = False
            before = (
                case.layer3_2.calculation_status, case.layer3_2.interpretation_status,
                case.layer3_2.run_id, case.layer3_2.result, case.layer3_2.error,
            )
            with self.assertRaisesRegex(ValueError, "Layer 3.2 is disabled"):
                ApplicationController(case).run_layer32()
            self.assertEqual((
                case.layer3_2.calculation_status, case.layer3_2.interpretation_status,
                case.layer3_2.run_id, case.layer3_2.result, case.layer3_2.error,
            ), before)

    def test_disabling_layer32_invalidates_only_layer32_and_suppresses_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = prepared_case(root / "case")
            case.layer3_2 = Layer32Service().run(case)
            layer31_run_id = case.layer3_1.run_id
            configuration = type(case.configuration).from_dict(case.configuration.to_dict())
            configuration.layer32_enabled = False
            ApplicationController(case).configure(configuration)
            self.assertEqual(case.layer3_2.calculation_status, "stale")
            self.assertEqual(case.layer3_1.run_id, layer31_run_id)
            self.assertFalse(case.configuration.layer32_enabled)
            outputs = export_case(case, root / "exports")
            self.assertFalse(any(path.name.startswith("layer3_2_") for path in outputs))

    def test_consequence_first_contract_and_absolute_thresholds(self) -> None:
        exposure = np.asarray([0.0, 50.0], dtype=np.float32)
        scaling = 0.0029365813
        fields = nonlocal_consequence_fields(exposure, scaling)
        expected = 100.0 * (1.0 - np.exp(-scaling * exposure))
        self.assertTrue(np.allclose(fields["additional_modelled_survival_reduction_percent"], expected))
        self.assertEqual(DEFAULT_FIELD, "additional_modelled_survival_reduction_percent")
        self.assertEqual(FIELD_METADATA["cumulative_nonlocal_hazard"]["label"], "Cumulative mediator exposure H")
        self.assertNotIn("toxicity", FIELD_METADATA[DEFAULT_FIELD]["label"].lower())
        rgb, _low, _high = _colour_map(np.asarray([[0.0, 1.0]]), (0.0, 1.0))
        self.assertEqual(rgb[0, 0].tolist(), [68, 1, 84])
        self.assertEqual(rgb[0, 1].tolist(), [253, 231, 37])
        for reduction, expected_h in ((2.5, 8.6), (5.0, 17.5), (10.0, 35.9), (20.0, 76.0)):
            self.assertAlmostEqual(equivalent_exposure_h(reduction, scaling), expected_h, delta=0.11)

    def test_fraction_history_q_is_used_in_baseline_survival(self) -> None:
        p = np.asarray([10.0], dtype=np.float32)
        q = np.asarray([20.0], dtype=np.float32)
        actual = baseline_survival(p, q, 0.03, 0.003)[0]
        self.assertAlmostEqual(float(actual), float(np.exp(-0.03 * 10.0 - 0.003 * 20.0)), places=7)
        self.assertNotAlmostEqual(float(actual), float(np.exp(-0.03 * 10.0 - 0.003 * 100.0)), places=4)

    def test_no_uptake_solver_has_zero_vascular_contract(self) -> None:
        parameters = resolved_parameters({
            "pde_steps": 4, "history_interval_steps": 2,
            "emission_max_ros": 0.0, "emission_max_cytokine": 0.0,
        })
        dose = np.ones((5, 5, 5), dtype=np.float32)
        solved = solve_no_uptake(dose, (2.0, 2.0, 2.0), np.ones_like(dose, dtype=bool), parameters)
        self.assertEqual(solved["uptake_model"], "none")
        self.assertEqual(solved["uptake_coefficient"], 0.0)
        self.assertFalse(any("vascular" in key or "uptake" in key for key in DEFAULT_PARAMETERS))
        self.assertTrue(np.array_equal(solved["hazard"], np.zeros_like(dose)))
        lq = np.full_like(dose, 0.5)
        self.assertTrue(np.array_equal(final_survival(lq, solved["hazard"], 1.0), lq))

    def test_service_reuses_graph_and_preserves_physical_dose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = prepared_case(Path(directory))
            dose_path = Path(case.layer1.result["manifest"]["validated_native_dose"]["path"])
            before = file_hash(dose_path)
            record = Layer32Service().run(case)
            self.assertEqual(record.calculation_status, "completed_with_warnings")
            self.assertEqual(file_hash(dose_path), before)
            self.assertFalse(record.result["physical_dose_mutated"])
            self.assertEqual(record.result["model"]["uptake_model"], "none")
            self.assertEqual(record.result["model"]["uptake_coefficient"], 0.0)
            self.assertFalse(record.result["model"]["vascular_geometry_used"])
            self.assertEqual(
                [item["edge_id"] for item in record.result["edge_metrics"]],
                [item["edge_id"] for item in case.layer2_2.result["edges"]],
            )
            self.assertTrue(all(item["purpose"] == "visualisation_only_not_ipvdr_calculation" for item in record.result["edge_profiles"]))
            calculated_shells = [
                item["additional_model_derived_effect_equivalent_dose"]
                for item in record.result["peri_gtv_spill_shells"]
                if item["additional_model_derived_effect_equivalent_dose"].get("status") == "calculated"
            ]
            self.assertTrue(all(item["mean"] >= 0.0 for item in calculated_shells))
            scenarios = {item["scenario"]: item["status"] for item in record.result["comparison_scenarios"]}
            self.assertEqual(scenarios["physical_lq_baseline"], "calculated")
            self.assertEqual(scenarios["nonlocal_no_vascular_sink"], "calculated")
            self.assertEqual(scenarios["nonlocal_anatomical_vascular_sink"], "not_available")
            categories = {item["category"] for item in record.result["modelled_regional_exposure_and_consequence"]}
            self.assertTrue({"target", "vertex", "valley", "peri_gtv_shell", "adjacent_oar"}.issubset(categories))

    def test_oar_spill_viewer_and_export_use_stored_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = prepared_case(root / "case")
            case.layer3_2 = Layer32Service().run(case)
            oar = case.layer3_2.result["oar_biological_spill"]
            self.assertEqual(len(oar), 1)
            self.assertEqual(oar[0]["oar_name"], "Heart")
            self.assertEqual(oar[0]["compliance_assessment"], "not_performed")
            data = prepare_layer32_viewer_data(case)
            self.assertIn("biological_effect_equivalent_dose_gy", data.fields)
            self.assertEqual(data.oar_masks[0][0], "Heart")
            outputs = export_case(case, root / "exports")
            self.assertIn(root / "exports" / "layer3_2_nonlocal_effect_results.json", outputs)
            self.assertIn(root / "exports" / "layer3_2_fields.npz", outputs)

    def test_spatial_package_preserves_scalar_volume_colour_and_lps_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = prepared_case(root / "case")
            case.layer3_2 = Layer32Service().run(case)
            data = prepare_layer32_viewer_data(case)
            field_name = "biological_effect_equivalent_dose_gy"
            surfaces = multilevel_surfaces(data.fields[field_name], data.geometry)
            self.assertEqual(len(surfaces), 4)
            self.assertTrue(all(surface.vertices_lps_mm.shape[1] == 3 for surface in surfaces))
            origin = indices_to_lps(np.asarray([[0.0, 0.0, 0.0]]), data.geometry)[0]
            self.assertTrue(np.allclose(origin, np.asarray(data.geometry["origin"], dtype=float)))
            masks = {
                "GTV": data.fields["gtv_mask"],
                "VTVH_vertices": data.fields["vertex_union_mask"],
                **dict(data.oar_masks),
            }
            outputs = export_spatial_package(
                {key: data.fields[key] for key in FIELD_LABELS}, masks,
                data.geometry, field_name, root / "spatial",
            )
            suffixes = {path.suffix for path in outputs}
            self.assertTrue({".vti", ".vtp", ".ply", ".glb", ".3mf", ".stl", ".json"}.issubset(suffixes))
            glb = next(path for path in outputs if path.suffix == ".glb")
            self.assertEqual(glb.read_bytes()[:4], b"glTF")
            three_mf = next(path for path in outputs if path.suffix == ".3mf")
            with zipfile.ZipFile(three_mf) as archive:
                self.assertIn("3D/3dmodel.model", archive.namelist())
            manifest_path = next(path for path in outputs if path.name == "layer3_2_spatial_export_manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["coordinate_system"], "DICOM patient LPS")
            self.assertEqual(manifest["selected_field"], field_name)
            self.assertEqual(len(manifest["stl_isosurfaces"]), 4)
            self.assertIn("threshold geometry only", manifest["limitations"][0])

    def test_stale_graph_blocks_layer32_without_hiding_upstream_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = prepared_case(Path(directory), include_oar=False)
            case.layer2_2.mark_stale("graph changed")
            record = ApplicationController(case).run_layer32()
            self.assertEqual(record.calculation_status, "blocked")
            self.assertIn("current completed Layer 2.2", record.error)
            self.assertIn(case.layer3_1.calculation_status, {"completed", "completed_with_warnings"})


if __name__ == "__main__":
    unittest.main()
