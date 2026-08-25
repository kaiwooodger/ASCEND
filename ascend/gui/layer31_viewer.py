"""Read-only 2D/3D presentation of stored Layer 3.1 biological fields."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QWidget

from ascend.gui.layer31_cad_projector import _build_cad_scene_bundle
from ascend.gui.layer31_field_adapter import prepare_layer31_viewer_data
from ascend.gui.layer31_legacy_renderers import BiologicalScene3D, SoftwareBiologicalScene3D
from ascend.gui.layer31_result_widgets import RegionalResultCard, SurvivalContributionBar, SurvivalDistributionCanvas
from ascend.gui.layer31_slice_renderer import BiologicalSliceCanvas, BiologyColorBar
from ascend.gui.layer31_viewer_cad import Layer31CadMixin, _MeshWorker
from ascend.gui.layer31_viewer_models import CADProjectionOptions, CADSceneBundle, Layer31ViewerData
from ascend.gui.layer31_viewer_ui import build_layer31_viewer_ui
from ascend.layer3.spatial_biology import (
    BiologyColorScaleController,
    BiologyViewerState,
    voxel_to_world_lps,
)
from ascend.layer3.visualization import BiologicalMeshResult

__all__ = [
    "BiologicalScene3D",
    "BiologicalSliceCanvas",
    "BiologyColorBar",
    "CADProjectionOptions",
    "CADSceneBundle",
    "Layer31Viewer",
    "Layer31ViewerData",
    "RegionalResultCard",
    "SoftwareBiologicalScene3D",
    "SurvivalContributionBar",
    "SurvivalDistributionCanvas",
    "_build_cad_scene_bundle",
    "prepare_layer31_viewer_data",
]


class Layer31Viewer(Layer31CadMixin, QWidget):
    """Integrated biological-map viewer; it performs display processing only."""

    scenarioRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.data: Layer31ViewerData | None = None
        self.mesh_result: BiologicalMeshResult | None = None
        self.viewer_state = BiologyViewerState()
        self.cad_bundle: CADSceneBundle | None = None
        self._mesh_generation = 0
        self._mesh_workers: set[_MeshWorker] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._mesh_cache: dict[tuple[Any, ...], CADSceneBundle] = {}
        self._display_scales: dict[str, tuple[float, float]] = {}
        self._last_mesh_coverage: float | None = None
        self.crosshair: tuple[int, int, int] | None = None
        self._mesh_timer = QTimer(self)
        self._mesh_timer.setSingleShot(True)
        self._mesh_timer.setInterval(220)
        self._mesh_timer.timeout.connect(self._start_mesh_generation)

        build_layer31_viewer_ui(self)

    def set_data(self, data: Layer31ViewerData) -> None:
        self.data = data
        self.field.clear()
        self.roi.clear()
        self._display_scales.clear()
        self._last_mesh_coverage = None
        self._populate_data_choices(data)
        self._configure_data_capabilities(data)
        self._position_crosshair(data)
        self._configure_quantity_buttons()
        self._configure_scenario_context(data)
        self._update_regional_results()
        self.distribution.set_data(data)
        self._update_comparison_state()
        self._selection_changed()

    def _populate_data_choices(self, data: Layer31ViewerData) -> None:
        preferred_order = [
            "physical_course_dose_gy",
            "negative_log10_survival_MLQ",
            "voxel_survival_MLQ",
            "course_effect_MLQ",
            *[key for key in data.fields if "BED" in key],
            *[key for key in data.fields if "EQD2" in key],
            "LQ_high_dose_warning_mask",
        ]
        seen: set[str] = set()
        for key in preferred_order:
            if key in seen or key not in data.field_metadata:
                continue
            seen.add(key)
            item = data.field_metadata[key]
            self.field.addItem(f"{item['label']} · {item['units']}", key)
        for key, item in data.field_metadata.items():
            if key not in seen:
                self.field.addItem(f"{item['label']} · {item['units']}", key)
        for name in data.masks:
            self.roi.addItem(name, name)

    def _configure_data_capabilities(self, data: Layer31ViewerData) -> None:
        self._configure_cad_overlays()
        has_oars = any(name.startswith("OAR:") for name in data.masks)
        self.anatomy_checks["OAR"].blockSignals(True)
        self.anatomy_checks["OAR"].setEnabled(has_oars)
        self.anatomy_checks["OAR"].setChecked(has_oars)
        self.anatomy_checks["OAR"].blockSignals(False)
        self.show_vertex_centres.setEnabled(bool(data.vertex_centres_lps_mm))
        self.show_neighbour_graph.setEnabled(bool(data.graph_edges_lps_mm))
        if not data.vertex_centres_lps_mm:
            self.show_vertex_centres.setChecked(False)
        if not data.graph_edges_lps_mm:
            self.show_neighbour_graph.setChecked(False)

    def _position_crosshair(self, data: Layer31ViewerData) -> None:
        shape = next(iter(data.fields.values())).shape
        gtv = data.masks.get("Region: Whole GTV")
        if gtv is not None and np.any(gtv):
            coordinates = np.nonzero(gtv)
            self.crosshair = tuple(int(round(float(np.mean(axis)))) for axis in coordinates)
        else:
            self.crosshair = tuple(int(value // 2) for value in shape)
        for orientation, axis in (("axial", 0), ("sagittal", 2), ("coronal", 1)):
            self.sliders[orientation].blockSignals(True)
            self.sliders[orientation].setRange(0, shape[axis] - 1)
            self.sliders[orientation].setValue(shape[axis] // 2)
            self.sliders[orientation].blockSignals(False)

    def _configure_scenario_context(self, data: Layer31ViewerData) -> None:
        branch = data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        scenario = str(branch.get("scenario_id") or "")
        for key, button in self.scenario_buttons.items():
            button.setChecked(key == scenario)
            button.setEnabled(True)
        context = data.result.get("treatment_context") or {}
        history = data.result.get("fraction_history") or {}
        treatment = context.get("treatment_delivery_mode") or context.get("treatment_context") or "Resolved treatment"
        self.context_label.setText(
            f"Treatment: {treatment} · {history.get('number_of_biological_fraction_events', 0)} biological fraction event(s) · "
            "same validated anatomy and navigation across every field."
        )
        self.context_status.setText(str(branch.get("status") or branch.get("calculation_status") or "LOADED").upper())

    def _request_scenario(self, scenario: str) -> None:
        if self.data is None:
            return
        current = str((self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}).get("scenario_id") or "")
        if scenario == current:
            return
        for button in self.scenario_buttons.values():
            button.setEnabled(False)
        self.scenario_note.setText(f"RECALCULATING {scenario} through the Layer 3.1 scientific service…")
        self.scenarioRequested.emit(scenario)

    def _configure_quantity_buttons(self) -> None:
        if self.data is None:
            return
        mapping = {
            "dose": "physical_course_dose_gy",
            "sf_log": "negative_log10_survival_MLQ",
            "sf": "voxel_survival_MLQ",
            "effect": "course_effect_MLQ",
        }
        mapping["bed"] = next((key for key in self.data.fields if "BED" in key), "")
        mapping["eqd2"] = next((key for key in self.data.fields if "EQD2" in key), "")
        self._quantity_fields = mapping
        for key, button in self.quantity_buttons.items():
            available = bool(mapping.get(key) and mapping[key] in self.data.fields)
            button.setEnabled(available)
        default_key = "effect" if mapping.get("effect") in self.data.fields else "bed" if mapping.get("bed") in self.data.fields else "dose"
        if mapping.get(default_key) in self.data.fields:
            self.field.setCurrentIndex(max(self.field.findData(mapping[default_key]), 0))
            self.quantity_buttons[default_key].setChecked(True)

    def _quantity_button_changed(self, key: str, checked: bool) -> None:
        if not checked or not hasattr(self, "_quantity_fields"):
            return
        self.cad_biology_overlay.setChecked(True)
        field = self._quantity_fields.get(key)
        index = self.field.findData(field)
        if index >= 0:
            self.field.setCurrentIndex(index)

    def _sync_quantity_button(self, field: str) -> None:
        if not hasattr(self, "_quantity_fields"):
            return
        for key, mapped in self._quantity_fields.items():
            if mapped == field:
                self.quantity_buttons[key].blockSignals(True)
                self.quantity_buttons[key].setChecked(True)
                self.quantity_buttons[key].blockSignals(False)
                break

    def _slice_changed(self, orientation: str, value: int) -> None:
        if self.data is None:
            return
        current = list(self.crosshair or (0, 0, 0))
        axis = {"axial": 0, "coronal": 1, "sagittal": 2}[orientation]
        current[axis] = int(value)
        self.crosshair = tuple(current)
        self._refresh_views()
        self._update_voxel_chain()

    def _voxel_selected(self, z_index: int, y_index: int, x_index: int) -> None:
        self.crosshair = (z_index, y_index, x_index)
        for orientation, value in (("axial", z_index), ("coronal", y_index), ("sagittal", x_index)):
            self.sliders[orientation].blockSignals(True)
            self.sliders[orientation].setValue(value)
            self.sliders[orientation].blockSignals(False)
        self._refresh_views()
        self._update_voxel_chain()

    def _anatomy_changed(self, _checked: bool = False) -> None:
        if self.data is None:
            return
        current = str(self.roi.currentData() or "")
        categories = {
            "GTV": lambda name: "GTV" in name and "Other" not in name,
            "Vertices": lambda name: "VTV_H" in name or "Vertices" in name,
            "Valleys": lambda name: "VTV_L" in name or "Valleys" in name,
            "OAR": lambda name: name.startswith("OAR:"),
        }
        allowed = [
            name
            for name in self.data.masks
            if any(self.anatomy_checks[key].isChecked() and predicate(name) for key, predicate in categories.items())
        ]
        if current not in allowed and allowed:
            index = self.roi.findData(allowed[0])
            if index >= 0:
                self.roi.setCurrentIndex(index)
        self.show_structures.setChecked(bool(allowed))
        self._refresh_views()
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _focus_region(self, region_id: str) -> None:
        names = {"H": "Region: Vertices", "V": "Region: Valleys", "O": "Region: Other GTV"}
        index = self.roi.findData(names.get(region_id, ""))
        if index >= 0:
            self.show_structures.setChecked(True)
            self.roi.setCurrentIndex(index)

    def _update_regional_results(self) -> None:
        if self.data is None:
            return
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        records = list((branch.get("regional_survival") or {}).get("records", []))
        by_region = {str(item.get("region_id")): item for item in records}
        for region, card in self.regional_cards.items():
            card.set_record(by_region.get(region))
        self.contribution_bar.set_records(records)
        mean_sf = branch.get("mean_tumour_survival_fraction")
        eud = branch.get("tumour_eud_gy")
        sf_text = f"{float(mean_sf):.5g}" if mean_sf is not None else "—"
        eud_text = f"{float(eud):.4g} Gy" if eud is not None else "NOT APPLICABLE"
        self.primary_sf.setText(f"MEAN TUMOUR SF\n{sf_text}")
        self.primary_eud.setText(f"MLQ TUMOUR EUD\n{eud_text}")
        self.whole_tumour_card.setText(f"WHOLE TUMOUR\nMean SF  {sf_text}\nEUD  {eud_text}")

    def _update_comparison_state(self) -> None:
        if self.data is None:
            return
        context = self.data.result.get("treatment_context") or {}
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        mean_sf = branch.get("mean_tumour_survival_fraction")
        eud = branch.get("tumour_eud_gy")
        self.comparison_left.setText(
            "CURRENT VALIDATED COURSE\n\n"
            f"{context.get('treatment_delivery_mode') or context.get('dose_context') or 'Configured treatment'}\n\n"
            f"Mean SF: {float(mean_sf):.5g}\n"
            if mean_sf is not None
            else "CURRENT VALIDATED COURSE\n\nMean SF: —\n"
        )
        if eud is not None:
            self.comparison_left.setText(self.comparison_left.text() + f"EUD: {float(eud):.4g} Gy")
        self.comparison_right.setText(
            "PAIRED COMPARISON COURSE\n\nNOT CONFIGURED\n\n"
            "Direct comparison requires a second hash-verified Layer 3.1 field set on the same validated geometry. ASCEND does not duplicate or infer one course."
        )

    def _refresh_views(self, _value: Any = None) -> None:
        if self.data is None:
            return
        field, roi = str(self.field.currentData()), str(self.roi.currentData())
        visible = self._visible_mask_names()
        for orientation, canvas in self.canvases.items():
            canvas.set_view(
                self.data,
                field,
                self.sliders[orientation].value(),
                roi,
                self.show_structures.isChecked(),
                self.show_warning.isChecked(),
                self._scalar_range(),
                self.crosshair,
                visible,
            )

    def _visible_mask_names(self) -> list[str]:
        if self.data is None:
            return []
        selected: list[str] = []
        preferred = {
            "GTV": ["Region: Whole GTV"],
            "Vertices": ["Region: Vertices"],
            "Valleys": ["Region: Valleys"],
            "OAR": [name for name in self.data.masks if name.startswith("OAR:")],
        }
        for category, names in preferred.items():
            if self.anatomy_checks[category].isChecked():
                selected.extend(name for name in names if name in self.data.masks)
        return selected

    def _scalar_range(self) -> tuple[float, float] | None:
        if self.data is None or self.field.currentData() is None:
            return None
        field_id = str(self.field.currentData())
        if field_id in self._display_scales:
            return self._display_scales[field_id]
        fallback = tuple(self.data.field_metadata[field_id]["display_range"])
        try:
            mode, percentiles, manual = self._colour_scale_request()
            resolved = self._resolve_colour_scale(field_id, mode, percentiles, manual)
        except ValueError:
            return fallback
        result = (float(resolved[0]), float(resolved[1]))
        self._display_scales[field_id] = result
        return result

    def _colour_scale_request(self) -> tuple[str, tuple[float, float], tuple[float, float] | None]:
        mode = self.range_mode.currentIndex()
        if mode == 2:
            manual = (float(self.range_min.text()), float(self.range_max.text()))
            if manual[1] <= manual[0]:
                raise ValueError("INVALID_DISPLAY_RANGE")
            return "MANUAL", (2.0, 98.0), manual
        if mode == 1:
            return "FULL RANGE", (2.0, 98.0), None
        if mode == 3:
            percentiles = (float(self.percentile_min.text()), float(self.percentile_max.text()))
            if not 0.0 <= percentiles[0] < percentiles[1] <= 100.0:
                raise ValueError("INVALID_PERCENTILE_RANGE")
            return "PERCENTILE", percentiles, None
        return "ROBUST", (2.0, 98.0), None

    def _resolve_colour_scale(
        self,
        field_id: str,
        mode: str,
        percentiles: tuple[float, float],
        manual: tuple[float, float] | None,
    ) -> tuple[float, float]:
        if self.data is None:
            raise ValueError("BIOLOGY_FIELD_UNAVAILABLE")
        contract = self.data.spatial_fields.get(field_id)
        if contract is not None:
            return BiologyColorScaleController(mode, percentiles).resolve(contract, roi_mask=None, manual=manual)
        if manual is not None:
            return manual
        selected = self._stable_field_values(field_id)
        if mode == "FULL RANGE":
            return float(np.min(selected)), float(np.max(selected))
        return tuple(map(float, np.percentile(selected, percentiles)))

    def _stable_field_values(self, field_id: str) -> np.ndarray:
        if self.data is None:
            raise ValueError("BIOLOGY_FIELD_UNAVAILABLE")
        values = np.asarray(self.data.fields[field_id], dtype=float)
        stable_mask = np.isfinite(values)
        tumour = self.data.masks.get("Region: Whole GTV")
        if tumour is not None and np.any(tumour):
            stable_mask &= np.asarray(tumour, dtype=bool)
        selected = values[stable_mask]
        if not selected.size:
            raise ValueError("BIOLOGY_FIELD_UNAVAILABLE")
        return selected

    def _apply_range_requested(self, _value: Any = None) -> None:
        if self.field.currentData() is not None:
            self._display_scales.pop(str(self.field.currentData()), None)
        self._selection_changed()

    def _selection_changed(self, _index: int = -1) -> None:
        if self.data is None or self.field.currentData() is None or self.roi.currentData() is None:
            return
        self._refresh_views()
        field = str(self.field.currentData())
        meta = self.data.field_metadata[field]
        self._sync_quantity_button(field)
        low, high = self._scalar_range() or tuple(meta["display_range"])
        actual_range = tuple(meta["display_range"])
        self.colour_bar.set_scale(meta, (float(low), float(high)), (float(actual_range[0]), float(actual_range[1])))
        self._update_field_summary(meta, low, high)
        self._update_biological_map_status(field)
        self._update_voxel_chain()
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _update_field_summary(self, meta: dict[str, Any], low: float, high: float) -> None:
        if self.data is None:
            return
        self.map_help.setText(
            f"{meta['category']}  |  {meta['label']}  |  {meta['equation']}\n"
            f"{meta['interpretation']}  Complete-field colour range: {low:.5g} to {high:.5g} {meta['units']}."
        )
        self.summary_title.setText(str(meta["label"]).upper())
        self.summary_equation.setText(f"Equation\n{meta['equation']}")
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        self.summary_details.setText(
            f"Units: {meta['units']}\nComplete-volume range: {low:.5g} – {high:.5g}\n"
            f"Model: {branch.get('formalism_id') or 'LQ reference'}\nScenario: {branch.get('scenario_id') or 'Not applicable'}\n"
            + "\n".join(self._model_parameter_lines(branch))
            + f"\n\n{meta['interpretation']}"
        )
        warnings = list(branch.get("warnings", []))
        warning_text = "WARNINGS\n" + "\n".join(warnings) if warnings else "No stored model warning for this view."
        self.warning_summary.setText(warning_text)

    @staticmethod
    def _model_parameter_lines(branch: dict[str, Any]) -> list[str]:
        parameters = branch.get("model_parameters") or {}
        definitions = (
            ("alpha_beta_gy", "α/β", "Gy"),
            ("alpha_per_gy", "α", "Gy⁻¹"),
            ("beta_per_gy2", "β", "Gy⁻²"),
            ("delta_per_gy", "δ", "Gy⁻¹"),
            ("repair_half_time", "Repair half-time", str(parameters.get("time_unit") or "")),
        )
        return [f"{label}: {parameters[key]} {units}".rstrip() for key, label, units in definitions if parameters.get(key) is not None]

    def _update_voxel_chain(self) -> None:
        if self.data is None or self.crosshair is None or self.field.currentData() is None:
            return
        z_index, y_index, x_index = self.crosshair
        point = (z_index, y_index, x_index)
        field = str(self.field.currentData())
        displayed = float(self.data.fields[field][point])
        lps = voxel_to_world_lps(np.asarray([[z_index, y_index, x_index]], dtype=float), self.data.geometry)[0]
        lines = [
            f"Position LPS: x {lps[0]:.3f}, y {lps[1]:.3f}, z {lps[2]:.3f} mm",
            f"Grid index: z {z_index}, y {y_index}, x {x_index}",
            f"Displayed: {displayed:.6g} {self.data.field_metadata[field]['units']}",
        ]
        lines.extend(self._stored_voxel_lines(point, field))
        lines.append(f"Region: {self._voxel_region(point)}")
        contract = self.data.spatial_fields.get(field)
        if contract and contract.treatment_components:
            component_names = [
                str(item.get("component_id") or item.get("component_type") or "component") for item in contract.treatment_components
            ]
            lines.append("Treatment components: " + ", ".join(component_names))
        self.voxel_chain.setText("\n".join(lines))

    def _stored_voxel_lines(self, point: tuple[int, int, int], field: str) -> list[str]:
        if self.data is None:
            return []
        definitions = (
            ("physical_course_dose_gy", "Physical course dose", "Gy"),
            ("voxel_survival_MLQ", "MLQ SF", ""),
            ("negative_log10_survival_MLQ", "−log₁₀(SF)", ""),
            ("course_effect_MLQ", "MLQ K", ""),
        )
        lines = [
            self._voxel_field_line(field_id, label, units, point) for field_id, label, units in definitions if field_id in self.data.fields
        ]
        alpha_beta = self.data.field_metadata[field].get("alpha_beta_gy")
        if alpha_beta is not None:
            lines[1:1] = self._tissue_voxel_lines(float(alpha_beta), point)
        return lines

    def _tissue_voxel_lines(self, alpha_beta: float, point: tuple[int, int, int]) -> list[str]:
        if self.data is None:
            return []
        fields: list[tuple[str, str]] = []
        for token, label in (("BED", "s-BED"), ("EQD2", "s-EQD2")):
            field_id = next(
                (
                    key
                    for key, meta in self.data.field_metadata.items()
                    if token in key and meta.get("alpha_beta_gy") is not None and np.isclose(float(meta["alpha_beta_gy"]), alpha_beta)
                ),
                None,
            )
            if field_id is not None:
                fields.append((field_id, label))
        lines = [self._voxel_field_line(key, label, str(self.data.field_metadata[key]["units"]), point) for key, label in fields]
        lines.append(f"Tissue parameter: α/β {alpha_beta:g} Gy")
        return lines

    def _voxel_field_line(
        self,
        field_id: str,
        label: str,
        units: str,
        point: tuple[int, int, int],
    ) -> str:
        if self.data is None:
            return f"{label}: NOT STORED"
        suffix = f" {units}" if units else ""
        return f"{label}: {float(self.data.fields[field_id][point]):.6g}{suffix}"

    def _voxel_region(self, point: tuple[int, int, int]) -> str:
        if self.data is None:
            return "Outside selected tumour regions"
        region_names = ("Region: Vertices", "Region: Valleys", "Region: Other GTV", "Region: Whole GTV")
        return next(
            (name.replace("Region: ", "") for name in region_names if name in self.data.masks and self.data.masks[name][point]),
            "Outside selected tumour regions",
        )

    def _viewer_tab_changed(self, index: int) -> None:
        if index == 1 and self.data is not None:
            self._mesh_timer.start(10)
