from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ascend.gui.dose_gradient_panel import DoseGradientPanel
from ascend.gui.saddle_graph_panel import SaddleGraphPanel
from ascend.layer2.graph.dose_gradient import analyse_icru91_dose_gradient
from ascend.layer2.graph.exports import export_layer22_extensions
from ascend.layer2.graph.saddle_analysis import SaddleConfiguration, analyse_saddle_graph
from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.graph.spatial_sampling import GridGeometry

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


def _gradient(dose: np.ndarray, *, prescription: float | None = 20.0, targets: int = 1) -> dict:
    return analyse_icru91_dose_gradient(
        case_id="ANALYTIC", dose_gy=dose, geometry=_geometry(dose.shape),
        prescription_dose_gy=prescription, prescription_source="protocol_configuration",
        high_dose_target_volume_cc=0.5 * targets, number_of_targets=targets,
        target_volumes_cc=[0.5] * targets,
    )


def test_icru91_paddick_gi_uses_half_and_full_prescription_isodose_volumes() -> None:
    dose = np.zeros((5, 5, 5), dtype=float)
    dose[1:4, 1:4, 1:4] = 10.0
    dose[2, 2, 2] = 20.0
    result = _gradient(dose)
    assert np.isclose(result["piv_half_cc"], 0.027)
    assert np.isclose(result["piv_cc"], 0.001)
    assert np.isclose(result["gradient_index"], 27.0)
    assert result["formula"] == "GI = PIV_half / PIV = V(D >= 0.5 * prescription dose) / V(D >= prescription dose)"
    assert len(result["isodose_volume_profile"]) == 21


def test_icru91_gradient_missing_prescription_and_empty_piv_are_explicit() -> None:
    dose = np.zeros((5, 5, 5), dtype=float)
    missing = _gradient(dose, prescription=None)
    assert missing["calculation_status"] == "NOT_CALCULATED_MISSING_PRESCRIPTION"
    assert missing["gradient_index"] is None
    empty = _gradient(dose)
    assert empty["calculation_status"] == "NOT_CALCULATED_EMPTY_PIV"
    assert empty["piv_cc"] == 0.0 and empty["gradient_index"] is None


def test_icru91_gradient_flags_grid_clipping_and_multiple_target_context() -> None:
    dose = np.zeros((5, 5, 5), dtype=float)
    dose[:, 2, 2] = 10.0
    dose[2, 2, 2] = 20.0
    result = _gradient(dose, targets=3)
    assert "HALF_PRESCRIPTION_ISODOSE_TOUCHES_DOSE_GRID_BOUNDARY" in result["warnings"]
    assert "MULTIPLE_TARGETS_GI_INCLUDES_COMBINED_LOW_DOSE_WASH" in result["warnings"]
    assert "SUB_CC_TARGET_CONTEXT_INTERPRET_GI_CAUTIOUSLY" in result["warnings"]


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
        assert extensions["dose_gradient"]["schema_version"] == "1.0"
        assert extensions["dose_gradient"]["gradient_index"] == 1.0
        assert extensions["saddle_graph"]["schema_version"] == "1.0"
        outputs = export_layer22_extensions(result, Path(folder) / "exports")
        assert {path.name for path in outputs} >= {
            "layer2_2_icru91_dose_gradient.json", "layer2_2_icru91_isodose_volume_profile.csv",
            "layer2_2_saddle_graph.json", "layer2_2_saddle_edges.csv", "layer2_2_saddle_paths.csv",
        }
        exported = json.loads((Path(folder) / "exports" / "layer2_2_saddle_graph.json").read_text())
        assert exported["edges"][0]["saddle_pvdr"] == extensions["saddle_graph"]["edges"][0]["saddle_pvdr"]
        with (Path(folder) / "exports" / "layer2_2_icru91_isodose_volume_profile.csv").open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        assert float(row["relative_prescription_percent"]) == 25.0


def test_headless_gradient_and_saddle_panels_consume_stored_records_only() -> None:
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory() as folder:
        result = Layer22Service().run(synthetic_case(Path(folder), explicit_vertices=True)).result
        extensions = result["layer2_2_extensions"]
        gradient_panel = DoseGradientPanel(); gradient_panel.set_result(extensions["dose_gradient"])
        saddle_panel = SaddleGraphPanel(); saddle_panel.set_result(extensions["saddle_graph"])
        assert gradient_panel.table.rowCount() == 21
        assert "1" in gradient_panel.metric_cards["gradient_index"].text()
        assert saddle_panel.table.rowCount() == len(result["edges"])
        assert saddle_panel.mode.count() == 5
        assert "Saddle dose" in saddle_panel.evidence.toPlainText()
        gradient_panel.close(); saddle_panel.close(); application.processEvents()
