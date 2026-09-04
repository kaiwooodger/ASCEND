"""CAD controls, asynchronous scene generation, and exports for Layer 3.1."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ascend.gui.layer31_cad_projector import build_cad_scene_bundle
from ascend.gui.layer31_viewer_models import CADProjectionOptions, CADSceneBundle
from ascend.layer3.spatial_biology import voxel_spacing_zyx_mm, voxel_to_world_lps, world_to_voxel_lps
from ascend.layer3.visualization import BiologicalMeshResult
from ascend.visualization.biology.validation import validate_volume


class _MeshSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class _MeshWorker(QRunnable):
    def __init__(self, generation: int, operation: Any) -> None:
        super().__init__()
        self.generation = generation
        self.operation = operation
        self.signals = _MeshSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.generation, self.operation())
        except Exception as exc:
            self.signals.failed.emit(self.generation, str(exc))


class Layer31CadMixin:
    """Coordinate display-only CAD state and asynchronous scene presentation."""

    def _configure_render_timers(self) -> None:
        self._mesh_timer = QTimer(self)
        self._mesh_timer.setSingleShot(True)
        self._mesh_timer.setInterval(140)
        self._mesh_timer.timeout.connect(self._start_mesh_generation)
        self._opacity_timer = QTimer(self)
        self._opacity_timer.setSingleShot(True)
        self._opacity_timer.setInterval(120)
        self._opacity_timer.timeout.connect(self._apply_cad_opacity)

    def _set_linked_view(self, orientation: str) -> None:
        self.scene.set_view(orientation)
        if orientation in self.canvases:
            self.canvases[orientation].setFocus()

    def _show_cad_controls(self) -> None:
        self.cad_controls_dialog.show()
        self.cad_controls_dialog.raise_()
        self.cad_controls_dialog.activateWindow()

    def _overlay_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.overlay_tab_keys):
            return
        field = getattr(self, "_quantity_fields", {}).get(self.overlay_tab_keys[index])
        field_index = self.field.findData(field)
        if field_index >= 0:
            self.field.setCurrentIndex(field_index)

    def _sync_overlay_tab(self, key: str) -> None:
        if key not in self.overlay_tab_keys:
            return
        self.overlay_tabs.blockSignals(True)
        self.overlay_tabs.setCurrentIndex(self.overlay_tab_keys.index(key))
        self.overlay_tabs.blockSignals(False)

    def _set_overlay_tab_enabled(self, key: str, enabled: bool) -> None:
        if key in self.overlay_tab_keys:
            self.overlay_tabs.setTabEnabled(self.overlay_tab_keys.index(key), enabled)

    def _slice_zoom_requested(self, source: str, factor: float) -> None:
        for orientation, canvas in self.canvases.items():
            if orientation != source:
                canvas.zoom_by(factor)
        self.scene.zoom_by(1.0 / factor)

    def _slice_pan_requested(self, source: str, x_pixels: float, y_pixels: float) -> None:
        for orientation, canvas in self.canvases.items():
            if orientation != source:
                canvas.pan_by(x_pixels, y_pixels)
        self.scene.pan_by(x_pixels, y_pixels)

    def _cad_zoom_requested(self, factor: float) -> None:
        for canvas in self.canvases.values():
            canvas.zoom_by(factor)

    def _cad_pan_requested(self, x_pixels: float, y_pixels: float) -> None:
        for canvas in self.canvases.values():
            canvas.pan_by(x_pixels, y_pixels)

    def _cad_rotation_requested(self, degrees: float) -> None:
        for canvas in self.canvases.values():
            canvas.rotate_by(degrees)

    def _sync_scene_crosshair(self) -> None:
        if self.data is None or self.crosshair is None:
            return
        position = voxel_to_world_lps(np.asarray([self.crosshair], dtype=float), self.data.geometry)[0]
        point = tuple(map(float, position))
        self.viewer_state.selected_world_position_lps = point
        self.scene.set_selected_world_position(point)

    def _zoom_linked_views(self, zoom_in: bool) -> None:
        slice_factor = 1.2 if zoom_in else 1.0 / 1.2
        cad_factor = 1.0 / 1.2 if zoom_in else 1.2
        for canvas in self.canvases.values():
            canvas.zoom_by(slice_factor)
        self.scene.zoom_by(cad_factor)

    def _rotate_linked_views(self, degrees: float) -> None:
        for canvas in self.canvases.values():
            canvas.rotate_by(degrees)
        self.scene.rotate_by(degrees)

    def _fit_linked_views(self) -> None:
        for canvas in self.canvases.values():
            canvas.reset_view()
        self.scene.reset_view()

    def _configure_cad_overlays(self) -> None:
        """Expose stored BED/EQD2 field pairs without creating GUI calculations."""
        if self.data is None:
            return
        grouped: dict[float, dict[str, str]] = {}
        for field_id, meta in self.data.field_metadata.items():
            alpha_beta = meta.get("alpha_beta_gy")
            if alpha_beta is None:
                continue
            record = grouped.setdefault(float(alpha_beta), {})
            if "EQD2" in field_id:
                record["eqd2"] = field_id
            elif "BED" in field_id:
                record["bed"] = field_id
        tumour_alpha_beta = next(
            (
                float(item["assignment"]["alpha_beta_gy"])
                for item in ((self.data.result.get("layer3_1a_conventional_lq") or {}).get("roi_summaries") or [])
                if (item.get("assignment") or {}).get("canonical_role") in {"GTV", "VTV_H", "VTV_L"}
                and (item.get("assignment") or {}).get("alpha_beta_gy") is not None
            ),
            None,
        )
        self.cad_overlay_parameter.blockSignals(True)
        self.cad_overlay_parameter.clear()
        selected_index = 0
        for index, alpha_beta in enumerate(sorted(grouped)):
            record = {**grouped[alpha_beta], "alpha_beta_gy": alpha_beta}
            self.cad_overlay_parameter.addItem(f"α/β {alpha_beta:g} Gy", record)
            if tumour_alpha_beta is not None and np.isclose(alpha_beta, tumour_alpha_beta):
                selected_index = index
        if self.cad_overlay_parameter.count():
            self.cad_overlay_parameter.setCurrentIndex(selected_index)
        self.cad_overlay_parameter.blockSignals(False)
        current = self.cad_overlay_parameter.currentData() or {}
        self.cad_bed_overlay.setEnabled(bool(current.get("bed")))
        self.cad_eqd2_overlay.setEnabled(bool(current.get("eqd2")))
        if not self.cad_bed_overlay.isEnabled():
            self.cad_bed_overlay.setChecked(False)
        if not self.cad_eqd2_overlay.isEnabled():
            self.cad_eqd2_overlay.setChecked(False)
        self._update_cad_metric_cards(self._cad_overlay_field())

    def _cad_overlay_toggled(self, kind: str, checked: bool) -> None:
        """Keep BED and EQD2 overlays individually switchable but non-overlapping."""
        if getattr(self, "_cad_toggle_guard", False):
            return
        self._cad_toggle_guard = True
        try:
            if checked and kind == "bed":
                self.cad_eqd2_overlay.setChecked(False)
            elif checked and kind == "eqd2":
                self.cad_bed_overlay.setChecked(False)
            if checked:
                self.cad_physical_overlay.setChecked(False)
        finally:
            self._cad_toggle_guard = False
        if checked:
            self.cad_biology_overlay.setChecked(True)
            selected = self._cad_overlay_field()
            index = self.field.findData(selected)
            if index >= 0:
                self.field.setCurrentIndex(index)
        elif not self.cad_bed_overlay.isChecked() and not self.cad_eqd2_overlay.isChecked() and not self.cad_physical_overlay.isChecked():
            self.cad_biology_overlay.setChecked(False)
        self._cad_controls_changed()

    def _cad_physical_toggled(self, checked: bool) -> None:
        if getattr(self, "_cad_toggle_guard", False):
            return
        self._cad_toggle_guard = True
        try:
            if checked:
                self.cad_bed_overlay.setChecked(False)
                self.cad_eqd2_overlay.setChecked(False)
        finally:
            self._cad_toggle_guard = False
        if checked:
            self.cad_biology_overlay.setChecked(True)
            index = self.field.findData("physical_course_dose_gy")
            if index >= 0:
                self.field.setCurrentIndex(index)
        elif not self.cad_bed_overlay.isChecked() and not self.cad_eqd2_overlay.isChecked():
            self.cad_biology_overlay.setChecked(False)
        self._cad_controls_changed()

    def _sync_cad_overlay_to_field(self, field_id: str) -> None:
        """Make the shared field selector authoritative for both renderers."""
        if self.data is None or getattr(self, "_cad_toggle_guard", False):
            return
        is_bed = "BED" in field_id
        is_eqd2 = "EQD2" in field_id
        is_physical = field_id == "physical_course_dose_gy"
        if is_bed or is_eqd2:
            key = "bed" if is_bed else "eqd2"
            for index in range(self.cad_overlay_parameter.count()):
                if (self.cad_overlay_parameter.itemData(index) or {}).get(key) == field_id:
                    self.cad_overlay_parameter.blockSignals(True)
                    self.cad_overlay_parameter.setCurrentIndex(index)
                    self.cad_overlay_parameter.blockSignals(False)
                    break
        self._cad_toggle_guard = True
        try:
            self.cad_biology_overlay.setChecked(True)
            self.cad_bed_overlay.setChecked(is_bed)
            self.cad_eqd2_overlay.setChecked(is_eqd2)
            self.cad_physical_overlay.setChecked(is_physical)
        finally:
            self._cad_toggle_guard = False

    def _cad_overlay_field(self) -> str | None:
        self._cad_field_block_reason = None
        if not self.cad_biology_overlay.isChecked():
            return None
        if self.cad_physical_overlay.isChecked():
            selected = "physical_course_dose_gy"
        else:
            record = self.cad_overlay_parameter.currentData()
            if not isinstance(record, dict):
                record = {}
            selected = None
            if self.cad_bed_overlay.isChecked() and record.get("bed"):
                selected = str(record["bed"])
            elif self.cad_eqd2_overlay.isChecked() and record.get("eqd2"):
                selected = str(record["eqd2"])
            elif self.data is not None and self.field.currentData() is not None:
                selected = str(self.field.currentData())
        if self.data is not None and self.field.currentData() is not None:
            if selected and selected in self.data.biological_volumes:
                scope = str(self.data.field_metadata.get(selected, {}).get("tissue_scope") or "")
                oar_region = str(self.cad_region.currentData() or "").startswith("OAR:")
                selected_alpha_beta = self.data.field_metadata.get(selected, {}).get("alpha_beta_gy")
                if oar_region and selected_alpha_beta is not None:
                    assigned_alpha_beta = self.data.roi_alpha_beta_by_mask.get(str(self.cad_region.currentData()))
                    if assigned_alpha_beta is None:
                        self._cad_field_block_reason = "OAR_LQ_PARAMETER_ASSIGNMENT_MISSING"
                        return None
                    if not np.isclose(float(selected_alpha_beta), assigned_alpha_beta):
                        self._cad_field_block_reason = "OAR_LQ_PARAMETER_ASSIGNMENT_MISMATCH"
                        return None
                if scope == "tumour" and oar_region:
                    self._cad_field_block_reason = "TUMOUR_MLQ_FIELD_NOT_VALID_FOR_OAR"
                    return None
                if scope == "normal" and not oar_region:
                    self._cad_field_block_reason = "NORMAL_TISSUE_MLQ_FIELD_NOT_VALID_FOR_TUMOUR"
                    return None
                return selected
        return None

    def _cad_mode_changed(self, _index: int = -1) -> None:
        mode = str(self.cad_mode.currentData() or "VOLUME")
        cutaway = mode == "SLICE"
        iso = mode in {"ISOSURFACE", "COMBINED"}
        for widget in (self.cut_axis, self.cut_offset, self.cut_invert, self.cut_azimuth, self.cut_elevation, self.cut_reset):
            widget.setEnabled(cutaway)
        self.isosurface_thresholds.setEnabled(iso)
        self.viewer_state.display_mode = mode
        self._cad_controls_changed()

    def _reset_cut_plane(self) -> None:
        self.cut_offset.setValue(50)
        self.cut_azimuth.setValue(0)
        self.cut_elevation.setValue(0)
        self.cut_invert.setChecked(False)
        self._cad_controls_changed()

    def _cad_region_changed(self, _index: int = -1) -> None:
        name = str(self.cad_region.currentData() or "Region: Whole GTV")
        self.viewer_state.active_region = name
        if self.data is not None:
            alpha_beta = self.data.roi_alpha_beta_by_mask.get(name)
            if alpha_beta is not None:
                for index in range(self.cad_overlay_parameter.count()):
                    record = self.cad_overlay_parameter.itemData(index) or {}
                    if record.get("alpha_beta_gy") is not None and np.isclose(float(record["alpha_beta_gy"]), alpha_beta):
                        self.cad_overlay_parameter.setCurrentIndex(index)
                        break
            selected = str(self.field.currentData() or "")
            pairs = {
                "voxel_survival_MLQ": "voxel_survival_MLQ_normal_tissue",
                "negative_log10_survival_MLQ": "negative_log10_survival_MLQ_normal_tissue",
                "course_effect_MLQ": "course_effect_MLQ_normal_tissue",
            }
            desired = pairs.get(selected) if name.startswith("OAR:") else next(
                (tumour for tumour, normal in pairs.items() if normal == selected), None
            )
            if desired in self.data.fields:
                field_index = self.field.findData(desired)
                if field_index >= 0:
                    self.field.setCurrentIndex(field_index)
            self._configure_quantity_buttons(preserve_selection=True)
            index = self.roi.findData(name)
            if index >= 0:
                self.roi.setCurrentIndex(index)
        if self.cad_bundle is not None:
            self.cad_bundle.selected_region_name = name
            self._set_scene_bundle(self.cad_bundle, name)

    def _cad_opacity_changed(self, _value: int = 0) -> None:
        self.viewer_state.gtv_opacity = self.gtv_opacity.value() / 100.0
        self.viewer_state.oar_opacity = self.oar_opacity.value() / 100.0
        self.viewer_state.isosurface_opacity = self.iso_opacity.value() / 100.0
        if self.cad_bundle is None:
            return
        self.cad_bundle.gtv_opacity = self.viewer_state.gtv_opacity
        self.cad_bundle.oar_opacity = self.viewer_state.oar_opacity
        self.cad_bundle.isosurface_opacity = self.viewer_state.isosurface_opacity
        self.cad_bundle.volume_opacity = self.viewer_state.isosurface_opacity
        self._opacity_timer.start()

    def _apply_cad_opacity(self) -> None:
        self._opacity_timer.stop()
        if self.cad_bundle is None:
            return
        self._set_scene_bundle(self.cad_bundle, str(self.cad_region.currentData() or ""))

    def _apply_landscape_preset(self) -> None:
        self.cad_show_anatomy.setChecked(True)
        self.anatomy_checks["GTV"].setChecked(True)
        self.anatomy_checks["Vertices"].setChecked(True)
        self.anatomy_checks["OAR"].setChecked(True)
        self.show_vertex_centres.setChecked(bool(self.data and self.data.vertex_centres_lps_mm))
        self.cad_bed_overlay.setChecked(True)
        self.cad_mode.setCurrentIndex(self.cad_mode.findData("COMBINED"))
        self.isosurface_thresholds.setText("P50,P75,P90")
        self.cad_contours.setChecked(True)
        self._cad_controls_changed()

    def _resolved_isosurface_thresholds(self) -> tuple[float, ...]:
        if self.data is None:
            return ()
        field_id = self._cad_overlay_field()
        gtv = self.data.masks.get("Region: Whole GTV")
        if not field_id or gtv is None:
            return ()
        values = np.asarray(self.data.fields[field_id], dtype=float)[np.asarray(gtv, dtype=bool)]
        values = values[np.isfinite(values)]
        if not values.size:
            return ()
        thresholds: list[float] = []
        for token in self.isosurface_thresholds.text().split(",")[:4]:
            value = token.strip().upper()
            try:
                thresholds.append(float(np.percentile(values, float(value[1:]))) if value.startswith("P") else float(value.split()[0]))
            except ValueError:
                continue
        return tuple(sorted(set(item for item in thresholds if np.isfinite(item))))

    def _cad_point_picked(self, x_lps: float, y_lps: float, z_lps: float) -> None:
        if self.data is None:
            return
        point = (float(x_lps), float(y_lps), float(z_lps))
        indices = world_to_voxel_lps(np.asarray([point]), self.data.geometry)[0]
        if not np.isfinite(indices).all():
            return
        shape = np.asarray(next(iter(self.data.fields.values())).shape)
        voxel = np.clip(np.rint(indices).astype(int), 0, shape - 1)
        self.viewer_state.selected_world_position_lps = point
        self.scene.set_selected_world_position(point)
        self._voxel_selected(int(voxel[0]), int(voxel[1]), int(voxel[2]))

    def _cad_mask_names(self) -> tuple[str, ...]:
        if self.data is None or not self.cad_show_anatomy.isChecked():
            return ()
        candidates: list[str] = []
        if self.anatomy_checks["GTV"].isChecked():
            candidates.append("Region: Whole GTV")
        if self.anatomy_checks["Vertices"].isChecked():
            candidates.append("Region: Vertices")
        if self.anatomy_checks["Valleys"].isChecked():
            candidates.append("Region: Valleys")
        if self.anatomy_checks["OAR"].isChecked():
            candidates.extend(
                name
                for name in self.data.masks
                if name.startswith("OAR:") and name.split(":", 1)[-1].strip().upper() not in {"BODY", "EXTERNAL", "BODY-PTV"}
            )
        return tuple(name for name in dict.fromkeys(candidates) if name in self.data.masks and np.asarray(self.data.masks[name]).any())

    def _cad_smoothing(self) -> dict[str, Any]:
        if not self.display_smoothing.isChecked():
            return {"method": "none", "iterations": 0, "lambda": 0.0, "mu": 0.0}
        if self.data is None:
            return {"method": "taubin_non_shrinking", "iterations": 12, "lambda": 0.25, "mu": -0.27}
        return dict(
            (self.data.result.get("visualisation") or {}).get("smoothing")
            or {
                "method": "taubin_non_shrinking",
                "iterations": 12,
                "lambda": 0.25,
                "mu": -0.27,
            }
        )

    def _cad_controls_changed(self, _value: Any = None) -> None:
        if self.data is None:
            return
        record = self.cad_overlay_parameter.currentData() or {}
        bed_available = bool(record.get("bed"))
        eqd2_available = bool(record.get("eqd2"))
        self.cad_bed_overlay.setEnabled(bed_available)
        self.cad_eqd2_overlay.setEnabled(eqd2_available)
        # Never retain a checked state for a field that is absent at the newly
        # selected tissue parameter.  Block signals because this method already
        # owns the single required CAD refresh.
        if not bed_available and self.cad_bed_overlay.isChecked():
            self.cad_bed_overlay.blockSignals(True)
            self.cad_bed_overlay.setChecked(False)
            self.cad_bed_overlay.blockSignals(False)
        if not eqd2_available and self.cad_eqd2_overlay.isChecked():
            self.cad_eqd2_overlay.blockSignals(True)
            self.cad_eqd2_overlay.setChecked(False)
            self.cad_eqd2_overlay.blockSignals(False)
        overlay_field = self._cad_overlay_field()
        self._update_cad_metric_cards(overlay_field)
        if overlay_field:
            index = self.field.findData(overlay_field)
            if index >= 0 and self.field.currentIndex() != index:
                self.field.setCurrentIndex(index)
        if self.tabs.currentIndex() == 0:
            self._mesh_timer.start()

    def _update_cad_metric_cards(self, field_id: str | None) -> None:
        if self.data is None or not field_id or field_id not in self.data.field_metadata:
            for key, card in self.cad_metric_cards.items():
                card.setText(f"{key.upper()}\nNOT STORED")
            return
        meta = self.data.field_metadata[field_id]
        alpha_beta = meta.get("alpha_beta_gy")
        kind = "eqd2" if "EQD2" in field_id else "bed" if "BED" in field_id else None
        summary = next(
            (
                item
                for item in ((self.data.result.get("layer3_1a_conventional_lq") or {}).get("roi_summaries") or [])
                if (item.get("assignment") or {}).get("canonical_role") == "GTV"
                and alpha_beta is not None
                and np.isclose(float((item.get("assignment") or {}).get("alpha_beta_gy", np.nan)), float(alpha_beta))
            ),
            None,
        )
        for key, card in self.cad_metric_cards.items():
            title_text = key.upper()
            metric = f"{kind}_{key}" if kind else ""
            value = (summary.get("metrics") or {}).get(metric) if summary else None
            card.setText(f"{title_text}\n{float(value):.5g} {meta['units']}" if value is not None else f"{title_text}\nNOT STORED")

    def _mesh_key(self) -> tuple[Any, ...]:
        smoothing = self._cad_smoothing()
        return (
            self._cad_mask_names(),
            self._cad_overlay_field(),
            self._scalar_range(),
            str(self.cad_region.currentData() or "Region: Whole GTV"),
            str(self.cad_mode.currentData() or "SURFACE"),
            self.cut_axis.currentText().lower(),
            self.cut_offset.value(),
            self.cut_invert.isChecked(),
            self.cut_azimuth.value(),
            self.cut_elevation.value(),
            self._resolved_isosurface_thresholds(),
            self.show_vertex_centres.isChecked(),
            self.show_neighbour_graph.isChecked(),
            self.cad_contours.isChecked(),
            str(self.volume_opacity_preset.currentData() or "biological_effect"),
            (
                str(smoothing.get("method", "none")),
                int(smoothing.get("iterations", 0)),
                float(smoothing.get("lambda", 0.0)),
                float(smoothing.get("mu", 0.0)),
            ),
        )

    def _start_mesh_generation(self) -> None:
        if self.data is None:
            return
        anatomy_names = self._cad_mask_names()
        overlay_field = self._cad_overlay_field()
        if not anatomy_names and not overlay_field:
            self.scene.clear()
            self.mesh_status.setText("CAD display is off. Enable Anatomical CAD, s-BED overlay, or s-EQD2 overlay.")
            self.export_button.setEnabled(False)
            return
        key = self._mesh_key()
        if key in self._mesh_cache:
            self._apply_mesh_result(self._mesh_cache[key], cached=True)
            return
        if key in self._mesh_worker_keys.values():
            self.mesh_status.setText("BUILDING — waiting for the current matching 3D scene request.")
            return
        settings = self._cad_smoothing()
        scalar_range = self._scalar_range()
        data = self.data
        mode = str(self.cad_mode.currentData() or "SURFACE")
        cut_axis = self.cut_axis.currentText().lower()
        cut_fraction = self.cut_offset.value() / 100.0
        cut_inverted = self.cut_invert.isChecked()
        cut_azimuth = float(self.cut_azimuth.value())
        cut_elevation = float(self.cut_elevation.value())
        thresholds = self._resolved_isosurface_thresholds()
        opacity_preset = str(self.volume_opacity_preset.currentData() or "biological_effect")
        show_contours = self.cad_contours.isChecked()
        gtv_opacity = self.gtv_opacity.value() / 100.0
        oar_opacity = self.oar_opacity.value() / 100.0
        iso_opacity = self.iso_opacity.value() / 100.0
        self._mesh_generation += 1
        generation = self._mesh_generation
        self.mesh_status.setText("BUILDING — validated anatomical surfaces and biological overlay are generated outside the GUI thread.")
        self.export_button.setEnabled(False)
        projection_options = CADProjectionOptions(
            smoothing=settings,
            scalar_range=scalar_range,
            display_mode=mode,
            cut_axis=cut_axis,
            cut_fraction=cut_fraction,
            cut_inverted=cut_inverted,
            cut_azimuth_degrees=cut_azimuth,
            cut_elevation_degrees=cut_elevation,
            isosurface_thresholds=thresholds,
            show_contours=show_contours,
            gtv_opacity=gtv_opacity,
            oar_opacity=oar_opacity,
            isosurface_opacity=iso_opacity,
            show_vertex_centres=self.show_vertex_centres.isChecked(),
            show_graph=self.show_neighbour_graph.isChecked(),
            selected_region_name=str(self.cad_region.currentData() or "Region: Whole GTV"),
            volume_opacity_preset=opacity_preset,
        )
        worker = _MeshWorker(
            generation,
            lambda: build_cad_scene_bundle(data, anatomy_names, overlay_field, projection_options),
        )
        worker.cache_key = key
        self._mesh_workers.add(worker)
        self._mesh_worker_keys[generation] = key
        worker.signals.finished.connect(self._mesh_finished)
        worker.signals.failed.connect(self._mesh_failed)
        self._thread_pool.start(worker)

    def _mesh_finished(self, generation: int, result: CADSceneBundle) -> None:
        self._mesh_workers = {item for item in self._mesh_workers if item.generation != generation}
        key = self._mesh_worker_keys.pop(generation, None)
        if key is not None:
            self._mesh_cache[key] = result
        if key != self._mesh_key():
            return
        self._apply_mesh_result(result, cached=False)

    def _set_scene_bundle(self, bundle: CADSceneBundle, focused_name: str) -> bool:
        try:
            self.scene.set_bundle(bundle, focused_name)
            return True
        except Exception as exc:
            self.scene.clear()
            self.export_button.setEnabled(False)
            self.mesh_status.setText(f"FAILED — BIOLOGICAL_RENDERER_INITIALISATION_FAILED: {exc}")
            return False

    def _apply_mesh_result(self, result: CADSceneBundle, *, cached: bool) -> None:
        result.gtv_opacity = self.gtv_opacity.value() / 100.0
        result.oar_opacity = self.oar_opacity.value() / 100.0
        result.isosurface_opacity = self.iso_opacity.value() / 100.0
        result.volume_opacity = result.isosurface_opacity
        self.cad_bundle = result
        available = bool(result.anatomy_meshes or result.overlay_mesh or result.special_meshes or result.biological_volume)
        self.mesh_result = (
            result.overlay_mesh or next(iter(result.special_meshes.values()), None) or next(iter(result.anatomy_meshes.values()), None)
        )
        self.export_button.setEnabled(available)
        if not available:
            self.scene.clear()
            self.mesh_status.setText(
                "3D visualisation unavailable: no selected anatomical or biological surface passed display QC. Numerical results remain valid."
            )
            return
        if not self._set_scene_bundle(result, str(self.cad_region.currentData() or "")):
            return
        self._update_mesh_completion_status(result, cached)
        presented = self._present_scalar_surface(result) or self._present_biological_volume(result)
        if not presented:
            self._present_anatomy_or_blocked_overlay(result)
        if result.overlay_field_id:
            self._update_biological_map_status(result.overlay_field_id)

    def _update_mesh_completion_status(self, result: CADSceneBundle, cached: bool) -> None:
        vertex_count = self._displayed_vertex_count(result)
        overlay = result.overlay_label or "OFF"
        failures = f" · {len(result.failures)} unavailable surface(s)" if result.failures else ""
        state = "CACHED" if cached else "COMPLETED"
        smoothing = "ON" if result.smoothing_enabled else "OFF"
        self.mesh_status.setText(
            f"{state} · {len(result.anatomy_meshes)} anatomical surface(s) · "
            f"mode {result.mode} · overlay {overlay} · {vertex_count} displayed vertices · DICOM patient LPS · "
            f"display smoothing {smoothing}{failures} · scientific voxel fields unchanged."
        )

    @staticmethod
    def _displayed_vertex_count(result: CADSceneBundle) -> int:
        vertex_count = sum(
            len(item.display_surface.vertices_lps_mm) for item in result.anatomy_meshes.values() if item.display_surface is not None
        )
        if result.overlay_mesh and result.overlay_mesh.display_surface is not None:
            vertex_count += len(result.overlay_mesh.display_surface.vertices_lps_mm)
        vertex_count += sum(
            len(item.display_surface.vertices_lps_mm) for item in result.special_meshes.values() if item.display_surface is not None
        )
        return vertex_count

    def _present_scalar_surface(self, result: CADSceneBundle) -> bool:
        scalar_mesh = result.overlay_mesh or next(iter(result.special_meshes.values()), None)
        if scalar_mesh is None or scalar_mesh.display_surface is None:
            return False
        values = np.asarray(scalar_mesh.display_surface.scalar_values, dtype=float)
        finite = values[np.isfinite(values)]
        value_range = (
            f"{float(finite.min()):.4g}–{float(finite.max()):.4g} {result.overlay_units}" if finite.size else "no valid surface samples"
        )
        qc = scalar_mesh.qc
        coverage = float(qc.get("mesh_coverage_percent", qc.get("scalar_sampling_coverage_percent", 0.0)))
        self._last_mesh_coverage = coverage
        alignment = str(qc.get("mesh_alignment_status") or ("GREEN" if coverage >= 99.0 else "AMBER"))
        median = qc.get("median_sampling_distance_mm")
        maximum = qc.get("maximum_sampling_distance_mm")
        distance_text = self._sampling_distance_text(median, maximum)
        self.cad_legend.setText(
            f"{result.mode.title()} · {result.overlay_label} · surface range {value_range} · ten fixed scalar bands. "
            f"FIELD VALIDATED | MESH {alignment} | COVERAGE {coverage:.2f}% | {distance_text} | PATIENT LPS | mm. "
            "Invalid samples are NaN, never zero. Smoothing affects display vertices only."
        )
        self._sync_surface_colour_bar(result)
        return True

    @staticmethod
    def _sampling_distance_text(median: Any, maximum: Any) -> str:
        if median is None or maximum is None:
            return "sampling distance unavailable"
        return f"median {float(median):.3g} mm · max {float(maximum):.3g} mm"

    def _sync_surface_colour_bar(self, result: CADSceneBundle) -> None:
        if self.data is None or result.overlay_field_id not in self.data.field_metadata:
            return
        meta = self.data.field_metadata[result.overlay_field_id]
        actual = tuple(meta["display_range"])
        display = self._scalar_range() or actual
        self.colour_bar.set_scale(meta, display, actual)

    def _present_biological_volume(self, result: CADSceneBundle) -> bool:
        volume = result.biological_volume
        if volume is None or not result.overlay_field_id:
            return False
        report = validate_volume(volume)
        self.cad_legend.setText(
            f"{result.mode.title()} · {result.overlay_label} · true volume range "
            f"{report.diagnostics['true_minimum']:.4g}–{report.diagnostics['true_maximum']:.4g} {result.overlay_units}. "
            f"Grid {volume.geometry.dimensions_xyz} · spacing "
            f"{tuple(np.round(volume.geometry.spacing_mm, 4))} mm · DICOM patient LPS. "
            "Masked voxels are unavailable, never zero; percentage isosurface thresholds are visualisation-only."
        )
        return True

    def _present_anatomy_or_blocked_overlay(self, result: CADSceneBundle) -> None:
        if not result.overlay_field_id:
            block_reason = getattr(self, "_cad_field_block_reason", None)
            if block_reason:
                self.cad_legend.setText(
                    f"BIOLOGICAL MAP BLOCKED — {block_reason}. OAR maps require the corresponding declared tissue parameters; tumour or mismatched fields are not substituted."
                )
                return
            self.cad_legend.setText(
                "Biological overlay OFF · showing validated anatomical masks only. Every configured OAR retains its distinct deterministic colour."
            )
            return
        reason = "; ".join(f"{key}: {value}" for key, value in result.failures.items()) or "BIOLOGY_FIELD_UNAVAILABLE"
        self.mesh_status.setText(
            f"BLOCKED — requested biological map was not rendered: {reason}. Neutral anatomy only; no fallback field was substituted."
        )
        self.cad_legend.setText(
            "BIOLOGICAL MAP BLOCKED | INVALID VALUES REMAIN NaN | no fallback to physical dose, BED, EQD2, nearest-neighbour, or zero."
        )

    def _update_biological_map_status(self, field_id: str) -> None:
        if self.data is None or field_id not in self.data.field_metadata:
            return
        meta = self.data.field_metadata[field_id]
        volume = self.data.biological_volumes.get(field_id)
        shape = np.asarray(self.data.fields[field_id]).shape
        valid = int(np.count_nonzero(np.isfinite(self.data.fields[field_id])))
        vertex = int(np.count_nonzero(self.data.masks.get("Region: Vertices", np.zeros(shape, bool))))
        valley = int(np.count_nonzero(self.data.masks.get("Region: Valleys", np.zeros(shape, bool))))
        if volume is not None:
            spacing = tuple(map(float, volume.geometry.spacing_mm))
            components = ", ".join(volume.treatment_components) or "Not declared"
            model = str(volume.metadata.get("model_name") or meta.get("category") or "Stored Layer 3.1 model")
        else:
            spacing = tuple(map(float, voxel_spacing_zyx_mm(self.data.geometry)[::-1]))
            components = "Not declared"
            model = str(meta.get("category") or "Stored Layer 3.1 model")
        coverage = f"{self._last_mesh_coverage:.2f}%" if self._last_mesh_coverage is not None else "N/A"
        self.biological_map_status.setText(
            "BIOLOGICAL MAP STATUS\n"
            f"Endpoint: {meta.get('label')}\nModel: {model}\nTissue: {self.roi.currentData() or 'N/A'}\n"
            f"Treatment components: {components}\nGrid z,y,x: {shape}\nSpacing x,y,z: {spacing} mm\n"
            f"Valid voxels: {valid}\nVertex voxels: {vertex}\nValley voxels: {valley}\n"
            f"Mesh sample validity: {coverage}\nRendering: PASS"
        )

    def _mesh_failed(self, generation: int, message: str) -> None:
        self._mesh_workers = {item for item in self._mesh_workers if item.generation != generation}
        key = self._mesh_worker_keys.pop(generation, None)
        if key != self._mesh_key():
            return
        self.scene.clear()
        self.mesh_result = None
        self.cad_bundle = None
        self.export_button.setEnabled(False)
        self.mesh_status.setText(f"FAILED — 3D visualisation unavailable: {message}. Numerical results remain valid.")

    def _export(self) -> None:
        bundle = getattr(self, "cad_bundle", None)
        if bundle is None or not (bundle.anatomy_meshes or bundle.overlay_mesh or bundle.special_meshes):
            return
        folder = QFileDialog.getExistingDirectory(self, "Export Layer 3.1 biological surface")
        if not folder:
            return
        target = Path(folder)
        copied = []
        groups: list[tuple[str, BiologicalMeshResult]] = [(f"anatomy_{name}", mesh) for name, mesh in bundle.anatomy_meshes.items()]
        if bundle.overlay_mesh is not None:
            groups.append((f"overlay_{bundle.overlay_field_id}", bundle.overlay_mesh))
        groups.extend((f"special_{label}", mesh) for label, mesh in bundle.special_meshes.items())
        for label, mesh in groups:
            safe = "".join(character if character.isalnum() else "_" for character in label).strip("_")
            for key in ("raw_stl", "stl", "vtp", "metadata"):
                source = mesh.artifacts.get(key)
                if source:
                    destination = target / f"layer31_{safe}_{key}{Path(source).suffix}"
                    shutil.copy2(source, destination)
                    copied.append(destination.name)
        QMessageBox.information(self, "ASCEND Layer 3.1 export", f"Exported {len(copied)} files.")

    def _export_screenshot(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export Spatial Biology Viewer", "layer31_spatial_biology.png", "PNG image (*.png)"
        )
        if not path:
            return
        native_window = getattr(self.scene, "window", None)
        if callable(native_window):
            native_window = None
        screen = native_window.screen() if native_window is not None else None
        image = screen.grabWindow(int(native_window.winId())) if screen is not None else self.scene.grab()
        if image.isNull() or not image.save(path, "PNG"):
            QMessageBox.warning(self, "ASCEND Layer 3.1 export", "PNG_EXPORT_FAILED")
            return
        QMessageBox.information(self, "ASCEND Layer 3.1 export", "Exported the current quantitative 3D view as PNG.")
