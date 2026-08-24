from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile

import numpy as np

from ascend.gui.layer31_viewer import Layer31ViewerData, _build_cad_scene_bundle, prepare_layer31_viewer_data
from ascend.layer3.history import reconstruct_fraction_history
from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.response.course import run_sensitivity_scenario_matrix
from ascend.layer3.response.mlq import (
    NORMAL_SCENARIOS, TUMOUR_SCENARIOS, lea_catcheside_factor, mlq_effect,
)
from ascend.layer3.visualization import build_biological_mesh, lps_to_indices, sample_scalar_field_lps
from ascend.treatment.models import TreatmentContext
from ascend.validation.provenance import canonical_hash, file_hash

from .helpers import synthetic_case


def _lq_assignment(case, inventory_index: int = 0, alpha_beta: float = 10.0) -> None:
    item = case.layer1.result["manifest"]["roi_inventory"][inventory_index]
    case.configuration.layer31_roi_parameters.append({
        "roi_identity": item["roi_identity"],
        "alpha_beta_gy": alpha_beta,
        "parameter_source": "synthetic closed-form test",
        "parameter_source_type": "configured_reference",
        "parameter_set_version": "test-v1",
        "assignment_method": "identity_bound_test",
    })
    case.configuration.layer31_materialise_full_maps_on_run = True
    case.configuration_hash = canonical_hash(case.configuration.to_dict())


def _kinetic_parameters(identifier: str) -> dict:
    return {
        "parameter_set_id": identifier,
        "parameter_source": "synthetic validation",
        "model_source": "specified test equation",
        "alpha_per_gy": 0.3,
        "beta_per_gy2": 0.03,
        "delta_per_gy": 0.02,
        "repair_half_time": 0.5,
        "treatment_delivery_time": 0.2,
        "time_unit": "hours",
    }


def _components(case, other=None, *, approach="LRT_INTEGRATED", fractions=1) -> None:
    sources = [("LRT", case)] + ([("CERT", other)] if other is not None else [])
    case.configuration.treatment_approach = approach
    case.configuration.treatment_components = [
        {
            "component_id": identifier,
            "component_type": identifier,
            "fraction_count": fractions,
            "source": "synthetic_validation",
        }
        for identifier, _source in sources
    ]
    case.configuration.layer31_component_sources = [
        {
            "component_id": identifier,
            "layer1_result_path": source.layer1.result_path,
            "fraction_dose_model": "identical_fractions",
        }
        for identifier, source in sources
    ]
    case.configuration_hash = canonical_hash(case.configuration.to_dict())


def _field(result: dict, kind: str, alpha_beta: float) -> np.ndarray:
    layer31a = result["layer3_1a_conventional_lq"]
    record = next(item for item in layer31a["spatial_fields"] if item["alpha_beta_gy"] == alpha_beta)
    key = record[f"spatial_{kind}_LQ_array_key"]
    path = Path(layer31a["artifacts"]["spatial_fields_path"])
    assert file_hash(path) == layer31a["artifacts"]["spatial_fields_sha256"]
    with np.load(path, allow_pickle=False) as archive:
        return np.asarray(archive[key])


def test_identical_fraction_closed_form_bed_and_eqd2() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _components(case, fractions=5); _lq_assignment(case)
        result = Layer31Service().run(case).result
        bed = _field(result, "BED", 10.0); eqd2 = _field(result, "EQD2", 10.0)
        # Five repeated 1 Gy fractions in the low-dose region: P=5, Q=5.
        assert np.isclose(bed[0, 0, 0], 5.5)
        assert np.isclose(eqd2[0, 0, 0], 5.5 / 1.2)
        assert result["fraction_history"]["number_of_biological_fraction_events"] == 5


def test_integrated_cross_term_and_sequential_course_are_distinct() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); integrated = synthetic_case(root / "integrated"); second = synthetic_case(root / "second")
        _components(integrated, second, approach="LRT_INTEGRATED"); _lq_assignment(integrated)
        integrated_result = Layer31Service().run(integrated).result
        integrated_bed = _field(integrated_result, "BED", 10.0)
        # Same fraction: d=5+5=10, BED=10+10^2/10=20.
        assert np.isclose(integrated_bed[0, 0, 0], 20.0)
        sequential = synthetic_case(root / "sequential"); _components(sequential, second, approach="LRT_SEQUENTIAL_CERT"); _lq_assignment(sequential)
        sequential_result = Layer31Service().run(sequential).result
        sequential_bed = _field(sequential_result, "BED", 10.0)
        # Separate events: P=10, Q=5^2+5^2=50, BED=15.
        assert np.isclose(sequential_bed[0, 0, 0], 15.0)
        assert integrated_result["fraction_history"]["component_grouping"].startswith("same_fraction")
        assert sequential_result["fraction_history"]["component_grouping"] == "biologically_separate_fraction_events"


def test_voxelwise_bed_is_not_bed_of_roi_mean_dose() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _lq_assignment(case)
        result = Layer31Service().run(case).result; bed = _field(result, "BED", 10.0)
        dose = np.full(bed.shape, 5.0); dose[10, 6, 6] = dose[10, 6, 14] = dose[10, 14, 6] = dose[10, 14, 14] = 20.0
        mean_of_voxel_bed = float(np.mean(bed))
        bed_of_mean = float(np.mean(dose) + np.mean(dose) ** 2 / 10.0)
        assert not np.isclose(mean_of_voxel_bed, bed_of_mean)


def test_tumour_reporting_masks_cannot_silently_use_different_alpha_beta() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _lq_assignment(case, 0, 10.0); _lq_assignment(case, 2, 3.0)
        result = Layer31Service().run(case).result["layer3_1a_conventional_lq"]
        assert result["status"] == "BLOCKED"
        assert "TUMOUR_TISSUE_PARAMETER_INCONSISTENT" in result["blocking_reasons"]


def test_high_dose_warning_is_spatial_and_does_not_switch_formalism() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _lq_assignment(case)
        case.configuration.layer31_lq_high_dose_warning_gy_per_fraction = 10.0
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result; layer31a = result["layer3_1a_conventional_lq"]
        assert layer31a["high_dose_warning"]["flagged_voxel_count"] == 4
        assert layer31a["high_dose_warning"]["model_switching"] is False
        assert result["layer3_1b_high_dose_sfrt_response"]["applicability_status"] == "APPLICABLE"


def test_unresolved_fraction_history_and_registration_are_blocked() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); first = synthetic_case(root / "first"); second = synthetic_case(root / "second")
        _components(first, fractions=1)
        first.configuration.layer31_component_sources[0]["fraction_dose_model"] = "unknown"
        context = TreatmentContext.from_case(first.configuration, first.layer1.result["manifest"])
        unresolved = reconstruct_fraction_history(Layer31Service._components(first), context)
        assert unresolved.status == "BLOCKED" and unresolved.reason == "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED"
        second_path = Path(second.layer1.result_path); payload = json.loads(second_path.read_text())
        payload["manifest"]["validated_geometry"]["origin"][0] = 12.0; second_path.write_text(json.dumps(payload, indent=2))
        second.layer1.result = payload; _components(first, second, approach="LRT_SEQUENTIAL_CERT")
        context = TreatmentContext.from_case(first.configuration, first.layer1.result["manifest"])
        registration = reconstruct_fraction_history(Layer31Service._components(first), context)
        assert registration.status == "BLOCKED"
        assert registration.reason == "BIOLOGICAL_SPATIAL_ACCUMULATION_UNRESOLVED"


def test_mlq_g_is_not_reciprocal_and_effect_is_finite() -> None:
    z = np.asarray([0.0, 0.2, 2.0, 20.0]); g = lea_catcheside_factor(z)
    assert g[0] == 1.0 and np.all(np.diff(g) < 0)
    assert not np.allclose(g[1:], 1.0 / g[1:])
    effect = mlq_effect(np.asarray([0.0, 2.0, 20.0, 100.0]), _kinetic_parameters("extreme"))
    assert np.isfinite(effect).all() and np.all(effect >= 0)


def test_repeated_fraction_mlq_uniform_eud_and_regional_contributions() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _components(case, fractions=5)
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result["layer3_1b_high_dose_sfrt_response"]
        assert result["applicability_status"] == "APPLICABLE"
        assert result["solver_status"] == "converged"
        assert result["residual"] <= result["solver_tolerance"]
        assert result["regional_survival"]["sum_residual"] < 1.0e-12
        assert math.isclose(
            result["equivalent_log_survival_effect"],
            -math.log(result["mean_tumour_survival_fraction"]), rel_tol=0.0, abs_tol=1.0e-12,
        )
        assert "clonogen_density_modelling" in result["limitations"]
        assert "distinct_peak_valley_survival_laws" in result["limitations"]


def test_standardised_scenario_matrix_has_nine_separated_records() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); tumour = _kinetic_parameters("tumour-base"); normal = _kinetic_parameters("normal-base")
        case.configuration.layer31_mlq_tumour_parameters = tumour; case.configuration.layer31_mlq_normal_parameters = normal
        case.configuration.layer31_sensitivity_sweep_enabled = True
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result["layer3_1c_sensitivity_scenario_matrix"]
        assert result["status"] == "PASS" and len(result["records"]) == 9
        assert {(item["tumour_scenario"], item["normal_scenario"]) for item in result["records"]} == {
            (tumour_id, normal_id) for tumour_id in TUMOUR_SCENARIOS for normal_id in NORMAL_SCENARIOS
        }
        assert all(item["applicability_status"] == "APPLICABLE" for item in result["records"])
        assert all(item["tumour_parameter_hash"] != item["normal_parameter_hash"] for item in result["records"])


def test_sequential_mixed_course_has_no_invented_tr_comparator() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); case = synthetic_case(root / "primary"); other = synthetic_case(root / "other")
        _components(case, other, approach="LRT_SEQUENTIAL_CERT")
        case.configuration.layer31_mlq_tumour_parameters = _kinetic_parameters("tumour")
        case.configuration.layer31_mlq_normal_parameters = _kinetic_parameters("normal")
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        result = Layer31Service().run(case).result
        assert result["layer3_1b_high_dose_sfrt_response"]["applicability_status"] == "APPLICABLE"
        assert result["layer3_1c_modelled_therapeutic_ratio"]["applicability_status"] == "NOT_APPLICABLE"
        assert result["layer3_1c_modelled_therapeutic_ratio"]["reason"] == "TR_REFERENCE_SCHEDULE_UNDEFINED"


def test_lps_round_trip_scalar_sampling_and_display_smoothing_invariance() -> None:
    shape = (12, 13, 14); mask = np.zeros(shape, bool); mask[3:9, 4:10, 5:11] = True
    z, y, x = np.indices(shape); field = (z + 2*y + 3*x).astype(np.float32)
    geometry = {
        "origin": [1.0, 2.0, 3.0], "row_direction": [1.0, 0.0, 0.0],
        "column_direction": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0],
        "offsets": list(np.arange(shape[0]) * 2.0), "spacing": [1.5, 1.25], "shape": list(shape),
    }
    points = np.asarray([[3.0, 4.0, 5.0], [7.25, 8.5, 9.75]])
    lps = np.column_stack((1 + points[:, 2]*1.25, 2 + points[:, 1]*1.5, 3 + points[:, 0]*2.0))
    assert np.allclose(lps_to_indices(lps, geometry), points)
    sampled, valid = sample_scalar_field_lps(field, lps, geometry)
    assert valid.all() and np.allclose(sampled, points[:, 0] + 2*points[:, 1] + 3*points[:, 2])
    with tempfile.TemporaryDirectory() as folder:
        first = build_biological_mesh(Path(folder), "ROI", mask, field, geometry, "BED", "Gy BED", {"iterations": 2})
        second = build_biological_mesh(Path(folder), "ROI", mask, field, geometry, "BED", "Gy BED", {"iterations": 18})
        assert first.status == second.status == "PASS"
        assert first.provenance["authoritative_field_hash_before_display_processing"] == second.provenance["authoritative_field_hash_before_display_processing"]
        assert first.provenance["authoritative_field_hash_before_display_processing"] == first.provenance["authoritative_field_hash_after_display_processing"]
        assert not np.array_equal(first.display_surface.vertices_lps_mm, second.display_surface.vertices_lps_mm)
        assert np.array_equal(first.raw_surface.vertices_lps_mm, second.raw_surface.vertices_lps_mm)


def test_constant_biological_field_builds_visible_cad_surface() -> None:
    shape = (80, 90, 100); mask = np.zeros(shape, bool); mask[35:45, 40:50, 45:55] = True
    field = np.full(shape, 7.0, dtype=np.float32)
    geometry = {
        "origin": [1.0, 2.0, 3.0], "row_direction": [1.0, 0.0, 0.0],
        "column_direction": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0],
        "offsets": list(np.arange(shape[0]) * 2.0), "spacing": [1.5, 1.25], "shape": list(shape),
    }
    with tempfile.TemporaryDirectory() as folder:
        result = build_biological_mesh(Path(folder), "ROI", mask, field, geometry, "constant", "unit")
        assert result.status == "PASS"
        assert result.display_surface is not None and len(result.display_surface.vertices_lps_mm) > 0
        assert result.qc["scalar_sampling_coverage_percent"] == 100.0
        low, high = result.qc["raw_patient_space_bounding_box_mm"]
        assert np.asarray(low)[0] > 40.0 and np.asarray(high)[0] < 80.0
        cached = build_biological_mesh(Path(folder), "ROI", mask, field, geometry, "constant", "unit")
        assert cached.status == "PASS" and cached.provenance["display_cache_hit"] is True
        assert np.array_equal(cached.display_surface.vertices_lps_mm, result.display_surface.vertices_lps_mm)


def test_layer31_viewer_loads_only_hash_verified_spatial_fields() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _lq_assignment(case); case.layer3_1 = Layer31Service().run(case)
        data = prepare_layer31_viewer_data(case)
        assert "physical_course_dose_gy" in data.fields
        assert any("spatial_BED_LQ" in key for key in data.fields)
        artifact = Path(case.layer3_1.result["layer3_1a_conventional_lq"]["artifacts"]["spatial_fields_path"])
        artifact.write_bytes(artifact.read_bytes() + b"corrupt")
        try:
            prepare_layer31_viewer_data(case)
        except ValueError as exc:
            assert "hash differs" in str(exc)
        else:
            raise AssertionError("Corrupt spatial archive was accepted")


def test_layer31_composite_cad_contains_anatomy_oar_and_biological_overlay() -> None:
    """The CAD handoff must retain anatomical context around the scalar GTV skin."""
    shape = (18, 20, 22)
    gtv = np.zeros(shape, dtype=bool); gtv[3:15, 4:16, 5:18] = True
    vertices = np.zeros(shape, dtype=bool); vertices[6:10, 7:11, 8:12] = True
    valleys = np.zeros(shape, dtype=bool); valleys[10:13, 10:14, 12:16] = True
    oar = np.zeros(shape, dtype=bool); oar[5:14, 14:19, 2:6] = True
    z, y, x = np.indices(shape)
    physical = (0.2 * z + 0.1 * y + 0.05 * x).astype(np.float32)
    bed = (physical + np.square(physical) / 10.0).astype(np.float32)
    field_id = "spatial_BED_LQ_ab_10"
    geometry = {
        "origin": [0.0, 0.0, 0.0], "row_direction": [1.0, 0.0, 0.0],
        "column_direction": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0],
        "offsets": list(np.arange(shape[0], dtype=float)), "spacing": [1.0, 1.0], "shape": list(shape),
    }
    with tempfile.TemporaryDirectory() as folder:
        data = Layer31ViewerData(
            fields={"physical_course_dose_gy": physical, field_id: bed},
            field_metadata={
                "physical_course_dose_gy": {"label": "Physical absorbed dose", "units": "Gy"},
                field_id: {"label": "Spatial BED", "units": "Gy BED", "alpha_beta_gy": 10.0},
            },
            masks={
                "Region: Whole GTV": gtv, "Region: Vertices": vertices,
                "Region: Valleys": valleys, "OAR: Heart": oar,
            },
            geometry=geometry, result={}, case_root=Path(folder),
            vertex_centres_lps_mm={"V1": (8.0, 8.0, 8.0), "V2": (13.0, 12.0, 10.0)},
            graph_edges_lps_mm=(((8.0, 8.0, 8.0), (13.0, 12.0, 10.0)),),
        )
        bundle = _build_cad_scene_bundle(
            data,
            ("Region: Whole GTV", "Region: Vertices", "Region: Valleys", "OAR: Heart"),
            field_id,
            {"method": "taubin_non_shrinking", "iterations": 4, "lambda": 0.25, "mu": -0.27},
            None,
        )
        assert not bundle.failures
        assert set(bundle.anatomy_meshes) == {
            "Region: Whole GTV", "Region: Vertices", "Region: Valleys", "OAR: Heart",
        }
        assert bundle.overlay_target == "Region: Whole GTV"
        assert bundle.overlay_field_id == field_id
        assert bundle.overlay_mesh is not None and bundle.overlay_mesh.status == "PASS"
        assert bundle.overlay_mesh.display_surface is not None
        assert bundle.overlay_mesh.qc["scalar_sampling_coverage_percent"] == 100.0
        assert bundle.smoothing_enabled is True
        cutaway = _build_cad_scene_bundle(
            data, ("Region: Whole GTV", "OAR: Heart"), field_id,
            {"method": "taubin_non_shrinking", "iterations": 2, "lambda": 0.25, "mu": -0.27},
            None, display_mode="CUTAWAY", cut_axis="axial", cut_fraction=0.5,
            cut_azimuth_degrees=22.0, cut_elevation_degrees=-18.0,
            show_vertex_centres=True, show_graph=True,
        )
        assert cutaway.mode == "CUTAWAY" and cutaway.overlay_mesh is not None
        assert cutaway.overlay_mesh.qc["mesh_alignment_status"] == "GREEN"
        assert len(cutaway.vertex_centres_lps_mm) == 2 and len(cutaway.graph_edges_lps_mm) == 1
        threshold = float(np.percentile(bed[gtv], 75.0))
        isosurfaces = _build_cad_scene_bundle(
            data, ("Region: Whole GTV", "OAR: Heart"), field_id,
            {"method": "none", "iterations": 0, "lambda": 0.0, "mu": 0.0},
            None, display_mode="ISOSURFACE", isosurface_thresholds=(threshold,), show_contours=True,
        )
        assert isosurfaces.mode == "ISOSURFACE" and len(isosurfaces.special_meshes) == 1
        assert isosurfaces.show_contours is True
