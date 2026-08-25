"""Portable path and offscreen-rendering smoke tests for the hosted-runner matrix."""

from __future__ import annotations

import stat
from pathlib import Path

import numpy as np
import pyvista as pv

from ascend.dicom.discovery import discover_case

from .test_dicom_and_geometry import write_dose


def test_dicom_discovery_accepts_native_paths_with_spaces_unicode_length_and_case(tmp_path: Path) -> None:
    nested = tmp_path / "ASCEND DICOM 数据" / ("long-directory-name-" * 6) / "Dose Files"
    nested.mkdir(parents=True)
    first = nested / "rTdOsE Mixed Case 01.DcM"
    duplicate_folder = tmp_path / "ASCEND DICOM 数据" / "separate RTDOSE folder"
    duplicate_folder.mkdir(parents=True)
    second = duplicate_folder / first.name
    write_dose(first)
    second.write_bytes(first.read_bytes())

    original_mode = nested.stat().st_mode
    try:
        nested.chmod(stat.S_IREAD | stat.S_IEXEC)
        inventory = discover_case(tmp_path)
    finally:
        nested.chmod(original_mode)

    assert inventory["counts"]["RTDOSE"] == 2
    discovered = [Path(item["path"]) for item in inventory["objects"]["RTDOSE"]]
    assert len(set(discovered)) == 2
    assert {path.name for path in discovered} == {first.name}


def test_synthetic_stl_biology_mapping_and_offscreen_frame(tmp_path: Path) -> None:
    source = pv.Sphere(radius=10.0, theta_resolution=12, phi_resolution=12)
    stl_path = tmp_path / "synthetic tumour geometry.stl"
    source.save(stl_path)
    mesh = pv.read(stl_path)

    assert mesh.n_points == 122
    assert mesh.n_cells == 240
    assert np.isfinite(np.asarray(mesh.bounds)).all()

    points = np.asarray(mesh.points, dtype=np.float64)
    sbed = 30.0 + points[:, 2]
    seqd2 = sbed / 1.2
    mesh.point_data["sBED_Gy"] = sbed
    mesh.point_data["sEQD2_Gy"] = seqd2
    assert np.isfinite(mesh.point_data["sBED_Gy"]).all()
    assert np.isfinite(mesh.point_data["sEQD2_Gy"]).all()

    image_path = tmp_path / "ascend-offscreen-smoke.png"
    plotter = pv.Plotter(off_screen=True, window_size=(320, 240))
    try:
        plotter.add_mesh(mesh, scalars="sBED_Gy", cmap="viridis")
        plotter.camera_position = "iso"
        plotter.show(auto_close=False)
        image = plotter.screenshot(image_path, return_img=True)
        assert plotter.renderer is not None
        assert plotter.camera is not None
        assert image.shape == (240, 320, 3)
        assert np.ptp(image) > 0
        assert image_path.is_file() and image_path.stat().st_size > 1000
    finally:
        plotter.close()
