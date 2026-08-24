"""Presentation-only biological mesh generation and scalar sampling.

The authoritative Layer 3.1 results remain voxel arrays. Mesh extraction,
repair, Taubin smoothing, normals, colours, and portable exports are isolated
display operations and are never accepted as inputs to a scientific metric.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import map_coordinates

from ascend.layer3.lq.basis import _deterministic_npz
from ascend.layer3.nonlocal_effect.spatial import (
    ScalarSurface, mask_surface, write_3mf, write_binary_stl, write_glb,
    write_ply, write_vtp,
)
from ascend.layer3.spatial_biology import (
    sample_surface_inward, validate_mesh_alignment, world_to_voxel_lps,
)
from ascend.validation.provenance import canonical_hash, file_hash


MESH_PIPELINE_VERSION = "ASCEND-L3.1-biological-mesh-v2.0"
DEFAULT_SMOOTHING = {
    "method": "taubin_non_shrinking",
    "iterations": 12,
    "lambda": 0.25,
    "mu": -0.27,
}


def _smoothing_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {})
    return {
        "method": raw.get("method", raw.get("smoothing_method", DEFAULT_SMOOTHING["method"])),
        "iterations": raw.get("iterations", raw.get("smoothing_iterations", DEFAULT_SMOOTHING["iterations"])),
        "lambda": raw.get("lambda", raw.get("smoothing_lambda", DEFAULT_SMOOTHING["lambda"])),
        "mu": raw.get("mu", raw.get("smoothing_mu", DEFAULT_SMOOTHING["mu"])),
    }


@dataclass(frozen=True)
class BiologicalMeshResult:
    status: str
    reason: str | None
    raw_surface: ScalarSurface | None
    display_surface: ScalarSurface | None
    vertex_normals: np.ndarray | None
    qc: dict[str, Any]
    provenance: dict[str, Any]
    artifacts: dict[str, str]

    def metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "qc": self.qc,
            "provenance": self.provenance,
            "artifacts": self.artifacts,
        }


def _geometry_values(geometry: dict[str, Any]) -> dict[str, np.ndarray]:
    spacing = geometry.get("spacing", geometry.get("in_plane_spacing_mm"))
    required = {
        "origin": geometry.get("origin"),
        "row_direction": geometry.get("row_direction", geometry.get("row_dir")),
        "column_direction": geometry.get("column_direction", geometry.get("col_dir")),
        "normal": geometry.get("normal"),
        "offsets": geometry.get("offsets"),
        "spacing": spacing,
        "shape": geometry.get("shape"),
    }
    if any(value is None for value in required.values()):
        raise ValueError("BIOLOGICAL_DISPLAY_GEOMETRY_INCOMPLETE")
    values = {key: np.asarray(value, dtype=float) for key, value in required.items()}
    if values["origin"].shape != (3,) or values["row_direction"].shape != (3,) or values["column_direction"].shape != (3,):
        raise ValueError("BIOLOGICAL_DISPLAY_GEOMETRY_INVALID")
    if values["normal"].shape != (3,) or values["spacing"].reshape(-1).size not in (2, 3):
        raise ValueError("BIOLOGICAL_DISPLAY_GEOMETRY_INVALID")
    if not all(np.isfinite(value).all() for value in values.values()):
        raise ValueError("BIOLOGICAL_DISPLAY_GEOMETRY_NONFINITE")
    values["pixel_spacing"] = values["spacing"].reshape(-1)[-2:]
    return values


def lps_to_indices(points_lps_mm: np.ndarray, geometry: dict[str, Any]) -> np.ndarray:
    """Invert the ASCEND DICOM-LPS voxel-centre coordinate transform."""
    return world_to_voxel_lps(points_lps_mm, geometry)


def sample_scalar_field_lps(
    field: np.ndarray,
    points_lps_mm: np.ndarray,
    geometry: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    """Sample an authoritative voxel field with deterministic trilinear interpolation."""
    values = np.asarray(field, dtype=np.float64)
    if values.ndim != 3 or not np.isfinite(values).all():
        raise ValueError("BIOLOGICAL_SCALAR_FIELD_INVALID")
    indices = lps_to_indices(points_lps_mm, geometry)
    shape = np.asarray(values.shape, dtype=float)
    valid = np.isfinite(indices).all(axis=1) & np.all(indices >= 0.0, axis=1) & np.all(indices <= shape - 1.0, axis=1)
    sampled = np.full(len(indices), np.nan, dtype=np.float32)
    if valid.any():
        sampled[valid] = map_coordinates(
            values,
            [indices[valid, 0], indices[valid, 1], indices[valid, 2]],
            order=1,
            mode="constant",
            cval=np.nan,
            prefilter=False,
        ).astype(np.float32)
    return sampled, valid


def _repair(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    face_valid = (
        np.all(faces >= 0, axis=1)
        & np.all(faces < len(vertices), axis=1)
        & (faces[:, 0] != faces[:, 1])
        & (faces[:, 1] != faces[:, 2])
        & (faces[:, 0] != faces[:, 2])
    )
    faces = faces[face_valid]
    if not len(faces):
        raise ValueError("BIOLOGICAL_MESH_HAS_NO_VALID_TRIANGLES")
    triangles = vertices[faces]
    area_twice = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
    faces = faces[np.isfinite(area_twice) & (area_twice > 1.0e-10)]
    if not len(faces):
        raise ValueError("BIOLOGICAL_MESH_HAS_NO_VALID_TRIANGLES")
    used = np.unique(faces)
    inverse = np.full(len(vertices), -1, dtype=np.int64)
    inverse[used] = np.arange(len(used))
    return np.ascontiguousarray(vertices[used], dtype=np.float32), np.ascontiguousarray(inverse[faces], dtype=np.uint32)


def _adjacency(faces: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
    edges = np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])).astype(np.int64)
    edges = np.vstack((edges, edges[:, ::-1]))
    order = np.argsort(edges[:, 0], kind="mergesort")
    return edges[order], np.bincount(edges[:, 0], minlength=count).astype(np.float64)


def taubin_smooth(vertices: np.ndarray, faces: np.ndarray, settings: dict[str, Any] | None = None) -> np.ndarray:
    """Apply deterministic conservative non-shrinking display smoothing."""
    configured = _smoothing_settings(settings)
    iterations = int(configured["iterations"])
    lam = float(configured["lambda"])
    mu = float(configured["mu"])
    if configured["method"] == "none":
        return np.ascontiguousarray(vertices, dtype=np.float32)
    if configured["method"] not in {"taubin_nonshrinking", "taubin_non_shrinking"}:
        raise ValueError("BIOLOGICAL_MESH_SMOOTHING_SETTINGS_INVALID")
    if iterations < 0 or iterations > 100 or not 0.0 <= lam <= 0.5 or not -0.6 <= mu <= 0.0:
        raise ValueError("BIOLOGICAL_MESH_SMOOTHING_SETTINGS_INVALID")
    values = np.asarray(vertices, dtype=np.float64).copy()
    edges, counts = _adjacency(np.asarray(faces), len(values))
    valid = counts > 0
    for _ in range(iterations):
        for factor in (lam, mu):
            sums = np.zeros_like(values)
            np.add.at(sums, edges[:, 0], values[edges[:, 1]])
            displacement = np.zeros_like(values)
            displacement[valid] = sums[valid] / counts[valid, None] - values[valid]
            values += factor * displacement
    if not np.isfinite(values).all():
        raise ValueError("BIOLOGICAL_MESH_SMOOTHING_NONFINITE")
    return np.ascontiguousarray(values, dtype=np.float32)


def vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    triangles = np.asarray(vertices, dtype=np.float64)[np.asarray(faces, dtype=np.int64)]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals = np.zeros(np.asarray(vertices).shape, dtype=np.float64)
    for column in range(3):
        np.add.at(normals, faces[:, column], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 0
    normals[valid] /= lengths[valid, None]
    return np.ascontiguousarray(normals, dtype=np.float32)


def _component_count(faces: np.ndarray, vertex_count: int) -> int:
    parent = np.arange(vertex_count, dtype=np.int64)

    def root(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    for face in np.asarray(faces, dtype=np.int64):
        anchor = root(int(face[0]))
        for value in face[1:]:
            other = root(int(value))
            if anchor != other:
                parent[other] = anchor
    return len({root(index) for index in range(vertex_count)})


def _colours(scalars: np.ndarray, scalar_range: tuple[float, float] | None = None) -> np.ndarray:
    values = np.asarray(scalars, dtype=np.float64)
    finite = np.isfinite(values)
    rgb = np.full((len(values), 3), [120, 120, 120], dtype=np.uint8)
    if not finite.any():
        return rgb
    low, high = scalar_range or (float(np.min(values[finite])), float(np.max(values[finite])))
    if not np.isfinite([low, high]).all() or high < low:
        raise ValueError("BIOLOGICAL_DISPLAY_SCALAR_RANGE_INVALID")
    fraction = np.full(len(values), 0.5) if high == low else np.clip((values - low) / (high - low), 0.0, 1.0)
    anchors = np.asarray([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]], dtype=float)
    position = fraction * (len(anchors) - 1)
    lower = np.zeros(len(values), dtype=int)
    lower[finite] = np.floor(position[finite]).astype(int)
    upper = np.minimum(lower + 1, len(anchors) - 1)
    weight = np.zeros(len(values), dtype=float)
    weight[finite] = position[finite] - lower[finite]
    rgb[finite] = np.round(anchors[lower[finite]] * (1.0 - weight[finite, None]) + anchors[upper[finite]] * weight[finite, None]).astype(np.uint8)
    return rgb


def _cached_mesh_result(directory: Path, key: str, scientific_hash: str, mask_hash: str) -> BiologicalMeshResult | None:
    """Restore a verified display cache without rerunning marching cubes."""
    metadata_path = directory / "mesh_metadata.json"
    archive_path = directory / "mesh_arrays.npz"
    if not metadata_path.is_file() or not archive_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        provenance = dict(metadata.get("provenance") or {})
        artifacts = dict(metadata.get("artifacts") or {})
        if provenance.get("cache_key") != key or provenance.get("mask_hash") != mask_hash:
            return None
        if provenance.get("authoritative_field_hash_before_display_processing") != scientific_hash:
            return None
        expected_archive_hash = artifacts.get("mesh_arrays_sha256")
        if not expected_archive_hash or file_hash(archive_path) != expected_archive_hash:
            return None
        for name, path in list(artifacts.items()):
            if name.endswith("_sha256") or name == "mesh_arrays":
                continue
            expected = artifacts.get(f"{name}_sha256")
            if expected and (not Path(str(path)).is_file() or file_hash(Path(str(path))) != expected):
                return None
        with np.load(archive_path, allow_pickle=False) as archive:
            raw_vertices = np.asarray(archive["raw_vertices_lps_mm"], dtype=np.float32)
            display_vertices = np.asarray(archive["display_vertices_lps_mm"], dtype=np.float32)
            faces = np.asarray(archive["faces"], dtype=np.uint32)
            normals = np.asarray(archive["display_vertex_normals"], dtype=np.float32)
            sampled = np.asarray(archive["display_vertex_scalars"], dtype=np.float32)
            colours = np.asarray(archive["display_vertex_rgb"], dtype=np.uint8)
        raw_surface = ScalarSurface(
            raw_vertices, faces, np.full(len(raw_vertices), np.nan, dtype=np.float32),
            np.tile(np.asarray([210, 220, 231], dtype=np.uint8), (len(raw_vertices), 1)), float("nan"),
        )
        display_surface = ScalarSurface(display_vertices, faces, sampled, colours, float("nan"))
        artifacts.update({"metadata": str(metadata_path), "metadata_sha256": file_hash(metadata_path)})
        restored_provenance = {**provenance, "display_cache_hit": True}
        return BiologicalMeshResult("PASS", None, raw_surface, display_surface, normals, dict(metadata.get("qc") or {}), restored_provenance, artifacts)
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return None


def build_biological_mesh(
    case_root: Path,
    roi_id: str,
    mask: np.ndarray,
    field: np.ndarray,
    geometry: dict[str, Any],
    field_id: str,
    units: str,
    smoothing: dict[str, Any] | None = None,
    scalar_range: tuple[float, float] | None = None,
    *,
    export_formats: tuple[str, ...] = ("vtp", "stl"),
    expected_tissue_mask: np.ndarray | None = None,
    green_coverage_percent: float = 99.0,
    block_coverage_percent: float = 95.0,
) -> BiologicalMeshResult:
    """Build, cache, QC, sample, and export one display-only ROI surface."""
    configured = _smoothing_settings(smoothing)
    mask_values = np.asarray(mask, dtype=bool)
    tissue_mask = np.asarray(expected_tissue_mask if expected_tissue_mask is not None else mask_values, dtype=bool)
    field_values = np.asarray(field, dtype=np.float32)
    scientific_hash = hashlib.sha256(np.ascontiguousarray(field_values).tobytes()).hexdigest()
    mask_hash = hashlib.sha256(np.ascontiguousarray(mask_values, dtype=np.uint8).tobytes()).hexdigest()
    tissue_mask_hash = hashlib.sha256(np.ascontiguousarray(tissue_mask, dtype=np.uint8).tobytes()).hexdigest()
    key = canonical_hash({
        "pipeline_version": MESH_PIPELINE_VERSION,
        "mask_hash": mask_hash,
        "expected_tissue_mask_hash": tissue_mask_hash,
        "field_hash": scientific_hash,
        "geometry": geometry,
        "smoothing": configured,
        "field_id": field_id,
        "scalar_range": scalar_range,
        "green_coverage_percent": green_coverage_percent,
        "block_coverage_percent": block_coverage_percent,
    })
    directory = Path(case_root) / "cache" / "layer3_1" / "visualization" / key
    directory.mkdir(parents=True, exist_ok=True)
    cached = _cached_mesh_result(directory, key, scientific_hash, mask_hash)
    if cached is not None:
        return cached
    try:
        extracted = mask_surface(mask_values, geometry)
        raw_vertices, faces = _repair(extracted.vertices_lps_mm, extracted.faces)
        display_vertices = taubin_smooth(raw_vertices, faces, configured)
        normals = vertex_normals(display_vertices, faces)
        sampling = sample_surface_inward(field_values, tissue_mask, display_vertices, normals, geometry)
        sampled, valid = sampling.values, sampling.valid
        alignment = validate_mesh_alignment(
            display_vertices, geometry, tissue_mask, sampling,
            green_threshold_percent=float(green_coverage_percent),
            block_threshold_percent=float(block_coverage_percent),
        )
        if alignment.status == "BLOCK":
            raise ValueError(str(alignment.error_code or "CAD_FRAME_ALIGNMENT_FAILED"))
        colours = _colours(sampled, scalar_range)
        raw_surface = ScalarSurface(
            raw_vertices, faces, np.full(len(raw_vertices), np.nan, dtype=np.float32),
            np.tile(np.asarray([210, 220, 231], dtype=np.uint8), (len(raw_vertices), 1)), float("nan"),
        )
        display_surface = ScalarSurface(display_vertices, faces, sampled, colours, float("nan"))
        raw_box = [np.min(raw_vertices, axis=0).tolist(), np.max(raw_vertices, axis=0).tolist()]
        display_box = [np.min(display_vertices, axis=0).tolist(), np.max(display_vertices, axis=0).tolist()]
        max_box_change = float(np.max(np.abs(np.asarray(display_box) - np.asarray(raw_box))))
        qc = {
            "finite_coordinates": bool(np.isfinite(display_vertices).all()),
            "non_empty_mesh": bool(len(display_vertices) and len(faces)),
            "valid_triangles": bool(np.all(faces < len(display_vertices))),
            "valid_normals": bool(np.isfinite(normals).all() and np.all(np.linalg.norm(normals, axis=1) <= 1.0001)),
            "raw_patient_space_bounding_box_mm": raw_box,
            "display_patient_space_bounding_box_mm": display_box,
            "maximum_display_bbox_change_mm": max_box_change,
            "connected_component_count": _component_count(faces, len(display_vertices)),
            "scalar_sampling_coverage_percent": 100.0 * float(valid.sum()) / float(len(valid)),
            "invalid_scalar_vertex_count": int((~valid).sum()),
            "coordinate_system": "DICOM patient LPS",
            "mesh_alignment_status": alignment.status,
            "mesh_coverage_percent": alignment.coverage_percent,
            "valid_samples": alignment.valid_samples,
            "total_samples": alignment.total_samples,
            "median_sampling_distance_mm": alignment.median_sampling_distance_mm,
            "maximum_sampling_distance_mm": alignment.maximum_sampling_distance_mm,
            "grid_patient_space_bounding_box_mm": alignment.grid_bounding_box_lps_mm,
            "roi_patient_space_bounding_box_mm": alignment.roi_bounding_box_lps_mm,
        }
        if not all(qc[key] for key in ("finite_coordinates", "non_empty_mesh", "valid_triangles", "valid_normals")):
            raise ValueError("BIOLOGICAL_MESH_DISPLAY_QC_FAILED")
        archive = directory / "mesh_arrays.npz"
        _deterministic_npz(archive, {
            "raw_vertices_lps_mm": raw_vertices,
            "display_vertices_lps_mm": display_vertices,
            "faces": faces,
            "display_vertex_normals": normals,
            "display_vertex_scalars": sampled,
            "display_vertex_rgb": colours,
            "sampled_points_lps_mm": sampling.sampled_points_lps_mm,
            "sampled_voxel_indices_zyx": sampling.voxel_indices_zyx,
            "sampling_distance_mm": sampling.sampling_distance_mm,
            "sampling_valid": sampling.valid.astype(np.uint8),
        })
        artifacts: dict[str, str] = {"mesh_arrays": str(archive), "mesh_arrays_sha256": file_hash(archive)}
        writers = {
            "vtp": lambda path: write_vtp(display_surface, path),
            "ply": lambda path: write_ply(display_surface, path),
            "glb": lambda path: write_glb(display_surface, path),
            "3mf": lambda path: write_3mf(display_surface, path),
            "stl": lambda path: write_binary_stl(display_surface, path, f"{roi_id} {field_id}"),
        }
        for extension in export_formats:
            if extension not in writers:
                raise ValueError(f"BIOLOGICAL_MESH_EXPORT_FORMAT_UNSUPPORTED: {extension}")
            path = directory / f"display_surface.{extension}"
            writers[extension](path)
            artifacts[extension] = str(path)
            artifacts[f"{extension}_sha256"] = file_hash(path)
        raw_stl = directory / "raw_surface.stl"
        write_binary_stl(raw_surface, raw_stl, f"{roi_id} raw mask surface")
        artifacts.update({"raw_stl": str(raw_stl), "raw_stl_sha256": file_hash(raw_stl)})
        provenance = {
            "pipeline_version": MESH_PIPELINE_VERSION,
            "roi_id": roi_id,
            "mask_hash": mask_hash,
            "expected_tissue_mask_hash": tissue_mask_hash,
            "authoritative_field_id": field_id,
            "authoritative_field_hash_before_display_processing": scientific_hash,
            "authoritative_field_hash_after_display_processing": hashlib.sha256(np.ascontiguousarray(field_values).tobytes()).hexdigest(),
            "units": units,
            "display_scalar_range": list(scalar_range) if scalar_range else "automatic_complete_3d_field",
            "surface_extraction": "marching_cubes_mask_level_0.5",
            "repair": "degenerate_triangle_removal_and_unused_vertex_compaction",
            "smoothing": configured,
            "smoothing_scope": "display_surface_vertices_only",
            "scalar_sampling": "DICOM-LPS-aware_mask-aware_trilinear_inward-normal",
            "invalid_value_representation": "NaN",
            "cross_tissue_interpolation": False,
            "alignment_gate": {
                "green_coverage_percent": float(green_coverage_percent),
                "block_coverage_percent": float(block_coverage_percent),
                "status": alignment.status,
            },
            "quantitative_field_smoothing": False,
            "cache_key": key,
        }
        metadata = directory / "mesh_metadata.json"
        metadata.write_text(json.dumps({"qc": qc, "provenance": provenance, "artifacts": artifacts}, indent=2), encoding="utf-8")
        artifacts.update({"metadata": str(metadata), "metadata_sha256": file_hash(metadata)})
        return BiologicalMeshResult("PASS", None, raw_surface, display_surface, normals, qc, provenance, artifacts)
    except (OSError, RuntimeError, ValueError) as exc:
        return BiologicalMeshResult(
            "UNAVAILABLE", str(exc), None, None, None,
            {"biological_calculation_affected": False},
            {
                "pipeline_version": MESH_PIPELINE_VERSION,
                "smoothing": configured,
                "quantitative_field_smoothing": False,
                "authoritative_field_hash": scientific_hash,
            },
            {},
        )
