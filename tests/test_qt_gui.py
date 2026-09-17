from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock, patch

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractButton, QApplication, QFileDialog, QLineEdit, QPushButton, QSizePolicy, QTextEdit, QWidget
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, RTStructureSetStorage

from ascend.app.controller import ApplicationController
from ascend import __release_name__, __release_series__, __validation_scope__, __version__
from ascend.gui.main_window import GraphCanvas, MainWindow, VerticesQACanvas, supporting_output_rows
from ascend.gui.layer31_viewer import Layer31Viewer, RegionalResultCard, SurvivalContributionBar
from ascend.gui.layer32_viewer import Layer32ProfileCanvas
from ascend.gui.theme import canonical_state
from ascend.models.case import ASCENDCase
from ascend.report_options import PDF_REPORT_OPTIONS
from ascend.layer3.response.mlq import validate_mlq_parameter_set, with_scenario


def _attach_rtstruct(case: ASCENDCase, root: Path, *names: str) -> Path:
    path = root / "RTSTRUCT.dcm"
    file_meta = FileMetaDataset()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.MediaStorageSOPClassUID = RTStructureSetStorage
    file_meta.MediaStorageSOPInstanceUID = "1.2.826.0.1.3680043.10.543.180"
    dataset = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.SOPClassUID = RTStructureSetStorage
    dataset.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    rois = []
    for number, name in enumerate(names, 1):
        roi = Dataset()
        roi.ROINumber = number
        roi.ROIName = name
        rois.append(roi)
    dataset.StructureSetROISequence = Sequence(rois)
    dataset.save_as(path, enforce_file_format=True)
    case.selected_objects["rtstruct"] = str(path)
    return path


class QtGuiTests(unittest.TestCase):
    def test_reset_button_reconstructs_every_page_and_discards_old_case(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow()
            window.controller.case = ASCENDCase(directory, case_id="OLD-PATIENT")
            window.controller.case.provenance = {"old_patient": "OLD-PATIENT"}
            window.source_path.setText("old DICOM directory")
            window.tps_csv.setText("old Eclipse reference")
            window._pending_eclipse_reference = "old Eclipse reference"
            window._layer31_roi_entries = [{"old": True}]
            window._oar_entries = [{"old": True}]
            window.export_result.setPlainText("old export")
            window.layer32_enabled.setChecked(True)
            window._set_pdf_report_options(False)
            window.layer22_viewer = QWidget()
            old_viewer = window.layer22_viewer
            old_pages = window.pages
            window.layer22_viewer_run_id = "OLD-RUN"
            window.reset_button.click()
            self.assertIsNone(window.controller.case)
            self.assertIsNot(window.pages, old_pages)
            self.assertEqual(window.pages.count(), 11)
            self.assertEqual(window.pages.currentIndex(), 0)
            self.assertEqual(window.source_path.text(), "")
            self.assertEqual(window.tps_csv.text(), "")
            self.assertIsNone(window._pending_eclipse_reference)
            self.assertEqual(window._layer31_roi_entries, [])
            self.assertEqual(window._oar_entries, [])
            self.assertEqual(window.export_result.toPlainText(), "")
            self.assertFalse(window.layer32_enabled.isChecked())
            self.assertTrue(all(check.isChecked() for check in window.pdf_report_checks.values()))
            self.assertIsNone(window.layer22_viewer)
            self.assertIsNone(window.layer22_viewer_run_id)
            self.assertFalse(old_viewer.isVisible())
            self.assertEqual(window.header_case.text(), "No case open")
            window.close()

    def test_reset_detaches_pending_biological_mesh_callbacks_and_closes_scene(self) -> None:
        from ascend.gui.layer31_viewer_cad import _MeshWorker

        window = MainWindow()
        viewer = Layer31Viewer()
        window.layer31_viewer = viewer
        worker = _MeshWorker(1, lambda: None)
        worker.signals.finished.connect(viewer._mesh_finished)
        worker.signals.failed.connect(viewer._mesh_failed)
        viewer._mesh_workers.add(worker)
        viewer._mesh_worker_keys[1] = ("old case",)
        viewer._mesh_cache[("old case",)] = None
        viewer._mesh_timer.start()
        viewer._opacity_timer.start()
        window.reset_button.click()
        self.assertFalse(viewer._mesh_timer.isActive())
        self.assertFalse(viewer._opacity_timer.isActive())
        self.assertEqual(viewer._mesh_workers, set())
        self.assertEqual(viewer._mesh_worker_keys, {})
        self.assertEqual(viewer._mesh_cache, {})
        self.assertIsNone(viewer.data)
        worker.signals.finished.emit(1, None)
        worker.signals.failed.emit(1, "old case error")
        window.close()

    def test_reset_is_disabled_until_background_work_finishes(self) -> None:
        window = MainWindow()
        controller = window.controller
        with patch.object(window.thread_pool, "start"):
            window._work(lambda: None)
        self.assertFalse(window.reset_button.isEnabled())
        window._reset_case()
        self.assertIs(window.controller, controller)
        next(iter(window._workers)).signals.finished.emit(None)
        self.assertTrue(window.reset_button.isEnabled())
        window.source_path.setText("unsaved case input")
        window.reset_button.click()
        self.assertEqual(window.source_path.text(), "")
        window.close()

    def test_pdf_export_screen_exposes_every_selectable_report_item(self) -> None:
        window = MainWindow()
        self.assertEqual(set(window.pdf_report_checks), set(PDF_REPORT_OPTIONS))
        self.assertTrue(all(checkbox.isChecked() for checkbox in window.pdf_report_checks.values()))
        window._set_pdf_report_options(False)
        self.assertFalse(any(checkbox.isChecked() for checkbox in window.pdf_report_checks.values()))
        window._set_pdf_report_options(True)
        self.assertTrue(all(checkbox.isChecked() for checkbox in window.pdf_report_checks.values()))
        window.close()

    def test_layer1_validation_callback_is_bound_to_the_window(self) -> None:
        window = MainWindow()
        with (
            patch.object(window, "_save_configuration", return_value=True) as save_configuration,
            patch.object(window, "_work") as work,
        ):
            window._run_layer1()
        save_configuration.assert_called_once_with(silent=True)
        work.assert_called_once_with(window.controller.run_layer1)
        window.close()

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_qt_workstation_has_complete_workflow(self) -> None:
        window = MainWindow()
        self.assertEqual(window.pages.count(), 11)
        self.assertIn("ASCEND 1.8.3", window.windowTitle())
        self.assertEqual(window.navigation.count(), 15)
        buttons = [item.text() for item in window.pages.widget(5).findChildren(QPushButton)]
        self.assertIn("Run Layer 2.2", buttons)
        vertex_buttons = [item.text() for item in window.pages.widget(6).findChildren(QPushButton)]
        self.assertIn("Run / refresh physical analysis", vertex_buttons)
        biological_buttons = [item.text() for item in window.pages.widget(7).findChildren(QPushButton)]
        self.assertIn("Run complete Layer 3.1", biological_buttons)
        self.assertIn("Load / refresh unified viewer", biological_buttons)
        self.assertEqual(window.layer31_tabs.count(), 7)
        self.assertTrue(any("3.1D TCP" in window.layer31_tabs.tabText(index) for index in range(window.layer31_tabs.count())))
        self.assertEqual(
            [window.layer31_tabs.tabText(index) for index in range(window.layer31_tabs.count())],
            [
                "1–9 Configure / run", "10–13 Map", "14 Whole-tumour SF / EUD",
                "15 Regional explanation", "16 Scenarios / therapeutic ratio", "3.1D TCP", "17 Provenance / export",
            ],
        )
        self.assertIsInstance(window.layer31b_contribution_bar, SurvivalContributionBar)
        self.assertFalse(window.layer31b_contribution_bar.isHidden())
        if sys.platform == "darwin":
            viewer = Layer31Viewer()
            self.assertEqual(type(viewer.scene).__name__, "PyVistaBiologicalScene3D")
            self.assertTrue(callable(viewer.scene.window))
        self.assertIn("1 Prepare case", window.layer31_workflow_order.text())
        self.assertIn("17 Audit provenance/export", window.layer31_workflow_order.text())
        self.assertEqual(window.layer31_status_pill.text(), "NOT RUN")
        layer32_buttons = [item.text() for item in window.pages.widget(8).findChildren(QPushButton)]
        self.assertIn("Run Layer 3.2", layer32_buttons)
        self.assertIn("Build / refresh 3D biological field viewer", layer32_buttons)
        self.assertFalse(window.layer32_enabled.isChecked())
        self.assertFalse(window.layer32_run_button.isEnabled())
        self.assertFalse(window.layer32_scaling.isEnabled())
        window.layer32_enabled.setChecked(True)
        self.assertTrue(window.layer32_run_button.isEnabled())
        self.assertTrue(window.layer32_scaling.isEnabled())
        window.layer32_enabled.setChecked(False)
        self.assertFalse(any("vascular" in item.placeholderText().lower() for item in window.pages.widget(8).findChildren(QLineEdit)))
        import_buttons = [item.text() for item in window.pages.widget(0).findChildren(QPushButton)]
        configuration_buttons = [item.text() for item in window.pages.widget(1).findChildren(QPushButton)]
        self.assertIn("Browse Eclipse file", import_buttons)
        self.assertIn("Browse Eclipse folder", import_buttons)
        self.assertEqual(window.layer1_tabs.count(), 4)
        self.assertEqual(window.layer21_tabs.count(), 2)
        self.assertEqual([window.layer21_tabs.itemText(index) for index in range(2)], ["Supporting outputs", "Provenance"])
        self.assertEqual(window.layer21_vertices_tabs.count(), 2)
        self.assertEqual(window.layer21_vertices_tabs.tabText(1), "Global FWHM")
        self.assertEqual(window.vertices_controls_card.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
        self.assertEqual(window.layer22_controls_card.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
        toolbox_buttons = [
            item for item in window.layer21_tabs.findChildren(QAbstractButton)
            if item.metaObject().className() == "QToolBoxButton"
        ]
        self.assertEqual(len(toolbox_buttons), 2)
        self.assertTrue(all(item.minimumHeight() >= 38 for item in toolbox_buttons))
        self.assertEqual(window.layer22_display_tabs.count(), 1)
        self.assertEqual(window.layer22_display_tabs.tabText(0), "3D masks / dose views")
        self.assertEqual(window.vertex_qa_tabs.count(), 6)
        self.assertEqual(
            [window.vertex_qa_tabs.tabText(index) for index in range(window.vertex_qa_tabs.count())],
            [
                "Hover graph overview", "ICRU 91 dose gradient", "Per-vertex QA",
                "Vertex layout / FWHM", "Saddle graphs", "OAR geometry",
            ],
        )
        self.assertFalse(window.windowIcon().isNull())
        self.assertEqual(QApplication.applicationDisplayName(), "ASCEND")
        self.assertFalse(any(
            "JSON" in item.text()
            for item in window.pages.widget(1).findChildren(QAbstractButton)
        ))
        self.assertEqual(window.protocol_endpoint_role.count(), 4)
        self.assertEqual(window.protocol_endpoint_kind.count(), 3)
        self.assertIn("Auto-fill from Eclipse reference", configuration_buttons)
        mapping_buttons = [item.text() for item in window.pages.widget(2).findChildren(QPushButton)]
        self.assertIn("Add / update OAR", mapping_buttons)
        self.assertIn("Remove selected OAR", mapping_buttons)
        self.assertEqual(window.oar_classification_selector.count(), 4)
        self.assertGreaterEqual(window.oar_classification_selector.findData("internal_target_structure"), 0)
        self.assertFalse(any(
            "Optional OAR geometry" in item.placeholderText()
            for item in window.pages.widget(2).findChildren(QTextEdit)
        ))
        window.close()

    def test_treatment_component_editor_is_structured_and_calculates_display_dose_per_fraction(self) -> None:
        window = MainWindow()
        window.component_id.setText("LRT")
        window.component_type.setCurrentText("LRT")
        window.component_prescription.setText("20")
        window.component_fractions.setText("1")
        window.component_rx_low.setText("5")
        window.component_rx_high.setText("20")
        window.component_gap.setText("7")
        window.component_prescription_source.setText("protocol_configuration")
        window._add_treatment_component()
        self.assertEqual(len(window._treatment_component_entries), 1)
        self.assertEqual(window.treatment_component_table.item(0, 4).text(), "20.0")
        self.assertEqual(window.analysis_component.currentData(), "LRT")
        self.assertFalse(any(
            "JSON" in item.text() for item in window.pages.widget(1).findChildren(QAbstractButton)
        ))
        window.close()

    def test_release_identity_is_the_183_case_reset_and_oar_report(self) -> None:
        self.assertEqual(__version__, "1.8.3")
        self.assertEqual(__release_series__, "ASCEND 1.8.x")
        self.assertEqual(__release_name__, "Case reset and explicit OAR EUD / SF reporting")
        self.assertIn("not clinically validated", __validation_scope__)

    def test_layer31_presets_support_explicit_tumour_override_and_delivery_source(self) -> None:
        window = MainWindow()
        self.assertEqual(window.layer31_high_dose_criterion.currentData(), "not_configured")
        self.assertFalse(window.layer31_high_dose_threshold.isEnabled())
        self.assertFalse(window.layer31_tr_fraction_count.isEnabled())

        window.layer31_tumour_scenario.setCurrentText("C1")
        tumour = window.layer31_tumour_kinetics
        self.assertEqual(tumour["alpha_beta_gy"].text(), "10")
        self.assertEqual(tumour["sf2"].text(), "0.48675225596")
        self.assertEqual(tumour["alpha_per_gy"].text(), "0.3")
        self.assertEqual(tumour["beta_per_gy2"].text(), "0.03")
        self.assertEqual(tumour["delta_per_gy"].text(), "0.15")
        self.assertEqual(tumour["repair_half_time"].text(), "60.0")
        self.assertEqual(tumour["delivery_time_source"].currentData(), "manual_case_configuration")
        self.assertTrue(tumour["alpha_per_gy"].isReadOnly())
        self.assertTrue(tumour["parameter_source"].isReadOnly())
        self.assertIn("Zhang H", tumour["parameter_source"].text())

        tumour["scenario_override"].setChecked(True)
        tumour["alpha_per_gy"].setText("0.25")
        tumour["beta_per_gy2"].setText("0.05")
        tumour["scenario_override_source"].setText("Exploratory sensitivity analysis")
        tumour["treatment_delivery_time"].setText("1.5")
        overridden = window._layer31_kinetic_parameters(tumour, "C1", "tumour")
        self.assertFalse(tumour["alpha_per_gy"].isReadOnly())
        self.assertEqual(overridden["alpha_per_gy"], 0.25)
        self.assertEqual(overridden["beta_per_gy2"], 0.05)
        self.assertEqual(overridden["alpha_beta_gy"], 5.0)
        self.assertTrue(overridden["scenario_parameter_override"])

        window.layer31_normal_scenario.setCurrentText("N1")
        normal = window.layer31_normal_kinetics
        self.assertEqual(normal["alpha_beta_gy"].text(), "3.102")
        self.assertEqual(normal["sf2"].text(), "0.2999918414087205")
        self.assertEqual(normal["kinetic_preset"].currentData(), "rss_grid_reference")
        self.assertEqual(normal["delta_per_gy"].text(), "0.15")
        self.assertEqual(normal["repair_half_time"].text(), "60.0")
        self.assertIn("PRESET", normal["status"].text())
        self.assertTrue(normal["parameter_set_id"].isReadOnly())

        window.layer31_tr_enabled.setChecked(True)
        self.assertTrue(window.layer31_tr_fraction_count.isEnabled())
        window.close()

    def test_layer31_rtplan_control_point_delivery_time_is_resolved_with_provenance(self) -> None:
        case = ASCENDCase("/tmp/ascend-gui-rtplan-delivery-test", case_id="RTPLAN_TIME")
        case.provenance["dicom_configuration_prefill"] = {
            "delivery_metadata": {
                "schema_version": "ASCEND-RTPLAN-delivery-v2",
                "status": "available",
                "plan_uid": "1.2.3.4",
                "beam_on_time_seconds_per_fraction": 90.0,
            }
        }
        window = MainWindow()
        window.controller = ApplicationController(case)
        window.layer31_tumour_scenario.setCurrentText("C1")
        tumour = window.layer31_tumour_kinetics
        tumour["delivery_time_source"].setCurrentIndex(
            tumour["delivery_time_source"].findData("rtplan_control_point_integration")
        )
        parameters = window._layer31_kinetic_parameters(tumour, "C1", "tumour")
        self.assertEqual(tumour["treatment_delivery_time"].text(), "1.5")
        self.assertTrue(tumour["treatment_delivery_time"].isReadOnly())
        self.assertEqual(parameters["treatment_delivery_time"], 1.5)
        self.assertEqual(parameters["time_unit"], "minutes")
        self.assertEqual(parameters["delivery_time_source"], "rtplan_control_point_integration")
        self.assertEqual(parameters["delivery_time_evidence"]["plan_uid"], "1.2.3.4")
        validated = validate_mlq_parameter_set(with_scenario(parameters, "C1", tissue="tumour"), "tumour")
        self.assertEqual(validated["treatment_delivery_time"], 1.5)
        window.close()

    def test_layer31_unified_viewer_is_embedded_in_map_tab(self) -> None:
        class DummyViewer(QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.scenarioRequested = MagicMock()
                self.data = None

            def set_data(self, data: object) -> None:
                self.data = data

        window = MainWindow()
        payload = object()
        with patch("ascend.gui.layer31_viewer.Layer31Viewer", DummyViewer):
            window._show_layer31_visualization(payload)
        self.assertIs(window.layer31_viewer.data, payload)
        self.assertGreaterEqual(window.layer31_viewer_layout.indexOf(window.layer31_viewer), 0)
        self.assertEqual(window.layer31_tabs.currentIndex(), 1)
        self.assertFalse(hasattr(window, "layer31_viewer_window"))
        window.close()

    def test_layer31_manual_tissue_assignment_has_explicit_default_provenance(self) -> None:
        window = MainWindow()
        self.assertEqual(window.layer31_parameter_source_type.currentData(), "user_selected")
        self.assertEqual(window.layer31_parameter_source.text(), "User-declared exploratory tissue parameter")
        self.assertEqual(window.layer31_parameter_set.text(), "manual-v1")
        window.layer31_parameter_source_type.setCurrentIndex(
            window.layer31_parameter_source_type.findData("configured_reference")
        )
        self.assertEqual(window.layer31_parameter_source.text(), "")
        self.assertEqual(window.layer31_parameter_set.text(), "")
        window.close()

    def test_layer32_profile_canvas_preserves_qpaintdevice_metric_and_paints(self) -> None:
        """Prevent data attributes from shadowing QWidget.metric and crashing Qt."""
        canvas = Layer32ProfileCanvas()
        self.assertTrue(callable(canvas.metric))
        canvas.resize(640, 360)
        canvas.set_profile(
            {
                "edge_id": 1,
                "distance_mm": [0.0, 1.0, 2.0],
                "physical_absorbed_dose_gy": [12.0, 3.0, 11.0],
                "biological_effect_equivalent_dose_gy": [13.0, 4.0, 12.0],
            },
            {
                "physical_ipvdr": 3.8,
                "biological_effect_equivalent_ipvdr": 3.1,
                "biological_ipvdr_shift": -0.7,
            },
        )
        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())
        canvas.close()

    def test_layer31_regional_cards_and_contribution_bar_present_stored_values(self) -> None:
        card = RegionalResultCard("V", "VALLEYS")
        card.set_record({
            "tumour_volume_fraction": 0.428,
            "mean_surviving_fraction": 0.118,
            "survivor_contribution_fraction": 0.781,
        })
        self.assertIn("42.80%", card.volume.text())
        self.assertIn("0.118", card.survival.text())
        self.assertIn("78.10%", card.contribution.text())
        bar = SurvivalContributionBar(); bar.resize(720, 96)
        bar.set_records([
            {"region_id": "H", "survivor_contribution_fraction": 0.0002},
            {"region_id": "V", "survivor_contribution_fraction": 0.781},
            {"region_id": "O", "survivor_contribution_fraction": 0.2188},
        ])
        pixmap = QPixmap(bar.size()); bar.render(pixmap)
        self.assertFalse(pixmap.isNull())
        card.close(); bar.close()

    def test_layer31_viewer_enforces_map_result_explanation_hierarchy(self) -> None:
        viewer = Layer31Viewer()
        self.assertEqual(
            viewer.hierarchy_label.text(),
            "1  MAP  →  2  WHOLE-TUMOUR RESULT  →  3  REGIONAL EXPLANATION",
        )
        self.assertIn("WHO DRIVES RESIDUAL TUMOUR SURVIVAL", viewer.regional_title.text())
        self.assertEqual(viewer.primary_sf.text(), "MEAN TUMOUR SF\n—")
        self.assertEqual(viewer.primary_eud.text(), "MLQ TUMOUR EUD\n—")
        self.assertTrue(viewer.display_smoothing.isEnabled())
        self.assertTrue(viewer.display_smoothing.isChecked())
        self.assertTrue(viewer.cad_show_anatomy.isChecked())
        self.assertTrue(viewer.cad_bed_overlay.isChecked())
        self.assertFalse(viewer.cad_eqd2_overlay.isChecked())
        viewer.cad_eqd2_overlay.setChecked(True)
        self.assertTrue(viewer.cad_eqd2_overlay.isChecked())
        self.assertFalse(viewer.cad_bed_overlay.isChecked())
        viewer.cad_eqd2_overlay.setChecked(False)
        self.assertFalse(viewer.cad_eqd2_overlay.isChecked())
        self.assertFalse(viewer.cad_bed_overlay.isChecked())
        self.assertEqual(viewer.cad_mode.count(), 5)
        self.assertEqual(
            [viewer.cad_mode.itemData(index) for index in range(5)],
            ["SURFACE", "VOLUME", "ISOSURFACE", "SLICE", "COMBINED"],
        )
        self.assertEqual(viewer.cad_mode.currentData(), "SLICE")
        self.assertEqual(viewer.range_mode.count(), 4)
        self.assertEqual(viewer.cad_region.count(), 4)
        self.assertIn("P90", viewer.isosurface_thresholds.text())
        self.assertTrue(viewer.cad_contours.isEnabled())
        self.assertEqual(viewer.gtv_opacity.value(), 96)
        self.assertEqual(viewer.oar_opacity.value(), 25)
        self.assertEqual(viewer.iso_opacity.value(), 45)
        self.assertEqual(len(viewer.cad_metric_cards), 4)
        viewer.close()

    def test_layer31_viewer_uses_responsive_stages_and_shared_navigation(self) -> None:
        viewer = Layer31Viewer()
        viewer.resize(1600, 900)
        viewer._apply_responsive_splitter_sizes()
        self.assertGreaterEqual(viewer.minimumHeight(), 600)
        self.assertEqual(viewer.workflow_tabs.count(), 3)
        self.assertEqual(viewer.tabs.count(), 2)
        self.assertEqual(viewer.tabs.tabText(0), "UNIFIED FOUR-PANE VIEWER")
        self.assertEqual(set(viewer.canvases), {"axial", "sagittal", "coronal"})
        self.assertEqual(viewer.workflow_tabs.sizePolicy().horizontalPolicy(), QSizePolicy.Ignored)
        self.assertLessEqual(viewer.scene.minimumWidth(), 300)
        self.assertGreater(viewer.workspace_splitter.sizes()[1], viewer.workspace_splitter.sizes()[0])
        self.assertEqual(viewer.cad_splitter.count(), 1)
        self.assertEqual(viewer._mesh_timer.interval(), 140)
        self.assertEqual(viewer._opacity_timer.interval(), 120)
        self.assertEqual(viewer.scene._interaction_timer.interval(), 33)
        self.assertIs(viewer.cad_show_anatomy, viewer.show_structures)
        self.assertEqual(set(viewer.navigation_controls), {
            "perspective", "axial", "sagittal", "coronal",
            "zoom_out", "zoom_in", "rotate_left", "rotate_right", "fit",
        })
        viewer.navigation_controls["zoom_in"].click()
        self.assertTrue(all(canvas.zoom > 1.0 for canvas in viewer.canvases.values()))
        viewer.navigation_controls["rotate_right"].click()
        self.assertTrue(all(canvas.rotation_degrees == 15.0 for canvas in viewer.canvases.values()))
        viewer.navigation_controls["fit"].click()
        self.assertTrue(all(canvas.zoom == 1.0 and canvas.rotation_degrees == 0.0 for canvas in viewer.canvases.values()))
        viewer.canvases["axial"].linkedZoomRequested.emit("axial", 1.15)
        self.assertTrue(viewer.canvases["sagittal"].zoom > 1.0)
        self.assertTrue(viewer.canvases["coronal"].zoom > 1.0)
        self.assertFalse(viewer.cad_controls_dialog.isVisible())
        viewer.cad_controls_button.click()
        self.assertTrue(viewer.cad_controls_dialog.isVisible())
        viewer.cad_controls_dialog.hide()
        viewer.data = object()
        viewer.cad_overlay_parameter.addItem("α/β 10 Gy", {"bed": "stored_BED", "eqd2": "stored_EQD2"})
        viewer._sync_cad_overlay_to_field("stored_EQD2")
        self.assertTrue(viewer.cad_eqd2_overlay.isChecked())
        self.assertFalse(viewer.cad_bed_overlay.isChecked())
        viewer._sync_cad_overlay_to_field("physical_course_dose_gy")
        self.assertTrue(viewer.cad_physical_overlay.isChecked())
        self.assertFalse(viewer.cad_eqd2_overlay.isChecked())
        viewer.close()

    def test_layer31_cad_starts_with_a_closer_camera_without_changing_slices(self) -> None:
        viewer = Layer31Viewer()
        plotter = MagicMock()
        plotter.renderer.actors = {}
        viewer.scene._plotter = plotter
        initial_slice_zooms = {name: canvas.zoom for name, canvas in viewer.canvases.items()}

        with patch.object(viewer.scene, "_capture"):
            viewer.scene._render_anatomy_only(SimpleNamespace(anatomy_meshes={}))

        plotter.reset_camera.assert_called_once_with()
        plotter.camera.Zoom.assert_called_once_with(viewer.scene.INITIAL_CAMERA_ZOOM_FACTOR)
        self.assertEqual(viewer.scene.INITIAL_CAMERA_ZOOM_FACTOR, 1.25)
        self.assertEqual(initial_slice_zooms, {name: canvas.zoom for name, canvas in viewer.canvases.items()})
        viewer.close()

    def test_layer31_map_tab_does_not_inherit_configuration_page_width(self) -> None:
        window = MainWindow()
        self.assertEqual(window.layer31_tabs.sizePolicy().horizontalPolicy(), QSizePolicy.Preferred)
        window.layer31_tabs.setCurrentIndex(1)
        self.assertEqual(window.layer31_tabs.sizePolicy().horizontalPolicy(), QSizePolicy.Ignored)
        self.assertTrue(window.layer31_page_title.isHidden())
        self.assertGreaterEqual(window.layer31_tabs.maximumHeight(), 700)
        window.layer31_tabs.setCurrentIndex(0)
        self.assertFalse(window.layer31_page_title.isHidden())
        window.close()

    def test_protocol_endpoint_editor_is_structured_and_deterministic(self) -> None:
        window = MainWindow()
        window.protocol_endpoint_role.setCurrentText("GTV")
        window.protocol_endpoint_kind.setCurrentIndex(0)
        window.protocol_endpoint_value.setText("95")
        window._add_protocol_endpoint()
        self.assertEqual(window.protocol_endpoint_table.rowCount(), 1)
        self.assertEqual(window._protocol_endpoint_entries[0]["id"], "gtv_d95")
        self.assertEqual(window.protocol_endpoint_table.item(0, 1).text(), "D95")
        window.close()

    def test_eclipse_endpoint_button_maps_selected_reference_and_refreshes_table(self) -> None:
        """Exercise the actual Qt button path rather than only parser helpers."""
        reference = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
D95% [Gy]: 18
D2% [Gy]: 21
V95%Rx [%]: 87
Dose [Gy] Volume [%]
0 100
21 0
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "eclipse.txt"
            reference_path.write_text(reference, encoding="utf-8")
            case = ASCENDCase(str(root / "case"), case_id="GENERAL003")
            case.initialise_directories()
            _attach_rtstruct(case, root, "Target", "Body")
            case.configuration.structure_roles = {"GTV": "Target"}
            window = MainWindow()
            window.controller = ApplicationController(case)
            window.tps_csv.setText(str(reference_path))
            with patch("ascend.gui.main_window.QMessageBox.information"):
                window.prefill_endpoint_button.click()
            self.assertEqual(case.configuration.tps_metrics_csv, str(reference_path))
            self.assertEqual(
                [item["id"] for item in case.configuration.protocol_native_endpoints],
                ["gtv_v95rx", "gtv_d2", "gtv_d95"],
            )
            self.assertEqual(window.protocol_endpoint_table.rowCount(), 3)
            self.assertIn("3 protocol endpoint(s) added", window.eclipse_import_status.text())
            self.assertEqual(
                [window.layer1_rasterisation_roi_selector.itemText(index) for index in range(1, window.layer1_rasterisation_roi_selector.count())],
                ["Target  ·  ROI 1"],
            )
            self.assertEqual(case.configuration.layer1_rasterisation_rois, [{
                "rtstruct_sop_instance_uid": "1.2.826.0.1.3680043.10.543.180", "roi_number": 1,
            }])
            self.assertNotIn("Body", [window.role_widgets["GTV"].itemText(index) for index in range(window.role_widgets["GTV"].count())])
            window.close()

    def test_eclipse_text_mapping_is_deferred_until_target_roles_are_saved(self) -> None:
        """A new case must not report a zero-endpoint success before role mapping."""
        reference = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
D95% [Gy]: 18
D2% [Gy]: 20
Dose [Gy] Volume [%]
0 100
20 0
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "eclipse.txt"
            reference_path.write_text(reference, encoding="utf-8")
            case = ASCENDCase(str(root / "case"), case_id="GENERAL003")
            case.initialise_directories()
            _attach_rtstruct(case, root, "Target", "Body")
            window = MainWindow()
            window.controller = ApplicationController(case)
            window.tps_csv.setText(str(reference_path))
            window._pending_eclipse_reference = str(reference_path)
            with patch("ascend.gui.workstation_configuration.QMessageBox.information"):
                mapped = window._prefill_protocol_endpoints()
            self.assertFalse(mapped)
            self.assertEqual(case.configuration.tps_metrics_csv, str(reference_path))
            self.assertEqual(case.configuration.protocol_native_endpoints, [])
            self.assertEqual(window._pending_eclipse_reference, str(reference_path))
            self.assertIn("mapping is pending", window.eclipse_import_status.text())
            self.assertNotIn("Mapped 0", window.eclipse_import_status.text())
            window.close()

    def test_dvh_verified_rois_prefill_downstream_selectors_without_layer1_result(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            case = ASCENDCase(str(root / "case"), case_id="DROPDOWN001")
            case.initialise_directories()
            _attach_rtstruct(case, root, "Target", "Optic_Chiasm", "Mandible", "Body")
            rtstruct_uid = "1.2.826.0.1.3680043.10.543.180"
            case.configuration.dvh_verified_rois = [
                {
                    "rtstruct_sop_instance_uid": rtstruct_uid,
                    "roi_number": roi_number,
                    "display_name": name,
                    "dvh_structure_name": name,
                    "dvh_verification_status": "verified",
                    "required_endpoints": ["D2", "D95"],
                    "supplied_endpoints": ["D2", "D95"],
                    "binding_method": "unique_normalised_structure_name",
                    "source_content_hashes": [f"hash-{roi_number}"],
                }
                for roi_number, name in ((2, "Optic_Chiasm"), (3, "Mandible"))
            ]
            window = MainWindow()
            window.controller = ApplicationController(case)
            window._load_role_options()

            expected = ["Optic_Chiasm  ·  ROI 2", "Mandible  ·  ROI 3"]
            for selector in (
                window.oar_roi_selector,
                window.layer31c_oar_selector,
                window.layer31_roi_selector,
            ):
                self.assertEqual(
                    [selector.itemText(index) for index in range(1, selector.count())],
                    expected,
                )
                self.assertEqual(selector.currentIndex(), 0)
                self.assertIsNone(selector.currentData())
                self.assertFalse(any("Body" in selector.itemText(index) for index in range(selector.count())))

            window.oar_roi_selector.setCurrentIndex(2)
            window.layer31c_oar_selector.setCurrentIndex(1)
            window.layer31_roi_selector.setCurrentIndex(2)
            window._load_role_options()
            self.assertEqual(window.oar_roi_selector.currentData()["roi_number"], 3)
            self.assertEqual(window.layer31c_oar_selector.currentData()["roi_number"], 2)
            self.assertEqual(window.layer31_roi_selector.currentData()["roi_identity"]["roi_number"], 3)
            window.close()

    def test_saving_target_roles_retries_a_deferred_eclipse_mapping(self) -> None:
        """Saving new-case mappings completes a previously deferred text import."""
        reference = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
D95% [Gy]: 18
D2% [Gy]: 20
Dose [Gy] Volume [%]
0 100
20 0
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "eclipse.txt"
            reference_path.write_text(reference, encoding="utf-8")
            case = ASCENDCase(str(root / "case"), case_id="GENERAL003")
            case.initialise_directories()
            _attach_rtstruct(case, root, "Target", "Body")
            window = MainWindow()
            window.controller = ApplicationController(case)
            window._load_configuration()
            window.tps_csv.setText(str(reference_path))
            window._pending_eclipse_reference = str(reference_path)
            window.role_widgets["GTV"].setCurrentText("Target")
            self.assertTrue(window._save_configuration(silent=True))
            self.assertEqual(case.configuration.structure_roles["GTV"], "Target")
            self.assertEqual([item["id"] for item in case.configuration.protocol_native_endpoints], ["gtv_d2", "gtv_d95"])
            self.assertIsNone(window._pending_eclipse_reference)
            self.assertIn("Imported 3 Eclipse record(s)", window.eclipse_import_status.text())
            window.close()

    def test_reference_without_required_d2_and_d95_shows_error_code(self) -> None:
        reference = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
Mean Dose [Gy]: 12
Dose [Gy] Volume [%]
0 100
20 0
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "eclipse.txt"
            reference_path.write_text(reference, encoding="utf-8")
            case = ASCENDCase(str(root / "case"), case_id="GENERAL003")
            case.initialise_directories()
            _attach_rtstruct(case, root, "Target", "Body")
            case.configuration.structure_roles = {"GTV": "Target"}
            window = MainWindow()
            window.controller = ApplicationController(case)
            window.tps_csv.setText(str(reference_path))
            with patch("ascend.gui.workstation_configuration.QMessageBox.critical") as critical:
                mapped = window._prefill_protocol_endpoints()
            self.assertFalse(mapped)
            self.assertEqual(case.configuration.protocol_native_endpoints, [])
            critical.assert_called_once()
            self.assertIn("TPS_DVH_REQUIRED_ENDPOINTS", window.eclipse_import_status.text())
            window.close()

    def test_eclipse_reference_selected_before_import_survives_case_loading(self) -> None:
        """Prevent case configuration loading from erasing the Import-page path."""
        reference = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
D95% [Gy]: 18
D2% [Gy]: 20
Dose [Gy] Volume [%]
0 100
20 0
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "eclipse.txt"
            reference_path.write_text(reference, encoding="utf-8")
            case = ASCENDCase(str(root / "case"), case_id="GENERAL003")
            case.initialise_directories()
            _attach_rtstruct(case, root, "Target", "Body")
            case.configuration.structure_roles = {"GTV": "Target"}
            window = MainWindow()
            window.tps_csv.setText(str(reference_path))
            window._pending_eclipse_reference = str(reference_path)
            window.controller = ApplicationController(case)
            window._after_case_loaded(None)
            self.assertEqual(window.tps_csv.text(), str(reference_path))
            self.assertEqual(case.configuration.tps_metrics_csv, str(reference_path))
            self.assertEqual([item["id"] for item in case.configuration.protocol_native_endpoints], ["gtv_d2", "gtv_d95"])
            window.close()

    def test_dicom_candidates_use_dedicated_tables_outside_the_configuration_form(self) -> None:
        """Keep variable-length RTPLAN evidence visible and structurally readable."""
        case = ASCENDCase("/tmp/ascend-gui-candidate-test", case_id="CANDIDATES")
        case.provenance["dicom_configuration_prefill"] = {
            "status": "available_with_choices",
            "plan_label": "PLAN_A",
            "dose_summation_type": "PLAN",
            "beam_count": 3,
            "fraction_candidates": [{
                "fraction_group_number": 1,
                "fractions": 5,
                "referenced_beam_count": 3,
                "source": "RTPLAN.FractionGroupSequence.NumberOfFractionsPlanned",
            }],
            "prescription_candidates": [{
                "dose_reference_number": 2,
                "dose_gy": 20.0,
                "label": "High dose vertex",
                "referenced_roi_number": 9,
                "dose_reference_type": "TARGET",
                "dose_reference_structure_type": "SITE",
                "source": "RTPLAN.DoseReferenceSequence.TargetPrescriptionDose",
            }],
            "warnings": ["multiple_rtplan_prescriptions_require_role_specific_selection"],
            "delivery_metadata": {
                "status": "available",
                "plan_label": "PLAN_A",
                "beam_count": 1,
                "treatment_beam_count": 1,
                "vmat_arc_count": 1,
                "total_mu_per_fraction": 200.0,
                "total_planned_mu": 1000.0,
                "beam_on_time_seconds_per_fraction": 20.0,
                "notes": ["Beam-on time excludes setup overhead."],
                "beams": [{
                    "beam_number": 1,
                    "beam_name": "ARC_1",
                    "delivery_technique": "VMAT",
                    "fraction_group_numbers": [1],
                    "meterset_mu": 200.0,
                    "beam_dose_gy": 2.0,
                    "mu_per_gy": 100.0,
                    "nominal_energy_mv": 6.0,
                    "dose_rate_mu_per_min": 600.0,
                    "gantry_start_deg": 181.0,
                    "gantry_end_deg": 179.0,
                    "gantry_rotation_direction": "CC",
                    "gantry_rotation_deg": 358.0,
                    "collimator_start_deg": 30.0,
                    "collimator_end_deg": 30.0,
                    "couch_start_deg": 0.0,
                    "couch_end_deg": 0.0,
                    "control_point_count": 3,
                    "beam_on_time_seconds": 20.0,
                }],
            },
        }
        window = MainWindow()
        window.controller = ApplicationController(case)
        window._load_configuration()
        self.assertEqual(window.dicom_candidate_tabs.count(), 2)
        self.assertEqual(window.dicom_fraction_candidates.rowCount(), 1)
        self.assertEqual(window.dicom_fraction_candidates.item(0, 1).text(), "5")
        self.assertEqual(window.dicom_prescription_candidates.rowCount(), 1)
        self.assertEqual(window.dicom_prescription_candidates.item(0, 1).text(), "20.0")
        self.assertIn("multiple rtplan prescriptions", window.dicom_candidate_warnings.detail.text())
        window.refresh()
        self.assertIn("1 VMAT arc", window.layer1_rtplan_summary.text())
        self.assertIn("200 MU/fraction", window.layer1_rtplan_summary.text())
        self.assertEqual(window.layer1_rtplan_beams.rowCount(), 1)
        self.assertEqual(window.layer1_rtplan_beams.item(0, 2).text(), "1")
        self.assertEqual(window.layer1_rtplan_beams.item(0, 5).text(), "100")
        window.close()

    def test_supporting_output_controls_have_explicit_disabled_state(self) -> None:
        window = MainWindow()
        window.supporting_outputs_enabled.setChecked(False)
        self.assertTrue(all(not item.isEnabled() for item in window.supporting_output_checks.values()))
        window.close()

    def test_default_launcher_uses_qt_and_gui_has_no_tk_dependency(self) -> None:
        project = Path(__file__).resolve().parents[1]
        launcher = (project / "run_ascend.py").read_text(encoding="utf-8")
        source = (project / "ascend" / "gui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("from ascend.gui import launch", launcher)
        self.assertNotIn("tkinter", source)
        self.assertIn("PySide6", source)

    def test_application_startup_does_not_require_optional_pdf_import(self) -> None:
        project = Path(__file__).resolve().parents[1]
        script = """
import builtins

original_import = builtins.__import__

def import_without_reportlab(name, *args, **kwargs):
    if name == "reportlab" or name.startswith("reportlab."):
        error = ModuleNotFoundError("No module named 'reportlab'")
        error.name = "reportlab"
        raise error
    return original_import(name, *args, **kwargs)

builtins.__import__ = import_without_reportlab
from ascend.gui import launch
assert callable(launch)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_graph_overview_formats_edge_specific_ipvdr_labels(self) -> None:
        self.assertEqual(GraphCanvas._edge_label({"edge_id": 4, "ipvdr": 9.4601562}), "E4  iPVDR 9.460")
        self.assertEqual(GraphCanvas._edge_label({"edge_id": 5, "ipvdr": None}), "E5  iPVDR —")

    def test_workstation_states_are_normalised_consistently(self) -> None:
        self.assertEqual(canonical_state("completed"), "PASS")
        self.assertEqual(canonical_state("completed_with_warnings"), "WARN")
        self.assertEqual(canonical_state("provisional"), "PROVISIONAL")
        self.assertEqual(canonical_state("outside_validated_scope"), "OUTSIDE SCOPE")
        self.assertEqual(canonical_state("not_implemented"), "NOT IMPLEMENTED")

    def test_result_pages_present_stored_records_without_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            case = ASCENDCase(directory, case_id="GUI_TEST")
            case.layer1_status = "PASS"
            case.configuration.structure_roles = {"GTV": "GTV", "T_L": "PTV"}
            case.configuration.structure_bindings = {
                "GTV": {"rtstruct_sop_instance_uid": "1.2.3", "roi_number": 7, "display_name": "GTV"},
            }
            case.layer2_1.calculation_status = "completed_with_warnings"
            case.layer2_1.interpretation_status = "provisional"
            case.layer2_1.result = {
                "warnings": ["manual_prescription"],
                "harmonised_metrics": [{
                    "metric_id": "mean_peak_dose", "value": 14.25, "units": "Gy",
                    "applicability": "valid", "warnings": [],
                }],
                "supporting_outputs": {
                    "vertex_analysis": {"status": "available", "source": "individual_masks"},
                    "high_dose_coverage_context": {
                        "applicability": "valid", "threshold_95pct_rxh_gy": 19.0,
                        "number_of_vertices": 1, "warnings": [],
                    },
                    "per_vertex_qa": [{
                        "vertex_id": "V01", "v95_rxh_pct": 96.1, "v95_rxh_applicability": "valid",
                        "dmean_gy": 14.25, "d95_gy": 13.1, "dmax_gy": 16.0, "volume_cc": 1.2,
                        "centroid_lps_mm": [0.0, 0.0, 0.0], "local_fwhm_mm": 8.4,
                        "fwhm_axes_mm": {"grid_x": 8.0, "grid_y": 8.4, "grid_z": 8.8},
                        "fwhm_half_max_dose_gy": 8.0, "nearest_vertex_id": None,
                        "nearest_vertex_distance_mm": None,
                    }],
                    "vertex_connections": [],
                    "global_fwhm_summary": {
                        "status": "available", "vertex_count": 1, "average_fwhm_mm": 8.4,
                        "median_fwhm_mm": 8.4, "minimum_fwhm_mm": 8.4, "maximum_fwhm_mm": 8.4,
                        "method": "Synthetic QA summary.",
                    },
                },
                "provenance": {"layer1_result_sha256": "abc"},
            }
            case.layer2_2.calculation_status = "completed"
            case.layer2_2.interpretation_status = "provisional"
            case.layer2_2.result = {
                "vertex_source": "individual_masks",
                "nodes": [
                    {"node": "V01", "centroid_lps_mm": [0, 0, 0], "peak_d50_gy": 14.0},
                    {"node": "V02", "centroid_lps_mm": [10, 5, 0], "peak_d50_gy": 13.5},
                ],
                "edges": [{
                    "edge_id": 1, "nodes": ["V01", "V02"], "length_mm": 11.18,
                    "edge_local_valley_d50_gy": 4.0, "ipvdr": 3.5,
                    "edge_status": "valid", "valid": True,
                }],
                "graph_summary": {"number_of_nodes": 2, "number_of_edges": 1},
                "plan_ipvdr": {"primary_median": 3.5},
            }
            locked_layer21 = deepcopy(case.layer2_1.result)
            locked_layer22 = deepcopy(case.layer2_2.result)
            window = MainWindow()
            window.controller = ApplicationController(case)
            window.refresh()
            self.assertEqual(window.metric_cards["mean_peak_dose"].value.text(), "14.25 Gy")
            self.assertEqual(window.layer21_vertex_table.rowCount(), 1)
            self.assertEqual(window.layer21_vertex_table.item(0, 0).text(), "V01")
            self.assertEqual(window.layer21_vertex_table.item(0, 6).text(), "1.2")
            self.assertEqual(window.layer21_vertex_table.item(0, 7).text(), "8.4")
            self.assertEqual(window.layer21_fwhm_average.text(), "8.40 mm")
            self.assertIn("D95: 13.10 Gy", VerticesQACanvas.hover_text(case.layer2_1.result["supporting_outputs"]["per_vertex_qa"][0]))
            self.assertNotEqual(
                VerticesQACanvas.fwhm_colour(4.0, 4.0, 12.0).name(),
                VerticesQACanvas.fwhm_colour(12.0, 4.0, 12.0).name(),
            )
            self.assertLess(window.layer1_tabs.maximumHeight(), 300)
            self.assertLessEqual(window.layer31_history_table.maximumHeight(), 220)
            self.assertLessEqual(window.layer32_configuration_summary.maximumHeight(), 150)
            self.assertGreater(window.layer21_support.rowCount(), 1)
            supporting_values = [
                window.layer21_support.item(row, 2).text()
                for row in range(window.layer21_support.rowCount())
                if window.layer21_support.item(row, 2)
            ]
            self.assertIn("19", supporting_values)
            self.assertTrue(window.export_supporting_json_button.isEnabled())
            self.assertIn("2 nodes", window.graph_result_summary.text())
            self.assertIn("iPVDR 3.500", GraphCanvas._edge_label(case.layer2_2.result["edges"][0]))
            self.assertEqual(window.vertex_qa_vertex_selector.count(), 3)
            window.vertex_qa_vertex_selector.setCurrentIndex(window.vertex_qa_vertex_selector.findData("V02"))
            self.assertEqual(window.graph_canvas.selected_node_name, "V02")
            self.assertEqual(window.vertices_canvas.selected_vertex_id, "V02")
            self.assertIn("D50", GraphCanvas.node_hover_text(case.layer2_2.result["nodes"][0]))
            self.assertIn("iPVDR", GraphCanvas.edge_hover_text(case.layer2_2.result["edges"][0]))
            self.assertEqual(window.mapping_table.item(0, 3).text(), "7")
            self.assertEqual(case.layer2_1.result, locked_layer21)
            self.assertEqual(case.layer2_2.result, locked_layer22)
            window.close()

    def test_oar_editor_adds_updates_and_removes_identity_bound_rows(self) -> None:
        window = MainWindow()
        identity = {"rtstruct_sop_instance_uid": "1.2.3", "roi_number": 17}
        window.oar_roi_selector.addItem(
            "Heart  ·  ROI 17",
            {**identity, "display_name": "Heart"},
        )
        window.oar_roi_selector.setCurrentIndex(1)
        window.oar_classification_selector.setCurrentIndex(
            window.oar_classification_selector.findData("separate_critical_oar")
        )
        window._add_or_update_oar()
        self.assertEqual(len(window._oar_entries), 1)
        self.assertEqual(
            {key: window._oar_entries[0][key] for key in ("rtstruct_sop_instance_uid", "roi_number")},
            identity,
        )
        self.assertEqual(window._oar_entries[0]["classification"], "separate_critical_oar")
        self.assertEqual(window.oar_table.item(0, 0).text(), "Heart")
        window.oar_classification_selector.setCurrentIndex(
            window.oar_classification_selector.findData("containing_organ")
        )
        window._add_or_update_oar()
        self.assertEqual(len(window._oar_entries), 1)
        self.assertEqual(window._oar_entries[0]["classification"], "containing_organ")
        window.oar_table.selectRow(0)
        window._remove_selected_oar()
        self.assertEqual(window._oar_entries, [])
        window.close()

    def test_supporting_output_table_and_json_export_preserve_stored_payload(self) -> None:
        payload = {
            "high_dose_coverage_context": {
                "applicability": "valid", "covered_vtvh_volume_cc": 12.5,
                "threshold_95pct_rxh_gy": 19.0, "warnings": ["technical_warning"],
            },
            "vertex_analysis": {"status": "available", "configured_or_derived_vertex_count": 4},
        }
        rows = supporting_output_rows(payload)
        self.assertTrue(any(row[2] == "12.5" and row[3] == "cc" for row in rows))
        self.assertTrue(any("technical_warning" in row[4] for row in rows))
        with TemporaryDirectory() as directory:
            case = ASCENDCase(directory, case_id="EXPORT_TEST")
            case.layer2_1.run_id = "L2_1_TEST"
            case.layer2_1.result = {"supporting_outputs": deepcopy(payload)}
            window = MainWindow()
            window.controller = ApplicationController(case)
            window.refresh()
            destination = Path(directory) / "supporting.json"
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "JSON files (*.json)")):
                window._export_supporting_outputs_json()
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), payload)
            self.assertEqual(case.layer2_1.result["supporting_outputs"], payload)
            window.close()

    def test_gui_does_not_rebuild_scientific_supporting_outputs(self) -> None:
        project = Path(__file__).resolve().parents[1]
        source = (project / "ascend" / "gui" / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("build_supporting_outputs", source)
        self.assertNotIn("ascend.scientific", source)
        self.assertNotIn("np.square", source)
        self.assertNotIn("q_map", source)
