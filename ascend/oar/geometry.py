"""Geometry calculations used by DICOM ingestion or validation diagnostics."""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
from scipy.ndimage import binary_erosion
from scipy.spatial import cKDTree


class OARClassification(str, Enum):
    """Enumerate supported o a r classification values."""
    CONTAINING_ORGAN = "containing_organ"
    TARGET_EXCLUDED_OAR = "target_excluded_oar"
    SEPARATE_CRITICAL_OAR = "separate_critical_oar"
    INTERNAL_TARGET_STRUCTURE = "internal_target_structure"


class OARGeometryService:
    """Optional geometry context. It does not calculate Layer 2.1 metrics or compliance."""

    @staticmethod
    def _surface_points(mask: np.ndarray, spacing_zyx_mm: tuple[float, float, float]) -> np.ndarray:
        surface = mask & ~binary_erosion(mask, border_value=0)
        return np.argwhere(surface) * np.asarray(spacing_zyx_mm, dtype=float)

    @classmethod
    def _minimum_surface_distance(
        cls,
        first: np.ndarray,
        second: np.ndarray,
        spacing_zyx_mm: tuple[float, float, float],
    ) -> float | None:
        if not first.any() or not second.any():
            return None
        if (first & second).any():
            return 0.0
        first_points = cls._surface_points(first, spacing_zyx_mm)
        second_points = cls._surface_points(second, spacing_zyx_mm)
        if not len(first_points) or not len(second_points):
            return None
        smaller, larger = (first_points, second_points) if len(first_points) <= len(second_points) else (second_points, first_points)
        distances, _indices = cKDTree(larger).query(smaller, k=1, workers=-1)
        return float(np.min(distances))

    def analyse(
        self,
        oar_name: str,
        oar_mask: np.ndarray,
        classification: OARClassification,
        vertex_masks: dict[str, np.ndarray],
        spacing_zyx_mm: tuple[float, float, float],
        aggregate_vtvh_mask: np.ndarray,
        voxel_volume_cc: float,
        oar_volume_cc: float | None = None,
        oar_volume_basis: str = "dose_sampled",
    ) -> dict[str, Any]:
        """Calculate analyse using the documented validated inputs."""
        if not oar_mask.any():
            raise ValueError("Only an explicitly supplied, non-empty Layer 1-validated OAR mask is accepted.")
        if aggregate_vtvh_mask.shape != oar_mask.shape or any(mask.shape != oar_mask.shape for mask in vertex_masks.values()):
            raise ValueError("OAR and vertex masks must share the validated native geometry.")
        sampled_oar_cc = float(oar_mask.sum() * voxel_volume_cc)
        sampled_vtvh_cc = float(aggregate_vtvh_mask.sum() * voxel_volume_cc)
        aggregate_overlap_cc = float((aggregate_vtvh_mask & oar_mask).sum() * voxel_volume_cc)
        rows = []
        for vertex_id, mask in vertex_masks.items():
            overlap_voxels = int((mask & oar_mask).sum())
            distance = self._minimum_surface_distance(oar_mask, mask, spacing_zyx_mm)
            relationship = (
                "overlap" if overlap_voxels > 0 else
                "separated" if distance is not None else
                "not_assessed"
            )
            rows.append({
                "vertex_id": vertex_id,
                "minimum_surface_distance_mm": distance,
                "overlap_volume_cc": float(overlap_voxels * voxel_volume_cc),
                "spatial_relationship": relationship,
                "zero_distance_reason": "mask_overlap" if distance == 0.0 and overlap_voxels > 0 else None,
            })
        valid_distances = [row for row in rows if row["minimum_surface_distance_mm"] is not None]
        nearest = min(valid_distances, key=lambda row: (row["minimum_surface_distance_mm"], row["vertex_id"])) if valid_distances else None
        aggregate_distance = self._minimum_surface_distance(oar_mask, aggregate_vtvh_mask, spacing_zyx_mm)
        aggregate_relationship = "overlap" if aggregate_overlap_cc > 0 else "separated"
        nearest_relationship = nearest.get("spatial_relationship") if nearest else "not_assessed"
        audit_findings = []
        if aggregate_distance == 0.0 and aggregate_overlap_cc > 0:
            audit_findings.append({
                "code": "zero_distance_due_to_mask_overlap",
                "severity": "INFO",
                "detail": (
                    "The reported 0.0 mm is minimum mask separation, not vertex diameter. "
                    "It is exactly zero because the structure and aggregate VTVH share native-grid voxels."
                ),
            })
        if classification == OARClassification.SEPARATE_CRITICAL_OAR and aggregate_overlap_cc > 0:
            audit_findings.append({
                "code": "configured_separate_oar_overlaps_vtvh",
                "severity": "WARN",
                "detail": (
                    "The supplied classification is separate_critical_oar, but the validated masks overlap. "
                    "Review whether the structure is a containing organ, target-excluded OAR, or an intentional overlap."
                ),
            })
        return {
            "oar_name": oar_name,
            "classification": classification.value,
            "oar_volume_cc": float(oar_volume_cc) if oar_volume_cc is not None else sampled_oar_cc,
            "oar_volume_basis": oar_volume_basis if oar_volume_cc is not None else "dose_sampled_native_mask",
            "dose_sampled_oar_volume_cc": sampled_oar_cc,
            "aggregate_vtvh_minimum_surface_distance_mm": aggregate_distance,
            "aggregate_vtvh_spatial_relationship": aggregate_relationship,
            "overlap_volume_cc": aggregate_overlap_cc,
            "overlap_percentage_of_oar": 100.0 * aggregate_overlap_cc / sampled_oar_cc if sampled_oar_cc else None,
            "overlap_percentage_of_vtvh": 100.0 * aggregate_overlap_cc / sampled_vtvh_cc if sampled_vtvh_cc else None,
            "nearest_vertex_id": nearest["vertex_id"] if nearest else None,
            "nearest_vertex_distance_mm": nearest["minimum_surface_distance_mm"] if nearest else None,
            "nearest_vertex_spatial_relationship": nearest_relationship,
            "nearest_vertex_zero_distance_reason": nearest.get("zero_distance_reason") if nearest else None,
            "per_vertex_geometry": rows,
            "geometry_audit": {
                "distance_quantity": "minimum_mask_surface_separation",
                "vertex_diameter_calculated": False,
                "zero_distance_semantics": "0.0 mm means mask overlap; it is not a zero-diameter vertex",
                "overlapping_vertex_count": sum(row["spatial_relationship"] == "overlap" for row in rows),
                "separated_vertex_count": sum(row["spatial_relationship"] == "separated" for row in rows),
                "findings": audit_findings,
            },
            "distance_method": "minimum native-voxel boundary-centre separation in physical z,y,x spacing; forced to zero when masks overlap",
            "compliance_interpretation": "not_performed",
            "note": "Overlap with a containing organ is descriptive and is not OAR noncompliance.",
        }
