"""Layer 2.2 spatial-PVDR orchestration over validated native geometry.

The service enforces the locked grid scope, delegates graph and sampling
primitives to the validated module, then persists explicit node, edge, and
provenance evidence for GUI and export adapters.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ascend.models.case import ASCENDCase, LayerRun
from ascend.models.status import CalculationStatus, InterpretationStatus
from ascend.scientific.legacy import layer21_validated as handoff
from ascend.scientific.legacy import layer22_validated as validated
from ascend.validation.provenance import base_provenance, file_hash, run_id


class OutsideValidatedScope(RuntimeError):
    """Identify usable geometry that has not been validated for Layer 2.2."""
    pass


def _geometry(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "origin": np.asarray(value["origin"], dtype=float),
        "row_direction": np.asarray(value.get("row_direction", value.get("row_dir")), dtype=float),
        "column_direction": np.asarray(value.get("column_direction", value.get("col_dir")), dtype=float),
        "normal": np.asarray(value["normal"], dtype=float),
        "offsets": np.asarray(value["offsets"], dtype=float),
        "spacing": np.asarray(value["spacing"], dtype=float),
        "shape": tuple(value["shape"]),
    }


class Layer22Service:
    """Coordinate the locked spatial graph calculation without GUI science."""
    algorithm_version = validated.VERSION

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else Path(validated.__file__).with_name("ascend_lrt_config.json")

    def run(self, case: ASCENDCase) -> LayerRun:
        """Build the nearest-neighbour graph and local midpoint-sphere PVDR."""
        if not case.layer1.result_path or not case.layer1.run_id:
            raise ValueError("Current Layer 1 result is required.")
        layer1_dir = Path(case.layer1.result_path).parent
        layer1, _layer21_dose, masks = handoff.load_handoff(layer1_dir)
        manifest = layer1["manifest"]
        native_dose = manifest.get("validated_native_dose", {})
        native_dose_path = Path(str(native_dose.get("path", "")))
        if not native_dose_path.is_file() or file_hash(native_dose_path) != native_dose.get("sha256"):
            raise ValueError("BLOCK_LAYER1_INPUT: full-precision validated native dose is missing or its hash differs.")
        dose = np.load(native_dose_path, allow_pickle=False)
        if dose.ndim != 3 or not np.isfinite(dose).all() or np.any(dose < 0):
            raise ValueError("BLOCK_LAYER1_INPUT: validated native dose must be finite, non-negative, and three-dimensional.")
        geometry_value = manifest.get("validated_geometry")
        if not geometry_value:
            raise ValueError("BLOCK_LAYER1_INPUT: validated physical geometry is absent.")
        geometry = _geometry(geometry_value)
        config = validated.load_config(self.config_path)
        spacing = validated.voxel_spacing_mm(geometry)
        allowed = np.asarray(config["grid"]["validated_isotropic_spacing_mm"], dtype=float)
        isotropy_tolerance = float(config["grid"]["isotropy_tolerance_mm"])
        spacing_tolerance = float(config["grid"]["spacing_tolerance_mm"])
        # Anisotropic or otherwise unvalidated spacing is not corrupt geometry:
        # Layer 1 and Layer 2.1 remain usable while Layer 2.2 reports scope.
        if np.max(np.abs(spacing - spacing[0])) > isotropy_tolerance:
            raise OutsideValidatedScope(f"Anisotropic RTDOSE spacing {spacing.tolist()} mm is outside the validated Layer 2.2 scope.")
        if not np.any(np.abs(allowed - spacing[0]) <= spacing_tolerance):
            raise OutsideValidatedScope(f"RTDOSE spacing {spacing[0]:g} mm is outside validated Layer 2.2 values {allowed.tolist()}.")
        roles = case.effective_structure_roles
        gtv_name = roles.get("GTV")
        high_name = roles.get("VTV_H")
        individual_names = roles.get("VTV_H_individual", [])
        if not isinstance(gtv_name, str) or gtv_name not in masks:
            raise validated.AuditBlock("A non-empty Layer 1-validated GTV mask is required.")
        selected: dict[str, np.ndarray] = {"GTV": masks[gtv_name]}
        if isinstance(high_name, str) and high_name in masks:
            selected["VTVH"] = masks[high_name]
        if isinstance(individual_names, list):
            for index, name in enumerate(individual_names, 1):
                if name not in masks:
                    raise validated.AuditBlock(f"Validated individual vertex mask is missing: {name}")
                selected[f"VTVH_{index:02d}"] = masks[name]
        names, vertex_masks, node_source = validated.prepare_vertices(selected)
        if any(mask.shape != dose.shape for mask in [selected["GTV"], *vertex_masks]):
            raise validated.AuditBlock("A Layer 1 mask shape differs from the validated RTDOSE grid.")
        centroids = np.vstack([validated.physical_centroid(mask, geometry) for mask in vertex_masks])
        edges, distances = validated.nearest_neighbour_edges(centroids, float(config["graph"]["tie_tolerance_mm"]))
        if not edges:
            raise validated.AuditBlock("Nearest-neighbour construction produced no graph edges.")
        nonzero = distances[np.isfinite(distances)]
        if nonzero.size and float(np.min(nonzero)) < float(config["graph"]["tie_tolerance_mm"]):
            raise validated.AuditBlock("Duplicate or numerically indistinguishable vertex centroids were detected.")
        all_vertices = np.logical_or.reduce(vertex_masks)
        node_d50: list[float] = []
        nodes: list[dict[str, Any]] = []
        voxel_cc = float(np.prod(spacing) / 1000.0)
        for name, mask, centroid in zip(names, vertex_masks, centroids):
            values = dose[mask & np.isfinite(dose)]
            if not len(values):
                raise validated.AuditBlock(f"Vertex {name} has no finite native RTDOSE samples.")
            d50 = float(np.median(values))
            node_d50.append(d50)
            nodes.append({
                "node": name, "centroid_lps_mm": [round(float(value), 6) for value in centroid],
                "voxel_count": int(mask.sum()), "volume_cc": float(mask.sum() * np.prod(spacing) / 1000.0),
                "peak_d50_gy": d50,
                "peak_d95_gy": validated.percentile_dose(values, 95.0),
                "peak_dmean_gy": float(np.mean(values)),
            })
        # The radius is physical millimetres in patient LPS space, not a screen
        # marker size.  The 3-D viewer consumes the same stored value to scale it.
        radius = float(config["valley"]["midpoint_sphere_radius_mm"])
        support_cc = float(config["valley"]["minimum_support_cc"])
        minimum_voxels = int(math.ceil(support_cc / voxel_cc))
        lengths = np.asarray([distances[a, b] for a, b in edges], dtype=float)
        if len(lengths) >= 4:
            q1_len, q3_len = np.percentile(lengths, [25, 75])
            outlier_limit = float(q3_len + 1.5 * (q3_len - q1_len))
        else:
            outlier_limit = None
        edge_records: list[dict[str, Any]] = []
        valid_values: list[float] = []
        excluded: list[dict[str, Any]] = []
        for edge_index, (first, second) in enumerate(edges, 1):
            midpoint = (centroids[first] + centroids[second]) / 2.0
            values = validated.sphere_values(midpoint, radius, geometry, dose, selected["GTV"], all_vertices)
            peak = float((node_d50[first] + node_d50[second]) / 2.0)
            valley = float(np.median(values)) if len(values) else None
            is_valid = len(values) >= minimum_voxels and valley is not None and valley > 0
            ratio = float(peak / valley) if is_valid else None
            length = float(distances[first, second])
            reason = None if is_valid else ("EXCLUDE_INSUFFICIENT_VALLEY_VOXELS" if len(values) < minimum_voxels else "EXCLUDE_ZERO_OR_INVALID_VALLEY")
            record = {
                "edge_id": edge_index, "nodes": [names[first], names[second]],
                "length_mm": length, "midpoint_lps_mm": [round(float(value), 6) for value in midpoint],
                "endpoint_peak_d50_gy": [node_d50[first], node_d50[second]],
                "edge_peak_d50_gy": peak, "edge_local_valley_d50_gy": valley,
                "valley_support_voxels": int(len(values)),
                "valley_sample_volume_cc": float(len(values) * voxel_cc),
                "minimum_support_voxels": minimum_voxels,
                "ipvdr": ratio, "valid": is_valid, "invalid_reason": reason,
                "edge_status": "WARN_EDGE_LENGTH_OUTLIER" if outlier_limit is not None and length > outlier_limit else ("PASS" if is_valid else reason),
            }
            edge_records.append(record)
            if is_valid:
                valid_values.append(ratio)
            else:
                excluded.append(record)
        minimum_edges = int(config["plan_endpoint"]["minimum_valid_edges"])
        if len(valid_values) < minimum_edges:
            raise validated.AuditBlock(f"Only {len(valid_values)} valid edges were available; at least {minimum_edges} are required.")
        values = np.asarray(valid_values)
        q1, median, q3 = np.percentile(values, [25, 50, 75])
        component_count = validated.graph_components(len(names), edges)
        warnings: list[str] = []
        if node_source != "INDIVIDUAL_VTVH_STRUCTURES":
            warnings.append("individual_vertices_unavailable_components_used")
        if component_count != 1:
            warnings.append("graph_disconnected")
        if excluded:
            warnings.append("excluded_edges_present")
        if any(item["edge_status"] == "WARN_EDGE_LENGTH_OUTLIER" for item in edge_records):
            warnings.append("edge_length_outlier")
        vertex_source = (
            "explicit_rtstruct_vertices"
            if node_source == "INDIVIDUAL_VTVH_STRUCTURES"
            else "connected_components_derived"
        )
        identifier = run_id("L2_2")
        payload = {
            "framework": "ASCEND", "layer": "2.2", "schema_version": "ASCEND-Layer2.2-graph-v1",
            "software_version": validated.VERSION, "run_id": identifier,
            "parent_layer1_run_id": case.layer1.run_id,
            "calculation_status": CalculationStatus.COMPLETED_WITH_WARNINGS.value if warnings else CalculationStatus.COMPLETED.value,
            "interpretation_status": InterpretationStatus.PROVISIONAL.value,
            "vertex_source": vertex_source,
            "method_description": "profile-orientation-independent 3-D nearest-neighbour graph; native RTDOSE sampling without interpolation",
            "warnings": warnings,
            "frozen_definitions": {
                "node_source": node_source, "vertex_source": vertex_source,
                "graph": config["graph"], "peak": config["peak"],
                "valley": config["valley"], "plan_endpoint": config["plan_endpoint"],
                "dose_sampling": "native RTDOSE voxels; no interpolation",
            },
            "grid": {"shape_zyx": list(dose.shape), "spacing_zyx_mm": spacing.tolist(), "voxel_volume_cc": voxel_cc},
            "nodes": nodes, "edges": edge_records, "excluded_edges": excluded,
            "graph_summary": {
                "number_of_nodes": len(names), "number_of_edges": len(edges),
                "number_of_components": component_count, "valid_edges": len(valid_values),
                "excluded_edges": len(excluded), "median_edge_length_mm": float(np.median(lengths)),
                "edge_length_iqr_mm": float(np.percentile(lengths, 75) - np.percentile(lengths, 25)),
                "edge_length_outlier_limit_mm": outlier_limit,
            },
            "plan_ipvdr": {
                "primary_median": float(median), "q1": float(q1), "q3": float(q3),
                "iqr": float(q3 - q1), "minimum": float(np.min(values)), "maximum": float(np.max(values)),
            },
            "provenance": {
                **base_provenance(case.configuration_hash or "", case.layer1.run_id),
                "algorithm_version": validated.VERSION,
                "algorithm_source_sha256": file_hash(validated.__file__),
                "configuration_sha256": file_hash(self.config_path),
                "rtdose_sop_instance_uid": manifest.get("rtdose_uid"),
                "rtstruct_sop_instance_uid": manifest.get("rtstruct_uid"),
                "rtplan_sop_instance_uid": manifest.get("rtplan_uid"),
                "dose_grid_spacing_mm": spacing.tolist(),
                "dose_context": case.configuration.dose_context,
                "treatment_delivery_mode": case.configuration.treatment_delivery_mode,
            },
        }
        output = case.root / "derived" / "layer2_2" / f"{identifier}.json"
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return LayerRun(
            layer="layer2_2", calculation_status=payload["calculation_status"],
            interpretation_status=payload["interpretation_status"], run_id=identifier,
            parent_layer1_run_id=case.layer1.run_id, result_path=str(output), result=payload,
            warnings=warnings,
        )
