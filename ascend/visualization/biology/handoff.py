"""Adapters from stored Layer 3.1 arrays to the canonical render contract."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from .models import BiologicalEndpoint, BiologicalVolume, VolumeGeometry


def volume_geometry_from_ascend(
    geometry: Mapping[str, Any],
    shape_zyx: tuple[int, int, int],
) -> VolumeGeometry:
    origin = np.asarray(geometry.get("origin"), dtype=float)
    row = np.asarray(geometry.get("row_direction", geometry.get("row_dir")), dtype=float)
    column = np.asarray(geometry.get("column_direction", geometry.get("col_dir")), dtype=float)
    normal = np.asarray(geometry.get("normal"), dtype=float)
    offsets = np.asarray(geometry.get("offsets"), dtype=float)
    spacing = np.asarray(geometry.get("spacing", geometry.get("in_plane_spacing_mm")), dtype=float).reshape(-1)
    if origin.shape != (3,) or row.shape != (3,) or column.shape != (3,) or normal.shape != (3,):
        raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
    if offsets.size != shape_zyx[0] or spacing.size not in {2, 3}:
        raise ValueError("BIOLOGICAL_VOLUME_GEOMETRY_INVALID")
    if offsets.size > 1:
        differences = np.diff(offsets)
        z_spacing = float(np.median(np.abs(differences)))
        if not np.allclose(np.abs(differences), z_spacing, rtol=1.0e-5, atol=1.0e-6):
            raise ValueError("BIOLOGICAL_VOLUME_NONUNIFORM_Z_UNSUPPORTED")
        z_direction = normal * (1.0 if differences[0] > 0.0 else -1.0)
    else:
        z_spacing = float(spacing[0]) if spacing.size == 3 else 1.0
        z_direction = normal
    voxel_zero_origin = origin + normal * float(offsets[0])
    spacing_xyz = np.asarray([spacing[-1], spacing[-2], z_spacing], dtype=float)
    direction_xyz = np.column_stack((row, column, z_direction))
    return VolumeGeometry(shape_zyx, voxel_zero_origin, spacing_xyz, direction_xyz)


def endpoint_from_field(field_id: str, metadata: Mapping[str, Any]) -> BiologicalEndpoint:
    identifier = field_id.lower()
    label = str(metadata.get("label") or "").lower()
    if field_id == "physical_course_dose_gy" or "physical" in label:
        return BiologicalEndpoint.PHYSICAL_DOSE
    if "eqd2" in identifier or "eqd2" in label:
        return BiologicalEndpoint.SEQD2
    if "bed" in identifier or "bed" in label:
        return BiologicalEndpoint.SBED
    if "negative_log10_survival_mlq" in identifier or "survival contrast" in label:
        return BiologicalEndpoint.MLQ_EFFECT
    if "voxel_survival_mlq" in identifier or "surviving fraction" in label:
        return BiologicalEndpoint.MLQ_SF
    if "course_effect_mlq" in identifier or "mlq effect" in label or "accumulated mlq effect" in label:
        return BiologicalEndpoint.MLQ_EFFECT
    raise ValueError("BIOLOGICAL_ENDPOINT_UNSUPPORTED")


def biological_volume_from_stored_field(
    values: np.ndarray,
    field_id: str,
    metadata: Mapping[str, Any],
    geometry: Mapping[str, Any],
    masks: Mapping[str, np.ndarray],
    *,
    treatment_components: tuple[str, ...] = (),
    provenance: Mapping[str, Any] | None = None,
) -> BiologicalVolume:
    array = np.asarray(values)
    canonical_geometry = volume_geometry_from_ascend(geometry, tuple(array.shape))
    tumour = masks.get("Region: Whole GTV")
    vertex = masks.get("Region: Vertices")
    valley = masks.get("Region: Valleys")
    endpoint = endpoint_from_field(field_id, metadata)
    units = str(metadata.get("units") or "")
    if endpoint in {BiologicalEndpoint.SBED, BiologicalEndpoint.SEQD2} and units.startswith("Gy"):
        # Preserve the detailed ASCEND label (for example Gy10) while the
        # endpoint registry retains dimensional units Gy.
        pass
    attached_metadata = {
        **dict(metadata),
        **dict(provenance or {}),
        "field_id": field_id,
        "scientific_data": True,
        "display_smoothing": False,
    }
    return BiologicalVolume(
        values=array,
        endpoint=endpoint,
        geometry=canonical_geometry,
        units=units,
        valid_mask=np.isfinite(array),
        tissue_mask=tumour,
        vertex_mask=vertex,
        valley_mask=valley,
        roi_masks=masks,
        treatment_components=treatment_components,
        metadata=attached_metadata,
    )
