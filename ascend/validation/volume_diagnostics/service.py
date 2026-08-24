"""Service-layer orchestration for the enclosing ASCEND package."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

import numpy as np
import pydicom
from scipy import ndimage

from ascend import __version__
from ascend.dicom.geometry import normalise_rtdose_geometry
from ascend.models.case import ASCENDCase
from ascend.scientific.legacy import layer1_validated as validated
from ascend.validation.eclipse_harness.reference_import import sha256_file

from .geometry import (
    DiagnosticConclusion,
    aggregate_component_comparison,
    array_hash,
    near_duplicate_polygons,
    overlap_metrics,
    parse_eclipse_volume_precision,
    polygon_self_intersects,
    polygon_signed_area,
    polygons_nested,
    three_volume_comparison,
)
from .reporting import write_outputs


SCHEMA_VERSION = "ASCEND-ECLIPSE-VOLUME-DIAGNOSTIC-v1"
TARGETS = ("all_vertices", "all_valleys")


def _locked_hashes(project_root: Path) -> dict[str, str]:
    root = project_root / "ascend" / "scientific" / "legacy"
    return {
        "Layer 1": sha256_file(root / "layer1_validated.py"),
        "Layer 2.1": sha256_file(root / "layer21_validated.py"),
        "Layer 2.2": sha256_file(root / "layer22_validated.py"),
    }


def _formal_records(comparison: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [item for item in comparison.get("records", comparison.get("comparisons", [])) if item.get("roi_name", "").casefold() == name.casefold()]


def _formal_volume_record(comparison: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [item for item in _formal_records(comparison, name) if item.get("endpoint") == "Volume"]
    if len(matches) != 1:
        raise ValueError(f"Expected one formal Volume record for {name}; found {len(matches)}.")
    record = matches[0]
    if record.get("pass_fail") != "fail":
        raise ValueError(f"The preserved formal Volume record for {name} is not a failure.")
    return record


def _reference_precision(comparison: dict[str, Any], name: str) -> dict[str, Any]:
    files = comparison.get("reference_import", {}).get("source_files", [])
    preferred = sorted(
        files,
        key=lambda item: (name.replace("all_", "") not in Path(item.get("path", "")).stem.casefold(), Path(item.get("path", "")).name),
    )
    errors = []
    for item in preferred:
        path = Path(item["path"])
        if not path.is_file():
            continue
        try:
            parsed = parse_eclipse_volume_precision(path.read_text(encoding="utf-8-sig"), name)
            parsed.update({"source_path": str(path), "source_sha256": sha256_file(path)})
            return parsed
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(str(exc))
    raise ValueError(f"Could not recover Eclipse source precision for {name}: {'; '.join(errors)}")


def _ct_geometry(paths: list[Path]) -> dict[str, Any]:
    datasets = [pydicom.dcmread(path, stop_before_pixels=True) for path in paths]
    first = datasets[0]
    orientation = np.asarray(first.ImageOrientationPatient, dtype=float)
    column_axis = orientation[:3] / np.linalg.norm(orientation[:3])
    row_axis = orientation[3:] / np.linalg.norm(orientation[3:])
    normal = np.cross(column_axis, row_axis)
    normal /= np.linalg.norm(normal)
    ordered = sorted((
        float(np.dot(np.asarray(item.ImagePositionPatient, dtype=float), normal)),
        np.asarray(item.ImagePositionPatient, dtype=float),
    ) for item in datasets)
    positions = np.asarray([item[0] for item in ordered], dtype=float)
    origins = np.stack([item[1] for item in ordered])
    return {
        "column_axis": column_axis,
        "row_axis": row_axis,
        "normal": normal,
        "row_spacing": float(first.PixelSpacing[0]),
        "column_spacing": float(first.PixelSpacing[1]),
        "rows": int(first.Rows),
        "columns": int(first.Columns),
        "positions": positions,
        "origins": origins,
        "slice_spacing": validated._median_positive_spacing(positions),
    }


def _roi_contours(structure: Any, roi_number: int, ct: dict[str, Any]) -> tuple[dict[float, list[dict[str, Any]]], dict[str, Any]]:
    candidates = [item for item in getattr(structure, "ROIContourSequence", []) if int(getattr(item, "ReferencedROINumber", -1)) == roi_number]
    if len(candidates) != 1:
        raise ValueError(f"Expected one ROIContourSequence entry for ROI {roi_number}; found {len(candidates)}.")
    planes: dict[float, list[dict[str, Any]]] = {}
    geometric_types: set[str] = set()
    referenced_uids: set[str] = set()
    total_points = 0
    raw_positions: list[float] = []
    for contour in getattr(candidates[0], "ContourSequence", []):
        contour_type = str(getattr(contour, "ContourGeometricType", "CLOSED_PLANAR")).upper()
        geometric_types.add(contour_type)
        data = np.asarray(getattr(contour, "ContourData", []), dtype=float)
        if contour_type not in {"CLOSED_PLANAR", "CLOSEDPLANAR_XOR"} or data.size < 9 or data.size % 3:
            continue
        points = data.reshape(-1, 3)
        raw_position = float(np.mean(points @ ct["normal"]))
        position = round(raw_position, 4)
        references = [
            str(getattr(item, "ReferencedSOPInstanceUID", ""))
            for item in getattr(contour, "ContourImageSequence", [])
            if str(getattr(item, "ReferencedSOPInstanceUID", ""))
        ]
        referenced_uids.update(references)
        total_points += len(points)
        raw_positions.append(raw_position)
        planes.setdefault(position, []).append({
            "points": points,
            "raw_position": raw_position,
            "geometric_type": contour_type,
            "referenced_image_sop_uids": references,
        })
    return planes, {
        "contour_sequence_count": len(getattr(candidates[0], "ContourSequence", [])),
        "total_polygon_count": sum(len(value) for value in planes.values()),
        "total_contour_point_count": total_points,
        "contour_geometric_types": sorted(geometric_types),
        "referenced_contour_image_sop_uids": sorted(referenced_uids),
        "raw_physical_positions_mm": raw_positions,
    }


def _plane_groups(positions: list[float], default_spacing: float) -> tuple[list[list[float]], float]:
    positive = [right - left for left, right in zip(positions, positions[1:]) if right > left]
    spacing = statistics.median(positive) if positive else default_spacing
    groups: list[list[float]] = []
    current = [positions[0]]
    for left, right in zip(positions, positions[1:]):
        if right - left > 1.5 * spacing:
            groups.append(current)
            current = [right]
        else:
            current.append(right)
    groups.append(current)
    return groups, float(spacing)


def _reconstruct(
    planes: dict[float, list[dict[str, Any]]],
    ct: dict[str, Any],
    dose_geometry: dict[str, Any],
    include_dose: bool,
) -> dict[str, Any]:
    positions = sorted(planes)
    groups, source_spacing = _plane_groups(positions, ct["slice_spacing"])
    ct_mask = np.zeros((len(ct["positions"]), ct["rows"], ct["columns"]), dtype=bool)
    plane_masks: dict[float, np.ndarray] = {}
    slices: list[dict[str, Any]] = []
    analytic_areas: dict[float, float] = {}
    for position in positions:
        ct_index = int(np.abs(ct["positions"] - position).argmin())
        plane_mask = np.zeros((ct["rows"], ct["columns"]), dtype=bool)
        projected_polygons = []
        signed_areas = []
        degenerate = False
        self_intersection = False
        raw_positions = []
        point_count = 0
        for record in planes[position]:
            points = record["points"]
            relative = points - ct["origins"][ct_index]
            polygon_rows = relative @ ct["row_axis"] / ct["row_spacing"]
            polygon_columns = relative @ ct["column_axis"] / ct["column_spacing"]
            plane_mask ^= validated.polygon_fill(polygon_rows, polygon_columns, ct["rows"], ct["columns"])
            projected = np.column_stack((points @ ct["column_axis"], points @ ct["row_axis"]))
            signed = polygon_signed_area(projected)
            projected_polygons.append(projected)
            signed_areas.append(signed)
            degenerate = degenerate or abs(signed) <= 1.0e-6
            self_intersection = self_intersection or polygon_self_intersects(projected)
            raw_positions.append(record["raw_position"])
            point_count += len(points)
        plane_masks[position] = plane_mask
        analytic_area = sum(abs(value) for value in signed_areas)
        analytic_areas[position] = analytic_area
        nested = polygons_nested(projected_polygons)
        duplicate = near_duplicate_polygons(projected_polygons)
        index = positions.index(position)
        slices.append({
            "physical_slice_coordinate_mm": position,
            "polygon_count": len(planes[position]),
            "total_contour_points": point_count,
            "signed_polygon_areas_mm2": signed_areas,
            "sum_absolute_polygon_area_mm2": analytic_area,
            "final_even_odd_xor_area_mm2": int(np.count_nonzero(plane_mask)) * ct["row_spacing"] * ct["column_spacing"],
            "nested_polygons": nested,
            "multiple_disjoint_polygons_possible": len(projected_polygons) > 1 and not nested,
            "duplicated_or_near_duplicated_contours": duplicate,
            "degenerate_polygon": degenerate,
            "self_intersection_detected": self_intersection,
            "raw_position_min_mm": min(raw_positions),
            "raw_position_max_mm": max(raw_positions),
            "raw_position_spread_mm": max(raw_positions) - min(raw_positions),
            "nearly_identical_nonidentical_plane_positions": max(raw_positions) - min(raw_positions) > 0,
            "spacing_to_previous_mm": None if index == 0 else position - positions[index - 1],
            "spacing_to_next_mm": None if index == len(positions) - 1 else positions[index + 1] - position,
        })
    for ct_index, ct_position in enumerate(ct["positions"]):
        active = next((group for group in groups if group[0] - source_spacing / 2 <= ct_position <= group[-1] + source_spacing / 2), None)
        if active is None:
            continue
        source = min(active, key=lambda value: abs(value - ct_position))
        ct_mask[ct_index] = plane_masks[source]

    between = 0.0
    end_planes = 0.0
    for group in groups:
        if len(group) == 1:
            end_planes += analytic_areas[group[0]] * ct["slice_spacing"]
        else:
            between += sum(
                0.5 * (analytic_areas[left] + analytic_areas[right]) * (right - left)
                for left, right in zip(group, group[1:])
            )
            end_planes += 0.5 * analytic_areas[group[0]] * (group[1] - group[0])
            end_planes += 0.5 * analytic_areas[group[-1]] * (group[-1] - group[-2])
    contour_volume = validated._contour_stack_volume_cc(positions, analytic_areas, ct["slice_spacing"])
    ct_voxel_cc = ct["slice_spacing"] * ct["row_spacing"] * ct["column_spacing"] / 1000.0
    dose_voxel_cc = validated.voxel_volume_cc(dose_geometry)
    dose_mask = None
    if include_dose:
        dose_mask = np.zeros(dose_geometry["shape"], dtype=bool)
        occupied_ct_indices = np.flatnonzero(np.any(ct_mask, axis=(1, 2)))
        grid_rows, grid_columns = np.mgrid[0:dose_geometry["shape"][1], 0:dose_geometry["shape"][2]]
        for frame in range(dose_geometry["shape"][0]):
            frame_origin = dose_geometry["origin"] + dose_geometry["offsets"][frame] * dose_geometry["normal"]
            last_row = (dose_geometry["shape"][1] - 1) * dose_geometry["spacing"][0] * dose_geometry["col_dir"]
            last_column = (dose_geometry["shape"][2] - 1) * dose_geometry["spacing"][1] * dose_geometry["row_dir"]
            projections = np.asarray([
                frame_origin @ ct["normal"],
                (frame_origin + last_row) @ ct["normal"],
                (frame_origin + last_column) @ ct["normal"],
                (frame_origin + last_row + last_column) @ ct["normal"],
            ])
            endpoints = validated._nearest_sorted_indices(ct["positions"], np.asarray([projections.min(), projections.max()]))
            lower, upper = sorted(map(int, endpoints))
            if not np.any((occupied_ct_indices >= lower) & (occupied_ct_indices <= upper)):
                continue
            points = (
                frame_origin
                + grid_columns[..., None] * dose_geometry["spacing"][1] * dose_geometry["row_dir"]
                + grid_rows[..., None] * dose_geometry["spacing"][0] * dose_geometry["col_dir"]
            )
            projected = points @ ct["normal"]
            slice_indices = validated._nearest_sorted_indices(ct["positions"], projected)
            relative = points - ct["origins"][slice_indices]
            ct_rows = np.floor(relative @ ct["row_axis"] / ct["row_spacing"] + 0.5).astype(int)
            ct_columns = np.floor(relative @ ct["column_axis"] / ct["column_spacing"] + 0.5).astype(int)
            valid = (ct_rows >= 0) & (ct_rows < ct["rows"]) & (ct_columns >= 0) & (ct_columns < ct["columns"])
            sampled = np.zeros(valid.shape, dtype=bool)
            sampled[valid] = ct_mask[slice_indices[valid], ct_rows[valid], ct_columns[valid]]
            dose_mask[frame] = sampled
    return {
        "ct_mask": ct_mask,
        "dose_mask": dose_mask,
        "slices": slices,
        "source_spacing_mm": source_spacing,
        "groups": groups,
        "contour_volume_cc": contour_volume,
        "ct_volume_cc": int(np.count_nonzero(ct_mask)) * ct_voxel_cc,
        "dose_volume_cc": None if dose_mask is None else int(np.count_nonzero(dose_mask)) * dose_voxel_cc,
        "ct_voxel_count": int(np.count_nonzero(ct_mask)),
        "dose_voxel_count": None if dose_mask is None else int(np.count_nonzero(dose_mask)),
        "ct_voxel_volume_cc": ct_voxel_cc,
        "dose_voxel_volume_cc": dose_voxel_cc,
        "between_plane_trapezoids_cc": between / 1000.0,
        "end_plane_contribution_cc": end_planes / 1000.0,
    }


def _diagnostic_conclusion(item: dict[str, Any]) -> DiagnosticConclusion:
    values = item["volume_representations"]
    precision = item["eclipse_reference_precision"]["reported_resolution_cc"]
    smallest = min(abs(values["differences"][key]["absolute_cc"]) for key in (
        "eclipse_minus_contour", "eclipse_minus_ct", "eclipse_minus_dose",
    ))
    reproducible = item["mask_reproducibility"]["dose_stored_vs_rerun_bitwise_equal"]
    anomaly = any(item["contour_stack_diagnostics"]["summary"][key] for key in (
        "near_duplicate_contours_detected", "self_intersection_detected", "degenerate_contours_detected",
    ))
    if anomaly:
        return DiagnosticConclusion(
            "multiple_contributing_factors", "moderate",
            "An RTSTRUCT geometry feature was detected, and all ASCEND volume representations remain outside the Eclipse reporting resolution.",
            True,
        )
    if reproducible and smallest > precision / 2:
        return DiagnosticConclusion(
            "contour_representation_difference", "moderate",
            "All three ASCEND representations remain above the Eclipse value by more than the half-resolution rounding interval. The stored dose mask is reproduced bitwise; the locked end-plane convention explains only part of the difference. Eclipse omits its internal volume convention, so exact causation remains unresolved.",
            True,
        )
    return DiagnosticConclusion(
        "unresolved", "low",
        "Available evidence does not isolate one geometric or reporting mechanism.",
        True,
    )


class EclipseVolumeDiagnosticService:
    """Coordinate the eclipse volume diagnostic workflow without GUI-side calculation."""
    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[3]

    def run(
        self,
        case: ASCENDCase,
        comparison_path: str | Path | None = None,
        output_directory: str | Path | None = None,
    ) -> dict[str, Any]:
        """Execute run and return its explicit calculation state and evidence."""
        comparison_file = Path(comparison_path) if comparison_path else case.root / "validation" / "eclipse_dvh" / "results" / "eclipse_dvh_comparisons.json"
        comparison = json.loads(comparison_file.read_text(encoding="utf-8"))
        target_records = {name: _formal_volume_record(comparison, name) for name in TARGETS}
        referenced_layer1_paths = {
            Path(record.get("provenance", {}).get("layer1_result_path", ""))
            for record in target_records.values()
        }
        referenced_layer1_paths.discard(Path("."))
        layer1_result_path = next(iter(referenced_layer1_paths)) if len(referenced_layer1_paths) == 1 else Path(case.layer1.result_path or "")
        if not layer1_result_path.is_file():
            raise ValueError("The Layer 1 result referenced by the formal comparison is unavailable.")
        layer1 = json.loads(layer1_result_path.read_text(encoding="utf-8"))
        manifest = layer1["manifest"]
        mask_archive = Path(manifest["mask_export"]["path"])
        native_dose = Path(manifest["validated_native_dose"]["path"])
        masks = np.load(mask_archive, allow_pickle=False)
        structure_path = Path(case.selected_objects["rtstruct"])
        structure = pydicom.dcmread(structure_path, stop_before_pixels=True)
        dose_header = pydicom.dcmread(case.selected_objects["rtdose"], stop_before_pixels=True)
        dose_geometry = normalise_rtdose_geometry(dose_header, validate_pixels=False)
        ct_paths = [Path(item) for item in case.selected_objects.get("image_series", [])]
        ct = _ct_geometry(ct_paths)
        inventory = {item["original_name"].casefold(): item for item in manifest["roi_inventory"]}
        roi_definitions = {int(item.ROINumber): item for item in getattr(structure, "StructureSetROISequence", [])}
        volume_definitions = manifest["rasterisation"]["volume_definitions"]
        role_by_name = {str(value).casefold(): role for role, value in case.configuration.structure_roles.items() if isinstance(value, str)}
        structures: list[dict[str, Any]] = []
        reconstructed_masks: dict[str, np.ndarray] = {}
        for name in TARGETS:
            formal = target_records[name]
            roi_number = int(formal["roi_number"])
            roi_definition = roi_definitions[roi_number]
            inventory_record = inventory[name.casefold()]
            canonical = formal["canonical_structure"]
            planes, contour_identity = _roi_contours(structure, roi_number, ct)
            reconstruction = _reconstruct(planes, ct, dose_geometry, include_dose=True)
            second_ct = _reconstruct(planes, ct, dose_geometry, include_dose=False)["ct_mask"]
            stored_dose_mask = np.asarray(masks[canonical], dtype=bool)
            rerun_dose_mask = reconstruction["dose_mask"]
            assert rerun_dose_mask is not None
            reconstructed_masks[name] = rerun_dose_mask
            stored_volumes = volume_definitions[canonical]
            for key, calculated in (
                ("anatomical_volume_contour_cc", reconstruction["contour_volume_cc"]),
                ("anatomical_volume_ct_cc", reconstruction["ct_volume_cc"]),
                ("dose_sampled_volume_cc", reconstruction["dose_volume_cc"]),
            ):
                if not np.isclose(float(stored_volumes[key]), float(calculated), atol=1.0e-9, rtol=0):
                    raise ValueError(f"Diagnostic reconstruction changed {name} {key}: {calculated} versus stored {stored_volumes[key]}")
            slice_rows = [{"structure": name, **row} for row in reconstruction["slices"]]
            topology_summary = {
                "physical_plane_count": len(planes),
                "polygon_count": contour_identity["total_polygon_count"],
                "total_contour_points": contour_identity["total_contour_point_count"],
                "planes_with_multiple_polygons": sum(row["polygon_count"] > 1 for row in reconstruction["slices"]),
                "nested_polygons_detected": any(row["nested_polygons"] for row in reconstruction["slices"]),
                "near_duplicate_contours_detected": any(row["duplicated_or_near_duplicated_contours"] for row in reconstruction["slices"]),
                "self_intersection_detected": any(row["self_intersection_detected"] for row in reconstruction["slices"]),
                "degenerate_contours_detected": any(row["degenerate_polygon"] for row in reconstruction["slices"]),
                "near_identical_nonidentical_planes_detected": any(row["nearly_identical_nonidentical_plane_positions"] for row in reconstruction["slices"]),
                "contour_plane_groups": [[group[0], group[-1], len(group)] for group in reconstruction["groups"]],
                "nominal_source_spacing_mm": reconstruction["source_spacing_mm"],
            }
            connectivity, component_count = ndimage.label(rerun_dose_mask, structure=np.ones((3, 3, 3), dtype=np.uint8))
            del connectivity
            dose_context = [
                {
                    "endpoint": record["endpoint"],
                    "ascend_value": record["ascend_value"],
                    "eclipse_value": record["eclipse_value"],
                    "units": record["units"],
                    "comparison_status": record["comparison_status"],
                    "pass_fail": record["pass_fail"],
                }
                for record in _formal_records(comparison, name)
                if record.get("endpoint") != "Volume"
            ]
            item = {
                "rtstruct_sop_instance_uid": str(structure.SOPInstanceUID),
                "roi_number": roi_number,
                "roi_name": str(roi_definition.ROIName),
                "roi_generation_algorithm": str(getattr(roi_definition, "ROIGenerationAlgorithm", "")) or None,
                **contour_identity,
                "physical_contour_slice_positions_mm": sorted(planes),
                "canonical_ascend_role": role_by_name.get(name.casefold()),
                "canonical_structure": canonical,
                "rasterisation_selection_reason": inventory_record["selection_reason"],
                "eclipse_harness_structure_identity": formal["structure_identity"],
                "formal_volume_finding": {
                    key: formal.get(key) for key in (
                        "comparison_status", "pass_fail", "eclipse_value", "ascend_value", "delta", "absolute_delta", "acceptance_limit", "acceptance_limit_units", "matching_status",
                    )
                },
                "eclipse_reference_precision": _reference_precision(comparison, name),
                "volume_representations": three_volume_comparison(
                    name, formal["eclipse_value"], stored_volumes["anatomical_volume_contour_cc"],
                    stored_volumes["anatomical_volume_ct_cc"], stored_volumes["dose_sampled_volume_cc"],
                ),
                "voxel_geometry": {
                    "ct_occupied_voxel_count": reconstruction["ct_voxel_count"],
                    "rtdose_occupied_voxel_count": reconstruction["dose_voxel_count"],
                    "ct_voxel_dimensions_mm": [ct["slice_spacing"], ct["row_spacing"], ct["column_spacing"]],
                    "rtdose_voxel_dimensions_mm": [float(value) for value in dose_geometry["spacing_zyx_mm"]],
                    "ct_voxel_volume_cc": reconstruction["ct_voxel_volume_cc"],
                    "rtdose_voxel_volume_cc": reconstruction["dose_voxel_volume_cc"],
                },
                "contour_stack_diagnostics": {
                    "summary": topology_summary,
                    "volume_decomposition": {
                        "between_plane_trapezoids_cc": reconstruction["between_plane_trapezoids_cc"],
                        "end_plane_contribution_cc": reconstruction["end_plane_contribution_cc"],
                        "locked_total_contour_stack_cc": reconstruction["contour_volume_cc"],
                        "analysis_only_without_end_plane_terms_cc": reconstruction["between_plane_trapezoids_cc"],
                    },
                    "slices": slice_rows,
                },
                "aggregate_component_analysis": aggregate_component_comparison(rerun_dose_mask, [], reconstruction["dose_voxel_volume_cc"]),
                "structural_context": {
                    "standalone_explicit_roi": True,
                    "individual_component_rois_available": False,
                    "connected_components_26": int(component_count),
                },
                "dose_endpoint_context": dose_context,
                "mask_reproducibility": {
                    "ct_analysis_first_hash": array_hash(reconstruction["ct_mask"]),
                    "ct_analysis_second_hash": array_hash(second_ct),
                    "ct_analysis_rerun_bitwise_equal": bool(np.array_equal(reconstruction["ct_mask"], second_ct)),
                    "stored_ct_mask_available": False,
                    "stored_ct_mask_limitation": "Layer 1 stores the CT-grid volume and dose-grid mask, but not the CT-grid mask array.",
                    "dose_mask_original_hash": array_hash(stored_dose_mask),
                    "dose_mask_rerun_hash": array_hash(rerun_dose_mask),
                    "dose_stored_vs_rerun_bitwise_equal": bool(np.array_equal(stored_dose_mask, rerun_dose_mask)),
                },
            }
            structures.append(item)
            del second_ct, reconstruction

        dose_voxel_cc = validated.voxel_volume_cc(dose_geometry)
        gtv = np.asarray(masks[manifest["effective_structure_roles"]["GTV"]], dtype=bool)
        overlap = [
            overlap_metrics("all_vertices", reconstructed_masks["all_vertices"], "GTV", gtv, dose_voxel_cc),
            overlap_metrics("all_valleys", reconstructed_masks["all_valleys"], "GTV", gtv, dose_voxel_cc),
            overlap_metrics("all_vertices", reconstructed_masks["all_vertices"], "all_valleys", reconstructed_masks["all_valleys"], dose_voxel_cc),
        ]
        overlap_lookup = {(item["structure_a"], item["structure_b"]): item for item in overlap}
        for item in structures:
            name = item["roi_name"]
            item["structural_context"]["overlap_with_gtv"] = overlap_lookup[(name, "GTV")]
            item["structural_context"]["overlap_between_peak_and_valley"] = overlap_lookup[("all_vertices", "all_valleys")]
            item["diagnostic_interpretation"] = _diagnostic_conclusion(item).to_dict()

        run = {
            "schema_version": SCHEMA_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "case_id": case.case_id,
            "objective": "Validation diagnostics for preserved all_vertices and all_valleys Eclipse volume failures.",
            "no_cerr_evidence_available": True,
            "scientific_baseline": {
                "ascend_version": __version__,
                "locked_scientific_source_hashes": _locked_hashes(self.project_root),
                "layer1_result_path": str(layer1_result_path),
                "layer1_result_sha256": sha256_file(layer1_result_path),
                "native_rtdose_artifact_sha256": sha256_file(native_dose),
                "native_mask_archive_sha256": sha256_file(mask_archive),
                "individual_stored_mask_hashes": {name: array_hash(np.asarray(masks[name], dtype=bool)) for name in masks.files if name != "dose_gy"},
            },
            "evidence": {
                "formal_comparison_path": str(comparison_file.resolve()),
                "formal_comparison_sha256": sha256_file(comparison_file),
                "rtstruct_path": str(structure_path),
                "rtstruct_sha256": sha256_file(structure_path),
                "rtdose_uid": manifest["rtdose_uid"],
                "rtstruct_uid": manifest["rtstruct_uid"],
                "rtplan_uid": manifest["rtplan_uid"],
            },
            "structures": structures,
            "overlap_analysis": overlap,
            "aggregate_component_outputs_applicable": False,
            "individual_vertex_outputs_applicable": False,
            "limitations": [
                "The Eclipse TXT source contains no RTSTRUCT SOP Instance UID or ROI number; matching remains the preserved unique fallback.",
                "The Eclipse TXT source reports one decimal place for structure volume and does not document its internal volume algorithm.",
                "Layer 1 does not store the CT mask array; CT reproducibility compares two independent diagnostic reconstructions, while dose reproducibility compares the stored mask with the reconstruction.",
                "No individual vertex or valley ROIs exist in this RTSTRUCT, so aggregate-versus-individual union testing is not applicable.",
                "No CERR evidence is available for this plan.",
            ],
            "formal_failures_preserved": all(item["formal_volume_finding"]["pass_fail"] == "fail" for item in structures),
            "scientific_algorithm_changed": False,
        }
        destination = Path(output_directory) if output_directory else case.root / "validation" / "eclipse_dvh" / "volume_diagnostics"
        run["artifacts"] = write_outputs(run, destination)
        return run
