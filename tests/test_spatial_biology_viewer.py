from __future__ import annotations

from pathlib import Path
import tempfile

import numpy as np

from ascend.layer3.spatial_biology import (
    BiologyColorScaleController, SpatialBiologyField, _nearest_mask_label,
    sample_surface_inward, validate_mesh_alignment, voxel_to_world_lps,
    world_to_voxel_lps,
)
from ascend.layer3.visualization import build_biological_mesh, sample_scalar_field_lps


def _geometry(shape: tuple[int, int, int] = (12, 13, 14)) -> dict:
    return {
        "origin": [10.0, 20.0, 30.0], "row_direction": [0.0, 1.0, 0.0],
        "column_direction": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, -1.0],
        "offsets": list(-2.0 * np.arange(shape[0])), "spacing": [1.5, 2.5], "shape": list(shape),
    }


def _position_field(shape: tuple[int, int, int], geometry: dict) -> np.ndarray:
    points = np.indices(shape).reshape(3, -1).T
    lps = voxel_to_world_lps(points, geometry)
    return (lps[:, 0] + 10.0 * lps[:, 1] + 100.0 * lps[:, 2]).reshape(shape).astype(np.float32)


def test_world_to_voxel_transform_and_roundtrip_have_no_axis_inversion() -> None:
    geometry = _geometry(); indices = np.asarray([[0.0, 0.0, 0.0], [3.25, 4.5, 7.75], [11.0, 12.0, 13.0]])
    lps = voxel_to_world_lps(indices, geometry)
    assert np.allclose(world_to_voxel_lps(lps, geometry), indices)
    field = _position_field((12, 13, 14), geometry)
    sampled, valid = sample_scalar_field_lps(field, lps, geometry)
    expected = lps[:, 0] + 10.0 * lps[:, 1] + 100.0 * lps[:, 2]
    assert valid.all() and np.allclose(sampled, expected, atol=2.0e-4)


def test_roi_label_nearest_sampling_is_categorical() -> None:
    mask = np.zeros((8, 8, 8), dtype=bool); mask[2:6, 2:6, 2:6] = True
    points = np.asarray([[3.49, 3.49, 3.49], [1.49, 3.0, 3.0], [20.0, 20.0, 20.0]])
    assert _nearest_mask_label(mask, points).tolist() == [True, False, False]


def test_surface_inward_sampling_avoids_cross_tissue_values() -> None:
    shape = (10, 10, 10); geometry = _geometry(shape)
    mask = np.zeros(shape, dtype=bool); mask[2:8, 2:8, 2:8] = True
    values = _position_field(shape, geometry); values[~mask] = 100000.0
    vertex_index = np.asarray([[4.0, 4.0, 7.5]])
    vertex = voxel_to_world_lps(vertex_index, geometry)
    normal = np.asarray([[0.0, 1.0, 0.0]])  # LPS direction of increasing x index.
    sampled = sample_surface_inward(values, mask, vertex, normal, geometry)
    assert sampled.valid.tolist() == [True]
    assert sampled.sampling_distance_mm[0] > 0.0
    assert sampled.values[0] < 100000.0
    sampled_lps = sampled.sampled_points_lps_mm[0]
    expected = sampled_lps[0] + 10.0 * sampled_lps[1] + 100.0 * sampled_lps[2]
    assert np.isclose(sampled.values[0], expected, atol=2.0e-3)


def test_invalid_vertex_handling_uses_nan_not_zero() -> None:
    shape = (10, 10, 10); geometry = _geometry(shape)
    mask = np.zeros(shape, dtype=bool); mask[2:8, 2:8, 2:8] = True
    values = np.ones(shape, dtype=np.float32)
    sampled = sample_surface_inward(values, mask, np.asarray([[9999.0, 9999.0, 9999.0]]), np.asarray([[1.0, 0.0, 0.0]]), geometry)
    assert sampled.valid.tolist() == [False]
    assert np.isnan(sampled.values[0])


def test_spatial_field_preserves_scalar_units_and_shared_range_policy() -> None:
    shape = (5, 5, 5); values = np.arange(np.prod(shape), dtype=np.float32).reshape(shape); mask = np.ones(shape, dtype=bool)
    contract = SpatialBiologyField(
        values, "S_BED", "Gy BED", (0.0, 0.0, 0.0), (1.0, 1.0, 1.0),
        ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)), shape,
        mask, np.ones(shape, dtype=np.uint8), {"GTV": mask}, "LQ", field_id="bed",
    )
    assert contract.units == "Gy BED" and contract.quantity == "S_BED"
    robust = BiologyColorScaleController("ROBUST").resolve(contract, roi_mask=mask)
    assert np.allclose(robust, np.percentile(values, (2, 98)))
    assert BiologyColorScaleController("FULL RANGE").resolve(contract) == (0.0, 124.0)


def test_alignment_gate_reports_coverage_and_blocks_misregistered_mesh() -> None:
    shape = (10, 10, 10); geometry = _geometry(shape); mask = np.zeros(shape, bool); mask[2:8, 2:8, 2:8] = True
    vertices = voxel_to_world_lps(np.asarray([[4.0, 4.0, 2.0], [4.0, 4.0, 7.0]]), geometry)
    normals = np.asarray([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    sampling = sample_surface_inward(np.ones(shape), mask, vertices, normals, geometry)
    report = validate_mesh_alignment(vertices, geometry, mask, sampling)
    assert report.status == "GREEN" and report.coverage_percent == 100.0
    far_vertices = vertices + 10000.0
    far_sampling = sample_surface_inward(np.ones(shape), mask, far_vertices, normals, geometry)
    blocked = validate_mesh_alignment(far_vertices, geometry, mask, far_sampling)
    assert blocked.status == "BLOCK" and blocked.error_code == "CAD_FRAME_ALIGNMENT_FAILED"


def test_synthetic_lattice_hotspots_remain_attached_to_lps_anatomy_and_cache() -> None:
    shape = (30, 30, 30); geometry = _geometry(shape); z, y, x = np.indices(shape)
    centre = np.asarray([15, 15, 15]); gtv = (z-centre[0])**2 + (y-centre[1])**2 + (x-centre[2])**2 <= 12**2
    field = np.full(shape, 20.0, dtype=np.float32)
    for hotspot in ((15, 15, 9), (15, 15, 21)):
        sphere = (z-hotspot[0])**2 + (y-hotspot[1])**2 + (x-hotspot[2])**2 <= 3**2
        field[sphere] = 100.0
    with tempfile.TemporaryDirectory() as folder:
        first = build_biological_mesh(Path(folder), "synthetic_lattice", gtv, field, geometry, "s_bed", "Gy BED", expected_tissue_mask=gtv)
        second = build_biological_mesh(Path(folder), "synthetic_lattice", gtv, field, geometry, "s_bed", "Gy BED", expected_tissue_mask=gtv)
        assert first.status == second.status == "PASS"
        assert first.qc["mesh_alignment_status"] == "GREEN"
        assert second.provenance["display_cache_hit"] is True
        assert np.array_equal(first.display_surface.vertices_lps_mm, second.display_surface.vertices_lps_mm)
        assert np.array_equal(first.display_surface.scalar_values, second.display_surface.scalar_values, equal_nan=True)
