"""Layer 3.2 orchestration over immutable ASCEND upstream evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np
from scipy import ndimage

from ascend import __version__
from ascend.dicom.roi import identity_key
from ascend.layer2.graph.service import _geometry
from ascend.layer3.lq.basis import _deterministic_npz
from ascend.layer3.lq.service import Layer31Service
from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.status import CalculationStatus, InterpretationStatus
from ascend.oar.geometry import OARGeometryService
from ascend.scientific.legacy import layer21_validated as handoff
from ascend.scientific.legacy import layer22_validated as graph_validated
from ascend.validation.provenance import base_provenance, canonical_hash, file_hash, run_id

from .metrics import (
    baseline_survival,
    effect_equivalent_dose,
    endpoint_summary,
    final_survival,
    nonlocal_consequence_fields,
    regional_exposure_consequence_summary,
    resize_field,
    sample_line_lps,
)
from .models import (
    LAYER32_ALGORITHM_VERSION,
    LAYER32_ARTIFACT_SCHEMA_VERSION,
    LAYER32_PARAMETER_SET_VERSION,
    LAYER32_SCHEMA_VERSION,
    SOURCE_MODEL,
    parameter_rows,
    resolved_parameters,
)
from .solver import solve_no_uptake


_CURRENT = {CalculationStatus.COMPLETED.value, CalculationStatus.COMPLETED_WITH_WARNINGS.value}


def _crop_slices(mask: np.ndarray, spacing: np.ndarray, margin_mm: float) -> tuple[slice, slice, slice]:
    occupied = np.argwhere(mask)
    if not len(occupied):
        raise ValueError("Layer 3.2 requires a non-empty validated GTV mask.")
    padding = np.ceil(float(margin_mm) / spacing).astype(int)
    lower = np.maximum(occupied.min(axis=0) - padding, 0)
    upper = np.minimum(occupied.max(axis=0) + padding + 1, np.asarray(mask.shape))
    return tuple(slice(int(first), int(second)) for first, second in zip(lower, upper))  # type: ignore[return-value]


def _slice_bounds(slices: tuple[slice, slice, slice]) -> list[list[int]]:
    return [[int(item.start or 0), int(item.stop or 0)] for item in slices]


def _cropped_geometry(geometry: dict[str, Any], slices: tuple[slice, slice, slice]) -> dict[str, Any]:
    z0, y0, x0 = (int(item.start or 0) for item in slices)
    z1, y1, x1 = (int(item.stop or 0) for item in slices)
    offsets = np.asarray(geometry["offsets"], dtype=float)
    origin = (
        geometry["origin"]
        + offsets[z0] * geometry["normal"]
        + x0 * geometry["spacing"][1] * geometry["row_direction"]
        + y0 * geometry["spacing"][0] * geometry["column_direction"]
    )
    return {
        "origin": np.asarray(origin, dtype=float),
        "row_direction": np.asarray(geometry["row_direction"], dtype=float),
        "column_direction": np.asarray(geometry["column_direction"], dtype=float),
        "normal": np.asarray(geometry["normal"], dtype=float),
        "offsets": offsets[z0:z1] - offsets[z0],
        "spacing": np.asarray(geometry["spacing"], dtype=float),
        "shape": (z1 - z0, y1 - y0, x1 - x0),
    }


def _json_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (list(map(float, value)) if isinstance(value, np.ndarray) else list(value) if isinstance(value, tuple) else value)
        for key, value in geometry.items()
    }


def _role_masks(case: ASCENDCase, masks: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray]:
    roles = case.effective_structure_roles
    gtv_name, high_name = roles.get("GTV"), roles.get("VTV_H")
    if not isinstance(gtv_name, str) or gtv_name not in masks:
        raise ValueError("Layer 3.2 requires the current Layer 1-validated GTV mask.")
    selected: dict[str, np.ndarray] = {"GTV": np.asarray(masks[gtv_name], dtype=bool)}
    if isinstance(high_name, str) and high_name in masks:
        selected["VTVH"] = np.asarray(masks[high_name], dtype=bool)
    individuals = roles.get("VTV_H_individual", [])
    if isinstance(individuals, list):
        for index, name in enumerate(individuals, 1):
            if name in masks:
                selected[f"VTVH_{index:02d}"] = np.asarray(masks[name], dtype=bool)
    names, vertex_masks, _source = graph_validated.prepare_vertices(selected)
    return selected["GTV"], dict(zip(names, vertex_masks)), np.logical_or.reduce(vertex_masks)


def _model_shape(native_shape: tuple[int, int, int], spacing: np.ndarray, target: float) -> tuple[int, int, int]:
    result = []
    for count, voxel_spacing in zip(native_shape, spacing):
        if count <= 1:
            result.append(1)
            continue
        physical_extent = max(count - 1, 1) * float(voxel_spacing)
        result.append(max(2, int(math.ceil(physical_extent / target)) + 1))
    return tuple(result)  # type: ignore[return-value]


def _masked_hazard_values(mask: np.ndarray, hazard_crop: np.ndarray, slices: tuple[slice, slice, slice]) -> np.ndarray:
    coordinates = np.argwhere(mask)
    values = np.zeros(len(coordinates), dtype=np.float32)
    starts = np.asarray([item.start or 0 for item in slices], dtype=int)
    stops = np.asarray([item.stop or 0 for item in slices], dtype=int)
    inside = np.all((coordinates >= starts) & (coordinates < stops), axis=1)
    local = coordinates[inside] - starts
    values[inside] = hazard_crop[local[:, 0], local[:, 1], local[:, 2]]
    return values


def _valley_union_mask(
    layer22: dict[str, Any],
    geometry: dict[str, Any],
    gtv_mask: np.ndarray,
    vertex_union: np.ndarray,
) -> np.ndarray:
    """Reconstruct the union of stored 3 mm edge-local valley supports."""
    radius = float(layer22.get("frozen_definitions", {}).get("valley", {}).get("midpoint_sphere_radius_mm", 3.0))
    result = np.zeros(gtv_mask.shape, dtype=bool)
    for edge in layer22.get("edges", []):
        midpoint = np.asarray(edge.get("midpoint_lps_mm"), dtype=float)
        relative = midpoint - geometry["origin"]
        centre_x = float(relative @ geometry["row_direction"] / geometry["spacing"][1])
        centre_y = float(relative @ geometry["column_direction"] / geometry["spacing"][0])
        centre_offset = float(relative @ geometry["normal"])
        x_radius = int(math.ceil(radius / geometry["spacing"][1]))
        y_radius = int(math.ceil(radius / geometry["spacing"][0]))
        x_indices = np.arange(max(0, math.floor(centre_x) - x_radius), min(gtv_mask.shape[2], math.ceil(centre_x) + x_radius + 1))
        y_indices = np.arange(max(0, math.floor(centre_y) - y_radius), min(gtv_mask.shape[1], math.ceil(centre_y) + y_radius + 1))
        z_indices = np.flatnonzero(np.abs(geometry["offsets"] - centre_offset) <= radius + 1.0e-9)
        if not len(x_indices) or not len(y_indices) or not len(z_indices):
            continue
        zz, yy, xx = np.meshgrid(z_indices, y_indices, x_indices, indexing="ij")
        points = (
            geometry["origin"]
            + geometry["offsets"][zz][..., None] * geometry["normal"]
            + xx[..., None] * geometry["spacing"][1] * geometry["row_direction"]
            + yy[..., None] * geometry["spacing"][0] * geometry["column_direction"]
        )
        inside = np.linalg.norm(points - midpoint, axis=-1) <= radius + 1.0e-9
        valid = inside & gtv_mask[zz, yy, xx] & ~vertex_union[zz, yy, xx]
        result[zz[valid], yy[valid], xx[valid]] = True
    return result


def _effect_values(
    p: np.ndarray, q: np.ndarray, mask: np.ndarray, hazard_crop: np.ndarray,
    slices: tuple[slice, slice, slice], alpha: float, beta: float, scaling: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    p_values, q_values = np.asarray(p[mask], dtype=np.float32), np.asarray(q[mask], dtype=np.float32)
    lq = baseline_survival(p_values, q_values, alpha, beta)
    hazard = _masked_hazard_values(mask, hazard_crop, slices)
    final = final_survival(lq, hazard, scaling)
    return p_values, effect_equivalent_dose(lq, alpha, beta), effect_equivalent_dose(final, alpha, beta)


def _graph_field_metrics(
    field: np.ndarray,
    gtv: np.ndarray,
    vertices: dict[str, np.ndarray],
    geometry: dict[str, Any],
    layer22: dict[str, Any],
) -> tuple[dict[str, float], dict[int, dict[str, Any]]]:
    node_d50 = {
        name: float(np.median(field[mask]))
        for name, mask in vertices.items()
    }
    radius = float(layer22.get("frozen_definitions", {}).get("valley", {}).get("midpoint_sphere_radius_mm", 3.0))
    union = np.logical_or.reduce(list(vertices.values()))
    edge_records: dict[int, dict[str, Any]] = {}
    ratios: list[float] = []
    for stored in layer22.get("edges", []):
        edge_id = int(stored["edge_id"])
        first, second = map(str, stored.get("nodes", []))
        midpoint = np.asarray(stored["midpoint_lps_mm"], dtype=float)
        valley_values = graph_validated.sphere_values(midpoint, radius, geometry, field, gtv, union)
        peak = float((node_d50[first] + node_d50[second]) / 2.0)
        valley = float(np.median(valley_values)) if len(valley_values) else None
        ratio = float(peak / valley) if valley is not None and valley > 0 else None
        edge_records[edge_id] = {
            "peak_d50": peak, "valley_d50": valley, "ipvdr": ratio,
            "valley_support_voxels": int(len(valley_values)),
        }
        if ratio is not None:
            ratios.append(ratio)
    summary = {
        "median": float(np.median(ratios)) if ratios else float("nan"),
        "minimum": float(np.min(ratios)) if ratios else float("nan"),
        "maximum": float(np.max(ratios)) if ratios else float("nan"),
    }
    return summary, edge_records


class Layer32Service:
    """Run a provisional non-local effect reinterpretation over stored evidence."""

    algorithm_version = LAYER32_ALGORITHM_VERSION
    schema_version = LAYER32_SCHEMA_VERSION

    def __init__(self) -> None:
        self.layer31_service = Layer31Service()

    @staticmethod
    def _require_dependencies(case: ASCENDCase) -> None:
        dependencies = {
            "Layer 1": case.layer1,
            "Layer 2.2": case.layer2_2,
            "Layer 3.1": case.layer3_1,
        }
        for label, record in dependencies.items():
            if record.calculation_status not in _CURRENT:
                raise ValueError(f"Layer 3.2 requires a current completed {label} result; found {record.calculation_status}.")
        if case.layer2_2.parent_layer1_run_id != case.layer1.run_id:
            raise ValueError("Layer 2.2 does not reference the current Layer 1 run.")
        if case.layer3_1.parent_layer1_run_id != case.layer1.run_id:
            raise ValueError("Layer 3.1 does not reference the current Layer 1 run.")
        if not (case.layer2_2.result or {}).get("edges"):
            raise ValueError("Layer 3.2 requires a stored Layer 2.2 graph with edges.")

    def run(self, case: ASCENDCase) -> LayerRun:
        """Calculate no-uptake fields, graph shifts, spill shells, and OAR summaries."""
        if not case.configuration.layer32_enabled:
            raise ValueError("Layer 3.2 is disabled. Enable the Layer 3.2 research model before running it.")
        self._require_dependencies(case)
        parameters = resolved_parameters(case.configuration.layer32_parameters)
        basis_result, configured_components, history_build = self.layer31_service.build_basis_with_history(case)
        if basis_result.basis is None:
            raise ValueError(basis_result.reason or "Layer 3.1 P/Q basis is unavailable.")
        layer1, _dose, masks = handoff.load_handoff(Path(case.layer1.result_path or "").parent)
        del _dose
        if history_build.history is None:
            raise ValueError(history_build.reason or "Layer 3.1 fraction history is unavailable.")
        basis = basis_result.basis
        stored_basis_hash = str((case.layer3_1.result or {}).get("basis", {}).get("basis_hash") or "")
        if not stored_basis_hash or stored_basis_hash != basis.basis_hash:
            raise ValueError("Layer 3.1 P/Q basis differs from the stored current Layer 3.1 result.")
        gtv, vertex_masks, vertex_union = _role_masks(case, masks)
        stored_node_names = [str(item.get("node")) for item in (case.layer2_2.result or {}).get("nodes", [])]
        if list(vertex_masks) != stored_node_names:
            raise ValueError("Layer 3.2 vertex ordering differs from the frozen Layer 2.2 graph.")
        p_map, q_map = basis.p_map, basis.q_map
        if p_map.shape != gtv.shape or q_map.shape != gtv.shape:
            raise ValueError("Layer 3.1 P/Q and Layer 1 masks do not share one validated geometry.")
        spacing = np.asarray(basis.dose_grid_spacing_mm, dtype=float)
        geometry = _geometry(layer1["manifest"]["validated_geometry"])
        slices = _crop_slices(gtv, spacing, float(parameters["model_domain_margin_mm"]))
        crop_geometry = _cropped_geometry(geometry, slices)
        p_crop = np.asarray(p_map[slices], dtype=np.float32)
        q_crop = np.asarray(q_map[slices], dtype=np.float32)
        gtv_crop = np.asarray(gtv[slices], dtype=bool)
        vertex_crop = {name: np.asarray(mask[slices], dtype=bool) for name, mask in vertex_masks.items()}
        model_shape = _model_shape(p_crop.shape, spacing, float(parameters["model_grid_target_spacing_mm"]))
        model_spacing = tuple(
            float(source_spacing if count <= 1 else (count - 1) * source_spacing / max(target_count - 1, 1))
            for count, source_spacing, target_count in zip(p_crop.shape, spacing, model_shape)
        )
        dose_model = resize_field(p_crop, model_shape, 1).astype(np.float32)
        gtv_model = resize_field(gtv_crop.astype(np.uint8), model_shape, 0).astype(bool)
        if not gtv_model.any():
            raise ValueError("Layer 3.2 model-grid conversion produced an empty GTV mask.")
        history_masks = {"GTV": gtv_model}
        solution = solve_no_uptake(dose_model, model_spacing, gtv_model, parameters, history_masks)
        hazard_crop = resize_field(solution["hazard"], p_crop.shape, 1).astype(np.float32)
        ros_crop = resize_field(solution["concentration"][0], p_crop.shape, 1).astype(np.float32)
        cytokine_crop = resize_field(solution["concentration"][1], p_crop.shape, 1).astype(np.float32)
        lq_crop = baseline_survival(p_crop, q_crop, parameters["alpha_per_gy"], parameters["beta_per_gy2"])
        survival_crop = final_survival(lq_crop, hazard_crop, parameters["nonlocal_scaling"])
        consequence_fields = nonlocal_consequence_fields(hazard_crop, parameters["nonlocal_scaling"])
        scaled_exposure_crop = consequence_fields["scaled_nonlocal_exposure"]
        multiplier_crop = consequence_fields["nonlocal_survival_multiplier"]
        reduction_percent_crop = consequence_fields["additional_modelled_survival_reduction_percent"]
        baseline_effect_crop = effect_equivalent_dose(
            lq_crop, parameters["alpha_per_gy"], parameters["beta_per_gy2"],
        )
        biological_effect_crop = effect_equivalent_dose(
            survival_crop, parameters["alpha_per_gy"], parameters["beta_per_gy2"],
        )
        additional_effect_crop = biological_effect_crop - baseline_effect_crop
        baseline_graph, baseline_edges = _graph_field_metrics(
            baseline_effect_crop, gtv_crop, vertex_crop, crop_geometry, case.layer2_2.result or {},
        )
        biological_graph, biological_edges = _graph_field_metrics(
            biological_effect_crop, gtv_crop, vertex_crop, crop_geometry, case.layer2_2.result or {},
        )
        edge_records: list[dict[str, Any]] = []
        profiles: list[dict[str, Any]] = []
        node_centroids = {
            str(item["node"]): np.asarray(item["centroid_lps_mm"], dtype=float)
            for item in (case.layer2_2.result or {}).get("nodes", [])
        }
        for stored in (case.layer2_2.result or {}).get("edges", []):
            edge_id = int(stored["edge_id"])
            physical = stored.get("ipvdr")
            baseline = baseline_edges[edge_id]
            biological = biological_edges[edge_id]
            shift = biological["ipvdr"] - physical if biological["ipvdr"] is not None and physical is not None else None
            nonlocal_shift = (
                biological["ipvdr"] - baseline["ipvdr"]
                if biological["ipvdr"] is not None and baseline["ipvdr"] is not None else None
            )
            physical_valley = stored.get("edge_local_valley_d50_gy")
            edge_records.append({
                "edge_id": edge_id, "nodes": stored.get("nodes"), "length_mm": stored.get("length_mm"),
                "physical_ipvdr": physical,
                "baseline_lq_effect_equivalent_ipvdr": baseline["ipvdr"],
                "biological_effect_equivalent_ipvdr": biological["ipvdr"],
                "biological_ipvdr_shift": shift,
                "nonlocal_only_ipvdr_shift": nonlocal_shift,
                "physical_valley_d50_gy": physical_valley,
                "biological_valley_effect_equivalent_d50_gy": biological["valley_d50"],
                "valley_effect_shift_gy_equivalent": (
                    biological["valley_d50"] - physical_valley
                    if biological["valley_d50"] is not None and physical_valley is not None else None
                ),
                "physical_peak_d50_gy": stored.get("edge_peak_d50_gy"),
                "biological_peak_effect_equivalent_d50_gy": biological["peak_d50"],
                "interpretation": "signed_shift; negative values indicate biological contrast compression",
            })
            first, second = map(str, stored.get("nodes", []))
            distance, physical_profile = sample_line_lps(p_crop, crop_geometry, node_centroids[first], node_centroids[second])
            _distance, effect_profile = sample_line_lps(biological_effect_crop, crop_geometry, node_centroids[first], node_centroids[second])
            _distance, delta_profile = sample_line_lps(additional_effect_crop, crop_geometry, node_centroids[first], node_centroids[second])
            _distance, hazard_profile = sample_line_lps(hazard_crop, crop_geometry, node_centroids[first], node_centroids[second])
            profiles.append({
                "edge_id": edge_id, "nodes": [first, second], "distance_mm": distance.tolist(),
                "physical_absorbed_dose_gy": physical_profile.tolist(),
                "biological_effect_equivalent_dose_gy": effect_profile.tolist(),
                "additional_model_derived_effect_equivalent_dose_gy": delta_profile.tolist(),
                "cumulative_nonlocal_hazard": hazard_profile.tolist(),
                "purpose": "visualisation_only_not_ipvdr_calculation",
            })
        valid_biological = [item["biological_effect_equivalent_ipvdr"] for item in edge_records if item["biological_effect_equivalent_ipvdr"] is not None]
        physical_median = (case.layer2_2.result or {}).get("plan_ipvdr", {}).get("primary_median")
        biological_median = float(np.median(valid_biological)) if valid_biological else None
        graph_summary = {
            "physical_plan_ipvdr_median": physical_median,
            "baseline_lq_effect_equivalent_ipvdr_median": baseline_graph["median"],
            "biological_effect_equivalent_ipvdr_median": biological_median,
            "biological_ipvdr_shift": biological_median - physical_median if biological_median is not None and physical_median is not None else None,
            "nonlocal_only_ipvdr_shift": biological_median - baseline_graph["median"] if biological_median is not None else None,
            "shift_label": "signed biological iPVDR shift; not assumed to be an uplift",
        }
        gtv_summary = {
            "physical_absorbed_dose": endpoint_summary(p_crop[gtv_crop], "Gy"),
            "baseline_lq_survival": endpoint_summary(lq_crop[gtv_crop], "fraction"),
            "cumulative_nonlocal_mediator_exposure_h": endpoint_summary(hazard_crop[gtv_crop], "dimensionless"),
            "scaled_nonlocal_exposure_sh": endpoint_summary(scaled_exposure_crop[gtv_crop], "log-survival decrement"),
            "nonlocal_survival_multiplier": endpoint_summary(multiplier_crop[gtv_crop], "fraction of LQ baseline retained"),
            "additional_modelled_survival_reduction": endpoint_summary(reduction_percent_crop[gtv_crop], "% relative to LQ baseline"),
            "baseline_lq_effect_equivalent_dose": endpoint_summary(baseline_effect_crop[gtv_crop], "Gy-equivalent"),
            "biological_effect_equivalent_dose": endpoint_summary(biological_effect_crop[gtv_crop], "Gy-equivalent"),
            "additional_model_derived_effect_equivalent_dose": endpoint_summary(additional_effect_crop[gtv_crop], "Gy-equivalent"),
            "final_survival_fraction": endpoint_summary(survival_crop[gtv_crop], "fraction"),
            "cumulative_nonlocal_hazard": endpoint_summary(hazard_crop[gtv_crop], "dimensionless"),
        }
        distance = ndimage.distance_transform_edt(~gtv_crop, sampling=tuple(spacing))
        shells = []
        for lower, upper in ((0.0, 5.0), (5.0, 15.0), (15.0, 30.0)):
            shell = (~gtv_crop) & (distance > lower) & (distance <= upper)
            shells.append({
                "shell_mm": [lower, upper], "voxel_count": int(shell.sum()),
                "physical_absorbed_dose": endpoint_summary(p_crop[shell], "Gy"),
                "biological_effect_equivalent_dose": endpoint_summary(biological_effect_crop[shell], "Gy-equivalent"),
                "additional_model_derived_effect_equivalent_dose": endpoint_summary(additional_effect_crop[shell], "Gy-equivalent"),
                "final_survival_fraction": endpoint_summary(survival_crop[shell], "fraction"),
            })
        voxel_volume_cc = float(np.prod(spacing) / 1000.0)
        regional_records: list[dict[str, Any]] = []

        def append_crop_region(region_id: str, display_name: str, category: str, region_mask: np.ndarray) -> None:
            regional_records.append(regional_exposure_consequence_summary(
                region_id, display_name, category,
                hazard_crop[region_mask], lq_crop[region_mask], survival_crop[region_mask], voxel_volume_cc,
            ))

        append_crop_region("GTV", "GTV", "target", gtv_crop)
        for vertex_name, vertex_mask in vertex_crop.items():
            append_crop_region(f"vertex:{vertex_name}", vertex_name, "vertex", vertex_mask)
        valley_union = _valley_union_mask(
            case.layer2_2.result or {}, crop_geometry, gtv_crop,
            np.asarray(vertex_union[slices], dtype=bool),
        )
        append_crop_region("valley_union", "Layer 2.2 valley-region union", "valley", valley_union)
        for lower, upper in ((0.0, 5.0), (5.0, 10.0)):
            region_mask = (~gtv_crop) & (distance > lower) & (distance <= upper)
            append_crop_region(
                f"peri_gtv_{lower:g}_{upper:g}_mm", f"Peri-GTV {lower:g}–{upper:g} mm", "peri_gtv_shell", region_mask,
            )
        inventory = layer1.get("manifest", {}).get("roi_inventory", [])
        inventory_by_identity = {
            identity_key(item["roi_identity"]): item for item in inventory if item.get("roi_identity")
        }
        oar_records: list[dict[str, Any]] = []
        oar_artifact_masks: dict[str, np.ndarray] = {}
        oar_array_map: list[dict[str, Any]] = []
        for oar_index, configured in enumerate(case.configuration.oar_structures, 1):
            identity = configured.get("roi_identity")
            item = inventory_by_identity.get(identity_key(identity)) if identity else None
            if not item or item.get("rasterisation_status") != "rasterised":
                raise ValueError("Every configured Layer 3.2 OAR must have a current Layer 1-rasterised identity mask.")
            key = str(item.get("canonical_mapping") or "")
            if key not in masks:
                raise ValueError(f"Layer 3.2 OAR mask is missing from the validated archive: {item.get('original_name')}")
            mask = np.asarray(masks[key], dtype=bool)
            array_key = f"OAR_{oar_index:03d}_mask"
            oar_artifact_masks[array_key] = np.asarray(mask[slices], dtype=np.uint8)
            oar_array_map.append({"array_key": array_key, "oar_name": str(configured.get("name") or item.get("original_name")), "roi_identity": identity})
            physical_values, baseline_values, effect_values = _effect_values(
                p_map, q_map, mask, hazard_crop, slices,
                parameters["alpha_per_gy"], parameters["beta_per_gy2"], parameters["nonlocal_scaling"],
            )
            nearest = min(
                ((name, OARGeometryService._minimum_surface_distance(mask, vertex, tuple(spacing))) for name, vertex in vertex_masks.items()),
                key=lambda pair: float("inf") if pair[1] is None else pair[1],
            )
            record = {
                "oar_name": str(configured.get("name") or item.get("original_name")),
                "roi_identity": identity, "classification": configured.get("classification"),
                "nearest_vertex_id": nearest[0], "nearest_vertex_distance_mm": nearest[1],
                "physical_absorbed_dose": endpoint_summary(physical_values, "Gy"),
                "baseline_lq_effect_equivalent_dose": endpoint_summary(baseline_values, "Gy-equivalent"),
                "biological_effect_equivalent_dose": endpoint_summary(effect_values, "Gy-equivalent"),
                "additional_model_derived_effect_equivalent_dose": endpoint_summary(effect_values - baseline_values, "Gy-equivalent"),
                "compliance_assessment": "not_performed",
            }
            oar_records.append(record)
            oar_hazard = _masked_hazard_values(mask, hazard_crop, slices)
            oar_baseline = baseline_survival(
                np.asarray(p_map[mask], dtype=np.float32), np.asarray(q_map[mask], dtype=np.float32),
                parameters["alpha_per_gy"], parameters["beta_per_gy2"],
            )
            oar_final = final_survival(oar_baseline, oar_hazard, parameters["nonlocal_scaling"])
            regional = regional_exposure_consequence_summary(
                f"oar:{identity_key(identity)}", record["oar_name"], "adjacent_oar",
                oar_hazard, oar_baseline, oar_final, voxel_volume_cc,
            )
            in_crop_voxels = int(np.asarray(mask[slices], dtype=bool).sum())
            regional["model_domain_voxel_count"] = in_crop_voxels
            regional["model_domain_fraction"] = float(in_crop_voxels / max(int(mask.sum()), 1))
            regional_records.append(regional)
        assay_observables = {
            "final_ros_like_field": endpoint_summary(ros_crop[gtv_crop], "model concentration"),
            "final_cytokine_like_field": endpoint_summary(cytokine_crop[gtv_crop], "model concentration"),
            "time_history": solution["history"],
            "scope": "model observables; not measured biomarkers or clinical outcomes",
        }
        identifier = run_id("L3_2")
        root = case.root / "derived" / "layer3_2"
        root.mkdir(parents=True, exist_ok=True)
        destination = root / identifier
        temporary = Path(tempfile.mkdtemp(prefix=f".tmp-{identifier}-", dir=root))
        try:
            archive = temporary / "layer3_2_fields.npz"
            _deterministic_npz(archive, {
                "physical_absorbed_dose_gy": p_crop,
                "baseline_lq_survival_fraction": lq_crop,
                "baseline_lq_effect_equivalent_dose_gy": baseline_effect_crop,
                "biological_effect_equivalent_dose_gy": biological_effect_crop,
                "additional_model_derived_effect_equivalent_dose_gy": additional_effect_crop,
                "cumulative_nonlocal_hazard": hazard_crop,
                "scaled_nonlocal_exposure": scaled_exposure_crop,
                "nonlocal_survival_multiplier": multiplier_crop,
                "additional_modelled_survival_reduction_percent": reduction_percent_crop,
                "final_survival_fraction": survival_crop,
                "ros_like_concentration": ros_crop,
                "cytokine_like_concentration": cytokine_crop,
                "gtv_mask": gtv_crop.astype(np.uint8),
                "vertex_union_mask": np.asarray(vertex_union[slices], dtype=np.uint8),
                **oar_artifact_masks,
            })
            warnings = sorted(set(basis.warnings) | {
                "hypothesis_generating_nonlocal_biological_model",
                "no_vascular_uptake_model",
                "model_domain_limited_to_gtv_plus_configured_margin",
                "model_grid_resampled_from_validated_physical_geometry",
                "not_a_clinical_outcome_prediction",
            })
            payload = {
                "schema_version": LAYER32_SCHEMA_VERSION,
                "algorithm_version": LAYER32_ALGORITHM_VERSION,
                "software_version": __version__,
                "run_id": identifier, "parent_layer1_run_id": case.layer1.run_id,
                "parent_layer2_2_run_id": case.layer2_2.run_id,
                "parent_layer3_1_run_id": case.layer3_1.run_id,
                "calculation_status": CalculationStatus.COMPLETED_WITH_WARNINGS.value,
                "interpretation_status": InterpretationStatus.PROVISIONAL.value,
                "scientific_position": "Hypothesis-generating non-local biological-effect reinterpretation",
                "physical_dose_mutated": False,
                "warnings": warnings,
                "field_definitions": {
                    "physical_absorbed_dose": "Immutable accumulated physical dose P(x) from Layer 3.1.",
                    "baseline_survival": "S_LQ(x) = exp[-alpha P(x) - beta Q(x)].",
                    "final_survival": "S(x) = S_LQ(x) exp[-s H(x)].",
                    "cumulative_nonlocal_mediator_exposure_h": (
                        "Time-integrated weighted exposure to the modelled ROS-like and cytokine-like fields. "
                        "Higher values indicate stronger accumulated modelled signalling. Dimensionless; not physical dose, "
                        "measured concentration, toxicity probability or clinical risk. Advanced technical synonym: hazard field."
                    ),
                    "scaled_nonlocal_exposure_sh": "sH(x), the configured non-local log-survival decrement.",
                    "nonlocal_survival_multiplier": "exp[-sH(x)], the fraction of baseline LQ survival retained.",
                    "additional_modelled_survival_reduction": (
                        "100[1-exp(-sH(x))]%, relative reduction from non-local signalling in the configured model; "
                        "not toxicity or cell-killing probability."
                    ),
                    "biological_effect_equivalent_dose": "Single-exposure LQ inversion of final model survival; not absorbed dose.",
                    "additional_model_derived_effect_equivalent_dose": "Final effect-equivalent field minus baseline LQ effect-equivalent field.",
                },
                "comparison_scenarios": [
                    {
                        "scenario": "physical_lq_baseline", "status": "calculated",
                        "label": "Physical/LQ baseline", "definition": "S_LQ(x) = exp[-alpha P(x)-beta Q(x)]",
                    },
                    {
                        "scenario": "nonlocal_no_vascular_sink", "status": "calculated",
                        "label": "Non-local model — no vascular sink", "definition": "S_final(x) = S_LQ(x) exp[-sH(x)]",
                    },
                    {
                        "scenario": "nonlocal_anatomical_vascular_sink", "status": "not_available",
                        "label": "Non-local model — anatomical vascular sink",
                        "reason": "No validated anatomical vessel mask or uptake model is accepted by ASCEND Layer 3.2.",
                    },
                ],
                "model": {
                    "equation": "dC_k/dt = D_k Laplacian(C_k) - lambda_k C_k + E_k(x)",
                    "species": ["ROS-like", "cytokine-like"],
                    "uptake_model": "none", "uptake_coefficient": 0.0,
                    "vascular_geometry_used": False, "immune_scalar_used": False,
                    "parameter_set_version": LAYER32_PARAMETER_SET_VERSION,
                    "parameters": parameters, "parameter_rows": parameter_rows(parameters),
                    "source_model": SOURCE_MODEL,
                    "cfl_stability_limit": solution["cfl_limit"],
                },
                "geometry": {
                    "validated_native_shape_zyx": list(p_map.shape),
                    "validated_native_spacing_zyx_mm": spacing.tolist(),
                    "model_crop_bounds_zyx": _slice_bounds(slices),
                    "model_crop_geometry": _json_geometry(crop_geometry),
                    "model_grid_shape_zyx": list(model_shape),
                    "model_grid_spacing_zyx_mm": list(model_spacing),
                    "interpolation": "linear fields; nearest-neighbour masks; exact target-shape enforcement",
                },
                "graph_summary": graph_summary, "edge_metrics": edge_records,
                "edge_profiles": profiles, "gtv_biological_context": gtv_summary,
                "peri_gtv_spill_shells": shells, "oar_biological_spill": oar_records,
                "modelled_regional_exposure_and_consequence": regional_records,
                "assay_observables": assay_observables,
                "artifacts": {
                    "schema_version": LAYER32_ARTIFACT_SCHEMA_VERSION,
                    "fields_path": str(destination / archive.name),
                    "fields_sha256": file_hash(archive),
                    "array_order": "z, y, x",
                    "oar_mask_arrays": oar_array_map,
                },
                "provenance": {
                    **base_provenance(case.configuration_hash or "", case.layer1.run_id),
                    "layer2_2_run_id": case.layer2_2.run_id,
                    "layer3_1_run_id": case.layer3_1.run_id,
                    "layer3_1_basis_hash": basis.basis_hash,
                    "layer3_2_parameter_hash": canonical_hash(parameters),
                    "frozen_graph_reused": True,
                    "edge_profile_role": "visualisation_only",
                },
            }
            result_path = temporary / "layer3_2_result.json"
            result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            if destination.exists():
                raise ValueError(f"Layer 3.2 run destination already exists: {destination}")
            temporary.rename(destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        final_path = destination / "layer3_2_result.json"
        return LayerRun(
            "layer3_2", CalculationStatus.COMPLETED_WITH_WARNINGS.value,
            InterpretationStatus.PROVISIONAL.value, identifier, case.layer1.run_id,
            str(final_path), payload, warnings,
        )
