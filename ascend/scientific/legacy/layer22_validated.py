#!/usr/bin/env python3
"""ASCEND-LRT Layer 2.2B: direction-independent graph iPVDR audit.

Consumes a completed ASCEND-LRT Layer 1 result and its native RTDOSE-grid
masks. Vertices are individual VTV_H structures when available, otherwise
26-connected components of the aggregate VTV_H mask. The primary graph is the
undirected union of all tied Euclidean nearest-neighbour relationships in 3-D.

Research software for controlled retrospective evaluation. It does not rank
plans, recommend treatment, or provide a clinically calibrated endpoint.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pydicom
except ImportError as exc:
    print("Missing dependency. Install with: python3 -m pip install -r requirements.txt")
    raise SystemExit(1) from exc


VERSION = "ASCEND-LRT-1.0.0-layer2.2B"
EXPECTED_RASTER_STANDARD = "ASCEND-LRT-L1-RASTER-CTNN-v1"

# ---------------------------------------------------------------------------
# Provenance and input verification
# ---------------------------------------------------------------------------

class AuditBlock(RuntimeError):
    """Raised when a mandatory safety or reproducibility condition fails."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    """Hash a logical mask in canonical contiguous uint8 representation."""
    return hashlib.sha256(np.ascontiguousarray(array, dtype=np.uint8).tobytes()).hexdigest()


def canonical_json_hash(value: Any) -> str:
    """Hash configuration content independently of whitespace/key ordering."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"graph", "peak", "valley", "plan_endpoint", "grid", "interpretation_gate"}
    missing = sorted(required - set(config))
    if missing:
        raise AuditBlock(f"Configuration is missing required sections: {', '.join(missing)}")
    return config


def discover_object(case_folder: Path, modality: str, sop_uid: str | None) -> Path:
    """Find the exact DICOM object used by Layer 1 using its SOP Instance UID."""
    candidates: list[tuple[Path, str]] = []
    for path in sorted(case_folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            dataset = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception:
            continue
        if str(getattr(dataset, "Modality", "")).upper() == modality:
            candidates.append((path, str(getattr(dataset, "SOPInstanceUID", ""))))
    if sop_uid:
        # Matching by UID prevents a newly exported dose in the same folder from
        # being substituted merely because its filename looks similar.
        matches = [path for path, uid in candidates if uid == sop_uid]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise AuditBlock(f"The Layer 1 {modality} SOP Instance UID is absent from the case folder.")
        raise AuditBlock(f"Duplicate {modality} objects share the Layer 1 SOP Instance UID.")
    if len(candidates) != 1:
        raise AuditBlock(f"Expected one {modality}; found {len(candidates)}. Supply the original Layer 1 case folder.")
    return candidates[0][0]


def dose_geometry(dataset: Any) -> dict[str, Any]:
    """Convert DICOM RTDOSE orientation metadata to explicit geometry vectors."""
    orientation = np.asarray(dataset.ImageOrientationPatient, dtype=float)
    row_direction, column_direction = orientation[:3], orientation[3:]
    normal = np.cross(row_direction, column_direction)
    offsets = np.asarray(getattr(dataset, "GridFrameOffsetVector", [0.0]), dtype=float)
    spacing = np.asarray(dataset.PixelSpacing, dtype=float)
    shape = (int(getattr(dataset, "NumberOfFrames", 1)), int(dataset.Rows), int(dataset.Columns))
    if len(offsets) != shape[0]:
        raise AuditBlock("GridFrameOffsetVector length does not match RTDOSE NumberOfFrames.")
    return {
        "origin": np.asarray(dataset.ImagePositionPatient, dtype=float),
        "row_direction": row_direction,
        "column_direction": column_direction,
        "normal": normal,
        "offsets": offsets,
        "spacing": spacing,
        "shape": shape,
    }


def voxel_spacing_mm(geometry: dict[str, Any]) -> np.ndarray:
    """Return z/y/x spacing and reject non-uniform frame separation."""
    offsets = geometry["offsets"]
    if len(offsets) < 2:
        raise AuditBlock("A multi-frame 3-D RTDOSE grid is required for graph analysis.")
    differences = np.abs(np.diff(offsets))
    if not np.allclose(differences, np.median(differences), rtol=0.0, atol=1.0e-4):
        raise AuditBlock("Non-uniform RTDOSE frame spacing is outside the frozen validation scope.")
    return np.asarray([float(np.median(differences)), *map(float, geometry["spacing"])])


def physical_centroid(mask: np.ndarray, geometry: dict[str, Any]) -> np.ndarray:
    """Calculate a vertex centroid in 3-D DICOM LPS patient coordinates."""
    indices = np.argwhere(mask)
    if not len(indices):
        raise AuditBlock("An empty vertex mask cannot define a graph node.")
    # The centroid is based on all vertex voxels, not a single axial slice.
    z, y, x = indices.mean(axis=0)
    z_offset = float(np.interp(z, np.arange(len(geometry["offsets"])), geometry["offsets"]))
    return (
        geometry["origin"]
        + z_offset * geometry["normal"]
        + x * geometry["spacing"][1] * geometry["row_direction"]
        + y * geometry["spacing"][0] * geometry["column_direction"]
    )


def split_components(mask: np.ndarray) -> list[np.ndarray]:
    """Deterministic 26-connected components without an additional dependency."""
    visited = np.zeros(mask.shape, dtype=bool)
    # Face-, edge- and corner-touching voxels belong to the same 26-connected node.
    neighbours = [
        (dz, dy, dx)
        for dz in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if (dz, dy, dx) != (0, 0, 0)
    ]
    components: list[np.ndarray] = []
    shape = mask.shape
    # np.argwhere provides deterministic z/y/x seed order for repeatable labels.
    for seed_array in np.argwhere(mask):
        seed = tuple(int(value) for value in seed_array)
        if visited[seed]:
            continue
        component = np.zeros(shape, dtype=bool)
        queue: deque[tuple[int, int, int]] = deque([seed])
        visited[seed] = True
        while queue:
            z, y, x = queue.popleft()
            component[z, y, x] = True
            for dz, dy, dx in neighbours:
                candidate = (z + dz, y + dy, x + dx)
                if (
                    0 <= candidate[0] < shape[0]
                    and 0 <= candidate[1] < shape[1]
                    and 0 <= candidate[2] < shape[2]
                    and mask[candidate]
                    and not visited[candidate]
                ):
                    visited[candidate] = True
                    queue.append(candidate)
        components.append(component)
    return components


def load_verified_masks(layer1: dict[str, Any], layer1_dir: Path) -> dict[str, np.ndarray]:
    """Load Layer 1 masks only after archive, voxel-count and mask-hash checks."""
    export = layer1.get("manifest", {}).get("mask_export") or {}
    # The local filename fallback lets a complete result folder be relocated
    # without weakening the recorded SHA-256 verification.
    configured_path = Path(str(export.get("path", "")))
    mask_path = configured_path if configured_path.is_file() else layer1_dir / "layer1_native_dose_masks.npz"
    if not mask_path.is_file():
        raise AuditBlock("Layer 1 native-dose-grid mask export is missing.")
    if export.get("sha256") and sha256_file(mask_path) != export["sha256"]:
        raise AuditBlock("Layer 1 mask archive SHA-256 does not match its manifest.")
    with np.load(mask_path, allow_pickle=False) as archive:
        masks = {name: np.asarray(archive[name], dtype=bool) for name in archive.files}
    expected = export.get("structures", {})
    for name, details in expected.items():
        if name not in masks:
            raise AuditBlock(f"Manifest structure {name} is absent from the mask archive.")
        if int(masks[name].sum()) != int(details.get("voxel_count", -1)):
            raise AuditBlock(f"Voxel count verification failed for {name}.")
        if details.get("mask_sha256") and sha256_array(masks[name]) != details["mask_sha256"]:
            raise AuditBlock(f"Mask SHA-256 verification failed for {name}.")
    return masks


def prepare_vertices(masks: dict[str, np.ndarray]) -> tuple[list[str], list[np.ndarray], str]:
    """Select individually segmented vertices or deterministic aggregate components."""
    individual_names = sorted(
        (name for name in masks if re.fullmatch(r"VTVH_\d+", name)),
        key=lambda name: int(name.split("_")[1]),
    )
    if len(individual_names) >= 2:
        # Individual structures preserve the TPS-defined identity of each vertex.
        vertex_masks = [masks[name] for name in individual_names]
        if "VTVH" in masks:
            union = np.logical_or.reduce(vertex_masks)
            if not np.array_equal(union, masks["VTVH"]):
                disagreement = int(np.logical_xor(union, masks["VTVH"]).sum())
                raise AuditBlock(
                    f"Individual VTV_H union differs from aggregate VTV_H by {disagreement} voxels; resolve structures before analysis."
                )
        return individual_names, vertex_masks, "INDIVIDUAL_VTVH_STRUCTURES"
    if "VTVH" not in masks:
        raise AuditBlock("At least two individual VTV_H structures or one aggregate VTV_H mask are required.")
    # Aggregate-mask splitting is the documented fallback when the TPS exported
    # all spatially separate vertices as one ROI.
    components = split_components(masks["VTVH"])
    if len(components) < 2:
        raise AuditBlock(f"Aggregate VTV_H produced {len(components)} 26-connected component(s); at least two are required.")
    if not np.array_equal(np.logical_or.reduce(components), masks["VTVH"]):
        raise AuditBlock("Connected-component union verification failed.")
    names = [f"VTVH_CC_{index:02d}" for index in range(1, len(components) + 1)]
    return names, components, "AGGREGATE_VTVH_26_CONNECTED_COMPONENTS"


def percentile_dose(values: np.ndarray, volume_percent: float) -> float:
    """D_p: minimum dose received by at least p percent of sampled voxels."""
    return float(np.percentile(values, 100.0 - volume_percent, method="higher"))


def nearest_neighbour_edges(centroids: np.ndarray, tie_tolerance_mm: float) -> tuple[list[tuple[int, int]], np.ndarray]:
    """Create the undirected union of every node's tied nearest neighbours."""
    # Full 3-D Euclidean distances in patient coordinates remove axial,
    # coronal and sagittal direction choices from the graph definition.
    distances = np.linalg.norm(centroids[:, None, :] - centroids[None, :, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    edges: set[tuple[int, int]] = set()
    for source in range(len(centroids)):
        nearest = float(np.min(distances[source]))
        # Retaining all distances within the frozen tolerance makes exact or
        # near-exact geometric ties independent of iteration order.
        for target in np.flatnonzero(np.abs(distances[source] - nearest) <= tie_tolerance_mm):
            edges.add(tuple(sorted((source, int(target)))))
    return sorted(edges), distances


def graph_components(number_of_nodes: int, edges: list[tuple[int, int]]) -> int:
    """Count disconnected graph components for an explicit topology warning."""
    adjacency = {node: set() for node in range(number_of_nodes)}
    for first, second in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    remaining = set(range(number_of_nodes))
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            neighbours = adjacency[stack.pop()] & remaining
            remaining.difference_update(neighbours)
            stack.extend(neighbours)
    return components


def sphere_values(
    midpoint_lps: np.ndarray,
    radius_mm: float,
    geometry: dict[str, Any],
    dose_gy: np.ndarray,
    gtv_mask: np.ndarray,
    all_vertices: np.ndarray,
) -> np.ndarray:
    """Return valid native-dose voxels in one edge-local valley sphere."""
    # Project the physical midpoint onto the RTDOSE axes only to bound the local
    # search. Final sphere membership is evaluated in physical millimetres.
    relative = midpoint_lps - geometry["origin"]
    centre_x = float(relative @ geometry["row_direction"] / geometry["spacing"][1])
    centre_y = float(relative @ geometry["column_direction"] / geometry["spacing"][0])
    centre_offset = float(relative @ geometry["normal"])
    x_radius = int(math.ceil(radius_mm / geometry["spacing"][1]))
    y_radius = int(math.ceil(radius_mm / geometry["spacing"][0]))
    x_indices = np.arange(max(0, math.floor(centre_x) - x_radius), min(dose_gy.shape[2], math.ceil(centre_x) + x_radius + 1))
    y_indices = np.arange(max(0, math.floor(centre_y) - y_radius), min(dose_gy.shape[1], math.ceil(centre_y) + y_radius + 1))
    z_indices = np.flatnonzero(np.abs(geometry["offsets"] - centre_offset) <= radius_mm + 1.0e-9)
    if not len(x_indices) or not len(y_indices) or not len(z_indices):
        return np.asarray([], dtype=float)
    zz, yy, xx = np.meshgrid(z_indices, y_indices, x_indices, indexing="ij")
    # Reconstruct each candidate voxel centre in DICOM LPS coordinates.
    points = (
        geometry["origin"]
        + geometry["offsets"][zz][..., None] * geometry["normal"]
        + xx[..., None] * geometry["spacing"][1] * geometry["row_direction"]
        + yy[..., None] * geometry["spacing"][0] * geometry["column_direction"]
    )
    inside_sphere = np.linalg.norm(points - midpoint_lps, axis=-1) <= radius_mm + 1.0e-9
    # Valley voxels must be inside the GTV and outside every high-dose vertex.
    valid = inside_sphere & gtv_mask[zz, yy, xx] & ~all_vertices[zz, yy, xx]
    values = dose_gy[zz[valid], yy[valid], xx[valid]]
    return values[np.isfinite(values)]


def analyse(case_folder: Path, layer1_dir: Path, config_path: Path) -> dict[str, Any]:
    """Verify inputs, calculate node/edge metrics, and assemble one audit result."""
    # Stage 1: accept only the frozen Layer 1 interface and an eligible case.
    result_path = layer1_dir / "layer1_result.json"
    if not result_path.is_file():
        raise AuditBlock("layer1_result.json was not found in the supplied Layer 1 directory.")
    layer1 = json.loads(result_path.read_text(encoding="utf-8"))
    manifest = layer1.get("manifest", {})
    if manifest.get("framework") != "ASCEND-LRT":
        raise AuditBlock("The input was not generated by the ASCEND-LRT Layer 1 release.")
    if manifest.get("dose_grid", {}).get("rasterisation_standard") != EXPECTED_RASTER_STANDARD:
        raise AuditBlock("Layer 1 rasterisation standard differs from the frozen Layer 2 interface.")
    if not layer1.get("eligibility", {}).get("layer_2_eligible", False):
        raise AuditBlock("Layer 1 did not mark this case eligible for Layer 2.")

    # Stage 2: recover the unchanged RTDOSE used by Layer 1 and decode Gy.
    config = load_config(config_path)
    dose_path = discover_object(case_folder, "RTDOSE", manifest.get("rtdose_uid"))
    expected_dose_hash = manifest.get("input_file_hashes", {}).get("rtdose")
    if expected_dose_hash and sha256_file(dose_path) != expected_dose_hash:
        raise AuditBlock("RTDOSE SHA-256 differs from the Layer 1 input.")
    dose_dataset = pydicom.dcmread(dose_path)
    if str(getattr(dose_dataset, "DoseUnits", "")).upper() != "GY":
        raise AuditBlock("RTDOSE DoseUnits must be GY.")
    scaling = float(dose_dataset.DoseGridScaling)
    dose_gy = dose_dataset.pixel_array.astype(float) * scaling
    if not np.isfinite(dose_gy).all() or np.any(dose_gy < 0):
        raise AuditBlock("RTDOSE contains non-finite or negative physical dose values.")
    # Stage 3: enforce the dose-grid geometry covered by the validation study.
    geometry = dose_geometry(dose_dataset)
    spacing = voxel_spacing_mm(geometry)
    allowed = [float(value) for value in config["grid"]["validated_isotropic_spacing_mm"]]
    if np.max(np.abs(spacing - spacing[0])) > float(config["grid"]["isotropy_tolerance_mm"]):
        raise AuditBlock(f"Anisotropic RTDOSE spacing {spacing.tolist()} mm is outside the frozen validation scope.")
    if not any(abs(float(spacing[0]) - value) <= float(config["grid"]["spacing_tolerance_mm"]) for value in allowed):
        raise AuditBlock(f"RTDOSE spacing {float(spacing[0]):g} mm is outside validated values {allowed}.")

    # Stage 4: verify downstream masks and turn vertices into graph nodes.
    masks = load_verified_masks(layer1, layer1_dir)
    if "GTV" not in masks or not masks["GTV"].any():
        raise AuditBlock("A non-empty GTV mask is required.")
    if any(mask.shape != dose_gy.shape for mask in masks.values()):
        raise AuditBlock("At least one Layer 1 mask shape differs from the RTDOSE grid.")
    names, vertex_masks, node_source = prepare_vertices(masks)
    centroids = np.vstack([physical_centroid(mask, geometry) for mask in vertex_masks])
    # Only the nearest-neighbour relationship uses graph theory; dose remains
    # sampled directly from physical RTDOSE voxels.
    tie_tolerance = float(config["graph"]["tie_tolerance_mm"])
    edges, distance_matrix = nearest_neighbour_edges(centroids, tie_tolerance)
    if not edges:
        raise AuditBlock("Nearest-neighbour construction produced no graph edges.")

    # Stage 5: calculate the primary and explanatory peak metrics per node.
    all_vertices = np.logical_or.reduce(vertex_masks)
    node_records: list[dict[str, Any]] = []
    node_d50: list[float] = []
    for name, mask, centroid in zip(names, vertex_masks, centroids):
        values = dose_gy[mask]
        # D50 is the primary peak statistic; D95 describes coverage and Dmean
        # describes total vertex dose burden without replacing D50.
        d50 = float(np.median(values))
        node_d50.append(d50)
        node_records.append({
            "node": name,
            "centroid_lps_mm": [round(float(value), 6) for value in centroid],
            "voxel_count": int(mask.sum()),
            "volume_cc": float(mask.sum() * np.prod(spacing) / 1000.0),
            "peak_d50_gy": d50,
            "peak_d95_gy": percentile_dose(values, 95.0),
            "peak_dmean_gy": float(np.mean(values)),
        })

    # Stage 6: calculate each edge-local valley and corresponding iPVDR.
    radius = float(config["valley"]["midpoint_sphere_radius_mm"])
    support_cc = float(config["valley"]["minimum_support_cc"])
    # Convert the physical minimum support volume to a grid-specific integer
    # threshold. ceil() prevents accepting less tissue than specified.
    minimum_voxels = int(math.ceil(support_cc / (float(np.prod(spacing)) / 1000.0)))
    edge_records: list[dict[str, Any]] = []
    valid_ipvdr: list[float] = []
    for edge_index, (first, second) in enumerate(edges, start=1):
        # Each valley is attached to a graph edge, not a global VTV_L statistic.
        midpoint = (centroids[first] + centroids[second]) / 2.0
        values = sphere_values(midpoint, radius, geometry, dose_gy, masks["GTV"], all_vertices)
        peak = float((node_d50[first] + node_d50[second]) / 2.0)
        valley = float(np.median(values)) if len(values) else None
        valid = len(values) >= minimum_voxels and valley is not None and valley > 0.0
        # iPVDR is reported only when the local denominator has adequate support
        # and is positive; invalid edges never enter the plan summary.
        ipvdr = peak / valley if valid else None
        if ipvdr is not None:
            valid_ipvdr.append(float(ipvdr))
        edge_records.append({
            "edge_id": edge_index,
            "nodes": [names[first], names[second]],
            "length_mm": float(distance_matrix[first, second]),
            "midpoint_lps_mm": [round(float(value), 6) for value in midpoint],
            "edge_peak_d50_gy": peak,
            "edge_local_valley_d50_gy": valley,
            "valley_support_voxels": int(len(values)),
            "minimum_support_voxels": minimum_voxels,
            "ipvdr": float(ipvdr) if ipvdr is not None else None,
            "valid": bool(valid),
            "invalid_reason": None if valid else ("INSUFFICIENT_SUPPORT" if len(values) < minimum_voxels else "NON_POSITIVE_VALLEY_DOSE"),
        })

    # Stage 7: robustly summarise the edge distribution at plan level.
    minimum_edges = int(config["plan_endpoint"]["minimum_valid_edges"])
    if len(valid_ipvdr) < minimum_edges:
        raise AuditBlock(f"Only {len(valid_ipvdr)} valid edges were available; at least {minimum_edges} are required.")
    values = np.asarray(valid_ipvdr, dtype=float)
    # Median is primary because one extreme edge should not dominate the plan.
    q1, median, q3 = np.percentile(values, [25, 50, 75])
    components = graph_components(len(names), edges)
    # WARN results remain visible and auditable without silently discarding an
    # otherwise valid engineering calculation.
    warnings: list[str] = []
    if node_source != "INDIVIDUAL_VTVH_STRUCTURES":
        warnings.append("Individual vertex structures were unavailable; nodes were derived by frozen 26-connected-component labelling.")
    if components != 1:
        warnings.append(f"Nearest-neighbour graph contains {components} disconnected components.")
    invalid_count = len(edges) - len(valid_ipvdr)
    if invalid_count:
        warnings.append(f"{invalid_count} edge(s) failed the frozen local-valley validity rule.")
    if str(manifest.get("plan_status", "UNKNOWN")).upper() != "APPROVED":
        warnings.append(f"RTPLAN approval status is {manifest.get('plan_status', 'UNKNOWN')}.")

    # Stage 8: package calculations with exact definitions and provenance.
    return {
        "framework": "ASCEND-LRT",
        "layer": "2.2B",
        "analysis": "direction-independent graph iPVDR",
        "status": "WARN" if warnings else "PASS",
        "interpretation_gate": config["interpretation_gate"],
        "warnings": warnings,
        "case": {
            "case_id": manifest.get("case_id"),
            "plan_label": manifest.get("plan_label"),
            "rtdose_uid": manifest.get("rtdose_uid"),
            "layer1_directory": str(layer1_dir.resolve()),
            "rtdose_path": str(dose_path.resolve()),
        },
        "frozen_definitions": {
            "node_source": node_source,
            "graph": config["graph"],
            "peak": config["peak"],
            "valley": config["valley"],
            "plan_endpoint": config["plan_endpoint"],
            "dose_sampling": "native RTDOSE voxels; no interpolation",
        },
        "grid": {
            "shape_zyx": list(dose_gy.shape),
            "spacing_zyx_mm": [float(value) for value in spacing],
            "voxel_volume_cc": float(np.prod(spacing) / 1000.0),
        },
        "graph_summary": {
            "number_of_nodes": len(names),
            "number_of_edges": len(edges),
            "number_of_components": components,
            "valid_edges": len(valid_ipvdr),
            "invalid_edges": invalid_count,
        },
        "nodes": node_records,
        "edges": edge_records,
        "plan_ipvdr": {
            "primary_median": float(median),
            "q1": float(q1),
            "q3": float(q3),
            "iqr": float(q3 - q1),
            "minimum": float(np.min(values)),
            "maximum": float(np.max(values)),
        },
        "provenance": {
            "software_version": VERSION,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.version,
            "platform": platform.platform(),
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "configuration_path": str(config_path.resolve()),
            "configuration_sha256": sha256_file(config_path),
            "configuration_content_sha256": canonical_json_hash(config),
            "layer1_result_sha256": sha256_file(result_path),
            "rtdose_sha256": sha256_file(dose_path),
            "mask_archive_sha256": manifest.get("mask_export", {}).get("sha256"),
        },
    }


def main() -> int:
    """Command-line entry point; return 2 for a blocked audit."""
    parser = argparse.ArgumentParser(description="ASCEND-LRT Layer 2.2B graph iPVDR audit")
    parser.add_argument("case_folder", type=Path, help="Folder containing the unchanged Layer 1 DICOM case")
    parser.add_argument("--layer1-dir", type=Path, required=True, help="Completed ASCEND-LRT Layer 1 result directory")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("ascend_lrt_config.json"))
    parser.add_argument("--output", type=Path, help="Output JSON path (default: inside Layer 1 result directory)")
    arguments = parser.parse_args()
    output_path = arguments.output or arguments.layer1_dir / "ascend_lrt_layer2_ipvdr.json"
    try:
        # A result is written only after the complete audit returns successfully.
        result = analyse(arguments.case_folder.resolve(), arguments.layer1_dir.resolve(), arguments.config.resolve())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Status: {result['status']}")
        print(f"Nodes: {result['graph_summary']['number_of_nodes']}")
        print(f"Valid edges: {result['graph_summary']['valid_edges']}")
        print(f"Plan median iPVDR: {result['plan_ipvdr']['primary_median']:.6g}")
        print(f"Result: {output_path.resolve()}")
        return 0
    except (AuditBlock, FileNotFoundError, ValueError, AttributeError) as exc:
        # BLOCK is explicit on stderr and uses a non-zero process exit code for
        # batch processing, logging and automated study pipelines.
        print(f"Status: BLOCK\nReason: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
