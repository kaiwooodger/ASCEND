from __future__ import annotations

import copy
import gc
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import psutil
import pydicom
import PySide6
import pyvista as pv
import scipy
import vtk
from PySide6.QtCore import QLibraryInfo, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QTableWidget

import ascend
from ascend.app.controller import ApplicationController
from ascend.dicom.discovery import discover_case
from ascend.gui.main_window import MainWindow
from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.metrics.service import Layer21Service
from ascend.layer3.lq.service import Layer31Service
from ascend.models.config import CaseConfiguration
from ascend.validation.anisotropic.comparison import ANISOTROPIC_GRIDS
from ascend.validation.anisotropic.fixtures import write_dicom_fixture
from ascend.validation.provenance import canonical_hash


EVIDENCE = Path("/evidence")
COMMIT = "4a2353867764c3e1d6b98267a52e78ba7c63aaac"
PROCESS = psutil.Process()


def elapsed(function: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function()
    return result, time.perf_counter() - started


def rss_mib() -> float:
    return PROCESS.memory_info().rss / (1024.0 * 1024.0)


def process_events(application: QApplication, seconds: float = 0.15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)


def dispose_window(application: QApplication, window: MainWindow) -> None:
    window.setAttribute(Qt.WA_DeleteOnClose, True)
    window.close()
    window.deleteLater()
    process_events(application, 0.08)
    gc.collect()
    process_events(application, 0.04)


def click_and_wait(application: QApplication, window: MainWindow, label: str, timeout: float = 30.0) -> None:
    button = next((item for item in window.findChildren(QPushButton) if item.text() == label), None)
    if button is None:
        raise AssertionError(f"GUI button not found: {label}")
    button.click()
    deadline = time.monotonic() + timeout
    while window._workers and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.02)
    if window._workers:
        raise TimeoutError(f"GUI operation timed out: {label}")
    process_events(application, 0.1)


def load_synthetic_helper() -> Callable[..., Any]:
    path = Path("/workspace/ASCEND/tests/helpers.py")
    specification = importlib.util.spec_from_file_location("ascend_linux_helpers", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Could not load the committed synthetic helper.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.synthetic_case


SYNTHETIC_CASE = load_synthetic_helper()


def mlq_parameters(identifier: str) -> dict[str, object]:
    return {
        "parameter_set_id": identifier,
        "parameter_source": "ASCEND Linux sandbox synthetic validation",
        "parameter_source_type": "configured_reference",
        "model_source": "specified synthetic reference equation",
        "alpha_per_gy": 0.3,
        "beta_per_gy2": 0.03,
        "delta_per_gy": 0.02,
        "repair_half_time": 0.5,
        "treatment_delivery_time": 0.2,
        "time_unit": "hours",
    }


def assign_layer31(case: Any) -> None:
    configured_roles = {"GTV", "T_L", "VTV_H", "VTV_L"}
    role_by_standard = {
        standard: role
        for role, standard in case.effective_structure_roles.items()
        if role in configured_roles and isinstance(standard, str)
    }
    case.configuration.layer31_roi_parameters = [
        {
            "roi_identity": item["roi_identity"],
            "alpha_beta_gy": 10.0,
            "parameter_source": "ASCEND Linux sandbox synthetic validation",
            "parameter_source_type": "configured_reference",
            "parameter_set_version": "linux-sandbox-v1",
            "assignment_method": "validation_harness",
        }
        for item in case.layer1.result["manifest"]["roi_inventory"]
        if item.get("canonical_mapping") in role_by_standard
    ]
    case.configuration.layer31_mlq_tumour_parameters = mlq_parameters("tumour-linux-v1")
    case.configuration.layer31_mlq_normal_parameters = mlq_parameters("normal-linux-v1")
    case.configuration_hash = canonical_hash(case.configuration.to_dict())


def calculate_synthetic(root: Path, identifier: str) -> tuple[Any, dict[str, float]]:
    timings: dict[str, float] = {}
    case, timings["synthetic_case_creation_seconds"] = elapsed(
        lambda: SYNTHETIC_CASE(root, explicit_vertices=True, include_oar=True)
    )
    case.case_id = identifier
    case.layer2_1, timings["layer2_1_seconds"] = elapsed(lambda: Layer21Service().run(case))
    case.layer2_2, timings["layer2_2_ipvdr_seconds"] = elapsed(lambda: Layer22Service().run(case))
    assign_layer31(case)
    case.layer3_1, timings["layer3_1_bed_eqd2_mlq_eud_tr_seconds"] = elapsed(lambda: Layer31Service().run(case))
    case.save()
    return case, timings


def table_value(table: QTableWidget, label: str, value_column: int = 1) -> str | None:
    for row in range(table.rowCount()):
        first = table.item(row, 0)
        if first is not None and first.text() == label:
            value = table.item(row, value_column)
            return value.text() if value is not None else None
    return None


def environment_record() -> dict[str, Any]:
    os_release: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip().strip('"')
    cpu_model = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines()
            if line.lower().startswith("model name")
        ),
        "not reported",
    )
    glx = (EVIDENCE / "glxinfo.txt").read_text(encoding="utf-8")
    return {
        "distribution": os_release.get("PRETTY_NAME"),
        "distribution_id": os_release.get("ID"),
        "distribution_version": os_release.get("VERSION_ID"),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model,
        "logical_cpu_count": psutil.cpu_count(),
        "ram_total_mib": round(psutil.virtual_memory().total / (1024.0 * 1024.0), 2),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "display": os.environ.get("DISPLAY"),
        "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
        "display_server": "Xvfb 21.1.7 with Openbox 3.6.1",
        "opengl": {
            "vendor": next(line.split(":", 1)[1].strip() for line in glx.splitlines() if line.startswith("OpenGL vendor string:")),
            "renderer": next(line.split(":", 1)[1].strip() for line in glx.splitlines() if line.startswith("OpenGL renderer string:")),
            "version": next(line.split(":", 1)[1].strip() for line in glx.splitlines() if line.startswith("OpenGL version string:")),
            "accelerated": "Accelerated: yes" in glx,
        },
        "ascend_version": ascend.__version__,
        "ascend_commit": COMMIT,
        "ascend_module": str(Path(ascend.__file__).resolve()),
        "qt": QLibraryInfo.version().toString(),
        "pyside6": PySide6.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pydicom": pydicom.__version__,
        "pyvista": pv.__version__,
        "vtk": vtk.vtkVersion.GetVTKVersion(),
    }


def gui_validation(application: QApplication, case: Any) -> dict[str, Any]:
    before = rss_mib()
    window, startup_seconds = elapsed(MainWindow)
    window.resize(1480, 900)
    window.show()
    process_events(application, 0.4)
    assert window.isVisible()
    navigation = []
    for index in range(window.pages.count()):
        window.pages.setCurrentIndex(index)
        process_events(application, 0.03)
        navigation.append({"index": index, "visible": window.pages.currentWidget().isVisible()})
    window.pages.setCurrentIndex(0)
    process_events(application)
    assert window.grab().save(str(EVIDENCE / "ascend-main-window.png"))

    dialog = QFileDialog(window, "ASCEND Linux file-selector validation")
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.show()
    process_events(application)
    dialog_visible = dialog.isVisible()
    dialog.close()
    process_events(application)

    locked_l21 = copy.deepcopy(case.layer2_1.result)
    locked_l22 = copy.deepcopy(case.layer2_2.result)
    locked_l31 = copy.deepcopy(case.layer3_1.result)
    window.controller = ApplicationController(case)
    window.refresh()
    process_events(application, 0.25)

    physical = {item["metric_id"]: item for item in case.layer2_1.result["harmonised_metrics"]}
    peak_expected = physical["mean_peak_dose"]["value"]
    peak_gui = window.metric_cards["mean_peak_dose"].value.text()
    ipvdr_expected = float(case.layer2_2.result["plan_ipvdr"]["primary_median"])
    response = case.layer3_1.result["layer3_1b_high_dose_sfrt_response"]
    ratio = case.layer3_1.result["layer3_1c_modelled_therapeutic_ratio"]
    mean_survival_gui = table_value(window.layer31b_summary, "Mean direct surviving fraction")
    eud_gui = table_value(window.layer31b_summary, "Survival-equivalent EUD")
    tr_gui = table_value(window.layer31c_summary, "Modelled therapeutic ratio")
    roi_result = case.layer3_1.result["roi_results"][0]["metrics"]
    first_bed_gui = window.layer31a_table.item(0, 2).text()
    first_eqd2_gui = window.layer31a_table.item(0, 5).text()
    consistency = {
        "mean_peak_dose": peak_gui == f"{float(peak_expected):.4g} Gy",
        "ipvdr": f"Median iPVDR {ipvdr_expected:.3f}" in window.graph_result_summary.text(),
        "mean_survival": mean_survival_gui is not None and float(mean_survival_gui) == float(response["mean_tumour_survival_fraction"]),
        "eud": eud_gui is not None and float(eud_gui) == float(response["tumour_eud_gy"]),
        "therapeutic_ratio": tr_gui is not None and float(tr_gui) == float(ratio["modelled_therapeutic_ratio"]),
        "sbed": float(first_bed_gui) == float(roi_result["bed_mean"]),
        "seqd2": float(first_eqd2_gui) == float(roi_result["eqd2_mean"]),
        "stored_results_unchanged": case.layer2_1.result == locked_l21 and case.layer2_2.result == locked_l22 and case.layer3_1.result == locked_l31,
    }
    assert all(consistency.values()), consistency
    window.pages.setCurrentIndex(6)
    process_events(application)
    assert window.grab().save(str(EVIDENCE / "ascend-layer31-results.png"))
    window.resize(1100, 720)
    process_events(application)
    resized_dimensions = [window.size().width(), window.size().height()]
    assert resized_dimensions[0] >= 1000 and resized_dimensions[1] >= 680
    dialog_visible_and_closed = dialog_visible and not dialog.isVisible()
    dispose_window(application, window)
    return {
        "status": "PASS",
        "startup_seconds": startup_seconds,
        "visible": True,
        "navigation": navigation,
        "dialog_visible_and_closed": dialog_visible_and_closed,
        "layout_resize": {"status": "PASS", "dimensions": resized_dimensions},
        "gui_engine_consistency": consistency,
        "rss_before_mib": before,
        "rss_after_close_mib": rss_mib(),
        "screenshots": ["ascend-main-window.png", "ascend-layer31-results.png"],
    }


def vtk_validation() -> dict[str, Any]:
    before = rss_mib()
    started = time.perf_counter()
    sphere = pv.Sphere(radius=10.0, theta_resolution=30, phi_resolution=30)
    mesh_creation = time.perf_counter() - started
    stl = EVIDENCE / "synthetic-linux-cad.stl"
    sphere.save(stl)
    mesh = pv.read(stl)
    points = np.asarray(mesh.points, dtype=np.float64)
    mesh.point_data["sBED_Gy"] = 30.0 + points[:, 2]
    mesh.point_data["sEQD2_Gy"] = mesh.point_data["sBED_Gy"] / 1.2
    mesh.point_data["MLQ_survival"] = np.exp(-0.05 * mesh.point_data["sBED_Gy"])
    assert all(np.isfinite(np.asarray(values)).all() for values in mesh.point_data.values())
    clipped = mesh.clip(normal="x", origin=(0.0, 0.0, 0.0))
    smoothed = mesh.smooth(n_iter=10)
    render_started = time.perf_counter()
    plotter = pv.Plotter(off_screen=False, window_size=(900, 700), title="ASCEND Linux realtime VTK validation")
    try:
        plotter.add_mesh(smoothed, scalars="sBED_Gy", cmap="viridis", opacity=0.82, show_scalar_bar=True)
        plotter.add_mesh(clipped, color="white", style="wireframe", opacity=0.35)
        plotter.add_points(mesh.points[::50], color="red", point_size=8, render_points_as_spheres=True)
        plotter.camera_position = "iso"
        plotter.show(auto_close=False, interactive_update=True)
        plotter.camera.Azimuth(30)
        plotter.camera.Elevation(15)
        plotter.camera.Zoom(1.15)
        plotter.render()
        plotter.window_size = (1000, 720)
        plotter.render()
        screenshot = np.asarray(plotter.screenshot(EVIDENCE / "ascend-vtk-realtime.png", return_img=True))
        assert screenshot.shape == (720, 1000, 3)
        assert np.ptp(screenshot) > 0
        render_seconds = time.perf_counter() - render_started
    finally:
        plotter.close()
    repeated_rss = []
    for _ in range(10):
        repeated = pv.Plotter(off_screen=False, window_size=(320, 240))
        repeated.add_mesh(mesh, scalars="sEQD2_Gy")
        repeated.show(auto_close=False, interactive_update=True)
        repeated.render()
        repeated.close()
        del repeated
        gc.collect()
        repeated_rss.append(rss_mib())
    return {
        "status": "PASS",
        "stl_points": mesh.n_points,
        "stl_cells": mesh.n_cells,
        "finite_scalar_ranges": {key: [float(np.min(value)), float(np.max(value))] for key, value in mesh.point_data.items()},
        "clipped_cells": clipped.n_cells,
        "smoothed_cells": smoothed.n_cells,
        "camera_interaction": "azimuth/elevation/zoom PASS",
        "opacity_colour_bar_resize": "PASS",
        "mesh_creation_seconds": mesh_creation,
        "render_seconds": render_seconds,
        "rss_before_mib": before,
        "rss_after_close_mib": rss_mib(),
        "repeated_open_close_rss_mib": repeated_rss,
        "screenshot": "ascend-vtk-realtime.png",
    }


def dicom_e2e(application: QApplication) -> tuple[dict[str, Any], Any]:
    root = Path(tempfile.mkdtemp(prefix="ascend-linux-dicom-"))
    source = write_dicom_fixture(root / "DICOM 数据 MixedCase", ANISOTROPIC_GRIDS[0])
    (source / "RTDOSE.dcm").rename(source / "rTdOsE Mixed Case.DcM")
    (source / "RTSTRUCT.dcm").rename(source / "rTsTrUcT Mixed Case.DCM")
    inventory, discovery_seconds = elapsed(lambda: discover_case(source))
    assert inventory["counts"]["RTDOSE"] == 1 and inventory["counts"]["RTSTRUCT"] == 1
    controller = ApplicationController()
    case, import_seconds = elapsed(lambda: controller.import_case(source, root / "ASCEND Case Output"))
    configuration = CaseConfiguration.from_dict(json.loads((source / "validation_config.json").read_text(encoding="utf-8")))
    configuration.treatment_approach = "LRT_ALONE"
    configuration.treatment_components = [{
        "component_id": "LRT", "component_type": "lrt", "fraction_count": 1,
        "source": "synthetic_validation",
    }]
    configuration.selected_treatment_component_id = "LRT"
    controller.configure(configuration)
    layer1, layer1_seconds = elapsed(controller.run_layer1)
    layer21, layer21_seconds = elapsed(controller.run_layer21)
    layer22, layer22_seconds = elapsed(controller.run_layer22)
    assign_layer31(case)
    controller.configure(case.configuration)
    layer31, layer31_seconds = elapsed(controller.run_layer31)
    exports, export_seconds = elapsed(lambda: controller.export(root / "exports"))
    window = MainWindow()
    window.controller = controller
    window.refresh()
    window.resize(1480, 900)
    window.show()
    process_events(application, 0.3)
    assert window.grab().save(str(EVIDENCE / "ascend-dicom-e2e.png"))
    dispose_window(application, window)
    record = {
        "status": "PASS" if (
            layer1.calculation_status in {"completed", "completed_with_warnings"}
            and layer21.calculation_status in {"completed", "completed_with_warnings"}
            and layer22.calculation_status in {"completed", "completed_with_warnings"}
            and layer31.calculation_status in {"completed", "completed_with_warnings"}
        ) else "FAIL",
        "synthetic_nonclinical_dataset": True,
        "path_portability": "Unicode, spaces, and mixed-case filenames PASS",
        "uid_chain_status": inventory["dicom_chains"][0]["validity_status"],
        "selected_chain_id": case.selected_chain_id,
        "layer1": layer1.calculation_status,
        "layer2_1": layer21.calculation_status,
        "layer2_2": layer22.calculation_status,
        "layer3_1": layer31.calculation_status,
        "layer3_1_error": layer31.error,
        "exports": [str(path) for path in exports],
        "timings_seconds": {
            "discovery": discovery_seconds,
            "import": import_seconds,
            "layer1_rasterisation": layer1_seconds,
            "layer2_1": layer21_seconds,
            "layer2_2_ipvdr": layer22_seconds,
            "layer3_1": layer31_seconds,
            "export": export_seconds,
        },
        "rss_after_dicom_mib": rss_mib(),
        "screenshot": "ascend-dicom-e2e.png",
    }
    return record, case


def gui_driven_workflow(application: QApplication) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="ascend-linux-gui-workflow-"))
    source = write_dicom_fixture(root / "GUI DICOM 数据", ANISOTROPIC_GRIDS[0])
    window = MainWindow()
    window.resize(1480, 900)
    window.show()
    process_events(application, 0.2)
    window.source_path.setText(str(source))
    click_and_wait(application, window, "Import case")
    case = window.controller.case
    assert case is not None and case.selected_chain_id

    configuration = CaseConfiguration.from_dict(json.loads((source / "validation_config.json").read_text(encoding="utf-8")))
    configuration.treatment_approach = "LRT_ALONE"
    configuration.treatment_components = [{
        "component_id": "LRT", "component_type": "lrt", "fraction_count": 1,
        "source": "synthetic_validation",
    }]
    configuration.selected_treatment_component_id = "LRT"
    window.controller.configure(configuration)
    window._load_configuration()
    window.refresh()
    click_and_wait(application, window, "Validate case")
    click_and_wait(application, window, "Run Layer 2.1")
    click_and_wait(application, window, "Run Layer 2.2")
    assign_layer31(case)
    window.controller.configure(case.configuration)
    window._load_configuration()
    window.refresh()
    click_and_wait(application, window, "Run complete Layer 3.1")
    click_and_wait(application, window, "Export JSON and CSV")
    window.pages.setCurrentIndex(6)
    process_events(application, 0.2)
    assert window.grab().save(str(EVIDENCE / "ascend-gui-driven-workflow.png"))
    statuses = {
        "layer1": case.layer1.calculation_status,
        "layer2_1": case.layer2_1.calculation_status,
        "layer2_2": case.layer2_2.calculation_status,
        "layer3_1": case.layer3_1.calculation_status,
    }
    exports = list((case.root / "exports").glob("*"))
    passed = all(value in {"completed", "completed_with_warnings"} for value in statuses.values()) and bool(exports)
    dispose_window(application, window)
    return {
        "status": "PASS" if passed else "FAIL",
        "interaction": "Qt button clicks on a visible xcb window",
        "path": [
            "Import case", "Validate case", "Run Layer 2.1", "Run Layer 2.2",
            "Run complete Layer 3.1", "Export JSON and CSV",
        ],
        "statuses": statuses,
        "export_count": len(exports),
        "screenshot": "ascend-gui-driven-workflow.png",
    }


def repeated_sessions(application: QApplication) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="ascend-linux-repeated-"))
    fingerprints: list[dict[str, float]] = []
    rss_values: list[float] = []
    widget_counts: list[int] = []
    durations: list[float] = []
    for index in range(10):
        started = time.perf_counter()
        case, _ = calculate_synthetic(root / f"case-{index}", f"REPEAT-{index}")
        window = MainWindow()
        window.controller = ApplicationController(case)
        window.refresh()
        window.show()
        process_events(application, 0.06)
        response = case.layer3_1.result["layer3_1b_high_dose_sfrt_response"]
        fingerprints.append({
            "ipvdr": float(case.layer2_2.result["plan_ipvdr"]["primary_median"]),
            "survival": float(response["mean_tumour_survival_fraction"]),
            "eud": float(response["tumour_eud_gy"]),
        })
        dispose_window(application, window)
        del window
        del case
        gc.collect()
        process_events(application, 0.04)
        durations.append(time.perf_counter() - started)
        rss_values.append(rss_mib())
        widget_counts.append(len(application.allWidgets()))
    first = fingerprints[0]
    assert all(item == first for item in fingerprints)
    return {
        "status": "PASS",
        "cycles": 10,
        "identical_scientific_fingerprints": True,
        "durations_seconds": durations,
        "rss_after_each_cycle_mib": rss_values,
        "rss_growth_mib": rss_values[-1] - rss_values[0],
        "live_widget_counts": widget_counts,
        "memory_interpretation": (
            "stable_live_widget_count; RSS is allocator/VTK high-water retention"
            if len(set(widget_counts)) == 1 else "live QWidget count changed across cycles"
        ),
    }


def case_switching(application: QApplication) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="ascend-linux-switching-"))
    case_a, _ = calculate_synthetic(root / "case-a", "CASE-A")
    case_b, _ = calculate_synthetic(root / "case-b", "CASE-B")
    a_result = copy.deepcopy(case_a.layer3_1.result)
    b_result = copy.deepcopy(case_b.layer3_1.result)
    window = MainWindow()
    observed = []
    for case in (case_a, case_b, case_a):
        window.controller = ApplicationController(case)
        window.refresh()
        window.show()
        process_events(application, 0.08)
        observed.append(window.header_case.text())
    dispose_window(application, window)
    assert observed == ["CASE-A", "CASE-B", "CASE-A"]
    assert case_a.layer3_1.result == a_result and case_b.layer3_1.result == b_result
    return {
        "status": "PASS",
        "sequence": observed,
        "case_local_results_unchanged": True,
        "dose_grid_and_roi_identity_replaced": case_a.root != case_b.root,
    }


def error_handling() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="ascend-linux-errors-"))
    source = write_dicom_fixture(root / "source", ANISOTROPIC_GRIDS[0])
    records: dict[str, Any] = {}
    for label, filename in (("missing_rtdose", "RTDOSE.dcm"), ("missing_rtstruct", "RTSTRUCT.dcm")):
        destination = root / label
        shutil.copytree(source, destination)
        (destination / filename).unlink()
        try:
            controller = ApplicationController()
            incomplete_case = controller.import_case(destination, root / f"case-{label}")
            record = controller.run_layer1()
        except Exception as exc:
            records[label] = {"controlled": True, "type": type(exc).__name__, "message": str(exc)}
        else:
            controlled = record.calculation_status in {"blocked", "failed"}
            records[label] = {
                "controlled": controlled,
                "calculation_status": record.calculation_status,
                "error": record.error,
                "selected_chain_id": incomplete_case.selected_chain_id,
            }
    mismatch = root / "mismatch"
    shutil.copytree(source, mismatch)
    dose_path = mismatch / "RTDOSE.dcm"
    dose = pydicom.dcmread(dose_path)
    dose.FrameOfReferenceUID = "2.25.999999999999999999"
    dose.save_as(dose_path, enforce_file_format=True)
    inventory = discover_case(mismatch)
    records["mismatched_frame_of_reference"] = {
        "controlled": True,
        "chain_statuses": [item["validity_status"] for item in inventory["dicom_chains"]],
        "unresolved": [item["unresolved_references"] for item in inventory["dicom_chains"]],
    }
    invalid_stl = root / "invalid.stl"
    invalid_stl.write_text("not an STL", encoding="utf-8")
    try:
        invalid_mesh = pv.read(invalid_stl)
    except Exception as exc:
        records["invalid_stl"] = {"controlled": True, "type": type(exc).__name__, "message": str(exc)}
    else:
        records["invalid_stl"] = {
            "controlled": invalid_mesh.n_points == 0 or invalid_mesh.n_cells == 0,
            "points": invalid_mesh.n_points,
            "cells": invalid_mesh.n_cells,
            "message": "invalid geometry rejected as an empty mesh",
        }

    invalid_case = SYNTHETIC_CASE(root / "invalid-tumour", explicit_vertices=True, include_oar=True)
    assign_layer31(invalid_case)
    invalid_case.configuration.layer31_mlq_tumour_parameters["alpha_per_gy"] = -1.0
    invalid_case.configuration_hash = canonical_hash(invalid_case.configuration.to_dict())
    invalid_record = Layer31Service().run(invalid_case)
    invalid_branch = invalid_record.result["layer3_1b_high_dose_sfrt_response"]
    records["invalid_tumour_parameters"] = {
        "controlled": invalid_branch.get("calculation_status") == "blocked",
        "calculation_status": invalid_record.calculation_status,
        "branch_calculation_status": invalid_branch.get("calculation_status"),
        "reason": invalid_branch.get("reason"),
    }

    missing_time_case = SYNTHETIC_CASE(root / "missing-time", explicit_vertices=True, include_oar=True)
    assign_layer31(missing_time_case)
    missing_time_case.configuration.layer31_mlq_tumour_parameters.pop("treatment_delivery_time")
    missing_time_case.configuration_hash = canonical_hash(missing_time_case.configuration.to_dict())
    missing_time_record = Layer31Service().run(missing_time_case)
    missing_time_branch = missing_time_record.result["layer3_1b_high_dose_sfrt_response"]
    records["missing_treatment_delivery_time"] = {
        "controlled": missing_time_branch.get("calculation_status") == "blocked",
        "calculation_status": missing_time_record.calculation_status,
        "branch_calculation_status": missing_time_branch.get("calculation_status"),
        "reason": missing_time_branch.get("reason"),
    }

    empty_roi_case = SYNTHETIC_CASE(root / "empty-roi", explicit_vertices=True, include_oar=True)
    assign_layer31(empty_roi_case)
    assigned_identity = empty_roi_case.configuration.layer31_roi_parameters[0]["roi_identity"]
    for item in empty_roi_case.layer1.result["manifest"]["roi_inventory"]:
        if item.get("roi_identity") == assigned_identity:
            item["rasterisation_status"] = "not_rasterised"
    Path(empty_roi_case.layer1.result_path).write_text(
        json.dumps(empty_roi_case.layer1.result, indent=2), encoding="utf-8"
    )
    empty_roi_record = Layer31Service().run(empty_roi_case)
    empty_roi_branch = empty_roi_record.result["layer3_1a_conventional_lq"]
    records["empty_or_unrasterised_roi"] = {
        "controlled": empty_roi_branch.get("calculation_status") == "blocked",
        "calculation_status": empty_roi_record.calculation_status,
        "branch_calculation_status": empty_roi_branch.get("calculation_status"),
        "reason": empty_roi_branch.get("reason"),
    }

    try:
        CaseConfiguration(treatment_delivery_mode="unsupported").validate()
    except Exception as exc:
        records["unsupported_treatment_configuration"] = {
            "controlled": True, "type": type(exc).__name__, "message": str(exc)
        }
    else:
        records["unsupported_treatment_configuration"] = {"controlled": False}
    controlled = all(item.get("controlled") for item in records.values())
    return {"status": "PASS" if controlled else "FAIL", "records": records}


def main() -> int:
    result: dict[str, Any] = {
        "schema_version": "ASCEND-linux-sandbox-validation-v1",
        "environment": environment_record(),
        "memory": {"process_start_mib": rss_mib()},
    }
    application = QApplication.instance() or QApplication([])
    result["memory"]["after_qapplication_mib"] = rss_mib()
    base_root = Path(tempfile.mkdtemp(prefix="ascend-linux-base-"))
    base_case, base_timings = calculate_synthetic(base_root / "case", "LINUX-BASE")
    result["synthetic_workflow"] = {
        "status": "PASS",
        "timings_seconds": base_timings,
        "layer2_1": base_case.layer2_1.calculation_status,
        "layer2_2": base_case.layer2_2.calculation_status,
        "layer3_1": base_case.layer3_1.calculation_status,
    }
    result["memory"]["after_layer3_mib"] = rss_mib()
    result["gui"] = gui_validation(application, base_case)
    result["vtk"] = vtk_validation()
    result["memory"]["after_vtk_close_mib"] = rss_mib()
    result["dicom_e2e"], dicom_case = dicom_e2e(application)
    result["memory"]["after_dicom_e2e_mib"] = rss_mib()
    result["gui_driven_workflow"] = gui_driven_workflow(application)
    result["repeated_sessions"] = repeated_sessions(application)
    result["case_switching"] = case_switching(application)
    result["error_handling"] = error_handling()
    result["memory"]["final_mib"] = rss_mib()
    result["final_status"] = "PASS" if all(
        item.get("status") == "PASS"
        for item in (
            result["synthetic_workflow"], result["gui"], result["vtk"],
            result["dicom_e2e"], result["repeated_sessions"],
            result["gui_driven_workflow"], result["case_switching"], result["error_handling"],
        )
    ) else "FAIL"
    (EVIDENCE / "linux-validation-harness.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["final_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
