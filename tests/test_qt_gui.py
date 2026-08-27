from __future__ import annotations

import os
import json
from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractButton, QApplication, QFileDialog, QLineEdit, QPushButton, QSizePolicy, QTextEdit

from ascend.app.controller import ApplicationController
from ascend import __release_name__, __release_series__, __validation_scope__, __version__
from ascend.gui.main_window import GraphCanvas, MainWindow, supporting_output_rows
from ascend.gui.layer31_viewer import Layer31Viewer, RegionalResultCard, SurvivalContributionBar
from ascend.gui.layer32_viewer import Layer32ProfileCanvas
from ascend.gui.theme import canonical_state
from ascend.models.case import ASCENDCase


class QtGuiTests(unittest.TestCase):
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
        self.assertEqual(window.pages.count(), 10)
        self.assertIn("ASCEND 1.4.0", window.windowTitle())
        self.assertEqual(window.navigation.count(), 14)
        buttons = [item.text() for item in window.pages.widget(5).findChildren(QPushButton)]
        self.assertIn("Run Layer 2.2", buttons)
        biological_buttons = [item.text() for item in window.pages.widget(6).findChildren(QPushButton)]
        self.assertIn("Run complete Layer 3.1", biological_buttons)
        self.assertIn("Open unified spatial viewer", biological_buttons)
        self.assertEqual(window.layer31_tabs.count(), 7)
        self.assertTrue(any("3.1D TCP" in window.layer31_tabs.tabText(index) for index in range(window.layer31_tabs.count())))
        self.assertEqual(
            [window.layer31_tabs.tabText(index) for index in range(window.layer31_tabs.count())],
            [
                "1–9 Configure / run", "10–13 Map", "14 Whole-tumour SF / EUD",
                "15 Regional explanation", "16 Scenarios / therapeutic ratio", "3.1D TCP", "17 Provenance / export",
            ],
        )
        if sys.platform == "darwin":
            viewer = Layer31Viewer()
            self.assertEqual(type(viewer.scene).__name__, "PyVistaBiologicalScene3D")
            self.assertTrue(callable(viewer.scene.window))
        self.assertIn("1 Prepare case", window.layer31_workflow_order.text())
        self.assertIn("17 Audit provenance/export", window.layer31_workflow_order.text())
        self.assertEqual(window.layer31_status_pill.text(), "NOT RUN")
        layer32_buttons = [item.text() for item in window.pages.widget(7).findChildren(QPushButton)]
        self.assertIn("Run Layer 3.2", layer32_buttons)
        self.assertIn("Build / refresh 3D biological field viewer", layer32_buttons)
        self.assertFalse(window.layer32_enabled.isChecked())
        self.assertFalse(window.layer32_run_button.isEnabled())
        self.assertFalse(window.layer32_scaling.isEnabled())
        window.layer32_enabled.setChecked(True)
        self.assertTrue(window.layer32_run_button.isEnabled())
        self.assertTrue(window.layer32_scaling.isEnabled())
        window.layer32_enabled.setChecked(False)
        self.assertFalse(any("vascular" in item.placeholderText().lower() for item in window.pages.widget(7).findChildren(QLineEdit)))
        import_buttons = [item.text() for item in window.pages.widget(0).findChildren(QPushButton)]
        configuration_buttons = [item.text() for item in window.pages.widget(1).findChildren(QPushButton)]
        self.assertIn("Browse Eclipse file", import_buttons)
        self.assertIn("Browse Eclipse folder", import_buttons)
        self.assertEqual(window.layer1_tabs.count(), 3)
        self.assertEqual(window.layer21_tabs.count(), 4)
        toolbox_buttons = [
            item for item in window.layer21_tabs.findChildren(QAbstractButton)
            if item.metaObject().className() == "QToolBoxButton"
        ]
        self.assertEqual(len(toolbox_buttons), 4)
        self.assertTrue(all(item.minimumHeight() >= 38 for item in toolbox_buttons))
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

    def test_release_identity_is_the_140_responsive_spatial_workstation(self) -> None:
        self.assertEqual(__version__, "1.4.0")
        self.assertEqual(__release_series__, "ASCEND 1.4.x")
        self.assertEqual(__release_name__, "Responsive spatial radiobiology workstation")
        self.assertIn("not clinically validated", __validation_scope__)

    def test_layer31_presets_are_locked_and_normal_kinetics_are_not_inferred(self) -> None:
        window = MainWindow()
        self.assertEqual(window.layer31_high_dose_criterion.currentData(), "not_configured")
        self.assertFalse(window.layer31_high_dose_threshold.isEnabled())
        self.assertFalse(window.layer31_tr_fraction_count.isEnabled())

        window.layer31_tumour_scenario.setCurrentText("C1")
        tumour = window.layer31_tumour_kinetics
        self.assertEqual(tumour["alpha_beta_gy"].text(), "10.0")
        self.assertEqual(tumour["sf2"].text(), "0.3")
        self.assertEqual(tumour["delta_per_gy"].text(), "0.15")
        self.assertEqual(tumour["repair_half_time"].text(), "60.0")
        self.assertTrue(tumour["parameter_source"].isReadOnly())
        self.assertIn("Zhang H", tumour["parameter_source"].text())

        window.layer31_normal_scenario.setCurrentText("N1")
        normal = window.layer31_normal_kinetics
        self.assertEqual(normal["alpha_beta_gy"].text(), "3.1")
        self.assertEqual(normal["sf2"].text(), "0.3")
        self.assertEqual(normal["delta_per_gy"].text(), "")
        self.assertEqual(normal["repair_half_time"].text(), "")
        self.assertIn("INCOMPLETE", normal["status"].text())
        preset = normal["kinetic_preset"].findData("zhang_grid_2022")
        normal["kinetic_preset"].setCurrentIndex(preset)
        self.assertEqual(normal["delta_per_gy"].text(), "0.15")
        self.assertEqual(normal["repair_half_time"].text(), "60.0")
        self.assertTrue(normal["parameter_set_id"].isReadOnly())

        window.layer31_tr_enabled.setChecked(True)
        self.assertTrue(window.layer31_tr_fraction_count.isEnabled())
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
        self.assertEqual(viewer.workflow_tabs.count(), 3)
        self.assertLessEqual(viewer.minimumSizeHint().width(), 520)
        self.assertLessEqual(viewer.scene.minimumWidth(), 300)
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
        viewer.data = object()
        viewer.cad_overlay_parameter.addItem("α/β 10 Gy", {"bed": "stored_BED", "eqd2": "stored_EQD2"})
        viewer._sync_cad_overlay_to_field("stored_EQD2")
        self.assertTrue(viewer.cad_eqd2_overlay.isChecked())
        self.assertFalse(viewer.cad_bed_overlay.isChecked())
        viewer._sync_cad_overlay_to_field("physical_course_dose_gy")
        self.assertTrue(viewer.cad_physical_overlay.isChecked())
        self.assertFalse(viewer.cad_eqd2_overlay.isChecked())
        viewer.close()

    def test_layer31_map_tab_does_not_inherit_configuration_page_width(self) -> None:
        window = MainWindow()
        self.assertEqual(window.layer31_tabs.sizePolicy().horizontalPolicy(), QSizePolicy.Preferred)
        window.layer31_tabs.setCurrentIndex(1)
        self.assertEqual(window.layer31_tabs.sizePolicy().horizontalPolicy(), QSizePolicy.Ignored)
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
            window.close()

    def test_eclipse_reference_selected_before_import_survives_case_loading(self) -> None:
        """Prevent case configuration loading from erasing the Import-page path."""
        reference = """Patient ID: GENERAL003
Plan: Plan-C
Total dose [Gy]: 20

Structure: Target
Volume [cc]: 10
D95% [Gy]: 18
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
            case.configuration.structure_roles = {"GTV": "Target"}
            window = MainWindow()
            window.tps_csv.setText(str(reference_path))
            window._pending_eclipse_reference = str(reference_path)
            window.controller = ApplicationController(case)
            window._after_case_loaded(None)
            self.assertEqual(window.tps_csv.text(), str(reference_path))
            self.assertEqual(case.configuration.tps_metrics_csv, str(reference_path))
            self.assertEqual([item["id"] for item in case.configuration.protocol_native_endpoints], ["gtv_d95"])
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
                    }],
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
            self.assertEqual(window.mapping_table.item(0, 3).text(), "7")
            self.assertEqual(case.layer2_1.result, locked_layer21)
            self.assertEqual(case.layer2_2.result, locked_layer22)
            window.close()

    def test_oar_editor_adds_updates_and_removes_identity_bound_rows(self) -> None:
        window = MainWindow()
        identity = {"rtstruct_sop_instance_uid": "1.2.3", "roi_number": 17}
        window.oar_roi_selector.addItem(
            "Heart  ·  ROI 17",
            {"name": "Heart", "display_name": "Heart", "roi_identity": identity},
        )
        window.oar_roi_selector.setCurrentIndex(1)
        window.oar_classification_selector.setCurrentIndex(
            window.oar_classification_selector.findData("separate_critical_oar")
        )
        window._add_or_update_oar()
        self.assertEqual(len(window._oar_entries), 1)
        self.assertEqual(window._oar_entries[0]["roi_identity"], identity)
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
