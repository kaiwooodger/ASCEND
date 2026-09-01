"""Dose-topographic saddle analysis on the locked Layer 2.2 graph."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Any, Sequence

import numpy as np

from .result_models import ASCEND_LAYER22_SADDLE_GRAPH_SCHEMA, SADDLE_GRAPH_ALGORITHM_VERSION, SaddleEdgeResult
from .spatial_sampling import GridGeometry, SpatialValidationError, capsule_selection, spherical_selection, validate_native_inputs


NEIGHBOUR_OFFSETS = tuple(
    (z, y, x)
    for z in (-1, 0, 1)
    for y in (-1, 0, 1)
    for x in (-1, 0, 1)
    if (z, y, x) != (0, 0, 0)
)


@dataclass(frozen=True)
class SaddleConfiguration:
    corridor_radius_mm: float = 3.0
    local_sampling_radius_mm: float = 3.0
    sensitivity_corridor_radii_mm: tuple[float, ...] = (2.0, 3.0, 4.0)
    minimum_saddle_voxels: int = 1
    uniform_dose_tolerance_gy: float = 1.0e-8

    def to_dict(self) -> dict[str, Any]:
        return {
            "corridor_radius_mm": self.corridor_radius_mm,
            "local_sampling_radius_mm": self.local_sampling_radius_mm,
            "sensitivity_corridor_radii_mm": list(self.sensitivity_corridor_radii_mm),
            "minimum_saddle_voxels": self.minimum_saddle_voxels,
            "uniform_dose_tolerance_gy": self.uniform_dose_tolerance_gy,
            "connectivity": 26,
            "path_objective": "maximise minimum native-grid dose",
        }


def _edge_failure(
    *,
    case_id: str,
    edge: dict[str, Any],
    radius_mm: float,
    local_radius_mm: float,
    status: str,
    warnings: tuple[str, ...] = (),
    corridor_voxel_count: int = 0,
    provenance: dict[str, Any] | None = None,
) -> SaddleEdgeResult:
    nodes = edge.get("nodes") or ["?", "?"]
    peaks = edge.get("endpoint_peak_d50_gy") or [None, None]
    midpoint = tuple(float(item) for item in edge.get("midpoint_lps_mm", (0.0, 0.0, 0.0)))
    return SaddleEdgeResult(
        case_id=case_id, edge_id=int(edge.get("edge_id", 0)), vertex_i_id=str(nodes[0]), vertex_j_id=str(nodes[1]),
        vertex_i_d50_gy=peaks[0], vertex_j_d50_gy=peaks[1], edge_peak_d50_gy=edge.get("edge_peak_d50_gy"),
        edge_length_mm=float(edge.get("length_mm", 0.0)), midpoint_xyz_mm=midpoint,
        midpoint_d50_gy=edge.get("edge_local_valley_d50_gy"), midpoint_pvdr=edge.get("ipvdr"),
        saddle_xyz_mm=None, raw_saddle_bottleneck_gy=None, saddle_local_d50_gy=None, saddle_pvdr=None,
        saddle_to_midpoint_mm=None, saddle_path_length_mm=None, midpoint_minus_saddle_gy=None,
        saddle_minus_midpoint_pvdr=None, corridor_radius_mm=radius_mm, local_sampling_radius_mm=local_radius_mm,
        corridor_voxel_count=corridor_voxel_count, saddle_roi_voxel_count=0, edge_status=status,
        exclusion_reason=status, warnings=warnings,
        provenance={**(provenance or {}), "algorithm_version": SADDLE_GRAPH_ALGORITHM_VERSION},
    )


def _widest_path(
    dose_gy: np.ndarray,
    corridor_indices: np.ndarray,
    source_indices: np.ndarray,
    target_indices: np.ndarray,
) -> tuple[list[int], float] | None:
    shape = dose_gy.shape
    corridor_linear = np.ravel_multi_index(corridor_indices.T, shape)
    allowed = set(map(int, corridor_linear))
    source = sorted(set(map(int, np.ravel_multi_index(source_indices.T, shape))) & allowed)
    targets = set(map(int, np.ravel_multi_index(target_indices.T, shape))) & allowed
    if not source or not targets:
        return None
    flat_dose = np.asarray(dose_gy, dtype=float).ravel()
    capacities: dict[int, float] = {}
    predecessors: dict[int, int | None] = {}
    queue: list[tuple[float, int]] = []
    for linear in source:
        capacities[linear] = float("inf")
        predecessors[linear] = None
        heapq.heappush(queue, (-float("inf"), linear))
    reached: int | None = None
    while queue:
        negative_capacity, linear = heapq.heappop(queue)
        capacity = -negative_capacity
        if capacity < capacities.get(linear, -float("inf")):
            continue
        if linear in targets:
            reached = linear
            break
        z, y, x = np.unravel_index(linear, shape)
        for dz, dy, dx in NEIGHBOUR_OFFSETS:
            candidate_index = (z + dz, y + dy, x + dx)
            if not (0 <= candidate_index[0] < shape[0] and 0 <= candidate_index[1] < shape[1] and 0 <= candidate_index[2] < shape[2]):
                continue
            candidate_linear = int(np.ravel_multi_index(candidate_index, shape))
            if candidate_linear not in allowed:
                continue
            candidate_capacity = min(capacity, float(flat_dose[candidate_linear]))
            previous_capacity = capacities.get(candidate_linear, -float("inf"))
            if candidate_capacity > previous_capacity:
                capacities[candidate_linear] = candidate_capacity
                predecessors[candidate_linear] = linear
                heapq.heappush(queue, (-candidate_capacity, candidate_linear))
    if reached is None:
        return None
    path = [reached]
    while predecessors[path[-1]] is not None:
        path.append(int(predecessors[path[-1]]))
    path.reverse()
    return path, float(capacities[reached])


def _single_radius_saddle(
    *,
    case_id: str,
    edge: dict[str, Any],
    dose_gy: np.ndarray,
    geometry: GridGeometry,
    gtv_mask: np.ndarray,
    vertex_masks: Sequence[np.ndarray],
    endpoint_indices: tuple[int, int],
    corridor_radius_mm: float,
    local_sampling_radius_mm: float,
    minimum_saddle_voxels: int,
    uniform_dose_tolerance_gy: float,
    provenance: dict[str, Any] | None,
) -> SaddleEdgeResult:
    first_index, second_index = endpoint_indices
    if not edge.get("valid", False):
        return _edge_failure(
            case_id=case_id, edge=edge, radius_mm=corridor_radius_mm, local_radius_mm=local_sampling_radius_mm,
            status="INVALID_ENDPOINT", provenance=provenance,
        )
    start = np.asarray(edge["endpoint_centroids_lps_mm"][0], dtype=float)
    end = np.asarray(edge["endpoint_centroids_lps_mm"][1], dtype=float)
    candidate_indices, inside_capsule = capsule_selection(start, end, corridor_radius_mm, geometry)
    candidate_indices = candidate_indices[inside_capsule]
    if not len(candidate_indices):
        return _edge_failure(
            case_id=case_id, edge=edge, radius_mm=corridor_radius_mm, local_radius_mm=local_sampling_radius_mm,
            status="DISCONNECTED_CORRIDOR", provenance=provenance,
        )
    gtv = np.asarray(gtv_mask, dtype=bool)
    unrelated = np.logical_or.reduce([
        np.asarray(mask, dtype=bool) for index, mask in enumerate(vertex_masks) if index not in {first_index, second_index}
    ]) if len(vertex_masks) > 2 else np.zeros(gtv.shape, dtype=bool)
    within_gtv = gtv[tuple(candidate_indices.T)]
    unrelated_hit = unrelated[tuple(candidate_indices.T)]
    corridor_indices = candidate_indices[within_gtv & ~unrelated_hit]
    if not len(corridor_indices):
        return _edge_failure(
            case_id=case_id, edge=edge, radius_mm=corridor_radius_mm, local_radius_mm=local_sampling_radius_mm,
            status="DISCONNECTED_CORRIDOR", provenance=provenance,
        )
    source_indices = np.argwhere(np.asarray(vertex_masks[first_index], dtype=bool) & gtv)
    target_indices = np.argwhere(np.asarray(vertex_masks[second_index], dtype=bool) & gtv)
    path_result = _widest_path(dose_gy, corridor_indices, source_indices, target_indices)
    if path_result is None:
        status = "UNRELATED_VERTEX_INTERSECTION" if np.any(unrelated_hit) else "NO_SADDLE_PATH"
        return _edge_failure(
            case_id=case_id, edge=edge, radius_mm=corridor_radius_mm, local_radius_mm=local_sampling_radius_mm,
            status=status, corridor_voxel_count=len(corridor_indices), provenance=provenance,
        )
    path_linear, bottleneck = path_result
    path_indices = np.column_stack(np.unravel_index(np.asarray(path_linear, dtype=int), dose_gy.shape))
    path_points = geometry.points_lps_mm(path_indices)
    path_dose = np.asarray(dose_gy, dtype=float).ravel()[path_linear]
    midpoint = np.asarray(edge["midpoint_lps_mm"], dtype=float)
    minimum = float(np.min(path_dose))
    tied = np.flatnonzero(np.isclose(path_dose, minimum, rtol=0.0, atol=1.0e-12))
    tied_distances = np.linalg.norm(path_points[tied] - midpoint[None, :], axis=1)
    tied_linear = np.asarray(path_linear, dtype=int)[tied]
    choice = int(tied[np.lexsort((tied_linear, tied_distances))[0]])
    saddle_index = path_indices[choice]
    saddle_point = path_points[choice]
    sphere_indices, sphere_inside = spherical_selection(saddle_point, local_sampling_radius_mm, geometry)
    sphere_indices = sphere_indices[sphere_inside]
    corridor_set = set(map(int, np.ravel_multi_index(corridor_indices.T, dose_gy.shape)))
    sphere_linear = np.ravel_multi_index(sphere_indices.T, dose_gy.shape) if len(sphere_indices) else np.empty(0, dtype=int)
    valid_sphere = np.asarray([
        int(linear) in corridor_set for linear in sphere_linear
    ], dtype=bool)
    valid_sphere &= gtv[tuple(sphere_indices.T)] & ~np.logical_or.reduce(vertex_masks)[tuple(sphere_indices.T)]
    saddle_values = np.asarray(dose_gy, dtype=float)[tuple(sphere_indices[valid_sphere].T)]
    if len(saddle_values) < minimum_saddle_voxels:
        return _edge_failure(
            case_id=case_id, edge=edge, radius_mm=corridor_radius_mm, local_radius_mm=local_sampling_radius_mm,
            status="INSUFFICIENT_SADDLE_SAMPLES", corridor_voxel_count=len(corridor_indices), provenance=provenance,
        )
    saddle_d50 = float(np.median(saddle_values))
    if not np.isfinite(saddle_d50) or saddle_d50 <= 0:
        return _edge_failure(
            case_id=case_id, edge=edge, radius_mm=corridor_radius_mm, local_radius_mm=local_sampling_radius_mm,
            status="NONPOSITIVE_SADDLE_DOSE", corridor_voxel_count=len(corridor_indices), provenance=provenance,
        )
    peak = float(edge["edge_peak_d50_gy"])
    midpoint_dose = float(edge["edge_local_valley_d50_gy"])
    midpoint_pvdr = float(edge["ipvdr"])
    saddle_pvdr = peak / saddle_d50
    segment_lengths = np.linalg.norm(np.diff(path_points, axis=0), axis=1)
    corridor_dose = np.asarray(dose_gy, dtype=float)[tuple(corridor_indices.T)]
    warnings: list[str] = []
    if float(np.max(corridor_dose) - np.min(corridor_dose)) <= uniform_dose_tolerance_gy:
        warnings.append("DEGENERATE_UNIFORM_DOSE_SADDLE")
    if np.any(unrelated_hit):
        warnings.append("UNRELATED_VERTEX_EXCLUDED_FROM_CORRIDOR")
    return SaddleEdgeResult(
        case_id=case_id, edge_id=int(edge["edge_id"]), vertex_i_id=str(edge["nodes"][0]), vertex_j_id=str(edge["nodes"][1]),
        vertex_i_d50_gy=float(edge["endpoint_peak_d50_gy"][0]), vertex_j_d50_gy=float(edge["endpoint_peak_d50_gy"][1]),
        edge_peak_d50_gy=peak, edge_length_mm=float(edge["length_mm"]), midpoint_xyz_mm=tuple(map(float, midpoint)),
        midpoint_d50_gy=midpoint_dose, midpoint_pvdr=midpoint_pvdr, saddle_xyz_mm=tuple(map(float, saddle_point)),
        raw_saddle_bottleneck_gy=bottleneck, saddle_local_d50_gy=saddle_d50, saddle_pvdr=saddle_pvdr,
        saddle_to_midpoint_mm=float(np.linalg.norm(saddle_point - midpoint)), saddle_path_length_mm=float(np.sum(segment_lengths)),
        midpoint_minus_saddle_gy=midpoint_dose - saddle_d50, saddle_minus_midpoint_pvdr=saddle_pvdr - midpoint_pvdr,
        corridor_radius_mm=corridor_radius_mm, local_sampling_radius_mm=local_sampling_radius_mm,
        corridor_voxel_count=int(len(corridor_indices)), saddle_roi_voxel_count=int(len(saddle_values)), edge_status="VALID",
        exclusion_reason=None, warnings=tuple(warnings),
        provenance={
            **(provenance or {}), "algorithm_version": SADDLE_GRAPH_ALGORITHM_VERSION,
            "connectivity": 26, "dose_sampling": "native RTDOSE voxels; no interpolation",
            "tie_break": "closest_to_midpoint_then_lowest_linear_voxel_index",
        },
        saddle_path_xyz_mm=tuple(tuple(map(float, point)) for point in path_points),
    )


def analyse_saddle_graph(
    *,
    case_id: str,
    dose_gy: np.ndarray,
    geometry: GridGeometry,
    gtv_mask: np.ndarray,
    vertex_masks: Sequence[np.ndarray],
    locked_edges: Sequence[dict[str, Any]],
    node_centroids_lps_mm: Sequence[Sequence[float]],
    configuration: SaddleConfiguration | None = None,
    provenance: dict[str, Any] | None = None,
    dose_units: str = "Gy",
) -> dict[str, Any]:
    """Attach topographic saddle evidence to the unchanged nearest-neighbour edges."""
    config = configuration or SaddleConfiguration()
    masks = [np.asarray(item, dtype=bool) for item in vertex_masks]
    validate_native_inputs(np.asarray(dose_gy), geometry, [np.asarray(gtv_mask, dtype=bool), *masks], dose_units=dose_units)
    records: list[SaddleEdgeResult] = []
    for edge in locked_edges:
        nodes = list(edge.get("nodes") or [])
        if len(nodes) != 2:
            raise SpatialValidationError("INVALID_ENDPOINT")
        indices = edge.get("endpoint_indices")
        if not isinstance(indices, (list, tuple)) or len(indices) != 2:
            raise SpatialValidationError("INVALID_ENDPOINT")
        first, second = int(indices[0]), int(indices[1])
        if (
            first == second or first < 0 or second < 0 or first >= len(masks) or second >= len(masks)
            or not masks[first].any() or not masks[second].any()
        ):
            records.append(_edge_failure(
                case_id=case_id, edge=edge, radius_mm=config.corridor_radius_mm,
                local_radius_mm=config.local_sampling_radius_mm, status="INVALID_ENDPOINT", provenance=provenance,
            ))
            continue
        edge_input = {
            **edge,
            "endpoint_centroids_lps_mm": [node_centroids_lps_mm[first], node_centroids_lps_mm[second]],
        }
        sensitivity: list[dict[str, Any]] = []
        primary: SaddleEdgeResult | None = None
        for radius in config.sensitivity_corridor_radii_mm:
            result = _single_radius_saddle(
                case_id=case_id, edge=edge_input, dose_gy=dose_gy, geometry=geometry, gtv_mask=gtv_mask,
                vertex_masks=masks, endpoint_indices=(first, second), corridor_radius_mm=float(radius),
                local_sampling_radius_mm=config.local_sampling_radius_mm,
                minimum_saddle_voxels=config.minimum_saddle_voxels,
                uniform_dose_tolerance_gy=config.uniform_dose_tolerance_gy, provenance=provenance,
            )
            sensitivity.append({
                "corridor_radius_mm": float(radius), "edge_status": result.edge_status,
                "raw_saddle_bottleneck_gy": result.raw_saddle_bottleneck_gy,
                "saddle_local_d50_gy": result.saddle_local_d50_gy, "saddle_pvdr": result.saddle_pvdr,
                "saddle_to_midpoint_mm": result.saddle_to_midpoint_mm,
            })
            if np.isclose(float(radius), config.corridor_radius_mm):
                primary = result
        if primary is None:
            primary = _single_radius_saddle(
                case_id=case_id, edge=edge_input, dose_gy=dose_gy, geometry=geometry, gtv_mask=gtv_mask,
                vertex_masks=masks, endpoint_indices=(first, second), corridor_radius_mm=config.corridor_radius_mm,
                local_sampling_radius_mm=config.local_sampling_radius_mm,
                minimum_saddle_voxels=config.minimum_saddle_voxels,
                uniform_dose_tolerance_gy=config.uniform_dose_tolerance_gy, provenance=provenance,
            )
        records.append(SaddleEdgeResult(**{**primary.__dict__, "sensitivity": tuple(sensitivity)}))
    valid = [item for item in records if item.edge_status == "VALID"]

    def distribution(attribute: str) -> dict[str, float | None]:
        values = np.asarray([getattr(item, attribute) for item in valid if getattr(item, attribute) is not None], dtype=float)
        if not len(values):
            return {"median": None, "q1": None, "q3": None, "iqr": None, "minimum": None}
        q1, median, q3 = np.percentile(values, [25, 50, 75])
        return {"median": float(median), "q1": float(q1), "q3": float(q3), "iqr": float(q3 - q1), "minimum": float(np.min(values))}

    exclusion_counts = {
        reason: sum(item.exclusion_reason == reason for item in records)
        for reason in sorted({item.exclusion_reason for item in records if item.exclusion_reason})
    }
    return {
        "schema_version": ASCEND_LAYER22_SADDLE_GRAPH_SCHEMA,
        "algorithm_version": SADDLE_GRAPH_ALGORITHM_VERSION,
        "calculation_status": "completed" if len(valid) == len(records) else "completed_with_warnings",
        "configuration": config.to_dict(), "edges": [item.to_dict() for item in records],
        "summary": {
            "midpoint_pvdr": distribution("midpoint_pvdr"), "saddle_pvdr": distribution("saddle_pvdr"),
            "median_saddle_displacement_mm": distribution("saddle_to_midpoint_mm")["median"],
            "median_absolute_midpoint_saddle_dose_difference_gy": (
                float(np.median([abs(item.midpoint_minus_saddle_gy) for item in valid if item.midpoint_minus_saddle_gy is not None]))
                if any(item.midpoint_minus_saddle_gy is not None for item in valid) else None
            ),
            "valid_edges": len(valid), "excluded_edges": len(records) - len(valid), "exclusion_counts": exclusion_counts,
        },
        "provenance": {
            **(provenance or {}), "algorithm_version": SADDLE_GRAPH_ALGORITHM_VERSION,
            "locked_graph_reused": True, "locked_midpoint_values_modified": False,
            "coordinate_system": "DICOM patient LPS mm", "resampling": "none",
        },
    }
