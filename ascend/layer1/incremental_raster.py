"""Incremental mask rasterization adapter that limits peak memory while preserving locked behavior."""

from __future__ import annotations

import contextlib
import mmap
import statistics
import threading
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pydicom

from ascend.scientific.legacy import layer1_validated as validated


_PATCH_LOCK = threading.RLock()


def masks_from_struct_incremental(
    struct: Any,
    geo: dict[str, Any],
    ct_paths: list[Path],
    result: Any,
    manually_confirmed_gtv: str,
    scratch: Path,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Handle masks from struct incremental for the enclosing ASCEND workflow."""
    mapping = validated.map_rois(struct, result, manually_confirmed_gtv)
    if len(ct_paths) < 2:
        raise ValueError("The complete referenced planning CT series is required for validated CT-grid rasterisation.")
    ct_datasets = [pydicom.dcmread(path, stop_before_pixels=True) for path in ct_paths]
    first = ct_datasets[0]
    orientation = np.asarray(first.ImageOrientationPatient, dtype=float)
    column_axis = orientation[:3] / np.linalg.norm(orientation[:3])
    row_axis = orientation[3:] / np.linalg.norm(orientation[3:])
    normal = np.cross(column_axis, row_axis); normal /= np.linalg.norm(normal)
    row_spacing, column_spacing = map(float, first.PixelSpacing)
    rows, columns = int(first.Rows), int(first.Columns)
    ordered = sorted((
        float(np.dot(np.asarray(dataset.ImagePositionPatient, dtype=float), normal)),
        np.asarray(dataset.ImagePositionPatient, dtype=float),
    ) for dataset in ct_datasets)
    ct_positions = np.asarray([position for position, _origin in ordered], dtype=float)
    ct_origins = np.stack([origin for _position, origin in ordered])
    ct_spacing = validated._median_positive_spacing(ct_positions)
    planes_by_structure: dict[str, dict[float, list[np.ndarray]]] = {}
    for roi in getattr(struct, "ROIContourSequence", []):
        canonical = mapping.get(int(getattr(roi, "ReferencedROINumber", -1)))
        if not canonical:
            continue
        planes = planes_by_structure.setdefault(canonical, {})
        for contour in getattr(roi, "ContourSequence", []):
            contour_type = str(getattr(contour, "ContourGeometricType", "CLOSED_PLANAR")).upper()
            data = np.asarray(getattr(contour, "ContourData", []), dtype=float)
            if contour_type == "POINT" or data.size < 9 or data.size % 3:
                continue
            if contour_type not in {"CLOSED_PLANAR", "CLOSEDPLANAR_XOR"}:
                continue
            points = data.reshape(-1, 3)
            position = round(float(np.mean(points @ normal)), 4)
            planes.setdefault(position, []).append(points)
    dose_grid_rows, dose_grid_columns = np.mgrid[0:geo["shape"][1], 0:geo["shape"][2]]
    dose_voxel_cc = validated.voxel_volume_cc(geo)
    masks: dict[str, np.ndarray] = {}
    volume_definitions: dict[str, dict[str, float]] = {}
    roi_details: list[dict[str, Any]] = []
    scratch.mkdir(parents=True, exist_ok=True)
    # Process one selected canonical structure at a time. The CT and writable
    # dose masks are released before the next structure is started.
    for canonical in sorted(planes_by_structure):
        planes = planes_by_structure[canonical]
        source_positions = sorted(planes)
        if not source_positions:
            continue
        positive = [right - left for left, right in zip(source_positions, source_positions[1:]) if right > left]
        source_spacing = statistics.median(positive) if positive else ct_spacing
        groups: list[list[float]] = []
        current = [source_positions[0]]
        for left, right in zip(source_positions, source_positions[1:]):
            if right - left > 1.5 * source_spacing:
                groups.append(current); current = [right]
            else:
                current.append(right)
        groups.append(current)
        ct_mask = np.zeros((len(ordered), rows, columns), dtype=bool)
        for ct_index, ct_position in enumerate(ct_positions):
            active = next((group for group in groups if group[0] - source_spacing / 2 <= ct_position <= group[-1] + source_spacing / 2), None)
            if active is None:
                continue
            source = min(active, key=lambda value: abs(value - ct_position))
            plane_mask = np.zeros((rows, columns), dtype=bool)
            for points in planes[source]:
                relative = points - ct_origins[ct_index]
                polygon_rows = relative @ row_axis / row_spacing
                polygon_columns = relative @ column_axis / column_spacing
                plane_mask ^= validated.polygon_fill(polygon_rows, polygon_columns, rows, columns)
            ct_mask[ct_index] = plane_mask
        areas = {
            position: sum(validated._polygon_area_mm2(points, column_axis, row_axis) for points in polygons)
            for position, polygons in planes.items()
        }
        contour_volume = validated._contour_stack_volume_cc(source_positions, areas, ct_spacing)
        ct_volume = float(ct_mask.sum() * ct_spacing * row_spacing * column_spacing / 1000.0)
        dose_mask = np.zeros(geo["shape"], dtype=bool)
        occupied_ct_indices = np.flatnonzero(np.any(ct_mask, axis=(1, 2)))
        for frame in range(geo["shape"][0]):
            frame_origin = geo["origin"] + geo["offsets"][frame] * geo["normal"]
            last_row = (geo["shape"][1] - 1) * geo["spacing"][0] * geo["col_dir"]
            last_column = (geo["shape"][2] - 1) * geo["spacing"][1] * geo["row_dir"]
            corner_projections = np.asarray([
                frame_origin @ normal,
                (frame_origin + last_row) @ normal,
                (frame_origin + last_column) @ normal,
                (frame_origin + last_row + last_column) @ normal,
            ])
            endpoint_indices = validated._nearest_sorted_indices(
                ct_positions, np.asarray([corner_projections.min(), corner_projections.max()])
            )
            lower, upper = sorted(map(int, endpoint_indices))
            if not np.any((occupied_ct_indices >= lower) & (occupied_ct_indices <= upper)):
                continue
            points = (
                frame_origin
                + dose_grid_columns[..., None] * geo["spacing"][1] * geo["row_dir"]
                + dose_grid_rows[..., None] * geo["spacing"][0] * geo["col_dir"]
            )
            projected = points @ normal
            slice_indices = validated._nearest_sorted_indices(ct_positions, projected)
            relative = points - ct_origins[slice_indices]
            ct_rows = np.floor(relative @ row_axis / row_spacing + 0.5).astype(int)
            ct_columns = np.floor(relative @ column_axis / column_spacing + 0.5).astype(int)
            valid = (ct_rows >= 0) & (ct_rows < rows) & (ct_columns >= 0) & (ct_columns < columns)
            sampled = np.zeros(valid.shape, dtype=bool)
            sampled[valid] = ct_mask[slice_indices[valid], ct_rows[valid], ct_columns[valid]]
            dose_mask[frame] = sampled
        volume_definitions[canonical] = {
            "anatomical_volume_contour_cc": contour_volume,
            "anatomical_volume_ct_cc": ct_volume,
            "dose_sampled_volume_cc": float(dose_mask.sum() * dose_voxel_cc),
        }
        roi_details.append({
            "standard_name": canonical,
            "source_planes": len(source_positions),
            "ct_planes_occupied": int(np.any(ct_mask, axis=(1, 2)).sum()),
            "dose_planes_occupied": int(np.any(dose_mask, axis=(1, 2)).sum()),
            **volume_definitions[canonical],
        })
        mask_path = scratch / f"mask_{len(masks):04d}.npy"
        np.save(mask_path, dose_mask, allow_pickle=False)
        masks[canonical] = np.load(mask_path, mmap_mode="r", allow_pickle=False)
        del ct_mask, dose_mask
    for name, mask in masks.items():
        if not mask.any():
            result.add("BLOCK", "Structure geometry", f"{name} mask is empty after CT-to-dose transfer.")
    for required in validated.REQUIRED_STRUCTURES:
        if required not in masks:
            result.add("BLOCK", "Structure geometry", f"{required} has no usable closed-planar contours.")
    summary = {
        "standard_id": validated.RASTER_STANDARD,
        "method": "incremental CT voxel-centre half-open XOR rasterisation; gap-aware nearest-source-plane propagation; nearest-neighbour CT-to-RTDOSE transfer",
        "processing": "one selected canonical structure at a time with read-only staged mask memory maps",
        "ct_grid": {"dimensions": [len(ordered), rows, columns], "voxel_spacing_mm": [ct_spacing, row_spacing, column_spacing]},
        "volume_definitions": volume_definitions,
        "roi_details": roi_details,
    }
    return masks, summary


@contextlib.contextmanager
def incremental_rasterisation(scratch: Path) -> Iterator[None]:
    """Temporarily route the locked validator through a behavior-equivalent low-memory raster adapter."""
    with _PATCH_LOCK:
        original = validated.masks_from_struct
        original_metrics = validated.independent_metrics

        def adapter(struct: Any, geo: dict[str, Any], ct_paths: list[Path], result: Any, manually_confirmed_gtv: str = ""):
            return masks_from_struct_incremental(struct, geo, ct_paths, result, manually_confirmed_gtv, scratch)

        def metrics_adapter(array: np.ndarray, mask: np.ndarray, voxel_cc: float):
            metrics = original_metrics(array, mask, voxel_cc)
            mapped = getattr(mask, "_mmap", None)
            advise = getattr(mapped, "madvise", None)
            if advise is not None and hasattr(mmap, "MADV_DONTNEED"):
                advise(mmap.MADV_DONTNEED)
            return metrics

        validated.masks_from_struct = adapter
        validated.independent_metrics = metrics_adapter
        try:
            yield
        finally:
            validated.masks_from_struct = original
            validated.independent_metrics = original_metrics
