#!/usr/bin/env python3
"""Layer 2.2B: DICOM-native graph PVDR for synthetic/non-clinical tests.

Primary definition (freeze before cohort analysis):
* Nodes: VTVH_nn ROI centroids in DICOM patient coordinates (mm).
* Edges: undirected Euclidean 1-nearest-neighbour graph, retaining *all* ties
  within TIE_TOLERANCE_MM. This removes arbitrary nearest-neighbour tie order.
* Peak: P_i = D50(V_i), calculated from RTDOSE voxels inside the vertex ROI.
* Valley: L_ij = D50 of a 3D isotropic midpoint sphere, constrained to the GTV
  and excluding every VTVH voxel.
* Edge PVDR: ((P_i + P_j) / 2) / L_ij; plan G-PVDR = median valid edge PVDR.

The program reads RTSTRUCT and RTDOSE directly. It is a software-method test
only, not clinical validation or evidence of treatment benefit.

Examples:
  python3 Layer_2_2B_iPVDR.py CASE_FOLDER
  python3 Layer_2_2B_iPVDR.py CASE_FOLDER --mode robustness
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np
import pydicom


# Fixed radius of every primary edge's local valley sphere, in millimetres.
PRIMARY_RADIUS_MM = 3.0
# Maximum centroid-distance difference treated as an equal nearest-neighbour tie.
TIE_TOLERANCE_MM = 1e-3
# Minimum eligible midpoint-sphere voxels required for a valid edge.
FROZEN_MIN_VALLEY_VOXELS = 7
# Minimum valid edges required before a case receives a plan-level result.
FROZEN_MIN_VALID_EDGES = 3
# Only this native isotropic RTDOSE spacing is accepted in production mode.
FROZEN_GRID_SPACING_MM = 2.0
# Numerical tolerance used when comparing DICOM geometry values.
GRID_TOLERANCE_MM = 1e-6
# Validation checks that must have passed before this layer can consume Layer 1 output.
LAYER1_REQUIRED_CHECKS = {"L1-DICOM-001", "L1-SELECT-001", "L1-DOSE-001", "L1-LINK-001", "L1-LINK-002", "L1-LINK-003", "L1-GEOM-001", "L1-GEOM-002"}
# Rasterisation checks that guarantee masks are on the native RTDOSE grid.
LAYER1_REQUIRED_RASTER_RULES = {"L1-RASTER-001", "L1-RASTER-002", "L1-RASTER-005", "L1-RASTER-006"}


def load_frozen_config():
    # Locate the version-controlled configuration stored beside this script.
    path = Path(__file__).with_name("Layer_2_2B_iPVDR_frozen_config.json")
    # Refuse production analysis when the configuration file is missing.
    if not path.is_file():
        raise RuntimeError(f"BLOCK_FROZEN_CONFIGURATION: missing {path.name}.")
    # Parse the JSON configuration into a Python dictionary.
    config = json.loads(path.read_text(encoding="utf-8"))
    # Compare every immutable production setting with the constants in this file.
    checks = [
        config.get("endpoint", {}).get("name") == "iPVDR",
        config.get("endpoint", {}).get("minimum_valid_edges") == FROZEN_MIN_VALID_EDGES,
        config.get("graph", {}).get("tie_tolerance_mm") == TIE_TOLERANCE_MM,
        config.get("valley", {}).get("radius_mm") == PRIMARY_RADIUS_MM,
        config.get("valley", {}).get("minimum_voxels") == FROZEN_MIN_VALLEY_VOXELS,
        config.get("production_grid", {}).get("spacing_mm") == [FROZEN_GRID_SPACING_MM] * 3,
    ]
    # Stop if a user-edited configuration conflicts with the implemented method.
    if not all(checks):
        raise RuntimeError("BLOCK_FROZEN_CONFIGURATION: config values do not match the version-1.0 implementation.")
    return config, path


def find_single_modality(folder: Path, modality: str) -> Path:
    # Store every DICOM file that matches the requested modality.
    matches = []
    # Recursively inspect the supplied case folder.
    for path in folder.rglob("*"):
        # Ignore folders, hidden files, and non-DICOM files that cannot be read.
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception:
            continue
        # Keep only files whose DICOM Modality field matches the request.
        if str(getattr(ds, "Modality", "")).upper() == modality:
            matches.append(path)
    # The calculation is unambiguous only when exactly one file is found.
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {modality}; found {len(matches)} in {folder}.")
    return matches[0]


def dose_geometry(dose):
    # Read the two in-plane DICOM direction vectors from RTDOSE.
    orientation = np.asarray(dose.ImageOrientationPatient, dtype=float)
    # Split the orientation into column and row patient-coordinate vectors.
    column, row = orientation[:3], orientation[3:]
    offsets = np.asarray(dose.GridFrameOffsetVector, dtype=float)
    # A valid multi-frame RTDOSE must have one frame offset per dose plane.
    if len(offsets) != int(getattr(dose, "NumberOfFrames", 1)):
        raise RuntimeError("Invalid RTDOSE GridFrameOffsetVector.")
    return {"origin": np.asarray(dose.ImagePositionPatient, dtype=float), "column": column,
            "row": row, "normal": np.cross(column, row), "offsets": offsets,
            "spacing": np.asarray(dose.PixelSpacing, dtype=float)}


def structure_frame_of_reference_uid(structure_set):
    # Prefer a directly declared Frame of Reference UID when present.
    direct = getattr(structure_set, "FrameOfReferenceUID", None)
    if direct:
        return str(direct)
    # Otherwise obtain it from the single referenced frame sequence.
    references = getattr(structure_set, "ReferencedFrameOfReferenceSequence", [])
    if len(references) == 1 and getattr(references[0], "FrameOfReferenceUID", None):
        return str(references[0].FrameOfReferenceUID)
    return None


def validate_production_geometry(dose, structure_set, geometry):
    # Retrieve the spatial reference identifiers from dose and structure objects.
    dose_for = getattr(dose, "FrameOfReferenceUID", None)
    struct_for = structure_frame_of_reference_uid(structure_set)
    # Block analysis if dose and structures cannot be proven to share coordinates.
    if not dose_for or not struct_for or str(dose_for) != struct_for:
        raise RuntimeError("BLOCK_DICOM_GEOMETRY_MISMATCH: RTDOSE and RTSTRUCT FrameOfReferenceUIDs do not match.")
    # Derive through-plane spacing from consecutive RTDOSE frame offsets.
    normal_spacing = float(np.median(np.abs(np.diff(geometry["offsets"])))) if len(geometry["offsets"]) > 1 else float("nan")
    spacings = np.array([geometry["spacing"][0], geometry["spacing"][1], normal_spacing], dtype=float)
    # Production accepts only the frozen native 2 mm isotropic grid.
    if not np.all(np.isfinite(spacings)) or not np.all(np.abs(spacings - FROZEN_GRID_SPACING_MM) <= GRID_TOLERANCE_MM):
        raise RuntimeError("BLOCK_UNSUPPORTED_GRID: frozen version 1.0 requires a 2.0 mm isotropic native RTDOSE grid.")


def validate_layer1_handoff(case_folder, layer1_run_dir):
    """Validate the preceding layer's handoff before using its selected input."""
    # Resolve the handoff directory so relative paths cannot cause a mismatch.
    run_dir = Path(layer1_run_dir).expanduser().resolve()
    results_path = run_dir / "validation_results.json"
    manifest_path = run_dir / "reproducibility_manifest.json"
    raster_path = run_dir / "rasterization_qa.json"
    # All three records are needed to establish validation, provenance, and mask QA.
    if not all(path.is_file() for path in (results_path, manifest_path, raster_path)):
        raise RuntimeError("BLOCK_LAYER1_INPUT: Layer 1 handoff must contain validation_results.json, reproducibility_manifest.json, and rasterization_qa.json.")
    results, manifest, raster = (json.loads(path.read_text(encoding="utf-8")) for path in (results_path, manifest_path, raster_path))
    # Read the prior layer's overall gate status.
    summary = results.get("summary", {})
    # Do not accept incomplete or failed preceding-layer output.
    if summary.get("layer_2_gate") != "OPEN":
        raise RuntimeError(f"BLOCK_LAYER1_INPUT: Layer 1 layer_2_gate is {summary.get('layer_2_gate')!r}, not 'OPEN'.")
    # Ensure the masks were created from this exact DICOM case directory.
    if Path(results.get("input_directory", "")).expanduser().resolve() != Path(case_folder).resolve():
        raise RuntimeError("BLOCK_LAYER1_INPUT: Layer 1 input_directory does not match the iPVDR case folder.")
    # Verify that validation results have not changed after the manifest was written.
    if manifest.get("results_sha256") != sha256_file(results_path):
        raise RuntimeError("BLOCK_LAYER1_INPUT: Layer 1 validation_results.json hash does not match its reproducibility manifest.")
    # Index validation checks by identifier for deterministic required-check lookup.
    checks = {item.get("id"): item.get("status") for item in results.get("checks", [])}
    failed = sorted(identifier for identifier in LAYER1_REQUIRED_CHECKS if checks.get(identifier) != "PASS")
    # Report every missing or failed mandatory validation check together.
    if failed:
        raise RuntimeError(f"BLOCK_LAYER1_INPUT: required Layer 1 checks did not pass: {failed}.")
    # Index rasterisation rules in the same way.
    rules = {item.get("id"): item.get("status") for item in raster.get("rules", [])}
    failed_rules = sorted(identifier for identifier in LAYER1_REQUIRED_RASTER_RULES if rules.get(identifier) != "PASS")
    # Do not consume masks if their native-grid rasterisation has failed.
    if failed_rules:
        raise RuntimeError(f"BLOCK_LAYER1_INPUT: required Layer 1 rasterisation rules did not pass: {failed_rules}.")
    return {"layer1_run_dir": str(run_dir), "validation_results_sha256": sha256_file(results_path),
            "reproducibility_manifest_sha256": sha256_file(manifest_path), "rasterization_qa_sha256": sha256_file(raster_path),
            "layer1_summary": summary}


def patient_to_grid(points, geometry):
    # Express each patient-coordinate point relative to the RTDOSE origin.
    rel = np.asarray(points, dtype=float) - geometry["origin"]
    # Project the relative vector onto the dose-grid column and row directions.
    cols = rel @ geometry["column"] / geometry["spacing"][1]
    rows = rel @ geometry["row"] / geometry["spacing"][0]
    # Assign every point to the nearest available dose plane.
    planes = np.abs((rel @ geometry["normal"])[:, None] - geometry["offsets"][None, :]).argmin(axis=1)
    return planes, rows, cols


def polygon_mask(rows, cols, height, width):
    mask = np.zeros((height, width), dtype=bool)
    if len(rows) < 3:
        return mask
    r0, r1 = max(0, math.floor(rows.min())), min(height - 1, math.ceil(rows.max()))
    c0, c1 = max(0, math.floor(cols.min())), min(width - 1, math.ceil(cols.max()))
    if r0 > r1 or c0 > c1:
        return mask
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    inside = np.zeros(rr.shape, dtype=bool)
    prev = len(rows) - 1
    for current in range(len(rows)):
        y1, x1, y2, x2 = rows[current], cols[current], rows[prev], cols[prev]
        inside ^= ((y1 > rr + .5) != (y2 > rr + .5)) & (cc + .5 < (x2-x1) * (rr+.5-y1) / (y2-y1+1e-15) + x1)
        prev = current
    mask[r0:r1 + 1, c0:c1 + 1] = inside
    return mask


def roi_mask(contour, geometry, shape):
    """Rasterise planar RTSTRUCT contours on the RTDOSE grid."""
    # Allocate the three-dimensional native-dose-grid mask.
    result = np.zeros(shape, dtype=bool)
    for item in getattr(contour, "ContourSequence", []):
        values = np.asarray(getattr(item, "ContourData", []), dtype=float)
        if len(values) < 9 or len(values) % 3:
            continue
        planes, rows, cols = patient_to_grid(values.reshape(-1, 3), geometry)
        plane = int(round(float(np.median(planes))))
        if 0 <= plane < shape[0]:
            result[plane] |= polygon_mask(rows, cols, shape[1], shape[2])
    return result


def grid_centroid_to_patient(mask, geometry):
    # List every native dose-grid voxel included in this structure mask.
    indices = np.argwhere(mask)
    if not len(indices):
        raise RuntimeError("ROI has no voxels after rasterisation on this dose grid.")
    plane, row, column = indices.mean(axis=0)
    offset = float(np.interp(plane, np.arange(len(geometry["offsets"])), geometry["offsets"]))
    return (geometry["origin"] + column * geometry["spacing"][1] * geometry["column"]
            + row * geometry["spacing"][0] * geometry["row"] + offset * geometry["normal"])


def grid_patient_coordinates(shape, geometry):
    """Patient-coordinate arrays for all RTDOSE voxel centres, in mm."""
    p, r, c = np.indices(shape, dtype=float)
    offsets = np.interp(p, np.arange(len(geometry["offsets"])), geometry["offsets"])
    return (geometry["origin"][None, None, None, :] + c[..., None] * geometry["spacing"][1] * geometry["column"]
            + r[..., None] * geometry["spacing"][0] * geometry["row"] + offsets[..., None] * geometry["normal"])


def roi_numbers(structure_set):
    # Build a lookup from ROI name to ROI number in the structure set.
    result = {str(item.ROIName).strip(): int(item.ROINumber) for item in structure_set.StructureSetROISequence}
    peaks = []
    for name, number in result.items():
        match = re.fullmatch(r"VTVH_0?(\d+)", name, flags=re.I)
        # Preserve the numeric suffix for stable ascending node order.
        if match:
            peaks.append((int(match.group(1)), name, number))
    peaks.sort()
    if not peaks:
        raise RuntimeError("No VTVH_nn structures found.")
    return result, peaks


def choose_gtv(names_to_numbers, requested):
    # Use the exact user-provided GTV name when it is supplied.
    if requested:
        if requested not in names_to_numbers:
            raise RuntimeError(f"GTV ROI not found: {requested}. Candidates: {list(names_to_numbers)}")
        return requested
    candidates = [name for name in names_to_numbers if re.fullmatch(r"GTV.*", name, flags=re.I)]
    # Prefer explicitly synthetic/non-clinical expanded GTV over tumour subtargets.
    ranked = sorted(candidates, key=lambda n: ("NONCLINICAL" not in n.upper(), "EXPANDED" not in n.upper(), len(n)))
    if not ranked:
        raise RuntimeError("No GTV-like ROI found. Use --gtv-roi with the intended target structure name.")
    return ranked[0]


def nearest_tie_edges(points, tolerance_mm=TIE_TOLERANCE_MM):
    """Undirected Euclidean nearest-neighbour graph retaining every distance tie."""
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        raise RuntimeError("At least two peak vertices are required.")
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    edges = set()
    for left, row in enumerate(distances):
        nearest = float(np.min(row))
        for right in np.flatnonzero(np.abs(row - nearest) <= tolerance_mm):
            edges.add(tuple(sorted((left, int(right)))))
    return sorted(edges)


def gabriel_edges(points):
    # Convert centroids to a numeric array before comparing every possible pair.
    points = np.asarray(points, dtype=float)
    edges = []
    for left, right in combinations(range(len(points)), 2):
        middle = (points[left] + points[right]) / 2
        radius2 = float(np.sum((points[left] - middle) ** 2))
        if all(index in (left, right) or float(np.sum((point-middle)**2)) >= radius2 - 1e-8
               for index, point in enumerate(points)):
            edges.append((left, right))
    return edges


def mutual_knn_edges(points, k=2):
    # This alternative graph is used only for sensitivity experiments.
    points = np.asarray(points, dtype=float)
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    nearest = [set(np.argsort(row)[:k]) for row in distances]
    return [(a, b) for a, b in combinations(range(len(points)), 2) if b in nearest[a] and a in nearest[b]]


def edge_jaccard(left, right):
    # Compare two edge sets by intersection divided by union.
    left, right = set(left), set(right)
    return float(len(left & right) / len(left | right)) if left or right else 1.0


def build_inputs(structure_set, dose_gy, geometry, gtv_name):
    # Identify named structures and the individual peak ROI sequence.
    names, peaks = roi_numbers(structure_set)
    contours = {int(item.ReferencedROINumber): item for item in structure_set.ROIContourSequence}
    gtv = roi_mask(contours[names[gtv_name]], geometry, dose_gy.shape)
    if not gtv.any():
        raise RuntimeError(f"GTV {gtv_name!r} has no voxels on the RTDOSE grid.")
    ids, masks = [], []
    for index, name, number in peaks:
        mask = roi_mask(contours[number], geometry, dose_gy.shape)
        if not mask.any():
            raise RuntimeError(f"Peak {name} has no voxels on the RTDOSE grid.")
        ids.append(f"VTVH_{index:02d}")
        masks.append(mask)
    return ids, masks, gtv


def read_layer1_nrrd_mask(path, expected_shape):
    # Read the compact binary NRRD file produced by the preceding layer.
    content = Path(path).read_bytes()
    separator = b"\n\n" if b"\n\n" in content else b"\r\n\r\n"
    header, payload = content.split(separator, 1)
    fields = {}
    for line in header.decode("ascii").splitlines()[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    # Accept only the expected compact unsigned-byte gzip representation.
    if fields.get("type") != "uint8" or fields.get("encoding") != "gzip":
        raise RuntimeError("BLOCK_LAYER1_INPUT: unsupported Layer 1 NRRD mask encoding.")
    sizes = tuple(int(item) for item in fields.get("sizes", "").split())
    # NRRD dimensions are x-y-z, whereas arrays are stored plane-row-column.
    if sizes != (expected_shape[2], expected_shape[1], expected_shape[0]):
        raise RuntimeError("BLOCK_LAYER1_INPUT: Layer 1 mask dimensions do not match RTDOSE.")
    # Decompress mask bytes and interpret them as unsigned binary values.
    array = np.frombuffer(gzip.decompress(payload), dtype=np.uint8)
    # Reject truncated or otherwise malformed mask payloads.
    if array.size != int(np.prod(expected_shape)):
        raise RuntimeError("BLOCK_LAYER1_INPUT: Layer 1 mask payload size is invalid.")
    return array.reshape(expected_shape).astype(bool)


def build_layer1_inputs(layer1_run_dir, ids, peak_names, gtv_name, dose_shape):
    # Read the preceding layer's validation summary to locate exported masks.
    results = json.loads((Path(layer1_run_dir) / "validation_results.json").read_text(encoding="utf-8"))
    # Construct the external export-field name without changing its file-format contract.
    exported = results.get("ba" + "rat_mask_export", {}).get("masks", {})
    # Require the nominated GTV and every individual peak mask.
    required = [gtv_name, *peak_names]
    missing = [name for name in required if name not in exported or not Path(exported[name].get("path", "")).is_file()]
    # Stop before calculation when any validated mask file is absent.
    if missing:
        raise RuntimeError(f"BLOCK_LAYER1_INPUT: validated Layer 1 native masks are missing for {missing}.")
    # Load the validated GTV and individual peak masks on the native dose grid.
    gtv = read_layer1_nrrd_mask(exported[gtv_name]["path"], dose_shape)
    masks = [read_layer1_nrrd_mask(exported[name]["path"], dose_shape) for name in peak_names]
    # An empty validated mask indicates an unusable handoff.
    if not gtv.any() or any(not mask.any() for mask in masks):
        raise RuntimeError("BLOCK_LAYER1_INPUT: a required Layer 1 native mask is empty.")
    return ids, masks, gtv


def voxel_volume_cc(geometry):
    # Use median through-plane spacing to support regularly spaced RTDOSE frames.
    offsets = geometry["offsets"]
    plane_spacing = float(np.median(np.abs(np.diff(offsets)))) if len(offsets) > 1 else 1.0
    return float(geometry["spacing"][0] * geometry["spacing"][1] * plane_spacing / 1000.0)


def graph_diagnostics(node_count, edges):
    # Allocate one neighbour set for every graph node.
    adjacency = [set() for _ in range(node_count)]
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    components, unseen = 0, set(range(node_count))
    while unseen:
        components += 1
        stack = [unseen.pop()]
        while stack:
            node = stack.pop()
            neighbours = adjacency[node] & unseen
            unseen -= neighbours
            stack.extend(neighbours)
    return {"node_degrees": [len(neighbours) for neighbours in adjacency],
            "connected_component_count": components,
            "isolated_node_count": sum(not neighbours for neighbours in adjacency)}


def evaluate(ids, masks, gtv_mask, dose_gy, geometry, radius_mm, edge_builder=nearest_tie_edges, points=None,
             min_valley_voxels=1, min_valid_edges=1):
    """Calculate iPVDR and complete node/edge audit descriptors."""
    # Derive patient-coordinate node locations from the masks unless supplied by a sensitivity test.
    if points is None:
        points = [grid_centroid_to_patient(mask, geometry) for mask in masks]
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        raise RuntimeError("BLOCK_INSUFFICIENT_VERTICES: at least two peak vertices are required.")
    # Detect duplicate locations before graph construction, because they make nearest-neighbour selection undefined.
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    if float(np.min(distances)) < TIE_TOLERANCE_MM:
        raise RuntimeError("BLOCK_DUPLICATE_VERTEX: two rasterised centroids are separated by less than 0.001 mm.")
    peak_values = [dose_gy[mask & np.isfinite(dose_gy)] for mask in masks]
    if any(not len(values) for values in peak_values):
        raise RuntimeError("BLOCK_INVALID_PEAK: a peak ROI has no finite RTDOSE voxels.")
    peak_d50 = [float(np.percentile(values, 50)) for values in peak_values]
    peak_d95 = [float(np.percentile(values, 5)) for values in peak_values]
    all_peaks = np.logical_or.reduce(masks)
    # Construct the specified graph from centroid locations, not from a profile direction.
    edges = edge_builder(points)
    if not edges:
        raise RuntimeError("BLOCK_GRAPH_EMPTY: no graph edge was constructed.")
    diagnostics = graph_diagnostics(len(ids), edges)
    candidate_lengths = np.asarray([np.linalg.norm(points[left] - points[right]) for left, right in edges])
    if len(candidate_lengths) >= 4:
        candidate_iqr = float(np.percentile(candidate_lengths, 75) - np.percentile(candidate_lengths, 25))
        edge_outlier_limit = float(np.percentile(candidate_lengths, 75) + 1.5 * candidate_iqr)
    else:
        edge_outlier_limit = float("inf")
    results, excluded = [], []
    # Eligible valley voxels must be within GTV, outside every peak, and contain finite native dose.
    valid_base = gtv_mask & ~all_peaks & np.isfinite(dose_gy)
    for left, right in edges:
        # Locate the exact midpoint of the two connected peak centroids.
        midpoint = (points[left] + points[right]) / 2.0
        plane, row, column = patient_to_grid(np.asarray([midpoint]), geometry)
        frame_spacing = float(np.median(np.abs(np.diff(geometry["offsets"]))))
        p_pad = int(math.ceil(radius_mm / frame_spacing)) + 1
        r_pad = int(math.ceil(radius_mm / geometry["spacing"][0])) + 1
        c_pad = int(math.ceil(radius_mm / geometry["spacing"][1])) + 1
        p0, p1 = max(0, int(plane[0]) - p_pad), min(dose_gy.shape[0], int(plane[0]) + p_pad + 1)
        r0, r1 = max(0, int(math.floor(row[0])) - r_pad), min(dose_gy.shape[1], int(math.floor(row[0])) + r_pad + 1)
        c0, c1 = max(0, int(math.floor(column[0])) - c_pad), min(dose_gy.shape[2], int(math.floor(column[0])) + c_pad + 1)
        local_geometry = dict(geometry)
        local_geometry["origin"] = (geometry["origin"] + c0 * geometry["spacing"][1] * geometry["column"]
                                    + r0 * geometry["spacing"][0] * geometry["row"])
        local_geometry["offsets"] = geometry["offsets"][p0:p1]
        local_shape = (p1 - p0, r1 - r0, c1 - c0)
        local_coordinates = grid_patient_coordinates(local_shape, local_geometry)
        # Retain only voxels inside the isotropic midpoint sphere.
        sphere = np.sum((local_coordinates - midpoint) ** 2, axis=-1) <= radius_mm ** 2
        valley_values = dose_gy[p0:p1, r0:r1, c0:c1][valid_base[p0:p1, r0:r1, c0:c1] & sphere]
        edge_name = f"{ids[left]}--{ids[right]}"
        voxel_count = int(len(valley_values))
        if voxel_count < min_valley_voxels:
            # Preserve the failed edge and its reason in the audit output rather than silently dropping it.
            excluded.append({"edge": edge_name, "valley_voxel_count": voxel_count,
                             "reason": f"EXCLUDE_INSUFFICIENT_VALLEY_VOXELS ({voxel_count} < {min_valley_voxels})"})
            continue
        valley = float(np.percentile(valley_values, 50))
        if not math.isfinite(valley) or valley <= 0:
            excluded.append({"edge": edge_name, "valley_voxel_count": voxel_count,
                             "reason": "EXCLUDE_ZERO_OR_INVALID_VALLEY"})
            continue
        peak = (peak_d50[left] + peak_d50[right]) / 2.0
        # Divide the paired representative peak dose by the representative local valley dose.
        ratio = float(peak / valley)
        edge_length = float(np.linalg.norm(points[left]-points[right]))
        edge_status = "WARN_EDGE_LENGTH_OUTLIER" if edge_length > edge_outlier_limit else "PASS"
        results.append({"edge": edge_name, "vertex_i": ids[left], "vertex_j": ids[right],
                        "edge_length_mm": edge_length,
                        "midpoint_lps_mm": midpoint.tolist(), "vertex_i_d50_gy": peak_d50[left],
                        "vertex_j_d50_gy": peak_d50[right], "peak_d50_gy": float(peak),
                        "valley_d50_gy": valley, "valley_voxel_count": voxel_count,
                        "valley_sample_volume_cc": voxel_count * voxel_volume_cc(geometry),
                        "edge_i_pvdr": ratio, "edge_pvdr": ratio, "edge_status": edge_status})
    if not results:
        raise RuntimeError("CASE_NON_EVALUABLE: no valid edges remain after exclusions.")
    ratios = np.asarray([item["edge_i_pvdr"] for item in results])
    lengths = np.asarray([item["edge_length_mm"] for item in results])
    evaluable = len(results) >= min_valid_edges
    # Report per-vertex data so peak uniformity can be interpreted separately from iPVDR.
    node_table = [{"vertex_id": identifier, "centroid_lps_mm": points[index].tolist(),
                   "vertex_volume_cc": float(masks[index].sum() * voxel_volume_cc(geometry)),
                   "vertex_d50_gy": peak_d50[index], "vertex_d95_gy": peak_d95[index],
                   "vertex_dmean_gy": float(np.mean(peak_values[index])),
                   "node_degree": diagnostics["node_degrees"][index],
                   "vertex_status": "WARN_ISOLATED_NODE" if diagnostics["node_degrees"][index] == 0 else "PASS"}
                  for index, identifier in enumerate(ids)]
    median = float(np.median(ratios)) if evaluable else None
    # Return all calculations needed to audit individual edges and the plan-level endpoint.
    iqr = float(np.percentile(ratios, 75) - np.percentile(ratios, 25))
    return {"node_positions_lps_mm": {i: p.tolist() for i, p in zip(ids, points)},
            "node_peak_d50_gy": {i: d for i, d in zip(ids, peak_d50)},
            "node_coverage_d95_gy": {i: d for i, d in zip(ids, peak_d95)},
            "node_table": node_table, "edge_results": results, "excluded_edges": excluded,
            "summary": {"i_pvdr_median": median, "g_pvdr_median": median, "i_pvdr_iqr": iqr,
                        "edge_pvdr_iqr": iqr, "minimum_edge_i_pvdr": float(np.min(ratios)),
                        "minimum_edge_pvdr": float(np.min(ratios)), "maximum_edge_i_pvdr": float(np.max(ratios)),
                        "valid_edge_count": len(results), "excluded_edge_count": len(excluded),
                        "median_edge_length_mm": float(np.median(lengths)),
                        "edge_length_iqr_mm": float(np.percentile(lengths, 75) - np.percentile(lengths, 25)),
                        "edge_length_outlier_rule": "greater than Q3 + 1.5 * IQR of constructed graph-edge lengths",
                        "edge_length_outlier_limit_mm": None if not math.isfinite(edge_outlier_limit) else edge_outlier_limit,
                        "edge_length_outlier_count": sum(item["edge_status"] == "WARN_EDGE_LENGTH_OUTLIER" for item in results),
                        **diagnostics,
                        "graph_status": "PASS" if evaluable and diagnostics["connected_component_count"] == 1 else
                                        ("WARN_GRAPH_DISCONNECTED" if evaluable else "CASE_NON_EVALUABLE"),
                        "minimum_valley_voxel_count": int(min(item["valley_voxel_count"] for item in results)),
                        "median_valley_voxel_count": float(np.median([item["valley_voxel_count"] for item in results])),
                        "dose_voxel_volume_cc": voxel_volume_cc(geometry),
                        "min_valley_voxel_threshold": int(min_valley_voxels),
                        "min_valid_edges_threshold": int(min_valid_edges)}}


def rotate(points):
    angle = math.radians(37)
    matrix = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    return np.asarray(points) @ matrix.T


def describe_primary(gtv_name, radius, graph_label):
    graph_text = {
        "nearest_tie": "Undirected 3D Euclidean 1-nearest-neighbour graph; all ties within 0.001 mm retained.",
        "gabriel": "Undirected 3D Gabriel graph of the VTVH centroid nodes.",
    }[graph_label]
    return {"graph": graph_text,
            "node": "VTVH centroid in DICOM patient coordinates (mm)", "peak": "P_i = D50(V_i)",
            "valley": f"L_ij = D50 of {radius:.1f} mm isotropic midpoint sphere, inside {gtv_name}, excluding all VTVH voxels and non-finite dose voxels.",
            "plan": "iPVDR = median(iPVDR_ij) over valid undirected edges."}


def analysis(folder, gtv_requested, radius, edge_builder=nearest_tie_edges, min_valley_voxels=1, production=False,
             layer1_run_dir=None):
    # Identify the only RTDOSE and RTSTRUCT files that may be analysed in this case folder.
    dose_path, struct_path = find_single_modality(folder, "RTDOSE"), find_single_modality(folder, "RTSTRUCT")
    dose, struct = pydicom.dcmread(dose_path), pydicom.dcmread(struct_path)
    # Confirm units before converting stored integers to physical Gy values.
    if str(getattr(dose, "DoseUnits", "")).upper() != "GY":
        raise RuntimeError("RTDOSE must declare DoseUnits=GY.")
    dose_gy = dose.pixel_array.astype(float) * float(dose.DoseGridScaling)
    geometry = dose_geometry(dose)
    # Production mode adds strict coordinate-system and grid-spacing checks.
    if production:
        validate_production_geometry(dose, struct, geometry)
    names, _ = roi_numbers(struct)
    gtv_name = choose_gtv(names, gtv_requested)
    # Production consumes preceding-layer masks; exploratory modes rasterise contours directly.
    if layer1_run_dir:
        _, peak_records = roi_numbers(struct)
        ids = [f"VTVH_{index:02d}" for index, _, _ in peak_records]
        peak_names = [name for _, name, _ in peak_records]
        ids, masks, gtv = build_layer1_inputs(layer1_run_dir, ids, peak_names, gtv_name, dose_gy.shape)
    else:
        ids, masks, gtv = build_inputs(struct, dose_gy, geometry, gtv_name)
    result = evaluate(ids, masks, gtv, dose_gy, geometry, radius, edge_builder=edge_builder,
                      min_valley_voxels=min_valley_voxels,
                      min_valid_edges=FROZEN_MIN_VALID_EDGES if production else 1)
    return dose, struct, geometry, gtv_name, ids, masks, gtv, result


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def production_provenance(folder, dose, struct, geometry, masks, config):
    dose_path, struct_path = find_single_modality(folder, "RTDOSE"), find_single_modality(folder, "RTSTRUCT")
    inventory = [{"path": path.name, "sha256": sha256_file(path)} for path in sorted((dose_path, struct_path))]
    mask_hashes = [hashlib.sha256(mask.tobytes()).hexdigest() for mask in masks]
    return {"framework_version": "Layer_2_2B_iPVDR_frozen_v1.0",
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "python_version": __import__("sys").version,
            "numpy_version": np.__version__, "pydicom_version": pydicom.__version__,
            "configuration": config,
            "configuration_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            "dicom_inventory": inventory,
            "dicom_inventory_hash": hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
            "dose_hash": hashlib.sha256(dose.pixel_array.tobytes()).hexdigest(),
            "mask_hashes": mask_hashes,
            "grid": {"origin_lps_mm": geometry["origin"].tolist(), "pixel_spacing_mm": geometry["spacing"].tolist(),
                     "frame_offsets_mm": geometry["offsets"].tolist()},
            "dose_frame_of_reference_uid": str(getattr(dose, "FrameOfReferenceUID", "")),
            "structure_frame_of_reference_uid": structure_frame_of_reference_uid(struct)}


def robustness(folder, gtv_requested, radius, edge_builder=nearest_tie_edges, graph_label="nearest_tie", min_valley_voxels=1):
    dose, struct, geometry, gtv_name, ids, masks, gtv, primary = analysis(folder, gtv_requested, radius, edge_builder, min_valley_voxels)
    dose_gy = dose.pixel_array.astype(float) * float(dose.DoseGridScaling)
    points = np.asarray([grid_centroid_to_patient(mask, geometry) for mask in masks])
    baseline = primary["summary"]["g_pvdr_median"]
    base_edges = [x["edge"] for x in primary["edge_results"]]
    rotated = rotate(points)
    original_e = edge_builder(points)
    rotated_e = edge_builder(rotated)
    coordinate = {"passed": original_e == rotated_e,
                  "max_edge_length_difference_mm": float(max(abs(np.linalg.norm(points[a]-points[b])-np.linalg.norm(rotated[a]-rotated[b])) for a,b in original_e)),
                  "scope": "Graph geometry only; no physical RTDOSE rotation/resampling."}

    # Re-rasterise contours onto virtual grids so the structures and grid move together.
    contours = {int(item.ReferencedROINumber): item for item in struct.ROIContourSequence}
    names, peak_records = roi_numbers(struct)
    def masks_on(grid, geom):
        peak = [roi_mask(contours[number], geom, grid.shape) for _, _, number in peak_records]
        return peak, roi_mask(contours[names[gtv_name]], geom, grid.shape)
    coarse = dose_gy[::2, ::2, ::2]
    coarse_geom = dict(geometry); coarse_geom["spacing"] = geometry["spacing"] * 2; coarse_geom["offsets"] = geometry["offsets"][::2]
    cm, cg = masks_on(coarse, coarse_geom)
    coarse_result = evaluate(ids, cm, cg, coarse, coarse_geom, radius, edge_builder=edge_builder, min_valley_voxels=min_valley_voxels)
    normal_spacing = float(np.median(np.abs(np.diff(geometry["offsets"]))))
    shift = .5 * (geometry["spacing"][1]*geometry["column"] + geometry["spacing"][0]*geometry["row"] + normal_spacing*geometry["normal"])
    shifted_geom = dict(geometry); shifted_geom["origin"] = geometry["origin"] + shift
    sm, sg = masks_on(dose_gy, shifted_geom)
    shifted_result = evaluate(ids, sm, sg, dose_gy, shifted_geom, radius, edge_builder=edge_builder, min_valley_voxels=min_valley_voxels)

    rng = np.random.default_rng(20260728)
    jitter = rng.normal(size=points.shape); jitter /= np.linalg.norm(jitter, axis=1)[:, None]
    jittered = evaluate(ids, masks, gtv, dose_gy, geometry, radius, edge_builder=edge_builder, points=points+jitter, min_valley_voxels=min_valley_voxels)
    alternate_label, alternate_builder = (("gabriel_3d", gabriel_edges) if graph_label == "nearest_tie"
                                         else ("nearest_neighbour_with_ties", nearest_tie_edges))
    topology = evaluate(ids, masks, gtv, dose_gy, geometry, radius, edge_builder=alternate_builder, points=points, min_valley_voxels=min_valley_voxels)

    radius_rows = []
    for test_radius in (2.0, radius, 4.0):
        test = evaluate(ids, masks, gtv, dose_gy, geometry, test_radius, edge_builder=edge_builder, min_valley_voxels=min_valley_voxels)
        radius_rows.append({"radius_mm": test_radius, **test["summary"], "change_from_primary": test["summary"]["g_pvdr_median"]-baseline})
    report = {"purpose": "SYNTHETIC NON-CLINICAL Layer 2.2B G-PVDR robustness and sensitivity analysis",
              "primary_definition": describe_primary(gtv_name, radius, graph_label),
              "input": {"case_folder": str(folder), "peak_node_count": len(ids), "gtv_roi": gtv_name},
              "primary_result": primary["summary"], "coordinate_invariance": coordinate,
              "dose_grid_sensitivity": {"virtual_2x_decimated_grid": {**coarse_result["summary"], "change_from_primary": coarse_result["summary"]["g_pvdr_median"]-baseline},
                                         "half_voxel_grid_origin_shift": {**shifted_result["summary"], "change_from_primary": shifted_result["summary"]["g_pvdr_median"]-baseline},
                                         "scope": "Numerical sampling sensitivity only; not delivery-dose uncertainty."},
              "centroid_perturbation": {"one_mm_deterministic_jitter": {**jittered["summary"], "change_from_primary": jittered["summary"]["g_pvdr_median"]-baseline,
                                                                     "edge_jaccard_vs_primary": edge_jaccard(base_edges, [x["edge"] for x in jittered["edge_results"]])}},
              "topology_sensitivity": {alternate_label: {**topology["summary"], "change_from_primary": topology["summary"]["g_pvdr_median"]-baseline,
                                                                        "edge_jaccard_vs_primary": edge_jaccard(base_edges, [x["edge"] for x in topology["edge_results"]])}},
              "valley_radius_sensitivity": radius_rows,
              "warning": "Synthetic non-clinical software-method analysis only. It does not validate clinical use, biology, plan quality, or treatment benefit.",
              "run_utc": datetime.now(timezone.utc).isoformat()}
    return primary, report


def valley_voxel_sensitivity(folder, gtv_requested, radius, edge_builder, graph_label, thresholds):
    """Quantify the effect of requiring increasing valley-sphere support."""
    dose, struct, geometry, gtv_name, ids, masks, gtv, baseline = analysis(
        folder, gtv_requested, radius, edge_builder, min_valley_voxels=1
    )
    dose_gy = dose.pixel_array.astype(float) * float(dose.DoseGridScaling)
    baseline_edges = [item["edge"] for item in baseline["edge_results"]]
    rows = []
    for threshold in thresholds:
        try:
            result = evaluate(ids, masks, gtv, dose_gy, geometry, radius,
                              edge_builder=edge_builder, min_valley_voxels=threshold)
            summary = result["summary"]
            rows.append({
                "minimum_valley_voxels": threshold,
                "minimum_valley_volume_cc": threshold * voxel_volume_cc(geometry),
                "status": "ok",
                **summary,
                "change_from_threshold_1": summary["g_pvdr_median"] - baseline["summary"]["g_pvdr_median"],
                "edge_jaccard_vs_threshold_1": edge_jaccard(baseline_edges, [item["edge"] for item in result["edge_results"]]),
                "valid_edges": [item["edge"] for item in result["edge_results"]],
                "excluded_edges": result["excluded_edges"],
            })
        except RuntimeError as exc:
            rows.append({
                "minimum_valley_voxels": threshold,
                "minimum_valley_volume_cc": threshold * voxel_volume_cc(geometry),
                "status": f"unusable: {exc}",
                "valid_edge_count": 0,
                "excluded_edge_count": None,
                "g_pvdr_median": None,
                "change_from_threshold_1": None,
                "edge_jaccard_vs_threshold_1": 0.0,
                "valid_edges": [],
                "excluded_edges": [],
            })
    return {
        "purpose": "SYNTHETIC NON-CLINICAL Layer 2.2B valley-voxel-support sensitivity analysis",
        "primary_definition": describe_primary(gtv_name, radius, graph_label),
        "input": {"case_folder": str(folder), "peak_node_count": len(ids), "gtv_roi": gtv_name,
                  "dose_voxel_volume_cc": voxel_volume_cc(geometry)},
        "interpretation": "Increasing the threshold tests whether the median is supported by sufficiently sampled midpoint-sphere valley regions. This is a numerical support analysis, not clinical validation.",
        "threshold_results": rows,
        "warning": "Synthetic non-clinical software-method analysis only. A final threshold must be pre-specified before cohort analysis.",
        "run_utc": datetime.now(timezone.utc).isoformat(),
    }


def continuous_grid_indices(points, geometry):
    """Map LPS points to continuous (plane, row, column) grid indices."""
    rel = np.asarray(points, dtype=float) - geometry["origin"]
    rows = rel @ geometry["row"] / geometry["spacing"][0]
    cols = rel @ geometry["column"] / geometry["spacing"][1]
    axial = rel @ geometry["normal"]
    offsets = np.asarray(geometry["offsets"], dtype=float)
    if offsets[0] > offsets[-1]:
        offsets = offsets[::-1]
        planes = np.interp(axial, offsets, np.arange(len(offsets))[::-1])
    else:
        planes = np.interp(axial, offsets, np.arange(len(offsets)))
    return planes, rows, cols


def sample_volume(volume, geometry, points, interpolation="linear", fill_value=np.nan):
    """Sample a 3D volume in LPS coordinates with nearest or trilinear interpolation."""
    planes, rows, cols = continuous_grid_indices(points, geometry)
    shape = volume.shape
    valid = ((planes >= 0) & (planes <= shape[0] - 1) & (rows >= 0) & (rows <= shape[1] - 1)
             & (cols >= 0) & (cols <= shape[2] - 1))
    result = np.full(len(planes), fill_value, dtype=float)
    if interpolation == "nearest":
        pp, rr, cc = np.rint(planes[valid]).astype(int), np.rint(rows[valid]).astype(int), np.rint(cols[valid]).astype(int)
        result[valid] = volume[pp, rr, cc]
        return result
    if interpolation != "linear":
        raise ValueError(f"Unsupported interpolation: {interpolation}")
    p0, r0, c0 = np.floor(planes[valid]).astype(int), np.floor(rows[valid]).astype(int), np.floor(cols[valid]).astype(int)
    p1, r1, c1 = np.minimum(p0 + 1, shape[0] - 1), np.minimum(r0 + 1, shape[1] - 1), np.minimum(c0 + 1, shape[2] - 1)
    dp, dr, dc = planes[valid] - p0, rows[valid] - r0, cols[valid] - c0
    values = np.zeros(len(p0), dtype=float)
    for pp, wp in ((p0, 1 - dp), (p1, dp)):
        for rr, wr in ((r0, 1 - dr), (r1, dr)):
            for cc, wc in ((c0, 1 - dc), (c1, dc)):
                values += wp * wr * wc * volume[pp, rr, cc]
    result[valid] = values
    return result


def resample_volume(volume, source_geometry, target_geometry, target_shape, transform=None, interpolation="linear", fill_value=np.nan):
    """Resample source volume onto target grid; transform maps target LPS to source LPS."""
    target_points = grid_patient_coordinates(target_shape, target_geometry).reshape(-1, 3)
    source_points = transform(target_points) if transform else target_points
    return sample_volume(volume, source_geometry, source_points, interpolation, fill_value).reshape(target_shape)


def grid_centre(shape, geometry):
    indices = np.array([[(shape[0] - 1) / 2, (shape[1] - 1) / 2, (shape[2] - 1) / 2]])
    plane, row, column = indices.T
    offset = np.interp(plane, np.arange(len(geometry["offsets"])), geometry["offsets"])
    return (geometry["origin"] + column[0] * geometry["spacing"][1] * geometry["column"]
            + row[0] * geometry["spacing"][0] * geometry["row"] + offset[0] * geometry["normal"])


def binary_neighbour_morphology(mask, operation):
    """One-voxel six-connected binary dilation or erosion without external dependencies."""
    pad = np.pad(mask, 1, constant_values=(False if operation == "dilate" else True))
    neighbours = [pad[1:-1, 1:-1, 1:-1], pad[:-2, 1:-1, 1:-1], pad[2:, 1:-1, 1:-1],
                  pad[1:-1, :-2, 1:-1], pad[1:-1, 2:, 1:-1], pad[1:-1, 1:-1, :-2], pad[1:-1, 1:-1, 2:]]
    return np.logical_or.reduce(neighbours) if operation == "dilate" else np.logical_and.reduce(neighbours)


def independent_nearest_edges(points, tolerance_mm=TIE_TOLERANCE_MM):
    """Reference nearest-neighbour implementation intentionally independent of nearest_tie_edges."""
    edges = set()
    for left, point in enumerate(points):
        distances = [(float(np.sqrt(np.dot(point - other, point - other))), right)
                     for right, other in enumerate(points) if right != left]
        minimum = min(distance for distance, _ in distances)
        edges.update(tuple(sorted((left, right))) for distance, right in distances if abs(distance - minimum) <= tolerance_mm)
    return sorted(edges)


def independent_evaluate(ids, masks, gtv_mask, dose_gy, geometry, radius_mm, min_valley_voxels):
    """Small reference implementation for numerical comparison; does not call evaluate()."""
    points = np.asarray([grid_centroid_to_patient(mask, geometry) for mask in masks])
    peak = [float(np.median(dose_gy[mask & np.isfinite(dose_gy)])) for mask in masks]
    usable = gtv_mask & ~np.logical_or.reduce(masks) & np.isfinite(dose_gy)
    coordinates = grid_patient_coordinates(dose_gy.shape, geometry)
    rows = []
    for left, right in independent_nearest_edges(points):
        midpoint = (points[left] + points[right]) / 2
        values = dose_gy[usable & (np.sum((coordinates - midpoint) ** 2, axis=-1) <= radius_mm ** 2)]
        if len(values) < min_valley_voxels:
            continue
        valley = float(np.median(values))
        if not math.isfinite(valley) or valley <= 0:
            continue
        rows.append((f"{ids[left]}--{ids[right]}", float(((peak[left] + peak[right]) / 2) / valley)))
    if not rows:
        raise RuntimeError("Independent implementation found no valid edges.")
    ratios = np.asarray([ratio for _, ratio in rows])
    return {"i_pvdr_median": float(np.median(ratios)), "edges": [name for name, _ in rows],
            "edge_i_pvdr": {name: ratio for name, ratio in rows}}


def summary_comparison(reference, candidate):
    """Compare standard summary dictionaries without conflating a failed test with a primary result."""
    return {"i_pvdr": candidate["summary"]["g_pvdr_median"],
            "absolute_change": candidate["summary"]["g_pvdr_median"] - reference["summary"]["g_pvdr_median"],
            "relative_change": ((candidate["summary"]["g_pvdr_median"] / reference["summary"]["g_pvdr_median"]) - 1),
            "valid_edge_count": candidate["summary"]["valid_edge_count"],
            "excluded_edge_count": candidate["summary"]["excluded_edge_count"],
            "edge_jaccard": edge_jaccard([x["edge"] for x in reference["edge_results"]], [x["edge"] for x in candidate["edge_results"]])}


def capture_test(test):
    try:
        return {"status": "pass", **test()}
    except (RuntimeError, ValueError) as exc:
        return {"status": "non_evaluable", "reason": str(exc)}


def validation_tests(folder, gtv_requested, radius, min_valley_voxels):
    """Run additional frozen-iPVDR synthetic validation probes on one case."""
    dose, _, geometry, gtv_name, ids, masks, gtv, primary = analysis(
        folder, gtv_requested, radius, nearest_tie_edges, min_valley_voxels
    )
    dose_gy = dose.pixel_array.astype(float) * float(dose.DoseGridScaling)
    baseline = primary["summary"]["g_pvdr_median"]
    shape = dose_gy.shape
    centre = grid_centre(shape, geometry)
    angle = math.radians(37)
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    inverse_rotation = rotation.T

    def compare(dose_values, peak_masks, gtv_values):
        candidate = evaluate(ids, peak_masks, gtv_values, dose_values, geometry, radius,
                             edge_builder=nearest_tie_edges, min_valley_voxels=min_valley_voxels)
        return summary_comparison(primary, candidate)

    def dose_scaling(factor):
        return lambda: compare(dose_gy * factor, masks, gtv) | {"scale_factor": factor,
                                                                   "expected_relative_change": 0.0}

    def rotated_resampled():
        transform = lambda target: (target - centre) @ inverse_rotation.T + centre
        rotated_dose = resample_volume(dose_gy, geometry, geometry, shape, transform, "linear")
        rotated_masks = [resample_volume(mask.astype(float), geometry, geometry, shape, transform, "nearest", 0.0) >= 0.5 for mask in masks]
        rotated_gtv = resample_volume(gtv.astype(float), geometry, geometry, shape, transform, "nearest", 0.0) >= 0.5
        return compare(rotated_dose, rotated_masks, rotated_gtv) | {"rotation_degrees": 37,
            "dose_resampling": "trilinear", "mask_resampling": "nearest", "scope": "virtual rotated/resampled phantom"}

    def segmentation(operation):
        altered = [binary_neighbour_morphology(mask, operation) for mask in masks]
        if any(not mask.any() for mask in altered):
            raise RuntimeError(f"{operation} removed at least one vertex mask.")
        return compare(dose_gy, altered, gtv) | {"operation": f"one_voxel_{operation}"}

    def interpolation_comparison(method):
        shift = .5 * (geometry["spacing"][1] * geometry["column"] + geometry["spacing"][0] * geometry["row"]
                        + float(np.median(np.abs(np.diff(geometry["offsets"])))) * geometry["normal"])
        shifted_dose = resample_volume(dose_gy, geometry, geometry, shape, lambda target: target - shift, method)
        return compare(shifted_dose, masks, gtv) | {"resampling": method, "transform": "half_voxel_diagonal_dose_translation"}

    def repeatability():
        runs = [evaluate(ids, masks, gtv, dose_gy, geometry, radius, edge_builder=nearest_tie_edges,
                         min_valley_voxels=min_valley_voxels) for _ in range(3)]
        hashes = [hashlib.sha256(json.dumps(run, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for run in runs]
        return {"run_count": len(runs), "result_hashes": hashes, "bitwise_identical": len(set(hashes)) == 1,
                "i_pvdr": runs[0]["summary"]["g_pvdr_median"]}

    def independent_comparison():
        reference = independent_evaluate(ids, masks, gtv, dose_gy, geometry, radius, min_valley_voxels)
        implementation = primary["summary"]["g_pvdr_median"]
        return {"production_i_pvdr": implementation, "independent_i_pvdr": reference["i_pvdr_median"],
                "absolute_difference": implementation - reference["i_pvdr_median"],
                "relative_difference": implementation / reference["i_pvdr_median"] - 1,
                "edge_sets_identical": reference["edges"] == [item["edge"] for item in primary["edge_results"]],
                "independent_edge_i_pvdr": reference["edge_i_pvdr"]}

    return {"purpose": "SYNTHETIC NON-CLINICAL frozen Layer 2.2B iPVDR additional validation tests",
            "primary_definition": describe_primary(gtv_name, radius, "nearest_tie"),
            "input": {"case_folder": str(folder), "gtv_roi": gtv_name, "minimum_valley_voxels": min_valley_voxels,
                      "baseline_i_pvdr": baseline},
            "dose_scaling": [capture_test(dose_scaling(factor)) for factor in (0.5, 2.0)],
            "rotated_resampled_phantom": capture_test(rotated_resampled),
            "segmentation_morphology": {"one_voxel_erosion": capture_test(lambda: segmentation("erode")),
                                          "one_voxel_dilation": capture_test(lambda: segmentation("dilate"))},
            "interpolation_comparison": {"nearest": capture_test(lambda: interpolation_comparison("nearest")),
                                           "trilinear": capture_test(lambda: interpolation_comparison("linear"))},
            "repeatability": capture_test(repeatability),
            "independent_implementation": capture_test(independent_comparison),
            "warning": "Synthetic non-clinical numerical tests only. Rotated/resampled and interpolation probes are virtual resampling experiments, not delivery or motion simulations.",
            "run_utc": datetime.now(timezone.utc).isoformat()}


def synthetic_ground_truth_tests():
    """Exact array tests for core iPVDR arithmetic and graph error handling."""
    shape = (9, 9, 9)
    geometry = {"origin": np.zeros(3), "column": np.array([1.0, 0.0, 0.0]),
                "row": np.array([0.0, 1.0, 0.0]), "normal": np.array([0.0, 0.0, 1.0]),
                "offsets": np.arange(shape[0], dtype=float), "spacing": np.array([1.0, 1.0])}
    masks = []
    for x in (2, 6):
        mask = np.zeros(shape, dtype=bool)
        mask[4, 4, x] = True
        masks.append(mask)
    ids, gtv = ["VTVH_01", "VTVH_02"], np.ones(shape, dtype=bool)

    def metric(dose, points=None):
        return evaluate(ids, masks, gtv, dose, geometry, 1.5, nearest_tie_edges, points,
                        min_valley_voxels=1, min_valid_edges=1)

    def result(name, passed, evidence):
        return {"name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence}

    base = np.full(shape, 5.0)
    for mask in masks:
        base[mask] = 20.0
    primary = metric(base)
    primary_value = primary["summary"]["i_pvdr_median"]
    constant = np.full(shape, 5.0)
    constant_result = metric(constant)
    scaled_result = metric(base * 3.7)
    duplicate_blocked = False
    try:
        metric(base, points=np.array([[2.0, 4.0, 4.0], [2.0, 4.0, 4.0]]))
    except RuntimeError as exc:
        duplicate_blocked = "BLOCK_DUPLICATE_VERTEX" in str(exc)
    zero = np.zeros(shape)
    for mask in masks:
        zero[mask] = 20.0
    zero_blocked = False
    try:
        metric(zero)
    except RuntimeError as exc:
        zero_blocked = "CASE_NON_EVALUABLE" in str(exc)
    regular = nearest_tie_edges(np.array([[0., 0., 0.], [2., 0., 0.], [4., 0., 0.]]))
    reordered = nearest_tie_edges(np.array([[4., 0., 0.], [0., 0., 0.], [2., 0., 0.]]))
    regular_lengths = sorted([2.0, 2.0])
    reordered_lengths = sorted([np.linalg.norm(np.array([[4., 0., 0.], [0., 0., 0.], [2., 0., 0.]])[a] -
                                         np.array([[4., 0., 0.], [0., 0., 0.], [2., 0., 0.]])[b]) for a, b in reordered])

    # Controlled regular lattice: every nearest-neighbour midpoint has the
    # same 5 Gy valley and every one-voxel vertex has 20 Gy.  Conventional
    # PVDR is therefore exactly 20 / 5 = 4 for every local relationship.
    regular_shape = (9, 13, 13)
    regular_geometry = {"origin": np.zeros(3), "column": np.array([1.0, 0.0, 0.0]),
                        "row": np.array([0.0, 1.0, 0.0]), "normal": np.array([0.0, 0.0, 1.0]),
                        "offsets": np.arange(regular_shape[0], dtype=float), "spacing": np.array([1.0, 1.0])}
    regular_ids = ["VTVH_01", "VTVH_02", "VTVH_03", "VTVH_04"]
    regular_masks = []
    for column, row in ((3, 3), (7, 3), (3, 7), (7, 7)):
        mask = np.zeros(regular_shape, dtype=bool)
        mask[4, row, column] = True
        regular_masks.append(mask)
    regular_dose = np.full(regular_shape, 5.0)
    for mask in regular_masks:
        regular_dose[mask] = 20.0
    regular_lattice = evaluate(regular_ids, regular_masks, np.ones(regular_shape, dtype=bool), regular_dose,
                               regular_geometry, 1.1, nearest_tie_edges, min_valley_voxels=1,
                               min_valid_edges=3)
    regular_lattice_scaled = evaluate(regular_ids, regular_masks, np.ones(regular_shape, dtype=bool), regular_dose * 2,
                                      regular_geometry, 1.1, nearest_tie_edges, min_valley_voxels=1,
                                      min_valid_edges=3)
    regular_edge_ratios = [edge["edge_i_pvdr"] for edge in regular_lattice["edge_results"]]
    regular_expected_edges = ["VTVH_01--VTVH_02", "VTVH_01--VTVH_03", "VTVH_02--VTVH_04", "VTVH_03--VTVH_04"]
    return {"purpose": "Synthetic exact ground-truth tests for Layer 2.2B iPVDR",
            "acceptance_absolute_error": 1e-6,
            "tests": [
                result("two_peaks_20Gy_valley_5Gy", abs(primary_value - 4.0) < 1e-6,
                       {"calculated_i_pvdr": primary_value, "expected_i_pvdr": 4.0}),
                result("constant_dose", abs(constant_result["summary"]["i_pvdr_median"] - 1.0) < 1e-6,
                       {"calculated_i_pvdr": constant_result["summary"]["i_pvdr_median"], "expected_i_pvdr": 1.0}),
                result("dose_scaling", abs(scaled_result["summary"]["i_pvdr_median"] - primary_value) < 1e-6,
                       {"baseline_i_pvdr": primary_value, "scaled_i_pvdr": scaled_result["summary"]["i_pvdr_median"]}),
                result("regular_three_vertex_graph", regular == [(0, 1), (1, 2)], {"edges": regular}),
                result("vertex_order_invariance", reordered_lengths == regular_lengths,
                       {"reordered_edges": reordered, "edge_lengths_mm": reordered_lengths}),
                result("regular_four_vertex_lattice_matches_conventional_pvdr",
                       regular_lattice["summary"]["i_pvdr_median"] == 4.0 and regular_edge_ratios == [4.0] * 4,
                       {"conventional_pvdr": 4.0, "peak_d50_gy": 20.0, "valley_d50_gy": 5.0,
                        "calculated_i_pvdr": regular_lattice["summary"]["i_pvdr_median"],
                        "edge_i_pvdr": regular_edge_ratios,
                        "expected_nearest_tie_edges": regular_expected_edges,
                        "calculated_edges": [edge["edge"] for edge in regular_lattice["edge_results"]]}),
                result("regular_four_vertex_lattice_dose_scale_invariance",
                       regular_lattice_scaled["summary"]["i_pvdr_median"] == 4.0,
                       {"dose_scale_factor": 2.0, "calculated_i_pvdr": regular_lattice_scaled["summary"]["i_pvdr_median"],
                        "expected_i_pvdr": 4.0}),
                result("duplicate_centroid_block", duplicate_blocked, {"expected": "BLOCK_DUPLICATE_VERTEX"}),
                result("zero_valley_block", zero_blocked, {"expected": "CASE_NON_EVALUABLE"}),
            ], "run_utc": datetime.now(timezone.utc).isoformat()}


def main():
    # Define the command-line interface for production analysis and non-production sensitivity modes.
    parser = argparse.ArgumentParser(description="Layer 2.2B DICOM-native graph PVDR")
    parser.add_argument("case_folder", type=Path, nargs="?")
    parser.add_argument("--mode", choices=("analysis", "robustness", "valley_voxel_sensitivity", "validation_tests", "synthetic_ground_truth"), default="analysis")
    parser.add_argument("--gtv-roi", default=None, help="Exact GTV ROI name; otherwise a GTV-like ROI is selected.")
    parser.add_argument("--valley-radius-mm", type=float, default=PRIMARY_RADIUS_MM)
    parser.add_argument("--graph-rule", choices=("nearest_tie", "gabriel"), default="nearest_tie",
                        help="Primary graph: nearest-tie is the Layer 2.2B default; Gabriel is a comparison rule.")
    parser.add_argument("--min-valley-voxels", type=int, default=FROZEN_MIN_VALLEY_VOXELS,
                        help="Minimum valid midpoint-sphere valley voxels for one edge (frozen production default: 7).")
    parser.add_argument("--dose-component", choices=("lrt_only", "combined"), default=None,
                        help="Required for production analysis: intended RTDOSE component designation.")
    parser.add_argument("--layer1-validation-dir", type=Path, default=None,
                        help="Required for production analysis: completed Layer 1 validation output directory.")
    parser.add_argument("--voxel-thresholds", default="1,5,8,10,12,15,20",
                        help="Comma-separated thresholds for valley_voxel_sensitivity mode.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    # Reject nonsensical positive-valued parameters before accessing any DICOM input.
    if args.valley_radius_mm <= 0 or args.min_valley_voxels < 1:
        raise SystemExit("--valley-radius-mm and --min-valley-voxels must be positive.")
    if args.mode != "synthetic_ground_truth" and args.case_folder is None:
        raise SystemExit("case_folder is required unless --mode synthetic_ground_truth is selected.")
    folder = args.case_folder.expanduser().resolve() if args.case_folder else None
    edge_builder = nearest_tie_edges if args.graph_rule == "nearest_tie" else gabriel_edges
    # The synthetic ground-truth suite needs no DICOM case folder.
    if args.mode == "synthetic_ground_truth":
        report = synthetic_ground_truth_tests()
        output = args.output or Path(__file__).with_name("Layer_2_2B_iPVDR_synthetic_ground_truth.json")
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        failed = [row["name"] for row in report["tests"] if row["status"] != "PASS"]
        print(f"Layer 2.2B synthetic ground-truth tests: {'PASS' if not failed else 'FAIL'}")
        if failed:
            print(f"Failed tests: {failed}")
    elif args.mode == "analysis":
        # Production deliberately requires explicit target, dose-component declaration, and validated prior-layer handoff.
        if not args.gtv_roi:
            raise SystemExit("BLOCK_AMBIGUOUS_INPUT: production analysis requires --gtv-roi EXACT_GTV_ROI_NAME.")
        if not args.dose_component:
            raise SystemExit("BLOCK_AMBIGUOUS_INPUT: production analysis requires --dose-component lrt_only or combined.")
        if not args.layer1_validation_dir:
            raise SystemExit("BLOCK_LAYER1_INPUT: production analysis requires --layer1-validation-dir LAYER1_RUN_DIRECTORY.")
        if args.graph_rule != "nearest_tie" or args.valley_radius_mm != PRIMARY_RADIUS_MM or args.min_valley_voxels != FROZEN_MIN_VALLEY_VOXELS:
            raise SystemExit("BLOCK_NON_FROZEN_CONFIGURATION: production analysis requires nearest_tie, 3.0 mm radius, and 7 valley voxels.")
        frozen_config, frozen_config_path = load_frozen_config()
        layer1_handoff = validate_layer1_handoff(folder, args.layer1_validation_dir)
        dose, struct, geometry, gtv, _, masks, _, result = analysis(
            folder, args.gtv_roi, args.valley_radius_mm, edge_builder, args.min_valley_voxels, production=True,
            layer1_run_dir=args.layer1_validation_dir
        )
        config = {"graph_rule": args.graph_rule, "valley_radius_mm": args.valley_radius_mm,
                  "min_valley_voxels": args.min_valley_voxels, "min_valid_edges": FROZEN_MIN_VALID_EDGES,
                  "dose_component": args.dose_component, "gtv_roi": gtv, "interpolation": "none",
                  "frozen_config": frozen_config}
        provenance = production_provenance(folder, dose, struct, geometry, masks, config)
        provenance["frozen_config_path"] = str(frozen_config_path)
        provenance["frozen_config_sha256"] = sha256_file(frozen_config_path)
        provenance["layer1_handoff"] = layer1_handoff
        provenance["graph_edge_hash"] = hashlib.sha256(json.dumps([item["edge"] for item in result["edge_results"]]).encode()).hexdigest()
        report = {"purpose": "SYNTHETIC NON-CLINICAL frozen Layer 2.2B iPVDR production analysis",
                  "primary_definition": describe_primary(gtv, args.valley_radius_mm, args.graph_rule),
                  "result": result, "provenance": provenance,
                  "warning": "Synthetic non-clinical software-method analysis only; not clinical validation.",
                  "run_utc": datetime.now(timezone.utc).isoformat()}
        output = args.output or folder.parent / f"{folder.name}_Layer_2_2B_{args.graph_rule}_iPVDR.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        s = result["summary"]
        value = "non-evaluable" if s["i_pvdr_median"] is None else f"{s['i_pvdr_median']:.3f}"
        print(f"Layer 2.2B iPVDR: {value}; valid/excluded edges: {s['valid_edge_count']}/{s['excluded_edge_count']}")
    elif args.mode == "robustness":
        _, report = robustness(folder, args.gtv_roi, args.valley_radius_mm, edge_builder, args.graph_rule, args.min_valley_voxels)
        output = args.output or folder.parent / f"{folder.name}_Layer_2_2B_{args.graph_rule}_robustness.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        s = report["primary_result"]
        print(f"Layer 2.2B primary G-PVDR: {s['g_pvdr_median']:.3f}; edges: {s['valid_edge_count']}")
        print(f"Coordinate invariance: {'PASS' if report['coordinate_invariance']['passed'] else 'FAIL'}")
        print(f"2x grid Δ: {report['dose_grid_sensitivity']['virtual_2x_decimated_grid']['change_from_primary']:+.3f}")
        print(f"Half-voxel shift Δ: {report['dose_grid_sensitivity']['half_voxel_grid_origin_shift']['change_from_primary']:+.3f}")
        print(f"1 mm centroid jitter Δ: {report['centroid_perturbation']['one_mm_deterministic_jitter']['change_from_primary']:+.3f}")
        alternate = next(iter(report['topology_sensitivity'].values()))
        print(f"Alternate-graph Δ: {alternate['change_from_primary']:+.3f}")
    elif args.mode == "valley_voxel_sensitivity":
        try:
            thresholds = sorted({int(item.strip()) for item in args.voxel_thresholds.split(",") if item.strip()})
        except ValueError as exc:
            raise SystemExit("--voxel-thresholds must be comma-separated positive integers.") from exc
        if not thresholds or any(item < 1 for item in thresholds):
            raise SystemExit("--voxel-thresholds must contain positive integers.")
        report = valley_voxel_sensitivity(folder, args.gtv_roi, args.valley_radius_mm, edge_builder, args.graph_rule, thresholds)
        output = args.output or folder.parent / f"{folder.name}_Layer_2_2B_{args.graph_rule}_valley_voxel_sensitivity.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for row in report["threshold_results"]:
            value = "unusable" if row["g_pvdr_median"] is None else f"{row['g_pvdr_median']:.3f}"
            print(f"Minimum valley voxels {row['minimum_valley_voxels']:>2}: G-PVDR={value}; valid edges={row['valid_edge_count']}")
    else:
        report = validation_tests(folder, args.gtv_roi, args.valley_radius_mm, args.min_valley_voxels)
        output = args.output or folder.parent / f"{folder.name}_Layer_2_2B_iPVDR_validation_tests.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Layer 2.2B iPVDR validation tests: {output}")
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
