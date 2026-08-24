#!/usr/bin/env python3
"""LRT Project (LATTE) — Layer 1 physical-dose validation tool. 

Desktop GUI for importing RTDOSE, RTSTRUCT, RTPLAN and (optionally) CT/TPS
metrics.  It creates a provenance-recorded physical-dose manifest, compact DVH
audit, and explicit Layer 2/3 eligibility decision.  Layer 1 deliberately does
not calculate LRT graph metrics, BED/EQD2, LQ survival, or clinical advice. 

LATTE = Lattice Assessment Toolkit for Treatment Evaluation 

Install once (Terminal)/Requirments for running:  python3 -m pip install pydicom numpy
Run:                   python3 ~/Desktop/LRT_Layer_1.py

For research/technical validation only.  Review all WARN/BLOCK findings and
visually validate dose/contour alignment in the TPS before clinical use.
"""


from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import platform
import re
import statistics
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pydicom
except ImportError as exc:
    print("Missing dependency. Install with: python3 -m pip install pydicom numpy")
    raise SystemExit(1) from exc


VERSION = "0.3.3-layer1-volume-gated"
RASTER_STANDARD = "BARAT-L1-RASTER-CTNN-GAPSAFE-v3"
# Permanent cross-grid representation controls.  These do not claim that a
# dose-grid mask is an anatomical truth: anatomical volume remains the
# contour-stack estimate.  They detect when rasterisation or dose sampling is
# too coarse to support downstream mask-based analysis.
VOLUME_REPRESENTATION_ABS_TOL_CC = 0.10
VOLUME_REPRESENTATION_REL_TOL = 0.02
SAMPLING_COVER_WARN_PCT = 5.0
REQUIRED_NATIVE_DOSE_COVER_MIN_PCT = 95.0
# Edit these aliases to match the local retrospective naming convention. Mapping
# is exact after normalisation; ambiguous mappings are never silently selected.
# VTVH/VTVL are optional and are not inferred from PTV names or dose levels.


STRUCTURE_ALIASES = {
    "GTV": ["CTV", "GTV_PRIMARY", "GTVP", "LRT_GTV"],
    # Optional Layer 2.1 peripheral treatment target.  It is never inferred
    # from the GTV because V95%Rx(PTVlow) requires the protocol-defined target.
    "PTVLOW": ["PTV_LOW", "PTVLOW", "PTV2000", "PTV_2000", "PTV1", "PTV_LOWER_DOSE", "PTV"],
    "VTVH": ["VTVH", "VTV_H", "all_vertices"],
    "VTVL": ["VTVL", "VTV_L", "all_valleys"],
}
REQUIRED_STRUCTURES = {"GTV"}

@dataclass
class Finding:
    level: str  
    check: str
    detail: str
# results of one individual check  level="PASS,WARN.BLOCK",check=name of check perfromed,detail = explaination of what was found

@dataclass # CaseResult stores all results associated with one case
class CaseResult:
    manifest: dict[str, Any] = field(default_factory=dict) #stores infromation about the case 
    findings: list[Finding] = field(default_factory=list) #stores all PASS,WARN and BLOCK findings 
    mappings: list[dict[str, str]] = field(default_factory=list)#stores relationship between strucutre names or imported data and the frameworks expected names
    dvh_audit: list[dict[str, Any]] = field(default_factory=list)#stores DVH measurments or audit comparisions
    dvh_summary: list[dict[str, Any]] = field(default_factory=list)#compact per-structure DVH results for spreadsheet review
    eligibility: dict[str, Any] = field(default_factory=dict)#stores whether the case is suitable for further analysis, use field(default_factory=list) to make a new empty list for every CaseResult object 
    mask_arrays: dict[str, Any] = field(default_factory=dict, repr=False)  # transient native-dose-grid masks exported for downstream layers
    dose_array_gy: Any = field(default=None, repr=False)  # validated native RTDOSE physical dose for Layer 2 handoff

    def add(self, level: str, check: str, detail: str) -> None: #This is a convenience method. It creates a new Finding and adds it to the case’s findings list.
        self.findings.append(Finding(level, check, detail)) # Append findings to a list

    @property #This code calculates the overall status of the case from all entries in self.findings.
    def status(self) -> str:
        return "BLOCK" if any(x.level == "BLOCK" for x in self.findings) else ("WARN" if any(x.level == "WARN" for x in self.findings) else "PASS")


def uid(ds: Any, name: str) -> str | None: # retrieves an attribute from an object and returns it
    value = getattr(ds, name, None) 
    return str(value) if value else None


def frame_of_reference_uid(ds: Any) -> str | None: #retrieves the Frame of Reference UID from a DICOM dataset.
    """Return the Frame of Reference UID, including its standard RTSTRUCT location."""
    direct = uid(ds, "FrameOfReferenceUID")
    if direct:
        return direct
    sequences = getattr(ds, "ReferencedFrameOfReferenceSequence", [])
    return uid(sequences[0], "FrameOfReferenceUID") if sequences else None


def norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", name.upper())


def sha256(path: Path) -> str: #SHA-256 hash is a fixed-length identifier based on the exact contents of a file. It can be used to verify that a DICOM file has not changed.
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def references(ds: Any, target_uid: str) -> bool:
    """Recursively look for a UID in a DICOM dataset's reference sequences."""
    if not target_uid:
        return False
    for element in ds.iterall():
        try:
            values = element.value if getattr(element, "VM", 1) > 1 else [element.value]
            if any(str(value) == target_uid for value in values):
                return True
        except Exception:
            pass
    return False 


def classify_component(text: str) -> str: #classifies a text label into a radiotherapy treatment component category.
    t = text.upper().replace(" ", "_")
    is_lrt = "LRT" in t or "LATTICE" in t
    if is_lrt and ("CERT" in t or "CONVENT" in t): return "LRT_PLUS_CERT"
    if is_lrt and "COMPONENT" in t: return "LRT_COMPONENT"
    if "CERT" in t and "COMPONENT" in t: return "CERT_COMPONENT"
    if is_lrt: return "LRT_ONLY"
    if "CERT" in t or "CONVENT" in t: return "CERT_ONLY"
    if "COMPOSITE" in t: return "COMPOSITE_PHYSICAL_DOSE"
    return "UNKNOWN"


def dose_geometry(dose: Any) -> dict[str, Any]: #extracts the spatial geometry of a DICOM RTDOSE grid and returns it in a standard dictionary.
    orient = np.asarray(getattr(dose, "ImageOrientationPatient", [1, 0, 0, 0, 1, 0]), dtype=float)
    row_dir, col_dir = orient[:3], orient[3:]
    normal = np.cross(row_dir, col_dir)
    offsets = np.asarray(getattr(dose, "GridFrameOffsetVector", [0.0]), dtype=float)
    frames = int(getattr(dose, "NumberOfFrames", 1))
    if len(offsets) != frames: offsets = np.arange(frames, dtype=float)
    spacing = np.asarray(dose.PixelSpacing, dtype=float)  # row, column mm
    return {"origin": np.asarray(dose.ImagePositionPatient, dtype=float), "row_dir": row_dir,
            "col_dir": col_dir, "normal": normal, "offsets": offsets, "spacing": spacing,
            "shape": (frames, int(dose.Rows), int(dose.Columns))}


def patient_to_grid(points: np.ndarray, geo: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]: #This function converts physical patient coordinates into RTDOSE grid indices.
    rel = points - geo["origin"]
    # DICOM first orientation vector corresponds to image columns; second to rows.
    cols = rel @ geo["row_dir"] / geo["spacing"][1]
    rows = rel @ geo["col_dir"] / geo["spacing"][0]
    positions = rel @ geo["normal"]
    zs = np.abs(positions[:, None] - geo["offsets"][None, :]).argmin(axis=1)
    return zs, rows, cols


def polygon_fill(rows: np.ndarray, cols: np.ndarray, height: int, width: int) -> np.ndarray:  #converts a polygon defined by row and column coordinates into a 2D Boolean mask
    """Half-open even-odd rasteriser evaluated at CT voxel centres."""
    mask = np.zeros((height, width), dtype=bool)
    if len(rows) < 3: return mask
    r0, r1 = max(0, math.floor(rows.min())), min(height - 1, math.ceil(rows.max()))
    c0, c1 = max(0, math.floor(cols.min())), min(width - 1, math.ceil(cols.max()))
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    inside = np.zeros(rr.shape, dtype=bool)
    j = len(rows) - 1
    for i in range(len(rows)):
        yi, xi, yj, xj = rows[i], cols[i], rows[j], cols[j]
        crosses = ((yi > rr) != (yj > rr)) & (cc < (xj - xi) * (rr - yi) / (yj - yi + 1e-15) + xi)
        inside ^= crosses; j = i
    mask[r0:r1 + 1, c0:c1 + 1] = inside
    return mask


def map_rois(struct: Any, result: CaseResult, manually_confirmed_gtv: str = "") -> dict[int, str]: #reads ROI names from an RTSTRUCT, converts recognised names into standard structure names, 
    #records the mapping, and blocks the case when required structures are missing.
    aliases = {norm(v): key for key, values in STRUCTURE_ALIASES.items() for v in [key, *values]} #lokup table
    confirmed = norm(manually_confirmed_gtv) if manually_confirmed_gtv.strip() else "" #normalise the manually confrimed GTV name 
    rois = {int(x.ROINumber): str(x.ROIName) for x in getattr(struct, "StructureSetROISequence", [])} #extract ROI number and names
    if confirmed and confirmed not in {norm(name) for name in rois.values()}: #check if manually selected GTV exists 
        result.add("BLOCK", "Manual structure mapping", "The manually confirmed GTV name was not found in the selected RTSTRUCT.")
    canonical_to_numbers: dict[str, list[int]] = {} # empty list of mapping containers 
    mapping: dict[int, str] = {} # empty list for maping 
    for number, raw in rois.items(): #process every ROI
        raw_normalised = norm(raw) #normalise
        canonical = "GTV" if confirmed and raw_normalised == confirmed else aliases.get(raw_normalised) #determine the standard structure name
        # Export every closed-planar contour on the validated native dose grid.
        # Unmapped structures retain an immutable ROI-numbered key so supporting
        # modules can consume explicitly selected OARs without re-rasterisation.
        if not canonical:
            canonical = f"ROI_{number}_{re.sub(r'[^A-Za-z0-9_]+', '_', raw).strip('_') or 'UNNAMED'}"
        status = "MANUALLY_CONFIRMED" if confirmed and raw_normalised == confirmed else ("EXACT" if raw_normalised == norm(canonical) else ("CONFIGURED_ALIAS" if raw_normalised in aliases else "UNMAPPED_EXPORTED")) #mapping status
        result.mappings.append({"roi_number": str(number), "original_name": raw, "standard_name": canonical or "", "mapping_status": status}) #output result
        canonical_to_numbers.setdefault(canonical, []).append(number); mapping[number] = canonical
    for canonical, nums in canonical_to_numbers.items():
        if len(nums) > 1: #If several RTSTRUCT ROIs map to the same standard structure, the case receives a warning.
            result.add("WARN", "Structure mapping", f"Multiple ROIs map to {canonical}: {nums}; masks are combined.")
    for required in REQUIRED_STRUCTURES: #prevents further analysis because the required structure cannot be identified.
        if required not in canonical_to_numbers: #Unrecognised structures are not included.
            result.add("BLOCK", "Required structure", f"{required} is missing; configure STRUCTURE_ALIASES if it has a different name.")
    return mapping 


def _median_positive_spacing(values: np.ndarray) -> float:
    differences = np.diff(np.asarray(values, dtype=float))
    positive = differences[differences > 1.0e-6]
    return float(np.median(positive)) if positive.size else 1.0


def _nearest_sorted_indices(sorted_values: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Return deterministic nearest indices without constructing a large 4-D array."""
    right = np.searchsorted(sorted_values, values, side="left")
    right = np.clip(right, 0, len(sorted_values) - 1)
    left = np.clip(right - 1, 0, len(sorted_values) - 1)
    choose_left = np.abs(values - sorted_values[left]) <= np.abs(sorted_values[right] - values)
    return np.where(choose_left, left, right)


def _polygon_area_mm2(points: np.ndarray, column_axis: np.ndarray, row_axis: np.ndarray) -> float:
    coordinates = np.column_stack((points @ column_axis, points @ row_axis))
    x, y = coordinates[:, 0], coordinates[:, 1]
    return abs(float(0.5 * np.sum(x * np.roll(y, 1) - np.roll(x, 1) * y)))


def _contour_stack_volume_cc(positions: list[float], areas: dict[float, float], default_spacing: float) -> float:
    ordered = sorted(positions)
    if not ordered: return 0.0
    if len(ordered) == 1: return float(areas[ordered[0]] * default_spacing / 1000.0)
    positive_spacings = [right - left for left, right in zip(ordered, ordered[1:]) if right > left]
    nominal_spacing = statistics.median(positive_spacings) if positive_spacings else default_spacing
    # Do not integrate across large gaps between disconnected contour slabs.
    # Such a gap represents absent contour information, not continuous anatomy.
    groups: list[list[float]] = []
    current_group = [ordered[0]]
    for left, right in zip(ordered, ordered[1:]):
        if right - left > 1.5 * nominal_spacing:
            groups.append(current_group)
            current_group = [right]
        else:
            current_group.append(right)
    groups.append(current_group)
    volume = 0.0
    for group in groups:
        if len(group) == 1:
            volume += areas[group[0]] * default_spacing
            continue
        volume += sum(0.5 * (areas[left] + areas[right]) * (right - left) for left, right in zip(group, group[1:]))
        volume += 0.5 * areas[group[0]] * (group[1] - group[0])
        volume += 0.5 * areas[group[-1]] * (group[-1] - group[-2])
    return float(volume / 1000.0)


def masks_from_struct(struct: Any, geo: dict[str, Any], ct_paths: list[Path], result: CaseResult, manually_confirmed_gtv: str = "") -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Rasterise contours on referenced CT, then nearest-neighbour transfer to RTDOSE."""
    mapping = map_rois(struct, result, manually_confirmed_gtv)
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
    ordered = sorted((float(np.dot(np.asarray(ds.ImagePositionPatient, dtype=float), normal)), np.asarray(ds.ImagePositionPatient, dtype=float)) for ds in ct_datasets)
    ct_positions = np.asarray([position for position, _ in ordered], dtype=float)
    ct_origins = np.stack([origin for _, origin in ordered])
    ct_spacing = _median_positive_spacing(ct_positions)

    planes_by_structure: dict[str, dict[float, list[np.ndarray]]] = {}
    for roi in getattr(struct, "ROIContourSequence", []):
        canonical = mapping.get(int(getattr(roi, "ReferencedROINumber", -1)))
        if not canonical: continue
        planes = planes_by_structure.setdefault(canonical, {})
        for contour in getattr(roi, "ContourSequence", []):
            contour_type = str(getattr(contour, "ContourGeometricType", "CLOSED_PLANAR")).upper()
            data = np.asarray(getattr(contour, "ContourData", []), dtype=float)
            if contour_type == "POINT" or data.size < 9 or data.size % 3: continue
            if contour_type not in {"CLOSED_PLANAR", "CLOSEDPLANAR_XOR"}: continue
            points = data.reshape(-1, 3)
            position = round(float(np.mean(points @ normal)), 4)
            planes.setdefault(position, []).append(points)

    ct_masks: dict[str, np.ndarray] = {}
    contour_volumes: dict[str, float] = {}
    roi_details: list[dict[str, Any]] = []
    for canonical, planes in planes_by_structure.items():
        source_positions = sorted(planes)
        if not source_positions: continue
        source_spacing = statistics.median([right - left for left, right in zip(source_positions, source_positions[1:]) if right > left]) if len(source_positions) > 1 else ct_spacing
        # A large gap between contour planes denotes separate represented
        # contour slabs, not an instruction to propagate the nearest contour
        # through the empty gap.  Bridging such gaps can join disconnected
        # islands and add voxels far outside the intended structure.
        plane_groups: list[list[float]] = []
        current_group = [source_positions[0]]
        for left, right in zip(source_positions, source_positions[1:]):
            if right - left > 1.5 * source_spacing:
                plane_groups.append(current_group)
                current_group = [right]
            else:
                current_group.append(right)
        plane_groups.append(current_group)
        ct_mask = np.zeros((len(ordered), rows, columns), dtype=bool)
        for ct_index, ct_position in enumerate(ct_positions):
            active_group = next((group for group in plane_groups if group[0] - source_spacing / 2.0 <= ct_position <= group[-1] + source_spacing / 2.0), None)
            if active_group is None: continue
            source = min(active_group, key=lambda value: abs(value - ct_position))
            plane_mask = np.zeros((rows, columns), dtype=bool)
            for points in planes[source]:
                relative = points - ct_origins[ct_index]
                polygon_rows = relative @ row_axis / row_spacing
                polygon_columns = relative @ column_axis / column_spacing
                plane_mask ^= polygon_fill(polygon_rows, polygon_columns, rows, columns)
            ct_mask[ct_index] = plane_mask
        ct_masks[canonical] = ct_mask
        areas = {position: sum(_polygon_area_mm2(points, column_axis, row_axis) for points in polygons) for position, polygons in planes.items()}
        contour_volumes[canonical] = _contour_stack_volume_cc(source_positions, areas, ct_spacing)

    masks = {name: np.zeros(geo["shape"], dtype=bool) for name in ct_masks}
    grid_rows, grid_columns = np.mgrid[0:geo["shape"][1], 0:geo["shape"][2]]
    for frame in range(geo["shape"][0]):
        points = (geo["origin"] + geo["offsets"][frame] * geo["normal"] + grid_columns[..., None] * geo["spacing"][1] * geo["row_dir"] + grid_rows[..., None] * geo["spacing"][0] * geo["col_dir"])
        projected = points @ normal
        slice_indices = _nearest_sorted_indices(ct_positions, projected)
        relative = points - ct_origins[slice_indices]
        ct_rows = np.floor(relative @ row_axis / row_spacing + 0.5).astype(int)
        ct_columns = np.floor(relative @ column_axis / column_spacing + 0.5).astype(int)
        valid = (ct_rows >= 0) & (ct_rows < rows) & (ct_columns >= 0) & (ct_columns < columns)
        for name, ct_mask in ct_masks.items():
            sampled = np.zeros(valid.shape, dtype=bool)
            sampled[valid] = ct_mask[slice_indices[valid], ct_rows[valid], ct_columns[valid]]
            masks[name][frame] = sampled

    dose_voxel_cc = voxel_volume_cc(geo)
    volume_definitions: dict[str, dict[str, float]] = {}
    for name, mask in masks.items():
        ct_volume = float(ct_masks[name].sum() * ct_spacing * row_spacing * column_spacing / 1000.0)
        volume_definitions[name] = {
            "anatomical_volume_contour_cc": contour_volumes[name],
            "anatomical_volume_ct_cc": ct_volume,
            "dose_sampled_volume_cc": float(mask.sum() * dose_voxel_cc),
        }
        roi_details.append({"standard_name": name, "source_planes": len(planes_by_structure[name]), "ct_planes_occupied": int(np.any(ct_masks[name], axis=(1, 2)).sum()), "dose_planes_occupied": int(np.any(mask, axis=(1, 2)).sum()), **volume_definitions[name]})
        if not mask.any(): result.add("BLOCK", "Structure geometry", f"{name} mask is empty after CT-to-dose transfer.")
    for required in REQUIRED_STRUCTURES:
        if required not in masks: result.add("BLOCK", "Structure geometry", f"{required} has no usable closed-planar contours.")
    summary = {"standard_id": RASTER_STANDARD, "method": "CT voxel-centre half-open XOR rasterisation; gap-aware nearest-source-plane CT propagation; nearest-neighbour CT-to-RTDOSE transfer", "ct_grid": {"dimensions": [len(ordered), rows, columns], "voxel_spacing_mm": [ct_spacing, row_spacing, column_spacing]}, "volume_definitions": volume_definitions, "roi_details": roi_details}
    return masks, summary


def voxel_volume_cc(geo: dict[str, Any]) -> float: #voxel volume
    off = geo["offsets"]
    dz = float(np.median(np.abs(np.diff(off)))) if len(off) > 1 else 1.0
    return dz * float(np.prod(geo["spacing"])) / 1000.0


def dp(values: np.ndarray, percent: float) -> float: #percentile-based dose metrics (D_95,D_2) 
    """D_p: highest dose received by at least p% volume (nearest-rank convention)."""
    return float(np.percentile(values, 100.0 - percent, method="higher"))


def independent_metrics(dose_gy: np.ndarray, mask: np.ndarray, voxel_cc: float) -> dict[str, float]: #strucutre level DVH values including volume and mean dose 
    values = dose_gy[mask]
    if not len(values): raise ValueError("Cannot calculate DVH for empty mask")
    return {
        "Volume_cc": float(mask.sum() * voxel_cc),
        "D95_Gy": dp(values, 95),
        "D2_Gy": dp(values, 2),
        "Dmin_Gy": float(values.min()),
        "Dmax_Gy": float(values.max()),
        "Dmean_Gy": float(values.mean()),
    }


def volume_representation_audit(volume_definitions: dict[str, dict[str, float]], result: CaseResult) -> list[dict[str, Any]]:
    """Audit contour, CT, and native-dose representations without conflating them.

    Contour-stack volume is the anatomical estimate. CT and RTDOSE volumes are
    discretised representations used for rasterisation QA and dose sampling.
    A required structure with inadequate native-dose coverage is blocked from
    downstream mask-based analysis.
    """
    audit: list[dict[str, Any]] = []
    for structure, volumes in sorted(volume_definitions.items()):
        contour_cc = float(volumes.get("anatomical_volume_contour_cc", 0.0))
        ct_cc = float(volumes.get("anatomical_volume_ct_cc", 0.0))
        dose_cc = float(volumes.get("dose_sampled_volume_cc", 0.0))
        if contour_cc <= 0:
            result.add("BLOCK", "Volume representation", f"{structure}: contour-stack volume is zero or invalid.")
            continue
        tolerance_cc = max(VOLUME_REPRESENTATION_ABS_TOL_CC, VOLUME_REPRESENTATION_REL_TOL * contour_cc)
        ct_difference_cc = ct_cc - contour_cc
        dose_difference_cc = dose_cc - contour_cc
        sampling_cover_pct = 100.0 * dose_cc / contour_cc
        ct_status = "PASS" if abs(ct_difference_cc) <= tolerance_cc else "WARN"
        dose_status = "PASS" if abs(dose_difference_cc) <= tolerance_cc else "WARN"
        sampling_status = "PASS" if abs(sampling_cover_pct - 100.0) <= SAMPLING_COVER_WARN_PCT else "WARN"
        is_required = structure in REQUIRED_STRUCTURES
        native_gate = "PASS"
        if is_required and sampling_cover_pct < REQUIRED_NATIVE_DOSE_COVER_MIN_PCT:
            native_gate = "BLOCK"
            result.add("BLOCK", "Native RTDOSE sampling gate", f"{structure}: native-dose sampling cover is {sampling_cover_pct:.2f}%, below required {REQUIRED_NATIVE_DOSE_COVER_MIN_PCT:.1f}%. Do not pass this mask to downstream layers.")
        elif is_required:
            result.add("PASS", "Native RTDOSE sampling gate", f"{structure}: native-dose sampling cover is {sampling_cover_pct:.2f}% (minimum {REQUIRED_NATIVE_DOSE_COVER_MIN_PCT:.1f}%).")
        if ct_status == "WARN":
            result.add("WARN", "CT rasterisation volume", f"{structure}: CT-grid volume differs from contour-stack volume by {ct_difference_cc:+.3f} cc; tolerance is ±{tolerance_cc:.3f} cc.")
        if dose_status == "WARN" or sampling_status == "WARN":
            result.add("WARN", "RTDOSE sampling volume", f"{structure}: native RTDOSE mask represents {sampling_cover_pct:.2f}% of contour-stack volume ({dose_difference_cc:+.3f} cc). This is sampling QA, not an anatomical-volume replacement.")
        audit.append({
            "Structure": structure,
            "AnatomicalVolumeContour_cc": round(contour_cc, 5),
            "AnatomicalVolumeCT_cc": round(ct_cc, 5),
            "DoseSampledVolume_cc": round(dose_cc, 5),
            "CTminusContour_cc": round(ct_difference_cc, 5),
            "DoseminusContour_cc": round(dose_difference_cc, 5),
            "SamplingCover_pct": round(sampling_cover_pct, 3),
            "RepresentationTolerance_cc": round(tolerance_cc, 5),
            "CTRepresentationStatus": ct_status,
            "DoseRepresentationStatus": dose_status,
            "NativeDoseGate": native_gate,
            "AnatomicalVolumeDefinition": "RTSTRUCT contour-stack reconstruction",
        })
    return audit


def read_tps_metrics(path: Path | None) -> dict[tuple[str, str], tuple[float, str]]: #ads TPS-exported metrics from a CSV file and returns them in a lookup dictionary.
    if not path: return {}
    metrics = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try: metrics[(norm(row["structure_name"]), norm(row["metric_name"]))] = (float(row["value"]), row.get("unit", ""))
            except (KeyError, ValueError): continue
    return metrics


def fractionation(plan: Any, manual_dose: str, manual_n: str, result: CaseResult) -> dict[str, Any]:  #determines the prescription dose and fractionation from manual GUI inputs and/or the RTPLAN, checks for disagreements, calculates dose per fraction, and records whether the fractionation is usable.
    dose = float(manual_dose) if manual_dose.strip() else None; n = int(manual_n) if manual_n.strip() else None #converts values into total dose and number of fractions 
    source = "manual GUI" if dose is not None or n is not None else "RTPLAN"
    if plan: #If an RTPLAN is available, the function tries to extract its fractionation information.
        try:
            fg = plan.FractionGroupSequence[0]
            plan_n = int(getattr(fg, "NumberOfFractionsPlanned", 0)) or None
            # RTPLAN target doses may be in DoseReferenceSequence.
            plan_dose = next((float(x.TargetPrescriptionDose) for x in getattr(plan, "DoseReferenceSequence", []) if hasattr(x, "TargetPrescriptionDose")), None)
            if n is None: n = plan_n
            elif plan_n and n != plan_n: result.add("WARN", "Fractionation", f"GUI fractions ({n}) differs from RTPLAN ({plan_n}).")
            if dose is None: dose = plan_dose
            elif plan_dose and not math.isclose(dose, plan_dose, rel_tol=.01, abs_tol=.1): result.add("WARN", "Prescription", f"GUI dose ({dose:g} Gy) differs from RTPLAN ({plan_dose:g} Gy).")
        except (AttributeError, IndexError, TypeError, ValueError): pass 
    dpf = dose / n if dose is not None and n else None
    status = "VERIFIED" if dose is not None and n and dpf else "UNRESOLVED"
    if status == "UNRESOLVED": result.add("WARN", "Fractionation", "Prescription/fractionation unresolved: Layer 2 may proceed; biological Layer 3 is blocked.")
    return {"component_id": "primary", "component_type": "UNKNOWN", "prescription_dose_gy": dose, "number_of_fractions": n, "dose_per_fraction_gy": dpf, "fractionation_source": source, "fractionation_verification_status": status}


def discover_dicom_case(folder: Path) -> tuple[dict[str, list[Path]], list[Path]]:  #scans a folder for DICOM files, groups radiotherapy files by modality, collects CT/MR/PET images, and rejects folders that do not contain both an RTDOSE and RTSTRUCT.
    """Discover DICOM candidates without guessing among multiple Eclipse exports."""
    found: dict[str, list[Path]] = {"rtdose": [], "rtstruct": [], "rtplan": []}
    images: list[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            modality = str(getattr(ds, "Modality", "")).upper()
        except Exception:
            continue
        key = {"RTDOSE": "rtdose", "RTSTRUCT": "rtstruct", "RTPLAN": "rtplan"}.get(modality)
        if key: found[key].append(path)
        elif modality in {"CT", "MR", "PT"}: images.append(path)
    if not found["rtdose"] or not found["rtstruct"]:
        raise ValueError("No RTDOSE and RTSTRUCT pair was found. Select the individual files instead.")
    return found, images


def group_image_series(image_paths: list[Path]) -> dict[str, list[Path]]: #This function groups CT, MR, or PET DICOM image files into separate image series using their SeriesInstanceUID.
    """Group CT/MR/PT images by Series Instance UID, ignoring unreadable files."""
    series: dict[str, list[Path]] = {}
    for path in image_paths:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            series.setdefault(uid(ds, "SeriesInstanceUID") or f"MISSING_UID:{path.parent}", []).append(path)
        except Exception:
            continue
    return series


def inspect_image_series(image_paths: list[Path], struct: Any, dose: Any, result: CaseResult) -> dict[str, Any]: 
    """Decode every supplied planning image and verify a single valid series."""
    if not image_paths:
        result.add("WARN", "Planning-image series", "No planning CT/image series supplied; full image-series verification was skipped.")
        return {"provided": False, "number_of_images": 0}
    #Create containers for extracted information
    datasets = []
    unreadable = 0
    pixel_shapes: list[tuple[int, ...]] = []
    orientations: set[tuple[float, ...]] = set()
    spacings: set[tuple[float, ...]] = set()
    slice_locations: list[tuple[float, float, float]] = []
    for path in image_paths: #Fully read every supplied image
        try:
            ds = pydicom.dcmread(path)
            pixels = ds.pixel_array  # force full pixel-data decoding for every image in the series
            datasets.append((path, ds)); pixel_shapes.append(tuple(pixels.shape)) #Record each image’s geometry
            if hasattr(ds, "ImageOrientationPatient"): orientations.add(tuple(round(float(x), 6) for x in ds.ImageOrientationPatient))
            if hasattr(ds, "PixelSpacing"): spacings.add(tuple(round(float(x), 6) for x in ds.PixelSpacing))
            if hasattr(ds, "ImagePositionPatient"): slice_locations.append(tuple(round(float(x), 4) for x in ds.ImagePositionPatient))
        except Exception: unreadable += 1 #Count unreadable files
    if not datasets: #Block if no images were readable
        result.add("BLOCK", "Planning-image series", "No supplied planning images could be read as DICOM.")
        return {"provided": True, "number_of_images": 0}
    #Extract DICOM identifiers
    series = {uid(ds, "SeriesInstanceUID") for _, ds in datasets}
    frames = {frame_of_reference_uid(ds) for _, ds in datasets}
    studies = {uid(ds, "StudyInstanceUID") for _, ds in datasets}
    sops = [uid(ds, "SOPInstanceUID") for _, ds in datasets]
    #Require exactly one image series
    if len(series) != 1: result.add("BLOCK", "Planning-image series", f"Selected images contain {len(series)} series; supply exactly one planning image series.")
    #Verify the common coordinate system
    if len(frames) != 1 or frame_of_reference_uid(dose) not in frames or frame_of_reference_uid(struct) not in frames:
        result.add("BLOCK", "Planning-image geometry", "Planning image series Frame of Reference UID differs from RTDOSE/RTSTRUCT.")
    #Check study consistency
    if len(studies) != 1: result.add("WARN", "Planning-image study", "Planning image files have inconsistent Study Instance UIDs.")
    #Detect duplicate image identifiers
    if len(set(sops)) != len(sops): result.add("BLOCK", "Planning-image series", "Duplicate SOP Instance UIDs found in planning image series.")
    #Check pixel dimensions
    if len(set(pixel_shapes)) != 1: result.add("BLOCK", "Planning-image geometry", "Planning image pixel dimensions are inconsistent within the series.")
    #Check image orientation
    if len(orientations) > 1: result.add("BLOCK", "Planning-image geometry", "Planning image orientation varies within the series.")
    #Check pixel spacing
    if len(spacings) > 1: result.add("BLOCK", "Planning-image geometry", "Planning image pixel spacing varies within the series.")
    #Detect duplicate physical slice positions 
    if len(set(slice_locations)) != len(slice_locations): result.add("BLOCK", "Planning-image geometry", "Duplicate ImagePositionPatient locations found in planning image series.")
    #Identify the valid series UID
    series_uid = next(iter(series)) if len(series) == 1 else None
    #Check whether the RTSTRUCT references the series
    if series_uid and not references(struct, series_uid): result.add("WARN", "Planning-image linkage", "Planning image series was not found in RTSTRUCT references; confirm intended series.")
    if unreadable: result.add("WARN", "Planning-image series", f"{unreadable} supplied planning image files could not be read.") #Report unreadable images
    return {"provided": True, "number_of_images": len(datasets), "series_uid": series_uid, # Return a summary
            "frame_of_reference_uid": next(iter(frames)) if len(frames) == 1 else None,
            "modality": str(getattr(datasets[0][1], "Modality", "")), "unreadable_files": unreadable,
            "decoded_pixel_shape": list(pixel_shapes[0]) if len(set(pixel_shapes)) == 1 else None,
            "pixel_spacing_mm": list(next(iter(spacings))) if len(spacings) == 1 else None,
            "orientation": list(next(iter(orientations))) if len(orientations) == 1 else None,
            "slice_positions_available": len(slice_locations)}

# Validates the selected DICOM case, reconstructs the dose grid and structure masks, verifies file identity, geometry, linkage and fractionation, audits GTV DVH metrics against TPS values, 
# and records provenance, findings and analysis eligibility.
def validate(paths: dict[str, Path], tps_csv: Path | None, manual_dose: str, manual_n: str, component_label: str, manually_confirmed_gtv: str = "") -> CaseResult:
    r = CaseResult(); dose = pydicom.dcmread(paths["rtdose"]); struct = pydicom.dcmread(paths["rtstruct"]); plan = pydicom.dcmread(paths["rtplan"], stop_before_pixels=True) if paths.get("rtplan") else None
    required = {"rtdose": dose, "rtstruct": struct}
    for key, ds in required.items():
        expected = key.upper()
        if str(getattr(ds, "Modality", "")).upper() != expected:
            r.add("BLOCK", "DICOM modality", f"Selected {key} reports Modality={getattr(ds, 'Modality', None)}, not {expected}.")
    patient_ids = {str(getattr(x, "PatientID", "")) for x in required.values() if getattr(x, "PatientID", None)}
    studies = {uid(x, "StudyInstanceUID") for x in required.values()}
    fors = {frame_of_reference_uid(x) for x in required.values()}
    if len(patient_ids) > 1: r.add("BLOCK", "Patient identity", "RTDOSE and RTSTRUCT PatientID values conflict.")
    if len(studies) > 1: r.add("WARN", "Study linkage", "Study Instance UIDs differ; confirm intended import.")
    if len(fors) > 1: r.add("BLOCK", "Spatial reference", "Frame of Reference UIDs differ.")
    if plan:
        if str(getattr(plan, "Modality", "")).upper() != "RTPLAN": r.add("BLOCK", "DICOM modality", f"Selected RTPLAN reports Modality={getattr(plan, 'Modality', None)}.")
        plan_patient_id = str(getattr(plan, "PatientID", ""))
        if patient_ids and plan_patient_id and plan_patient_id not in patient_ids: r.add("BLOCK", "Patient identity", "RTPLAN PatientID conflicts with RTDOSE/RTSTRUCT.")
        if uid(plan, "StudyInstanceUID") and uid(plan, "StudyInstanceUID") not in studies: r.add("WARN", "Study linkage", "RTPLAN Study Instance UID differs from RTDOSE/RTSTRUCT.")
        if frame_of_reference_uid(plan) and frame_of_reference_uid(plan) not in fors: r.add("BLOCK", "Spatial reference", "RTPLAN Frame of Reference UID differs from RTDOSE/RTSTRUCT.")
        if not references(dose, uid(plan, "SOPInstanceUID")): r.add("BLOCK", "Plan-dose linkage", "RTDOSE does not reference selected RTPLAN SOP Instance UID.")
        if not references(plan, uid(struct, "SOPInstanceUID")): r.add("BLOCK", "Plan-structure linkage", "RTPLAN does not reference selected RTSTRUCT SOP Instance UID.")
        if str(getattr(plan, "ApprovalStatus", "UNKNOWN")).upper() != "APPROVED":
            r.add("WARN", "Plan approval", f"RTPLAN ApprovalStatus is {getattr(plan, 'ApprovalStatus', 'UNKNOWN')}; results are technical/research outputs only.")
    image_series = inspect_image_series(paths.get("image_series", []), struct, dose, r)
    if str(getattr(dose, "DoseUnits", "")).upper() != "GY": r.add("BLOCK", "Dose units", f"DoseUnits must be GY; received {getattr(dose, 'DoseUnits', None)}.")
    if str(getattr(dose, "DoseType", "PHYSICAL")).upper() != "PHYSICAL": r.add("WARN", "Dose type", f"RTDOSE DoseType is {getattr(dose, 'DoseType', None)}; confirm suitability for a physical-dose audit.")
    if str(getattr(dose, "DoseSummationType", "")).upper() not in {"PLAN", "MULTI_PLAN"}: r.add("WARN", "Dose summation", f"RTDOSE DoseSummationType is {getattr(dose, 'DoseSummationType', None)}; confirm it represents the intended plan dose.")
    try:
        scale = float(dose.DoseGridScaling)
        if not math.isfinite(scale) or scale <= 0: raise ValueError("DoseGridScaling must be a finite positive number")
        array = dose.pixel_array.astype(float) * scale
        if not np.isfinite(array).all() or (array < 0).any(): r.add("BLOCK", "Dose grid", "Dose grid contains non-finite or negative physical doses.")
    except Exception as exc: r.add("BLOCK", "Dose grid", f"Cannot reconstruct dose grid: {exc}"); array = np.zeros((1, 1, 1))
    geo = dose_geometry(dose)
    try: masks, raster_summary = masks_from_struct(struct, geo, paths.get("image_series", []), r, manually_confirmed_gtv)
    except Exception as exc: r.add("BLOCK", "Contour rasterisation", str(exc)); masks, raster_summary = {}, {"standard_id": "FAILED", "error": str(exc)}
    frac = fractionation(plan, manual_dose, manual_n, r)
    component = classify_component(component_label or str(getattr(plan, "RTPlanLabel", "")))
    frac["component_type"] = component
    hashes = {key: sha256(value) for key, value in paths.items() if isinstance(value, Path)}
    hashes.update({f"planning_image_{index + 1}": sha256(value) for index, value in enumerate(paths.get("image_series", []))})
    r.manifest = {"case_id": str(getattr(dose, "PatientID", "unknown")), "study_uid": uid(dose, "StudyInstanceUID"), "frame_of_reference_uid": frame_of_reference_uid(dose), "rtplan_uid": uid(plan, "SOPInstanceUID") if plan else None, "rtstruct_uid": uid(struct, "SOPInstanceUID"), "rtdose_uid": uid(dose, "SOPInstanceUID"), "dose_summation_type": str(getattr(dose, "DoseSummationType", "UNKNOWN")), "plan_label": str(getattr(plan, "RTPlanLabel", "")) if plan else "", "plan_status": str(getattr(plan, "ApprovalStatus", "UNKNOWN")) if plan else "NOT_AVAILABLE", "treatment_component": component, "fractionation": frac, "planning_image_series": image_series, "input_file_hashes": hashes, "dose_grid": {"array_order": "z, y, x", "dimensions": list(array.shape), "voxel_spacing_mm": [float(np.median(np.abs(np.diff(geo['offsets'])))) if len(geo['offsets']) > 1 else 1., *map(float, geo['spacing'])], "interpolation": "none; original RTDOSE grid retained", "mask_method": raster_summary.get("method"), "rasterisation_standard": raster_summary.get("standard_id")}, "rasterisation": raster_summary, "run": {"software_version": VERSION, "timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version, "platform": platform.platform()}}
    # Spreadsheet-facing DVH summary.  Dose Cover is D95: the dose received by
    # at least 95% of the sampled structure.  Sampling Cover quantifies how
    # closely the native RTDOSE-grid mask represents the contour-stack volume.
    volume_definitions = raster_summary.get("volume_definitions", {})
    volume_audit = volume_representation_audit(volume_definitions, r)
    raster_summary["volume_representation_validation"] = {
        "anatomical_volume_definition": "RTSTRUCT contour-stack reconstruction",
        "ct_and_dose_volume_definition": "discretised masks; QA and dose sampling only, not anatomical-volume replacements",
        "representation_tolerance": {
            "absolute_cc": VOLUME_REPRESENTATION_ABS_TOL_CC,
            "relative_fraction": VOLUME_REPRESENTATION_REL_TOL,
        },
        "sampling_cover_warning_deviation_pct": SAMPLING_COVER_WARN_PCT,
        "required_native_dose_cover_min_pct": REQUIRED_NATIVE_DOSE_COVER_MIN_PCT,
        "structures": volume_audit,
    }
    dose_voxel_cc = voxel_volume_cc(geo)
    for structure, mask in sorted(masks.items()):
        if not mask.any() or mask.shape != array.shape:
            continue
        metrics = independent_metrics(array, mask, dose_voxel_cc)
        volumes = volume_definitions.get(structure, {})
        anatomical_volume = volumes.get("anatomical_volume_contour_cc", metrics["Volume_cc"])
        dose_sampled_volume = volumes.get("dose_sampled_volume_cc", metrics["Volume_cc"])
        sampling_cover = 100.0 * dose_sampled_volume / anatomical_volume if anatomical_volume else None
        r.dvh_summary.append({
            "Structure": structure,
            "Volume_cc": round(float(anatomical_volume), 5),
            "DoseCover_D95_Gy": round(metrics["D95_Gy"], 5),
            "SamplingCover_pct": round(sampling_cover, 3) if sampling_cover is not None else None,
            "MinDose_Gy": round(metrics["Dmin_Gy"], 5),
            "MaxDose_Gy": round(metrics["Dmax_Gy"], 5),
            "MeanDose_Gy": round(metrics["Dmean_Gy"], 5),
        })
    tps = read_tps_metrics(tps_csv)
    if "GTV" in masks and masks["GTV"].any() and array.shape == masks["GTV"].shape:
        calculated = independent_metrics(array, masks["GTV"], voxel_volume_cc(geo))
        volumes = raster_summary.get("volume_definitions", {}).get("GTV", {})
        calculated["Volume_cc"] = volumes.get("anatomical_volume_contour_cc", calculated["Volume_cc"])
        calculated.update({
            "AnatomicalVolumeContour_cc": volumes.get("anatomical_volume_contour_cc"),
            "AnatomicalVolumeCT_cc": volumes.get("anatomical_volume_ct_cc"),
            "DoseSampledVolume_cc": volumes.get("dose_sampled_volume_cc"),
        })
        for metric, value in calculated.items():
            if value is None: continue
            lookup = (norm("GTV"), norm(metric.replace("_Gy", "").replace("_cc", "")))
            tps_value, unit = tps.get(lookup, (None, ""))
            difference = value - tps_value if tps_value is not None else None
            tol = max(.2, .02 * abs(tps_value)) if tps_value is not None and "Gy" in metric else (.03 * abs(tps_value) if tps_value else None)
            status = "NOT_ASSESSED" if difference is None else ("PASS" if abs(difference) <= tol else "WARN")
            r.dvh_audit.append({"structure": "GTV", "metric": metric, "TPS": tps_value, "Layer1_calculated": round(value, 5), "difference": round(difference, 5) if difference is not None else None, "unit": unit or ("Gy" if "Gy" in metric else "cc"), "status": status})
            if status == "WARN": r.add("WARN", "TPS agreement", f"GTV {metric}: Layer 1–TPS difference {difference:.3g} exceeds provisional tolerance.")
        if not tps:
            r.add("WARN", "TPS agreement", "No TPS DVH reference CSV was supplied; independent metrics were calculated but agreement is NOT_ASSESSED.")
    else: r.add("BLOCK", "DVH verification", "No valid GTV mask overlapping the dose grid.")
    r.mask_arrays = masks
    r.dose_array_gy = np.asarray(array, dtype=np.float32)
    layer1_ok = r.status != "BLOCK"; bio_ok = layer1_ok and frac["fractionation_verification_status"] == "VERIFIED"
    required_mask_gate = "BLOCK" if any(row.get("NativeDoseGate") == "BLOCK" for row in volume_audit) else "PASS"
    r.eligibility = {"layer_1_status": r.status, "layer_2_eligible": layer1_ok, "layer_3_1_eligible": bio_ok, "layer_3_2_eligible": False, "layer_3_3_eligible": bio_ok, "native_dose_mask_gate": required_mask_gate, "eligibility_reason": "Layer 2/3 mask-based analysis is blocked if a required structure has inadequate native-RTDOSE coverage. Layer 3.2 also requires separately imported, spatially registered LRT and cERT components; this single-component Layer 1 import does not establish that."}
    return r

# Creates a timestamped case-results folder, saves the complete validation output as JSON, exports available findings, structure mappings and DVH audit data as CSV files, and returns the output-folder path.
def save_result(result: CaseResult, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True); stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_case_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(result.manifest["case_id"])) or "unknown"
    out = folder / f"layer1_{safe_case_id}_{stamp}"; out.mkdir()
    if result.mask_arrays:
        mask_path = out / "layer1_native_dose_masks.npz"
        archive_arrays = {"dose_gy": np.asarray(result.dose_array_gy, dtype=np.float32)}
        archive_arrays.update({name: np.asarray(mask, dtype=np.uint8) for name, mask in result.mask_arrays.items()})
        np.savez_compressed(mask_path, **archive_arrays)
        result.manifest["mask_export"] = {
            "path": str(mask_path),
            "format": "numpy_npz_dose_float32_masks_uint8",
            "dose_key": "dose_gy",
            "dose_dtype": "float32",
            "array_order": "z,y,x",
            "sha256": sha256(mask_path),
            "structures": {
                name: {
                    "voxel_count": int(mask.sum()),
                    "mask_sha256": hashlib.sha256(np.ascontiguousarray(mask, dtype=np.uint8).tobytes()).hexdigest(),
                }
                for name, mask in result.mask_arrays.items()
            },
        }
    spreadsheet_rows = [{
        "Structure": row["Structure"],
        "Volume (cc)": row["Volume_cc"],
        "Dose Cover D95 (Gy)": row["DoseCover_D95_Gy"],
        "Sampling Cover (%)": row["SamplingCover_pct"],
        "Min Dose (Gy)": row["MinDose_Gy"],
        "Max Dose (Gy)": row["MaxDose_Gy"],
        "Mean Dose (Gy)": row["MeanDose_Gy"],
    } for row in result.dvh_summary]
    spreadsheet_json = {
        "report_type": "LATTE Layer 1 DVH summary",
        "case_id": result.manifest.get("case_id"),
        "status": result.status,
        "units": {"volume": "cc", "dose": "Gy", "sampling_cover": "percent"},
        "definitions": {
            "Volume (cc)": "Anatomical volume reconstructed from the RTSTRUCT contour stack.",
            "Dose Cover D95 (Gy)": "Dose received by at least 95% of the native-RTDOSE-grid sampled structure.",
            "Sampling Cover (%)": "Native RTDOSE-grid sampled volume divided by contour-stack anatomical volume, multiplied by 100.",
            "Min Dose (Gy)": "Minimum dose in the native-RTDOSE-grid sampled structure.",
            "Max Dose (Gy)": "Maximum dose in the native-RTDOSE-grid sampled structure.",
            "Mean Dose (Gy)": "Mean dose in the native-RTDOSE-grid sampled structure.",
        },
        "structures": spreadsheet_rows,
    }
    payload = {"manifest": result.manifest, "findings": [asdict(x) for x in result.findings], "structure_mapping": result.mappings, "dvh_summary": result.dvh_summary, "dvh_audit": result.dvh_audit, "eligibility": result.eligibility}
    (out / "layer1_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out / "Layer1_DVH_Spreadsheet.json").write_text(json.dumps(spreadsheet_json, indent=2), encoding="utf-8")
    volume_audit_rows = result.manifest.get("rasterisation", {}).get("volume_representation_validation", {}).get("structures", [])
    for name, rows in (("Layer1_DVH_Spreadsheet.csv", spreadsheet_rows), ("dvh_summary.csv", result.dvh_summary), ("dvh_audit.csv", result.dvh_audit), ("volume_representation_audit.csv", volume_audit_rows), ("structure_mapping.csv", result.mappings), ("findings.csv", [asdict(x) for x in result.findings])):
        if rows:
            with (out / name).open("w", newline="", encoding="utf-8") as f: writer = csv.DictWriter(f, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    write_summary_html(result, out)
    return out


def write_summary_html(result: CaseResult, out: Path) -> Path:
    """Write a compact, portable Layer 1 result page for technical review."""
    status_colour = {"PASS": "#176b3a", "WARN": "#9a6700", "BLOCK": "#b42318"}[result.status]
    case_id = html.escape(str(result.manifest.get("case_id", "unknown")))
    plan_label = html.escape(str(result.manifest.get("plan_label", ""))) or "Not available"
    findings = result.findings or [Finding("PASS", "Validation", "No findings.")]
    finding_rows = "".join(
        f"<tr><td class='{html.escape(item.level)}'>{html.escape(item.level)}</td>"
        f"<td>{html.escape(item.check)}</td><td>{html.escape(item.detail)}</td></tr>"
        for item in findings
    )
    audit_rows = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape('' if row.get(key) is None else str(row.get(key)))}</td>"
            for key in ("structure", "metric", "Layer1_calculated", "TPS", "difference", "unit", "status")
        ) + "</tr>"
        for row in result.dvh_audit
    ) or "<tr><td colspan='7'>No valid DVH metrics were calculated.</td></tr>"
    eligibility = html.escape(json.dumps(result.eligibility, indent=2))
    document = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><title>Layer 1 Result — {case_id}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;color:#17202a}}
h1{{margin-bottom:4px}} h2{{margin-top:30px}} .meta{{color:#52606d}} .status{{background:{status_colour};color:white;padding:8px 14px;border-radius:6px;font-weight:700;display:inline-block}}
table{{border-collapse:collapse;width:100%;margin:10px 0 24px}} th,td{{border:1px solid #cbd5e1;padding:8px;text-align:left;vertical-align:top}} th{{background:#f1f5f9}} .PASS{{color:#176b3a;font-weight:700}} .WARN{{color:#9a6700;font-weight:700}} .BLOCK{{color:#b42318;font-weight:700}}
pre{{background:#f8fafc;border:1px solid #cbd5e1;padding:12px;white-space:pre-wrap}}
</style></head><body>
<h1>LATTE Layer 1: Technical Result Summary</h1>
<p class='meta'>Case: <strong>{case_id}</strong> &nbsp; Plan: <strong>{plan_label}</strong></p>
<p><span class='status'>LAYER 1 STATUS: {result.status}</span></p>
<h2>Main DVH and volume results</h2>
<table><thead><tr><th>Structure</th><th>Metric</th><th>Layer 1</th><th>TPS</th><th>Difference</th><th>Unit</th><th>Status</th></tr></thead><tbody>{audit_rows}</tbody></table>
<h2>Validation findings</h2>
<table><thead><tr><th>Level</th><th>Check</th><th>Detail</th></tr></thead><tbody>{finding_rows}</tbody></table>
<h2>Eligibility</h2><pre>{eligibility}</pre>
<p class='meta'>Technical/research output only. Review WARN and BLOCK findings before downstream use.</p>
</body></html>"""
    path = out / "layer1_summary.html"
    path.write_text(document, encoding="utf-8")
    return path
"GUI for LATTE: Lattice Assessment Toolkit for Treatment Evaluation"
# Builds the Tkinter GUI for selecting or auto-detecting case files, collecting optional inputs, running Layer 1 validation, 
# saving audit results, and displaying status, findings and eligibility.
def gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, simpledialog
    root = tk.Tk(); root.title("LATTE: Lattice Assessment Toolkit for Treatment Evaluation"); root.geometry("900x680"); root.minsize(760, 560)
    fields: dict[str, tk.StringVar] = {k: tk.StringVar() for k in ("case_folder", "rtdose", "rtstruct", "rtplan", "image", "tps", "gtv_name", "dose", "fractions", "component", "output")}; fields["output"].set(str(Path.home() / "Desktop" / "LRT_Layer1_Output"))
    detected_images: list[Path] = []
    form = tk.Frame(root, padx=16, pady=12); form.pack(fill="x")
    button_titles = {
        "rtdose": "Choose RTDOSE (required) — 3D physical dose file",
        "rtstruct": "Choose RTSTRUCT (required) — contours / structures file",
        "rtplan": "Choose RTPLAN (recommended) — plan and fractionation file",
        "image": "Choose planning CT — full series required; use case-folder import",
        "tps": "Choose TPS CSV (optional) — exported DVH metrics",
        "output": "Choose output folder — reports and audit files",
    }
    selector_buttons: dict[str, tk.Button] = {}



    def show_selected(key: str, path: str) -> None:
        selector_buttons[key].configure(text=f"{button_titles[key]}\nSelected: {Path(path).name}")
    def choose(key: str, directory: bool = False):
        nonlocal detected_images
        chosen = filedialog.askdirectory() if directory else filedialog.askopenfilename(filetypes=[("DICOM / CSV", "*.dcm *.DCM *.csv"), ("All files", "*.*")])
        if chosen:
            fields[key].set(chosen)
            show_selected(key, chosen)
            if key == "image": detected_images = []  # individual-file selection replaces an auto-detected series

    def select_candidate(kind: str, candidates: list[Path], optional: bool = False) -> Path | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        options = "\n".join(f"{index + 1}. {path.name}" for index, path in enumerate(candidates))
        prompt = f"Multiple {kind} objects were found. Select the intended object:\n\n{options}"
        choice = simpledialog.askinteger(f"Select {kind}", prompt, minvalue=1, maxvalue=len(candidates), parent=root)
        if choice is None:
            if optional:
                return None
            raise ValueError(f"No {kind} was selected.")
        return candidates[choice - 1]

    def select_image_series(candidates: list[Path], struct: Any) -> list[Path]:
        groups = group_image_series(candidates)
        if not groups:
            return []
        referenced = [(series_uid, files) for series_uid, files in groups.items() if references(struct, series_uid)]
        available = referenced if referenced else list(groups.items())
        if len(available) == 1:
            return available[0][1]
        labels = []
        for index, (series_uid, files) in enumerate(available):
            header = pydicom.dcmread(files[0], stop_before_pixels=True)
            labels.append(f"{index + 1}. {getattr(header, 'Modality', 'IMAGE')} | {getattr(header, 'SeriesDescription', '') or series_uid} | {len(files)} images")
        choice = simpledialog.askinteger("Select planning image series", "Multiple planning image series were found. Select the intended planning series:\n\n" + "\n".join(labels), minvalue=1, maxvalue=len(available), parent=root)
        if choice is None:
            return []
        return available[choice - 1][1]

    def scan_folder(): #find the DICOM infromation automatically 
        nonlocal detected_images
        folder = filedialog.askdirectory(title="Choose the folder containing one DICOM radiotherapy case")
        if not folder: return
        try:
            discovered, all_images = discover_dicom_case(Path(folder))
            selected_dose = select_candidate("RTDOSE", discovered["rtdose"])
            selected_struct = select_candidate("RTSTRUCT", discovered["rtstruct"])
            selected_plan = select_candidate("RTPLAN", discovered["rtplan"], optional=True)
            assert selected_dose and selected_struct
            fields["case_folder"].set(folder)
            for key, selected in (("rtdose", selected_dose), ("rtstruct", selected_struct), ("rtplan", selected_plan)):
                fields[key].set(str(selected) if selected else "")
                if selected: show_selected(key, str(selected))
            struct = pydicom.dcmread(selected_struct, stop_before_pixels=True)
            detected_images = select_image_series(all_images, struct)
            fields["image"].set(f"{len(detected_images)} planning image files detected automatically")
            selector_buttons["image"].configure(text=f"Full planning image series detected automatically: {len(detected_images)} images")
            messagebox.showinfo("DICOM case detected", f"Selected RTDOSE, RTSTRUCT, {('RTPLAN and ' if selected_plan else '')}{len(detected_images)} planning image files.\nReview the populated fields, confirm GTV only if needed, then validate.")
        except Exception as exc: messagebox.showerror("Case-folder scan", str(exc))
    tk.Button(form, text="FASTEST: Choose DICOM case folder — automatically finds RTDOSE, RTSTRUCT, RTPLAN and CT series", command=scan_folder, bg="#d9eaff", padx=10, pady=8).pack(fill="x", pady=(0, 8))
    for key in ("rtdose", "rtstruct", "rtplan", "image", "tps", "output"):
        button = tk.Button(form, text=button_titles[key], anchor="w", command=lambda k=key: choose(k, k == "output"), padx=10, pady=5)
        button.pack(fill="x", pady=2); selector_buttons[key] = button
    def set_gtv():
        value = simpledialog.askstring("Confirm GTV mapping", "Type the exact RTSTRUCT ROI name you have manually confirmed is GTV:\n(Leave blank to clear it)", initialvalue=fields["gtv_name"].get(), parent=root)
        if value is not None: fields["gtv_name"].set(value)
    def set_fractionation():
        dose_value = simpledialog.askstring("Prescription", "Prescription total dose in Gy (optional):", initialvalue=fields["dose"].get(), parent=root)
        if dose_value is not None: fields["dose"].set(dose_value)
        n_value = simpledialog.askstring("Fractions", "Number of fractions (optional):", initialvalue=fields["fractions"].get(), parent=root)
        if n_value is not None: fields["fractions"].set(n_value)
    tk.Button(form, text="Optional: manually confirm an ambiguous ROI as GTV", command=set_gtv, anchor="w", padx=10).pack(fill="x", pady=(6, 2))
    tk.Button(form, text="Optional: enter prescription dose and fractions", command=set_fractionation, anchor="w", padx=10).pack(fill="x", pady=2)
    tk.Button(root, text="VALIDATE LAYER 1 CASE", command=lambda: run(), bg="#1565c0", fg="white", padx=20, pady=10).pack(fill="x", padx=16, pady=6)
    log = scrolledtext.ScrolledText(root, height=12, wrap="word", state="disabled"); log.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    def write(text: str): log.configure(state="normal"); log.delete("1.0", "end"); log.insert("end", text); log.configure(state="disabled")

    def show_result_page(result: CaseResult, out: Path) -> None:
        """Present the main technical result in a separate on-screen window."""
        status_colour = {"PASS": "#176b3a", "WARN": "#9a6700", "BLOCK": "#b42318"}[result.status]
        page = tk.Toplevel(root)
        page.title("LATTE Layer 1 — Result Summary")
        page.geometry("980x680")
        page.minsize(760, 480)
        header = tk.Frame(page, padx=18, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="LATTE Layer 1: Technical Result Summary", font=("Helvetica", 18, "bold")).pack(anchor="w")
        tk.Label(header, text=f"Case: {result.manifest.get('case_id', 'unknown')}    Plan: {result.manifest.get('plan_label', '') or 'Not available'}").pack(anchor="w", pady=(4, 8))
        tk.Label(header, text=f"LAYER 1 STATUS: {result.status}", foreground="white", background=status_colour, font=("Helvetica", 12, "bold"), padx=10, pady=5).pack(anchor="w")
        body = scrolledtext.ScrolledText(page, wrap="word", state="normal", font=("Menlo", 12), padx=14, pady=12)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        body.insert("end", "MAIN DVH AND VOLUME RESULTS\n\n")
        if result.dvh_audit:
            columns = ("structure", "metric", "Layer1_calculated", "TPS", "difference", "unit", "status")
            body.insert("end", "  ".join(f"{column:>20}" for column in columns) + "\n")
            body.insert("end", "-" * 145 + "\n")
            for row in result.dvh_audit:
                body.insert("end", "  ".join(f"{str(row.get(column, '') if row.get(column) is not None else ''):>20}" for column in columns) + "\n")
        else:
            body.insert("end", "No valid DVH metrics were calculated.\n")
        body.insert("end", "\nVALIDATION FINDINGS\n\n")
        for item in result.findings or [Finding("PASS", "Validation", "No findings.")]:
            body.insert("end", f"[{item.level}] {item.check}: {item.detail}\n")
        body.insert("end", "\nELIGIBILITY\n\n" + json.dumps(result.eligibility, indent=2) + "\n")
        body.insert("end", f"\nSaved report folder: {out}\nHTML summary: {out / 'layer1_summary.html'}\n")
        body.configure(state="disabled")
        tk.Button(page, text="Close", command=page.destroy, padx=18, pady=6).pack(pady=(0, 14))

    def run():
        try:
            paths = {k: Path(fields[k].get()) for k in ("rtdose", "rtstruct")};
            if not all(x.is_file() for x in paths.values()): raise ValueError("Select readable RTDOSE and RTSTRUCT files.")
            if fields["rtplan"].get(): paths["rtplan"] = Path(fields["rtplan"].get())
            if detected_images:
                paths["image_series"] = detected_images
            elif fields["image"].get():
                image_file = Path(fields["image"].get())
                if not image_file.is_file(): raise ValueError("Choose a planning CT/image file, or use the DICOM case-folder button to import the full series.")
                paths["image_series"] = [image_file]
            tps = Path(fields["tps"].get()) if fields["tps"].get() else None
            result = validate(paths, tps, fields["dose"].get(), fields["fractions"].get(), fields["component"].get(), fields["gtv_name"].get()); out = save_result(result, Path(fields["output"].get()))
            findings = "\n".join(f"[{x.level}] {x.check}: {x.detail}" for x in result.findings) or "[PASS] No findings."
            write(f"LAYER 1 STATUS: {result.status}\nOutput: {out}\n\n{findings}\n\nEligibility:\n{json.dumps(result.eligibility, indent=2)}")
            show_result_page(result, out)
        except Exception as exc: write(traceback.format_exc()); messagebox.showerror("Layer 1 failed", str(exc))
    root.mainloop()


if __name__ == "__main__": gui()
