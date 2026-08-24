"""Geometry calculations used by DICOM ingestion or validation diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
from typing import Any, Iterable

import numpy as np


DIAGNOSTIC_CLASSIFICATIONS = {
    "unresolved",
    "eclipse_reporting_precision_possible",
    "contour_representation_difference",
    "ct_voxelisation_difference",
    "dose_grid_sampling_difference",
    "aggregate_component_geometry_difference",
    "component_overlap_effect",
    "rtstruct_geometry_anomaly",
    "possible_ascend_reconstruction_issue",
    "multiple_contributing_factors",
}


@dataclass(frozen=True)
class DiagnosticConclusion:
    """Represent diagnostic conclusion state and behavior."""
    classification: str
    confidence: str
    evidence_summary: str
    discrepancy_unresolved: bool
    scientific_algorithm_change_required: bool = False

    def __post_init__(self) -> None:
        if self.classification not in DIAGNOSTIC_CLASSIFICATIONS:
            raise ValueError(f"Unsupported diagnostic classification: {self.classification}")
        if self.confidence not in {"high", "moderate", "low"}:
            raise ValueError(f"Unsupported diagnostic confidence: {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        return asdict(self)


def array_hash(mask: np.ndarray) -> str:
    """Handle array hash for the enclosing ASCEND workflow."""
    value = np.ascontiguousarray(np.asarray(mask, dtype=np.uint8))
    return hashlib.sha256(value.tobytes()).hexdigest()


def _relative_difference(numerator: float, denominator: float) -> float | None:
    if abs(denominator) <= 1.0e-12:
        return None
    return 100.0 * numerator / abs(denominator)


def three_volume_comparison(
    structure: str,
    eclipse_volume_cc: float,
    contour_volume_cc: float,
    ct_volume_cc: float,
    dose_volume_cc: float,
) -> dict[str, Any]:
    """Handle three volume comparison for the enclosing ASCEND workflow."""
    values = {
        "eclipse": float(eclipse_volume_cc),
        "contour": float(contour_volume_cc),
        "ct": float(ct_volume_cc),
        "dose": float(dose_volume_cc),
    }
    differences: dict[str, dict[str, float | None]] = {}
    for left, right in (
        ("eclipse", "contour"), ("eclipse", "ct"), ("eclipse", "dose"),
        ("ct", "contour"), ("dose", "contour"), ("dose", "ct"),
    ):
        delta = values[left] - values[right]
        differences[f"{left}_minus_{right}"] = {
            "absolute_cc": delta,
            "relative_to_second_percent": _relative_difference(delta, values[right]),
        }
    return {
        "structure": structure,
        "eclipse_volume_cc": values["eclipse"],
        "anatomical_volume_contour_cc": values["contour"],
        "anatomical_volume_ct_cc": values["ct"],
        "dose_sampled_volume_cc": values["dose"],
        "differences": differences,
        "formal_harness_comparator": "anatomical_volume_contour_cc",
    }


def mask_comparison(first: np.ndarray, second: np.ndarray, voxel_volume_cc: float) -> dict[str, Any]:
    """Handle mask comparison for the enclosing ASCEND workflow."""
    a = np.asarray(first, dtype=bool)
    b = np.asarray(second, dtype=bool)
    if a.shape != b.shape:
        raise ValueError(f"Mask shapes differ: {a.shape} versus {b.shape}")
    intersection = int(np.count_nonzero(a & b))
    first_count = int(np.count_nonzero(a))
    second_count = int(np.count_nonzero(b))
    explicit_only = int(np.count_nonzero(a & ~b))
    union_only = int(np.count_nonzero(b & ~a))
    symmetric = explicit_only + union_only
    denominator = first_count + second_count
    return {
        "first_voxel_count": first_count,
        "second_voxel_count": second_count,
        "intersection_voxels": intersection,
        "intersection_volume_cc": intersection * voxel_volume_cc,
        "explicit_only_voxels": explicit_only,
        "explicit_only_volume_cc": explicit_only * voxel_volume_cc,
        "union_only_voxels": union_only,
        "union_only_volume_cc": union_only * voxel_volume_cc,
        "symmetric_difference_voxels": symmetric,
        "symmetric_difference_volume_cc": symmetric * voxel_volume_cc,
        "dice_coefficient": 1.0 if denominator == 0 else 2.0 * intersection / denominator,
        "bitwise_equal": bool(np.array_equal(a, b)),
    }


def aggregate_component_comparison(
    explicit_mask: np.ndarray,
    component_masks: Iterable[np.ndarray],
    voxel_volume_cc: float,
) -> dict[str, Any]:
    """Handle aggregate component comparison for the enclosing ASCEND workflow."""
    components = [np.asarray(item, dtype=bool) for item in component_masks]
    if not components:
        return {
            "available": False,
            "reason": "no_individual_component_rois_available",
            "component_count": 0,
        }
    shape = np.asarray(explicit_mask).shape
    if any(item.shape != shape for item in components):
        raise ValueError("Component-mask shapes must match the explicit aggregate mask.")
    union = np.logical_or.reduce(components)
    sum_counts = sum(int(np.count_nonzero(item)) for item in components)
    union_count = int(np.count_nonzero(union))
    comparison = mask_comparison(explicit_mask, union, voxel_volume_cc)
    return {
        "available": True,
        "component_count": len(components),
        "explicit_aggregate_volume_cc": int(np.count_nonzero(explicit_mask)) * voxel_volume_cc,
        "union_volume_cc": union_count * voxel_volume_cc,
        "sum_individual_volume_cc": sum_counts * voxel_volume_cc,
        "union_voxel_count": union_count,
        "sum_individual_voxel_counts": sum_counts,
        "overlap_voxel_count": sum_counts - union_count,
        **comparison,
    }


def overlap_metrics(
    name_a: str,
    mask_a: np.ndarray,
    name_b: str,
    mask_b: np.ndarray,
    voxel_volume_cc: float,
) -> dict[str, Any]:
    """Handle overlap metrics for the enclosing ASCEND workflow."""
    a = np.asarray(mask_a, dtype=bool)
    b = np.asarray(mask_b, dtype=bool)
    if a.shape != b.shape:
        raise ValueError(f"Mask shapes differ: {a.shape} versus {b.shape}")
    count_a = int(np.count_nonzero(a))
    count_b = int(np.count_nonzero(b))
    intersection = int(np.count_nonzero(a & b))
    return {
        "structure_a": name_a,
        "structure_b": name_b,
        "intersection_voxels": intersection,
        "intersection_volume_cc": intersection * voxel_volume_cc,
        "fraction_a_overlapping_b_percent": None if count_a == 0 else 100.0 * intersection / count_a,
        "fraction_b_overlapping_a_percent": None if count_b == 0 else 100.0 * intersection / count_b,
        "a_fully_contained_in_b": bool(count_a > 0 and intersection == count_a),
        "b_fully_contained_in_a": bool(count_b > 0 and intersection == count_b),
    }


def contour_slice_groups(positions: Iterable[float], precision: int = 4) -> list[dict[str, Any]]:
    """Handle contour slice groups for the enclosing ASCEND workflow."""
    grouped: dict[float, list[float]] = {}
    for raw in positions:
        grouped.setdefault(round(float(raw), precision), []).append(float(raw))
    ordered = sorted(grouped)
    output = []
    for index, position in enumerate(ordered):
        raw_values = grouped[position]
        output.append({
            "physical_slice_coordinate_mm": position,
            "contour_count": len(raw_values),
            "raw_position_min_mm": min(raw_values),
            "raw_position_max_mm": max(raw_values),
            "raw_position_spread_mm": max(raw_values) - min(raw_values),
            "repeated_physical_slice_position": len(raw_values) > 1,
            "spacing_to_previous_mm": None if index == 0 else position - ordered[index - 1],
            "spacing_to_next_mm": None if index == len(ordered) - 1 else ordered[index + 1] - position,
        })
    return output


def parse_eclipse_volume_precision(text: str, roi_name: str) -> dict[str, Any]:
    """Parse eclipse volume precision using the documented input contract."""
    structure_pattern = re.compile(r"^Structure:\s*(.*?)\s*$", re.MULTILINE | re.IGNORECASE)
    matches = list(structure_pattern.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().casefold() != roi_name.strip().casefold():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end():end]
        volume = re.search(r"^Volume\s*\[(?:cm³|cc|cm3)\]\s*:\s*([^\s]+)\s*$", section, re.MULTILINE | re.IGNORECASE)
        if volume is None:
            raise ValueError(f"Eclipse structure section {roi_name!r} has no Volume field.")
        token = volume.group(1).strip().replace(",", ".")
        try:
            numeric = Decimal(token)
        except InvalidOperation as exc:
            raise ValueError(f"Eclipse volume for {roi_name!r} is not numeric: {token!r}") from exc
        decimal_places = max(0, -numeric.as_tuple().exponent)
        resolution = float(Decimal(1).scaleb(-decimal_places))
        volume_occurrences = re.findall(
            r"^Volume\s*\[(?:cm³|cc|cm3)\]\s*:\s*([^\s]+)\s*$",
            section, re.MULTILINE | re.IGNORECASE,
        )
        return {
            "roi_name": match.group(1).strip(),
            "field_label": volume.group(0).split(":", 1)[0].strip(),
            "reported_text": token,
            "reported_value_cc": float(numeric),
            "displayed_decimal_places": decimal_places,
            "reported_resolution_cc": resolution,
            "higher_precision_volume_present_in_same_structure_section": any(
                max(0, -Decimal(item.replace(",", ".")).as_tuple().exponent) > decimal_places
                for item in volume_occurrences
            ),
        }
    raise ValueError(f"Eclipse structure {roi_name!r} was not found.")


def polygon_signed_area(points_xy: np.ndarray) -> float:
    """Handle polygon signed area for the enclosing ASCEND workflow."""
    points = np.asarray(points_xy, dtype=float)
    if points.shape[0] < 3:
        return 0.0
    x, y = points[:, 0], points[:, 1]
    return float(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def point_in_polygon(point: np.ndarray, polygon: np.ndarray) -> bool:
    """Handle point in polygon for the enclosing ASCEND workflow."""
    x, y = map(float, point)
    poly = np.asarray(polygon, dtype=float)
    inside = False
    previous = len(poly) - 1
    for current in range(len(poly)):
        xi, yi = poly[current]
        xj, yj = poly[previous]
        if (yi > y) != (yj > y):
            crossing = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def polygons_nested(polygons_xy: list[np.ndarray]) -> bool:
    """Handle polygons nested for the enclosing ASCEND workflow."""
    for index, polygon in enumerate(polygons_xy):
        if not len(polygon):
            continue
        probe = polygon[0]
        if any(point_in_polygon(probe, other) for other_index, other in enumerate(polygons_xy) if other_index != index):
            return True
    return False


def _orientation(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def polygon_self_intersects(points_xy: np.ndarray, tolerance: float = 1.0e-9) -> bool:
    """Handle polygon self intersects for the enclosing ASCEND workflow."""
    points = np.asarray(points_xy, dtype=float)
    count = len(points)
    if count < 4:
        return False
    for first in range(count):
        a, b = points[first], points[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count} or (second + 1) % count == first:
                continue
            c, d = points[second], points[(second + 1) % count]
            o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
            o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
            if o1 * o2 < -tolerance and o3 * o4 < -tolerance:
                return True
    return False


def near_duplicate_polygons(polygons_xy: list[np.ndarray], tolerance_mm: float = 1.0e-3) -> bool:
    """Handle near duplicate polygons for the enclosing ASCEND workflow."""
    for first in range(len(polygons_xy)):
        a = polygons_xy[first]
        for second in range(first + 1, len(polygons_xy)):
            b = polygons_xy[second]
            if len(a) != len(b):
                continue
            distances = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
            if max(float(distances.min(axis=0).max()), float(distances.min(axis=1).max())) <= tolerance_mm:
                return True
    return False
