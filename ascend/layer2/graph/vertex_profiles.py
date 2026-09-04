"""Three-dimensional native-grid dose profiles around Layer 2.2 vertices."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

import numpy as np

from .result_models import (
    ASCEND_LAYER22_VERTEX_PROFILE_SCHEMA,
    VERTEX_PROFILE_ALGORITHM_VERSION,
    RadialShell,
    VertexProfileResult,
)
from .spatial_sampling import GridGeometry, SpatialValidationError, physical_centroid, validate_native_inputs


@dataclass(frozen=True)
class VertexProfileConfiguration:
    shell_width_mm: float | None = None
    isolated_margin_mm: float = 10.0
    background_inner_fraction: float = 0.8
    minimum_modulation_amplitude_gy: float = 0.1
    minimum_shell_voxels: int = 1
    minimum_domain_fraction: float = 0.5
    reversal_tolerance: float = 0.02

    def resolved_shell_width(self, geometry: GridGeometry) -> float:
        width = geometry.minimum_spacing_mm if self.shell_width_mm is None else float(self.shell_width_mm)
        if not np.isfinite(width) or width <= 0:
            raise SpatialValidationError("INVALID_SHELL_WIDTH")
        return width

    def to_dict(self, geometry: GridGeometry) -> dict[str, Any]:
        return {
            "shell_width_mm": self.resolved_shell_width(geometry),
            "isolated_margin_mm": self.isolated_margin_mm,
            "background_inner_fraction": self.background_inner_fraction,
            "minimum_modulation_amplitude_gy": self.minimum_modulation_amplitude_gy,
            "minimum_shell_voxels": self.minimum_shell_voxels,
            "minimum_domain_fraction": self.minimum_domain_fraction,
            "reversal_tolerance": self.reversal_tolerance,
        }


def _point(values: np.ndarray) -> tuple[float, float, float]:
    return tuple(float(item) for item in np.asarray(values, dtype=float))


def _first_crossing(radii: np.ndarray, profile: np.ndarray, threshold: float) -> float | None:
    finite = np.isfinite(radii) & np.isfinite(profile)
    x = np.concatenate(([0.0], radii[finite]))
    y = np.concatenate(([1.0], profile[finite]))
    for index in range(1, len(x)):
        previous, current = float(y[index - 1]), float(y[index])
        if previous >= threshold and current <= threshold:
            if np.isclose(previous, current):
                return float(x[index])
            fraction = (previous - threshold) / (previous - current)
            return float(x[index - 1] + fraction * (x[index] - x[index - 1]))
    return None


def _empty_result(
    case_id: str,
    vertex_id: str,
    roi_number: int | None,
    shell_width: float,
    status: str,
    warnings: tuple[str, ...],
    provenance: dict[str, Any],
) -> VertexProfileResult:
    return VertexProfileResult(
        case_id, vertex_id, roi_number, None, None, None, None, None, None, None, None, None, None, 0, None,
        None, None, None, None, None, None, None, None, None, None, None, {}, None, None, shell_width, status,
        warnings, provenance,
    )


def analyse_vertex_profile(
    *,
    case_id: str,
    vertex_id: str,
    vertex_roi_number: int | None,
    dose_gy: np.ndarray,
    geometry: GridGeometry,
    gtv_mask: np.ndarray,
    vertex_mask: np.ndarray,
    all_vertex_mask: np.ndarray,
    nearest_neighbour_distance_mm: float | None,
    configuration: VertexProfileConfiguration,
    provenance: dict[str, Any] | None = None,
) -> tuple[VertexProfileResult, tuple[RadialShell, ...]]:
    """Calculate one physical, unsmoothed radial profile on the native dose grid."""
    shell_width = configuration.resolved_shell_width(geometry)
    base_provenance = {
        **(provenance or {}),
        "algorithm_version": VERTEX_PROFILE_ALGORITHM_VERSION,
        "coordinate_system": "DICOM patient LPS mm",
        "dose_sampling": "native RTDOSE voxel centres; no interpolation or display smoothing",
        "centre_definition": "physical centroid of validated vertex ROI",
    }
    vertex = np.asarray(vertex_mask, dtype=bool)
    if not vertex.any():
        return _empty_result(case_id, vertex_id, vertex_roi_number, shell_width, "EMPTY_VERTEX_MASK", (), base_provenance), ()
    centre = physical_centroid(vertex, geometry)
    vertex_indices = np.argwhere(vertex)
    vertex_points = geometry.points_lps_mm(vertex_indices)
    vertex_dose = np.asarray(dose_gy, dtype=float)[vertex]
    core_d50 = float(np.median(vertex_dose))
    maximum_index = vertex_indices[int(np.argmax(vertex_dose))]
    maximum_point = geometry.points_lps_mm(maximum_index.reshape(1, 3))[0]
    voxel_weights = geometry.voxel_volumes_cc(vertex_indices[:, 0])
    dose_weights = vertex_dose * voxel_weights
    weight_sum = float(np.sum(dose_weights))
    dose_centroid = np.average(vertex_points, axis=0, weights=dose_weights) if weight_sum > 0 else centre.copy()
    volume_cc = float(np.sum(geometry.voxel_volumes_cc(vertex_indices[:, 0])))
    geometric_diameter = float(2.0 * np.cbrt(3.0 * volume_cc * 1000.0 / (4.0 * np.pi)))
    geometric_radius = geometric_diameter / 2.0
    fallback = nearest_neighbour_distance_mm is None or not np.isfinite(nearest_neighbour_distance_mm)
    maximum_radius = (
        geometric_radius + float(configuration.isolated_margin_mm)
        if fallback else float(nearest_neighbour_distance_mm) / 2.0
    )
    warnings: list[str] = ["ISOLATED_VERTEX_FALLBACK_RADIUS"] if fallback else []
    indices = geometry.local_indices(centre, maximum_radius)
    if not len(indices):
        return _empty_result(
            case_id, vertex_id, vertex_roi_number, shell_width, "DOSE_GRID_EXHAUSTED", tuple(warnings), base_provenance,
        ), ()
    points = geometry.points_lps_mm(indices)
    distances = np.linalg.norm(points - centre[None, :], axis=1)
    inside = distances <= maximum_radius + 1.0e-9
    indices, distances = indices[inside], distances[inside]
    values = np.asarray(dose_gy, dtype=float)[tuple(indices.T)]
    vertex_flags = np.asarray(all_vertex_mask, dtype=bool)[tuple(indices.T)]
    gtv_flags = np.asarray(gtv_mask, dtype=bool)[tuple(indices.T)]
    outer = (
        (distances >= configuration.background_inner_fraction * maximum_radius)
        & (distances <= maximum_radius + 1.0e-9)
        & ~vertex_flags
        & np.isfinite(values)
    )
    background_values = values[outer]
    if not len(background_values):
        result = _empty_result(
            case_id, vertex_id, vertex_roi_number, shell_width, "INSUFFICIENT_BACKGROUND_SAMPLES",
            tuple(warnings), base_provenance,
        )
        return replace(
            result, geometric_centroid_xyz_mm=_point(centre), dose_weighted_centroid_xyz_mm=_point(dose_centroid),
            maximum_dose_xyz_mm=_point(maximum_point), geometric_volume_cc=volume_cc,
            geometric_equivalent_diameter_mm=geometric_diameter, core_d50_gy=core_d50,
            nearest_neighbour_distance_mm=nearest_neighbour_distance_mm, maximum_profile_radius_mm=maximum_radius,
        ), ()
    background = float(np.median(background_values))
    background_iqr = float(np.percentile(background_values, 75) - np.percentile(background_values, 25))
    amplitude = core_d50 - background
    if not np.isfinite(amplitude) or amplitude <= configuration.minimum_modulation_amplitude_gy:
        result = _empty_result(
            case_id, vertex_id, vertex_roi_number, shell_width, "INSUFFICIENT_VERTEX_CONTRAST",
            tuple(warnings), base_provenance,
        )
        return replace(
            result, geometric_centroid_xyz_mm=_point(centre), dose_weighted_centroid_xyz_mm=_point(dose_centroid),
            maximum_dose_xyz_mm=_point(maximum_point), centroid_to_dose_centroid_mm=float(np.linalg.norm(dose_centroid - centre)),
            centroid_to_maximum_mm=float(np.linalg.norm(maximum_point - centre)), geometric_volume_cc=volume_cc,
            geometric_equivalent_diameter_mm=geometric_diameter, core_d50_gy=core_d50, background_d50_gy=background,
            background_iqr_gy=background_iqr, background_voxel_count=int(len(background_values)),
            modulation_amplitude_gy=amplitude, nearest_neighbour_distance_mm=nearest_neighbour_distance_mm,
            maximum_profile_radius_mm=maximum_radius,
        ), ()

    shells: list[RadialShell] = []
    number_of_shells = max(int(np.ceil(maximum_radius / shell_width)), 1)
    median_voxel_mm3 = geometry.median_voxel_volume_cc * 1000.0
    for shell_index in range(number_of_shells):
        inner = shell_index * shell_width
        outer_radius = min((shell_index + 1) * shell_width, maximum_radius)
        selection = (distances >= inner) & (distances < outer_radius if shell_index + 1 < number_of_shells else distances <= outer_radius + 1.0e-9)
        shell_values = values[selection & np.isfinite(values)]
        if len(shell_values) < configuration.minimum_shell_voxels:
            warnings.append("INSUFFICIENT_SHELL_SAMPLES")
            break
        shell_volume_mm3 = 4.0 * np.pi / 3.0 * (outer_radius**3 - inner**3)
        expected_voxels = max(shell_volume_mm3 / median_voxel_mm3, 1.0)
        domain_fraction = min(float(len(shell_values) / expected_voxels), 1.0)
        if shell_index >= 2 and domain_fraction < configuration.minimum_domain_fraction:
            warnings.append("DOSE_GRID_BOUNDARY_TRUNCATION")
            break
        q10, q25, q50, q75, q90 = np.percentile(shell_values, [10, 25, 50, 75, 90])
        selected_indices = indices[selection]
        sampled_cc = float(np.sum(geometry.voxel_volumes_cc(selected_indices[:, 0])))
        anisotropy = float((q90 - q10) / q50) if q50 > 0 else None
        shells.append(RadialShell(
            shell_index=shell_index,
            radius_mm=float((inner + outer_radius) / 2.0),
            inner_radius_mm=float(inner), outer_radius_mm=float(outer_radius),
            mean_dose_gy=float(np.mean(shell_values)), median_dose_gy=float(q50),
            q25_dose_gy=float(q25), q75_dose_gy=float(q75),
            standard_deviation_gy=float(np.std(shell_values)), interquartile_range_gy=float(q75 - q25),
            minimum_dose_gy=float(np.min(shell_values)), maximum_dose_gy=float(np.max(shell_values)),
            d10_gy=float(q10), d90_gy=float(q90), voxel_count=int(len(shell_values)), sampled_volume_cc=sampled_cc,
            gtv_fraction=float(np.mean(gtv_flags[selection])), other_vertex_fraction=float(np.mean(vertex_flags[selection] & ~vertex[tuple(indices[selection].T)])),
            dose_domain_fraction_estimate=domain_fraction, corrected_profile=float((q50 - background) / amplitude),
            anisotropy_index=anisotropy,
        ))
    if len(shells) < 2:
        result = _empty_result(
            case_id, vertex_id, vertex_roi_number, shell_width, "INSUFFICIENT_SHELL_SAMPLES",
            tuple(dict.fromkeys(warnings)), base_provenance,
        )
        return replace(
            result, geometric_centroid_xyz_mm=_point(centre), dose_weighted_centroid_xyz_mm=_point(dose_centroid),
            maximum_dose_xyz_mm=_point(maximum_point), geometric_volume_cc=volume_cc,
            geometric_equivalent_diameter_mm=geometric_diameter, core_d50_gy=core_d50, background_d50_gy=background,
            background_iqr_gy=background_iqr, background_voxel_count=int(len(background_values)),
            modulation_amplitude_gy=amplitude, nearest_neighbour_distance_mm=nearest_neighbour_distance_mm,
            maximum_profile_radius_mm=maximum_radius,
        ), tuple(shells)

    radii = np.asarray([item.radius_mm for item in shells], dtype=float)
    medians = np.asarray([item.median_dose_gy for item in shells], dtype=float)
    corrected = np.asarray([item.corrected_profile for item in shells], dtype=float)
    gradient = np.gradient(medians, radii, edge_order=1)
    shells = [replace(item, radial_gradient_gy_per_mm=float(gradient[index])) for index, item in enumerate(shells)]
    r80, r50, r20 = (_first_crossing(radii, corrected, threshold) for threshold in (0.8, 0.5, 0.2))
    reversals = np.diff(corrected)
    positive_reversals = reversals[reversals > configuration.reversal_tolerance]
    if len(positive_reversals):
        warnings.append("NON_MONOTONIC_PROFILE")
    for label, crossing in (("R80_CROSSING_NOT_FOUND", r80), ("R50_CROSSING_NOT_FOUND", r50), ("R20_CROSSING_NOT_FOUND", r20)):
        if crossing is None:
            warnings.append(label)
    diameter = 2.0 * r50 if r50 is not None else None
    penumbra = r20 - r80 if r20 is not None and r80 is not None and r20 >= r80 else None
    mean_gradient = 0.6 * amplitude / penumbra if penumbra is not None and penumbra > 0 else None
    negative_index = int(np.argmin(gradient))
    maximum_gradient = max(float(-gradient[negative_index]), 0.0)
    interval = (
        (radii >= r80) & (radii <= r20)
        if r80 is not None and r20 is not None else np.zeros(len(radii), dtype=bool)
    )
    anisotropy_values = np.asarray([item.anisotropy_index for item in shells], dtype=float)
    anisotropy = {
        "maximum_r80_to_r20": float(np.nanmax(anisotropy_values[interval])) if interval.any() else None,
        "median_r80_to_r20": float(np.nanmedian(anisotropy_values[interval])) if interval.any() else None,
        "direction_of_steepest_falloff": None,
        "direction_of_shallowest_falloff": None,
        "status": "SECONDARY_RESEARCH_OUTPUT",
        "reversal_count": int(len(positive_reversals)),
        "reversal_magnitude": float(np.sum(positive_reversals)) if len(positive_reversals) else 0.0,
    }
    result = VertexProfileResult(
        case_id=case_id, vertex_id=vertex_id, vertex_roi_number=vertex_roi_number,
        geometric_centroid_xyz_mm=_point(centre), dose_weighted_centroid_xyz_mm=_point(dose_centroid),
        maximum_dose_xyz_mm=_point(maximum_point), centroid_to_dose_centroid_mm=float(np.linalg.norm(dose_centroid - centre)),
        centroid_to_maximum_mm=float(np.linalg.norm(maximum_point - centre)), geometric_volume_cc=volume_cc,
        geometric_equivalent_diameter_mm=geometric_diameter, core_d50_gy=core_d50, background_d50_gy=background,
        background_iqr_gy=background_iqr, background_voxel_count=int(len(background_values)), modulation_amplitude_gy=amplitude,
        r80_mm=r80, r50_mm=r50, r20_mm=r20, dosimetric_diameter_mm=diameter,
        diameter_difference_mm=diameter - geometric_diameter if diameter is not None else None,
        diameter_ratio=diameter / geometric_diameter if diameter is not None and geometric_diameter > 0 else None,
        penumbra_80_20_mm=penumbra, mean_gradient_80_20_gy_per_mm=mean_gradient,
        maximum_gradient_gy_per_mm=maximum_gradient, maximum_gradient_radius_mm=float(radii[negative_index]),
        normalised_gradient_per_mm=maximum_gradient / amplitude, profile_anisotropy=anisotropy,
        nearest_neighbour_distance_mm=nearest_neighbour_distance_mm, maximum_profile_radius_mm=maximum_radius,
        shell_width_mm=shell_width, profile_status="VALID" if r50 is not None else "INCOMPLETE_CROSSINGS",
        warnings=tuple(dict.fromkeys(warnings)), provenance=base_provenance,
    )
    return result, tuple(shells)


def analyse_vertex_profiles(
    *,
    case_id: str,
    dose_gy: np.ndarray,
    geometry: GridGeometry,
    gtv_mask: np.ndarray,
    vertex_ids: Sequence[str],
    vertex_masks: Sequence[np.ndarray],
    nearest_neighbour_distances_mm: Sequence[float | None] | None = None,
    vertex_roi_numbers: Sequence[int | None] | None = None,
    configuration: VertexProfileConfiguration | None = None,
    provenance: dict[str, Any] | None = None,
    dose_units: str = "Gy",
) -> dict[str, Any]:
    """Calculate every vertex profile and return a versioned immutable-derived record."""
    config = configuration or VertexProfileConfiguration()
    masks = [np.asarray(item, dtype=bool) for item in vertex_masks]
    validate_native_inputs(np.asarray(dose_gy), geometry, [np.asarray(gtv_mask, dtype=bool), *masks], dose_units=dose_units)
    if len(vertex_ids) != len(masks) or len(set(vertex_ids)) != len(vertex_ids):
        raise SpatialValidationError("AMBIGUOUS_VERTEX_IDENTITY")
    all_vertices = np.logical_or.reduce(masks)
    distances = list(nearest_neighbour_distances_mm or [None] * len(masks))
    roi_numbers = list(vertex_roi_numbers or [None] * len(masks))
    results: list[VertexProfileResult] = []
    profiles: dict[str, list[dict[str, Any]]] = {}
    for index, (vertex_id, mask) in enumerate(zip(vertex_ids, masks)):
        result, shells = analyse_vertex_profile(
            case_id=case_id, vertex_id=str(vertex_id), vertex_roi_number=roi_numbers[index], dose_gy=dose_gy,
            geometry=geometry, gtv_mask=gtv_mask, vertex_mask=mask, all_vertex_mask=all_vertices,
            nearest_neighbour_distance_mm=distances[index], configuration=config, provenance=provenance,
        )
        results.append(result)
        profiles[str(vertex_id)] = [item.to_dict() for item in shells]
    valid = [item for item in results if item.profile_status == "VALID"]
    return {
        "schema_version": ASCEND_LAYER22_VERTEX_PROFILE_SCHEMA,
        "algorithm_version": VERTEX_PROFILE_ALGORITHM_VERSION,
        "calculation_status": "completed" if len(valid) == len(results) else "completed_with_warnings",
        "configuration": config.to_dict(geometry),
        "vertices": [item.to_dict() for item in results],
        "profiles": profiles,
        "summary": {
            "vertex_count": len(results), "valid_vertex_count": len(valid),
            "invalid_vertex_count": len(results) - len(valid),
            "status_counts": {status: sum(item.profile_status == status for item in results) for status in sorted({item.profile_status for item in results})},
        },
        "provenance": {
            **(provenance or {}), "algorithm_version": VERTEX_PROFILE_ALGORITHM_VERSION,
            "dose_grid_shape_zyx": list(geometry.shape_zyx),
            "dose_grid_spacing_summary_mm": [float(item) for item in (geometry.frame_thicknesses_mm.mean(), geometry.row_spacing_mm, geometry.column_spacing_mm)],
            "coordinate_system": "DICOM patient LPS mm", "resampling": "none",
        },
    }
