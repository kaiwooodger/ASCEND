from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ascend.gui.saddle_graph_panel import SaddleGraphPanel
from ascend.gui.vertex_profile_panel import VertexProfilePanel
from ascend.layer2.graph.exports import export_layer22_extensions
from ascend.layer2.graph.saddle_analysis import SaddleConfiguration, analyse_saddle_graph
from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.graph.spatial_sampling import GridGeometry
from ascend.layer2.graph.vertex_profiles import VertexProfileConfiguration, analyse_vertex_profiles

from .helpers import synthetic_case


def _geometry(
    shape: tuple[int, int, int],
    *,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    spacing: tuple[float, float, float] = (1.0, 1.0, 1.0),
    row: tuple[float, float, float] = (1.0, 0.0, 0.0),
    column: tuple[float, float, float] = (0.0, 1.0, 0.0),
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> GridGeometry:
    return GridGeometry.from_mapping({
        "origin": origin, "row_dir": row, "col_dir": column, "normal": normal,
        "offsets": (np.arange(shape[0]) * spacing[0]).tolist(), "spacing": [spacing[1], spacing[2]], "shape": shape,
    })


def _gaussian_profile_case(
    *,
    shape: tuple[int, int, int] = (51, 51, 51),
    spacing: tuple[float, float, float] = (1.0, 1.0, 1.0),
    background: float = 2.0,
    amplitude: float = 18.0,
    sigma_mm: float = 3.0,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    row: tuple[float, float, float] = (1.0, 0.0, 0.0),
    column: tuple[float, float, float] = (0.0, 1.0, 0.0),
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> tuple[np.ndarray, GridGeometry, np.ndarray, np.ndarray]:
    geometry = _geometry(shape, origin=origin, spacing=spacing, row=row, column=column, normal=normal)
    centre_index = np.asarray(shape) // 2
    centre = geometry.points_lps_mm(centre_index.reshape(1, 3))[0]
    indices = np.indices(shape).reshape(3, -1).T
    radii = np.linalg.norm(geometry.points_lps_mm(indices) - centre, axis=1).reshape(shape)
    dose = background + amplitude * np.exp(-(radii**2) / (2.0 * sigma_mm**2))
    vertex = np.zeros(shape, dtype=bool); vertex[tuple(centre_index)] = True
    return dose, geometry, np.ones(shape, dtype=bool), vertex


def _profile(dose: np.ndarray, geometry: GridGeometry, gtv: np.ndarray, vertex: np.ndarray, nearest: float = 40.0) -> dict:
    return analyse_vertex_profiles(
        case_id="ANALYTIC", dose_gy=dose, geometry=geometry, gtv_mask=gtv,
        vertex_ids=["V01"], vertex_masks=[vertex], nearest_neighbour_distances_mm=[nearest],
        configuration=VertexProfileConfiguration(minimum_shell_voxels=1),
    )["vertices"][0]


def test_radial_gaussian_crossings_diameter_penumbra_and_gradient_match_analytic_solution() -> None:
    sigma, amplitude = 3.0, 18.0
    dose, geometry, gtv, vertex = _gaussian_profile_case(sigma_mm=sigma, amplitude=amplitude)
    result = _profile(dose, geometry, gtv, vertex)
    expected = {threshold: sigma * np.sqrt(-2.0 * np.log(threshold)) for threshold in (0.8, 0.5, 0.2)}
    assert result["profile_status"] == "VALID"
    assert abs(result["r80_mm"] - expected[0.8]) < 0.5
    assert abs(result["r50_mm"] - expected[0.5]) < 0.5
    assert abs(result["r20_mm"] - expected[0.2]) < 0.5
    assert abs(result["dosimetric_diameter_mm"] - 2.0 * expected[0.5]) < 0.7
    assert abs(result["penumbra_80_20_mm"] - (expected[0.2] - expected[0.8])) < 0.7
    expected_gradient = amplitude / sigma * np.exp(-0.5)
    assert abs(result["maximum_gradient_gy_per_mm"] - expected_gradient) < 0.8
    assert abs(result["maximum_gradient_radius_mm"] - sigma) <= 0.5


def test_corrected_profile_is_invariant_to_uniform_background_addition() -> None:
    dose, geometry, gtv, vertex = _gaussian_profile_case()
    first = _profile(dose, geometry, gtv, vertex)
    second = _profile(dose + 11.75, geometry, gtv, vertex)
    for key in ("r80_mm", "r50_mm", "r20_mm", "dosimetric_diameter_mm", "penumbra_80_20_mm"):
        assert np.isclose(first[key], second[key], atol=1.0e-10)


def test_anisotropic_translation_and_rotation_use_physical_distances() -> None:
    base = _gaussian_profile_case(spacing=(2.0, 1.5, 1.0), shape=(27, 35, 51), origin=(12.0, -9.0, 41.0))
    rotated = _gaussian_profile_case(
        spacing=(2.0, 1.5, 1.0), shape=(27, 35, 51), origin=(112.0, 29.0, -17.0),
        row=(0.0, 1.0, 0.0), column=(-1.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0),
    )
    first = _profile(*base)
    second = _profile(*rotated)
    analytic_r50 = 3.0 * np.sqrt(2.0 * np.log(2.0))
    assert abs(first["r50_mm"] - analytic_r50) <= 2.0
    assert np.isclose(first["r50_mm"], second["r50_mm"], atol=1.0e-10)
    assert np.isclose(first["penumbra_80_20_mm"], second["penumbra_80_20_mm"], atol=1.0e-10)


def test_profile_failures_and_warnings_are_explicit_and_deterministic() -> None:
    dose, geometry, gtv, vertex = _gaussian_profile_case()
    uniform = _profile(np.full_like(dose, 5.0), geometry, gtv, vertex)
    assert uniform["profile_status"] == "INSUFFICIENT_VERTEX_CONTRAST"
    truncated = _profile(dose, geometry, gtv, vertex, nearest=4.0)
    assert truncated["r20_mm"] is None and "R20_CROSSING_NOT_FOUND" in truncated["warnings"]
    isolated = analyse_vertex_profiles(
        case_id="ANALYTIC", dose_gy=dose, geometry=geometry, gtv_mask=gtv,
        vertex_ids=["V01"], vertex_masks=[vertex], nearest_neighbour_distances_mm=[None],
        configuration=VertexProfileConfiguration(isolated_margin_mm=12.0, minimum_shell_voxels=1),
    )["vertices"][0]
    assert "ISOLATED_VERTEX_FALLBACK_RADIUS" in isolated["warnings"]
    centre = np.asarray(dose.shape) // 2
    radii = np.linalg.norm(np.indices(dose.shape).reshape(3, -1).T - centre, axis=1).reshape(dose.shape)
    nonmonotonic_dose = dose + 7.0 * np.exp(-((radii - 7.0) ** 2) / 0.3)
    nonmonotonic = _profile(nonmonotonic_dose, geometry, gtv, vertex)
    assert "NON_MONOTONIC_PROFILE" in nonmonotonic["warnings"]
    assert _profile(nonmonotonic_dose, geometry, gtv, vertex) == nonmonotonic


def test_profile_near_dose_grid_boundary_is_explicitly_flagged() -> None:
    shape = (31, 31, 31); geometry = _geometry(shape)
    z, y, x = np.indices(shape); centre = np.asarray([15, 15, 2])
    dose = 2.0 + 18.0 * np.exp(-((z - centre[0]) ** 2 + (y - centre[1]) ** 2 + (x - centre[2]) ** 2) / 18.0)
    vertex = np.zeros(shape, dtype=bool); vertex[tuple(centre)] = True
    result = _profile(dose, geometry, np.ones(shape, dtype=bool), vertex, nearest=40.0)
    assert "DOSE_GRID_BOUNDARY_TRUNCATION" in result["warnings"]


def _saddle_fixture(
    *,
    first_amplitude: float = 18.0,
    second_amplitude: float = 18.0,
    background: float = 2.0,
) -> tuple[np.ndarray, GridGeometry, np.ndarray, list[np.ndarray], list[dict], list[list[float]], float]:
    shape = (41, 41, 41); geometry = _geometry(shape)
    z, y, x = np.indices(shape); sigma, a = 2.5, 8.0
    dose = (
        background + first_amplitude * np.exp(-((x - 12) ** 2 + (y - 20) ** 2 + (z - 20) ** 2) / (2.0 * sigma**2))
        + second_amplitude * np.exp(-((x - 28) ** 2 + (y - 20) ** 2 + (z - 20) ** 2) / (2.0 * sigma**2))
    )
    first = np.zeros(shape, dtype=bool); first[20, 20, 12] = True
    second = np.zeros(shape, dtype=bool); second[20, 20, 28] = True
    midpoint_mask = (x - 20) ** 2 + (y - 20) ** 2 + (z - 20) ** 2 <= 9
    midpoint_d50 = float(np.median(dose[midpoint_mask]))
    endpoints = [float(dose[first][0]), float(dose[second][0])]; peak = float(np.mean(endpoints))
    edge = {
        "edge_id": 1, "nodes": ["V01", "V02"], "endpoint_indices": [0, 1], "valid": True,
        "endpoint_peak_d50_gy": endpoints, "edge_peak_d50_gy": peak, "length_mm": 16.0,
        "midpoint_lps_mm": [20.0, 20.0, 20.0], "edge_local_valley_d50_gy": midpoint_d50,
        "ipvdr": peak / midpoint_d50,
    }
    expected = background + 2.0 * 18.0 * np.exp(-(a**2) / (2.0 * sigma**2))
    return dose, geometry, np.ones(shape, dtype=bool), [first, second], [edge], [[12.0, 20.0, 20.0], [28.0, 20.0, 20.0]], expected


def _saddle(fixture: tuple, configuration: SaddleConfiguration | None = None) -> dict:
    dose, geometry, gtv, masks, edges, centroids, _expected = fixture
    return analyse_saddle_graph(
        case_id="ANALYTIC", dose_gy=dose, geometry=geometry, gtv_mask=gtv, vertex_masks=masks,
        locked_edges=edges, node_centroids_lps_mm=centroids,
        configuration=configuration or SaddleConfiguration(sensitivity_corridor_radii_mm=(2.0, 3.0, 4.0), minimum_saddle_voxels=1),
    )


def test_symmetric_gaussian_saddle_coordinate_bottleneck_and_midpoint_agreement() -> None:
    fixture = _saddle_fixture(); result = _saddle(fixture); edge = result["edges"][0]
    assert edge["edge_status"] == "VALID"
    assert np.allclose(edge["saddle_xyz_mm"], [20.0, 20.0, 20.0], atol=1.0)
    assert abs(edge["raw_saddle_bottleneck_gy"] - fixture[-1]) < 1.0e-10
    assert edge["saddle_path_xyz_mm"]
    assert np.isclose(edge["saddle_local_d50_gy"], edge["midpoint_d50_gy"])
    assert np.isclose(edge["saddle_pvdr"], edge["midpoint_pvdr"])


def test_asymmetry_disconnection_uniformity_zero_dose_and_sensitivity_statuses() -> None:
    asymmetric = _saddle(_saddle_fixture(first_amplitude=30.0, second_amplitude=6.0))["edges"][0]
    assert asymmetric["edge_status"] == "VALID" and asymmetric["saddle_to_midpoint_mm"] > 0
    fixture = list(_saddle_fixture()); fixture[2] = fixture[2].copy(); fixture[2][:, :, 19:22] = False
    disconnected = _saddle(tuple(fixture))["edges"][0]
    assert disconnected["edge_status"] in {"NO_SADDLE_PATH", "DISCONNECTED_CORRIDOR"}
    uniform_fixture = list(_saddle_fixture()); uniform_fixture[0] = np.full_like(uniform_fixture[0], 5.0)
    uniform = _saddle(tuple(uniform_fixture))["edges"][0]
    assert "DEGENERATE_UNIFORM_DOSE_SADDLE" in uniform["warnings"]
    zero_fixture = list(_saddle_fixture()); zero_fixture[0] = np.zeros_like(zero_fixture[0])
    zero = _saddle(tuple(zero_fixture))["edges"][0]
    assert zero["edge_status"] == "NONPOSITIVE_SADDLE_DOSE" and zero["saddle_pvdr"] is None
    assert _saddle(_saddle_fixture()) == _saddle(_saddle_fixture())


def test_saddle_translation_and_rotation_preserve_native_grid_result() -> None:
    original_fixture = list(_saddle_fixture())
    original = _saddle(tuple(original_fixture))["edges"][0]
    rotated_geometry = _geometry(
        original_fixture[0].shape, origin=(101.0, -37.0, 22.0),
        row=(0.0, 1.0, 0.0), column=(-1.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0),
    )
    endpoint_indices = np.asarray([[20, 20, 12], [20, 20, 28]])
    centroids = rotated_geometry.points_lps_mm(endpoint_indices).tolist()
    midpoint = np.mean(np.asarray(centroids), axis=0).tolist()
    original_fixture[1] = rotated_geometry
    original_fixture[4] = [{**original_fixture[4][0], "midpoint_lps_mm": midpoint}]
    original_fixture[5] = centroids
    transformed = _saddle(tuple(original_fixture))["edges"][0]
    for key in ("raw_saddle_bottleneck_gy", "saddle_local_d50_gy", "saddle_pvdr", "saddle_to_midpoint_mm", "saddle_path_length_mm"):
        assert np.isclose(original[key], transformed[key], atol=1.0e-10)
    assert np.allclose(transformed["saddle_xyz_mm"], rotated_geometry.points_lps_mm(np.asarray([[20, 20, 20]]))[0])


def test_third_vertex_is_excluded_and_invalid_edges_never_enter_aggregate_statistics() -> None:
    fixture = list(_saddle_fixture())
    third = np.zeros_like(fixture[3][0]); third[20, 25, 20] = True
    fixture[3] = [*fixture[3], third]; fixture[5] = [*fixture[5], [20.0, 25.0, 20.0]]
    result = _saddle(tuple(fixture))
    assert result["summary"]["valid_edges"] == 1
    invalid_edge = {**fixture[4][0], "edge_id": 2, "valid": False}
    fixture[4] = [fixture[4][0], invalid_edge]
    result = _saddle(tuple(fixture))
    assert result["summary"]["valid_edges"] == 1 and result["summary"]["excluded_edges"] == 1
    assert result["summary"]["exclusion_counts"] == {"INVALID_ENDPOINT": 1}


def test_unrelated_vertex_barrier_has_specific_exclusion_status() -> None:
    fixture = list(_saddle_fixture())
    z, y, x = np.indices(fixture[0].shape)
    barrier = (x == 20) & ((y - 20) ** 2 + (z - 20) ** 2 <= 25)
    fixture[3] = [*fixture[3], barrier]
    fixture[5] = [*fixture[5], [20.0, 20.0, 20.0]]
    edge = _saddle(tuple(fixture))["edges"][0]
    assert edge["edge_status"] == "UNRELATED_VERTEX_INTERSECTION"


def test_service_adds_versioned_extensions_without_changing_locked_midpoint_values_and_exports_reconstruct() -> None:
    with TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder), explicit_vertices=True)
        result = Layer22Service().run(case).result
        assert all(edge["ipvdr"] == 4.0 and edge["edge_local_valley_d50_gy"] == 5.0 for edge in result["edges"])
        extensions = result["layer2_2_extensions"]
        assert extensions["vertex_profiles"]["schema_version"] == "1.0"
        assert extensions["saddle_graph"]["schema_version"] == "1.0"
        outputs = export_layer22_extensions(result, Path(folder) / "exports")
        assert {path.name for path in outputs} >= {
            "layer2_2_vertex_profiles.json", "layer2_2_vertex_profiles.csv", "layer2_2_vertex_radial_profiles.csv",
            "layer2_2_saddle_graph.json", "layer2_2_saddle_edges.csv", "layer2_2_saddle_paths.csv",
        }
        exported = json.loads((Path(folder) / "exports" / "layer2_2_saddle_graph.json").read_text())
        assert exported["edges"][0]["saddle_pvdr"] == extensions["saddle_graph"]["edges"][0]["saddle_pvdr"]
        with (Path(folder) / "exports" / "layer2_2_vertex_profiles.csv").open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        assert float(row["dosimetric_diameter_mm"]) == extensions["vertex_profiles"]["vertices"][0]["dosimetric_diameter_mm"]


def test_headless_profile_and_saddle_panels_consume_stored_records_only() -> None:
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory() as folder:
        result = Layer22Service().run(synthetic_case(Path(folder), explicit_vertices=True)).result
        extensions = result["layer2_2_extensions"]
        profile_panel = VertexProfilePanel(); profile_panel.set_result(extensions["vertex_profiles"])
        saddle_panel = SaddleGraphPanel(); saddle_panel.set_result(extensions["saddle_graph"])
        assert profile_panel.table.rowCount() == 4
        assert "mm" in profile_panel.metric_cards["dosimetric_diameter_mm"].text()
        assert saddle_panel.table.rowCount() == len(result["edges"])
        assert saddle_panel.mode.count() == 5
        assert "Saddle dose" in saddle_panel.evidence.toPlainText()
        profile_panel.close(); saddle_panel.close(); application.processEvents()
