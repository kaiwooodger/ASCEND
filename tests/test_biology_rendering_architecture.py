from __future__ import annotations

import numpy as np
import pyvista as pv
import pytest

from ascend.visualization.biology.colour_scale import BiologicalColourScaleManager, endpoint_opacity
from ascend.visualization.biology.controller import BiologicalRenderController
from ascend.visualization.biology.handoff import volume_geometry_from_ascend
from ascend.visualization.biology.mesh_sampler import sample_biological_volume_on_surface
from ascend.visualization.biology.models import (
    BiologicalEndpoint, BiologicalRegion, BiologicalRenderMode,
    BiologicalVolume, VolumeGeometry,
)
from ascend.visualization.biology.slice_renderer import biological_slice
from ascend.visualization.biology.statistics import probe_volumes, region_statistics
from ascend.visualization.biology.validation import BiologicalRenderError, validate_mesh_overlap
from ascend.visualization.biology.volume_adapter import biological_volume_to_pyvista, masked_pyvista_volume, sample_patient_points


def _geometry(shape: tuple[int, int, int] = (9, 10, 11), spacing=(1.7, 2.1, 2.8)) -> VolumeGeometry:
    angle = np.deg2rad(30.0)
    direction = np.asarray([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, -1.0],
    ])
    return VolumeGeometry(shape, np.asarray([12.0, -7.0, 44.0]), np.asarray(spacing), direction)


def _field(geometry: VolumeGeometry) -> np.ndarray:
    indices = np.indices(geometry.shape).reshape(3, -1).T
    patient = geometry.voxel_to_patient(indices)
    return (patient[:, 0] + 2.0 * patient[:, 1] + 3.0 * patient[:, 2]).reshape(geometry.shape)


def _volume(
    endpoint: BiologicalEndpoint = BiologicalEndpoint.SBED,
    shape: tuple[int, int, int] = (9, 10, 11),
) -> BiologicalVolume:
    geometry = _geometry(shape)
    values = _field(geometry)
    tissue = np.ones(shape, dtype=bool)
    vertex = np.zeros(shape, dtype=bool); vertex[:, :, : shape[2] // 2] = True
    valley = tissue & ~vertex
    units = "fraction" if endpoint is BiologicalEndpoint.MLQ_SF else "dimensionless effect" if endpoint is BiologicalEndpoint.MLQ_EFFECT else "Gy"
    if endpoint is BiologicalEndpoint.MLQ_SF:
        values = np.exp(-np.abs(values - values.min()) / 100.0)
    elif endpoint is BiologicalEndpoint.MLQ_EFFECT:
        values = np.abs(values - values.min()) / 100.0
    return BiologicalVolume(values, endpoint, geometry, units, tissue_mask=tissue, vertex_mask=vertex, valley_mask=valley)


def test_geometry_roundtrip_is_explicit_rotated_and_anisotropic() -> None:
    geometry = _geometry()
    indices = np.asarray([[0.0, 0.0, 0.0], [2.25, 4.5, 7.75], [8.0, 9.0, 10.0]])
    assert np.allclose(geometry.patient_to_voxel(geometry.voxel_to_patient(indices)), indices, atol=1.0e-12)
    assert geometry.dimensions_xyz == (11, 10, 9)
    assert not np.allclose(geometry.spacing_mm, np.ones(3))


def test_ascend_geometry_handoff_matches_established_lps_transform() -> None:
    shape = (5, 6, 7)
    stored = {
        "origin": [10.0, 20.0, 30.0], "row_direction": [0.0, 1.0, 0.0],
        "column_direction": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, -1.0],
        "offsets": list(-2.0 * np.arange(shape[0])), "spacing": [1.5, 2.5], "shape": list(shape),
    }
    geometry = volume_geometry_from_ascend(stored, shape)
    index = np.asarray([3.0, 4.0, 5.0])
    expected = np.asarray([10.0, 20.0, 30.0]) + 6.0 * np.asarray([0.0, 0.0, 1.0])
    expected += 5.0 * 2.5 * np.asarray([0.0, 1.0, 0.0]) + 4.0 * 1.5 * np.asarray([1.0, 0.0, 0.0])
    assert np.allclose(geometry.voxel_to_patient(index), expected)


def test_biological_volume_is_defensively_immutable_and_rejects_bad_masks() -> None:
    source = np.ones((3, 4, 5)); geometry = VolumeGeometry((3, 4, 5), np.zeros(3), np.ones(3), np.eye(3))
    volume = BiologicalVolume(source, BiologicalEndpoint.SBED, geometry, "Gy", tissue_mask=np.ones(source.shape, bool))
    source[:] = 99.0
    assert np.all(volume.values == 1.0)
    with pytest.raises(ValueError, match="read-only"):
        volume.values[0, 0, 0] = 2.0
    with pytest.raises(ValueError, match="BIOLOGICAL_MASK_SHAPE_MISMATCH"):
        BiologicalVolume(np.ones((3, 4, 5)), BiologicalEndpoint.SBED, geometry, "Gy", tissue_mask=np.ones((3, 4, 4)))


def test_numpy_vtk_ordering_and_patient_sampling_preserve_all_axis_gradients() -> None:
    volume = _volume()
    grid = biological_volume_to_pyvista(volume)
    assert grid.dimensions == volume.geometry.dimensions_xyz
    assert np.allclose(grid.direction_matrix, volume.geometry.direction)
    assert np.array_equal(np.asarray(grid[volume.scalar_name]).reshape(volume.values.shape, order="C"), volume.values)
    indices = np.asarray([[0, 0, 0], [2, 4, 6], [8, 9, 10]], dtype=float)
    points = volume.geometry.voxel_to_patient(indices)
    sampled, valid = sample_patient_points(volume, points)
    expected = volume.values[tuple(indices.astype(int).T)]
    assert valid.all() and np.allclose(sampled, expected, atol=1.0e-8)


def test_masked_volume_keeps_invalid_regions_nan_not_zero() -> None:
    volume = _volume(); mask = np.zeros(volume.values.shape, bool); mask[2:5, 3:7, 4:8] = True
    grid = masked_pyvista_volume(volume, mask)
    restored = np.asarray(grid[volume.scalar_name]).reshape(volume.values.shape, order="C")
    assert np.isnan(restored[~mask]).all()
    assert np.array_equal(restored[mask], volume.values[mask])


def test_pyvista_surface_sampling_matches_analytic_patient_field() -> None:
    volume = _volume()
    indices = np.asarray([[1.5, 1.5, 1.5], [2.25, 4.5, 6.75], [7.0, 8.0, 9.0]])
    points = volume.geometry.voxel_to_patient(indices)
    surface = pv.PolyData(points)
    result = sample_biological_volume_on_surface(volume, surface)
    expected = points[:, 0] + 2.0 * points[:, 1] + 3.0 * points[:, 2]
    assert result.valid_fraction == 1.0
    assert np.allclose(result.values, expected, atol=1.0e-8)


def test_mesh_geometry_mismatch_blocks_without_auto_registration() -> None:
    volume = _volume(); surface = pv.Sphere(center=(5000.0, 5000.0, 5000.0))
    with pytest.raises(BiologicalRenderError) as caught:
        validate_mesh_overlap(volume, surface.points)
    assert caught.value.code == "BIOLOGICAL_RENDER_GEOMETRY_MISMATCH"
    assert "volume_bounds_mm" in caught.value.diagnostics
    assert "mesh_centroid_mm" in caught.value.diagnostics


def test_slice_probe_and_statistics_read_authoritative_arrays() -> None:
    sbed = _volume(BiologicalEndpoint.SBED); effect = _volume(BiologicalEndpoint.MLQ_EFFECT)
    section = biological_slice(sbed, "coronal", 4, sbed.vertex_mask)
    assert np.array_equal(section.values[section.valid_mask], sbed.values[:, 4, :][section.valid_mask])
    index = np.asarray([2, 3, 4]); patient = sbed.geometry.voxel_to_patient(index)
    probe = probe_volumes({sbed.endpoint: sbed, effect.endpoint: effect}, patient)
    assert probe.voxel_zyx == (2, 3, 4)
    assert probe.values[BiologicalEndpoint.SBED] == sbed.values[2, 3, 4]
    stats = region_statistics(sbed, sbed.tissue_mask)
    assert stats.valid_voxels == int(np.prod(sbed.values.shape))
    assert stats.vertex_mean is not None and stats.valley_mean is not None


def test_mlq_opacity_semantics_are_inverted_for_raw_survival() -> None:
    sf = np.asarray(endpoint_opacity(BiologicalEndpoint.MLQ_SF))
    effect = np.asarray(endpoint_opacity(BiologicalEndpoint.MLQ_EFFECT))
    assert sf[1] > sf[-1]
    assert effect[1] < effect[-1]


def test_controller_switches_modes_without_rebuilding_authoritative_volume() -> None:
    sbed = _volume(BiologicalEndpoint.SBED); eqd2 = BiologicalVolume(
        sbed.values / 1.2, BiologicalEndpoint.SEQD2, sbed.geometry, "Gy",
        tissue_mask=sbed.tissue_mask, vertex_mask=sbed.vertex_mask, valley_mask=sbed.valley_mask,
    )
    controller = BiologicalRenderController(); controller.load_volumes({sbed.endpoint: sbed, eqd2.endpoint: eqd2})
    original_values = sbed.values
    controller.set_endpoint(BiologicalEndpoint.SBED); controller.set_region(BiologicalRegion.WHOLE_TUMOUR)
    controller.set_render_mode(BiologicalRenderMode.VOLUME)
    plotter = pv.Plotter(off_screen=True, window_size=(160, 160))
    try:
        volume_scene = controller.render(plotter)
        assert "biological_volume" in volume_scene.actors
        controller.set_isosurfaces(tuple(np.percentile(sbed.values, (50, 75, 90))))
        controller.set_render_mode(BiologicalRenderMode.COMBINED)
        combined = controller.render(plotter)
        assert {"biological_volume", "isosurfaces"} <= set(combined.actors)
        controller.set_endpoint(BiologicalEndpoint.SEQD2)
        assert controller.volume is eqd2
        assert sbed.values is original_values and np.array_equal(sbed.values, original_values)
    finally:
        plotter.close()


def test_vtk_slice_plane_uses_patient_space_origin_and_normal() -> None:
    volume = _volume(); controller = BiologicalRenderController(); controller.load_volume(volume)
    controller.set_region(BiologicalRegion.WHOLE_TUMOUR); controller.set_render_mode(BiologicalRenderMode.SLICE)
    origin = volume.geometry.voxel_to_patient(np.asarray([4.0, 4.5, 5.0]))
    normal = volume.geometry.direction[:, 0]
    plotter = pv.Plotter(off_screen=True, window_size=(120, 120))
    try:
        scene = controller.render(plotter, slice_origin_mm=origin, slice_normal=normal)
        points = np.asarray(scene.datasets["orthogonal_slices"].points)
        assert len(points) > 0
        assert np.max(np.abs((points - origin) @ normal)) < 1.0e-5
    finally:
        plotter.close()


def test_spherical_internal_hotspot_is_visible_as_isosurface_not_boundary_map() -> None:
    shape = (25, 25, 25); geometry = VolumeGeometry(shape, np.zeros(3), np.ones(3), np.eye(3))
    z, y, x = np.indices(shape); radius = np.sqrt((z - 12) ** 2 + (y - 12) ** 2 + (x - 12) ** 2)
    values = np.where(radius < 4.0, 100.0, 10.0)
    tissue = radius <= 10.0
    volume = BiologicalVolume(values, BiologicalEndpoint.SBED, geometry, "Gy", tissue_mask=tissue)
    boundary = pv.Sphere(radius=10.0, center=(12.0, 12.0, 12.0), theta_resolution=24, phi_resolution=24)
    surface = sample_biological_volume_on_surface(volume, boundary)
    assert np.isclose(np.nanmax(surface.values), 10.0)
    controller = BiologicalRenderController(); controller.load_volume(volume)
    controller.set_region(BiologicalRegion.WHOLE_TUMOUR); controller.set_render_mode(BiologicalRenderMode.ISOSURFACE); controller.set_isosurfaces((50.0,))
    plotter = pv.Plotter(off_screen=True, window_size=(160, 160))
    try:
        scene = controller.render(plotter)
        assert scene.datasets["isosurfaces"].n_points > 0
        bounds = scene.datasets["isosurfaces"].bounds
        assert np.allclose([(bounds.x_min + bounds.x_max) / 2, (bounds.y_min + bounds.y_max) / 2, (bounds.z_min + bounds.z_max) / 2], [12, 12, 12], atol=0.25)
    finally:
        plotter.close()


def test_colour_scale_remains_locked_across_mask_changes() -> None:
    volume = _volume(); manager = BiologicalColourScaleManager()
    locked = manager.resolve(volume, mask=volume.vertex_mask, absolute=(0.0, 200.0), lock=True)
    changed = manager.resolve(volume, mask=volume.valley_mask)
    assert changed is locked
