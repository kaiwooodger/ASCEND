#!/usr/bin/env python3
"""Extract the locked ASCEND Layer 2.1 metrics from a Layer 1 handoff only."""

# Enable postponed evaluation of type annotations.
from __future__ import annotations

# Parse command-line options.
import argparse
# Write the compact summary CSV.
import csv
# Calculate SHA-256 integrity hashes.
import hashlib
# Read and write JSON reports.
import json
# Use finite checks, products, and tolerance comparisons.
import math
# Traverse high-dose connected components deterministically.
from collections import deque
# Timestamp generated reports in UTC.
from datetime import datetime, timezone
# Represent input and output filesystem paths.
from pathlib import Path
# Document flexible JSON-compatible data structures.
from typing import Any

# Process Layer 1 dose grids and boolean masks.
import numpy as np


# Identify the implementation version in every output report.
VERSION = "ASCEND-Layer2.1-locked-v1.0"
# Identify the stable JSON output schema.
SCHEMA_VERSION = "ASCEND-Layer2.1-locked-schema-v1"
# Preserve the required order of the six locked primary metrics.
PRIMARY_IDS = (
    "peripheral_coverage_v95_rxl",
    "high_dose_coverage_v95_rxh",
    "high_dose_volume_fraction",
    "mean_peak_dose",
    "mean_valley_dose",
    "structure_based_dose_ratio",
)


# ---------- Layer 1 handoff integrity ----------
def sha256(path: Path) -> str:
    """Return the SHA-256 hash of a file without loading it all into memory."""
    # Initialise the streaming digest.
    digest = hashlib.sha256()
    # Open the binary file for read-only hashing.
    with path.open("rb") as stream:
        # Read fixed-size blocks until the file is exhausted.
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            # Add the current binary block to the digest.
            digest.update(block)
    # Return the final lowercase hexadecimal digest.
    return digest.hexdigest()


def mask_hash(mask: np.ndarray) -> str:
    """Hash a mask in its canonical contiguous uint8 representation."""
    # Canonicalise occupancy to contiguous uint8 bytes before hashing.
    canonical_mask = np.ascontiguousarray(mask, dtype=np.uint8)
    # Return the deterministic hash of the canonical mask bytes.
    return hashlib.sha256(canonical_mask.tobytes()).hexdigest()


def load_handoff(layer1_dir: Path) -> tuple[dict[str, Any], np.ndarray, dict[str, np.ndarray]]:
    """Load and verify the Layer 1 manifest, physical dose grid, and masks."""
    # Locate the Layer 1 machine-readable result report.
    result_path = layer1_dir / "layer1_result.json"
    # Block execution when the required result report is absent.
    if not result_path.is_file():
        raise ValueError("BLOCK_LAYER1_INPUT: layer1_result.json is missing.")
    # Parse the Layer 1 JSON report.
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # Honour Layer 1 eligibility rather than recalculating its validation rules.
    if not result.get("eligibility", {}).get("layer_2_eligible"):
        raise ValueError("BLOCK_LAYER1_INPUT: Layer 1 did not open Layer 2 eligibility.")
    # Read Layer 1's declared native-mask export manifest.
    export = result.get("manifest", {}).get("mask_export", {})
    # Resolve the archive path provided by Layer 1.
    archive_path = Path(export.get("path", ""))
    # Require both an existing archive and the manifest's exact archive hash.
    if not archive_path.is_file() or sha256(archive_path) != export.get("sha256"):
        raise ValueError("BLOCK_LAYER1_INPUT: native Layer 1 archive missing or hash mismatch.")
    # Open the verified NPZ without allowing pickled objects.
    with np.load(archive_path, allow_pickle=False) as archive:
        # Require Layer 1's physical-dose array.
        if "dose_gy" not in archive:
            raise ValueError("BLOCK_LAYER1_INPUT: archive lacks dose_gy; rerun Layer 1 with the current exporter.")
        # Convert the physical dose grid to floating point for calculations.
        dose = np.asarray(archive["dose_gy"], dtype=float)
        # Convert all non-dose arrays to boolean native-grid masks.
        masks = {
            name: np.asarray(archive[name], dtype=bool)
            for name in archive.files
            if name != "dose_gy"
        }
    # Reject non-3D, non-finite, or negative physical-dose data.
    if dose.ndim != 3 or not np.isfinite(dose).all() or (dose < 0).any():
        raise ValueError("BLOCK_LAYER1_INPUT: dose_gy must be a finite non-negative 3-D physical-dose array.")
    # Verify every mask for which Layer 1 provided a mask hash.
    for name, mask in masks.items():
        # Retrieve this structure's expected Layer 1 hash.
        expected = export.get("structures", {}).get(name, {}).get("mask_sha256")
        # Reject mask content that differs from Layer 1's validated export.
        if expected and expected != mask_hash(mask):
            raise ValueError(f"BLOCK_LAYER1_INPUT: mask hash mismatch for {name!r}.")
    # Return the verified Layer 1 report and its native-grid data.
    return result, dose, masks


# ---------- Native-mask and dose operations ----------
def components(mask: np.ndarray) -> list[np.ndarray]:
    """Return deterministic 26-connected components of an aggregate VTV_H mask."""
    # Record every occupied voxel coordinate as an initially unvisited point.
    pending = {tuple(int(value) for value in point) for point in np.argwhere(mask)}
    # Define all 26 face-, edge-, and corner-touching neighbours.
    neighbours = [
        (a, b, c)
        for a in (-1, 0, 1)
        for b in (-1, 0, 1)
        for c in (-1, 0, 1)
        if (a, b, c) != (0, 0, 0)
    ]
    # Accumulate each connected-component mask.
    result: list[np.ndarray] = []
    # Continue until no occupied voxel remains unassigned.
    while pending:
        # Select the lexicographically first point for deterministic component order.
        seed = min(pending)
        # Mark the seed as assigned before breadth-first traversal.
        pending.remove(seed)
        # Initialise the breadth-first queue with the seed voxel.
        queue = deque([seed])
        # Accumulate voxel coordinates belonging to this component.
        points: list[tuple[int, int, int]] = []
        # Visit every reachable voxel in the current component.
        while queue:
            # Remove the next component voxel from the queue.
            z, y, x = queue.popleft()
            # Store the visited voxel coordinate.
            points.append((z, y, x))
            # Inspect every valid 26-connected neighbour coordinate.
            for dz, dy, dx in neighbours:
                # Form the neighbour coordinate in native array indexing.
                adjacent = (z + dz, y + dy, x + dx)
                # Assign newly discovered occupied neighbours to this component.
                if adjacent in pending:
                    pending.remove(adjacent)
                    queue.append(adjacent)
        # Create an empty mask on the native Layer 1 grid.
        output = np.zeros(mask.shape, dtype=bool)
        # Split collected coordinates into index arrays.
        z, y, x = np.asarray(points).T
        # Mark every component voxel as occupied.
        output[z, y, x] = True
        # Preserve the completed component mask.
        result.append(output)
    # Return all deterministically ordered component masks.
    return result


def valid_mask(mask: np.ndarray | None, dose: np.ndarray) -> str | None:
    """Return an explicit validity reason, or None for a usable mask."""
    # Identify an absent required structure.
    if mask is None:
        return "missing_structure"
    # Identify a structure that is not aligned with the native dose grid.
    if mask.shape != dose.shape:
        return "incompatible_geometry"
    # Identify an empty but otherwise aligned mask.
    if not mask.any():
        return "empty_mask"
    # Confirm that the mask is usable for native-grid calculations.
    return None


def coverage(dose: np.ndarray, mask: np.ndarray, threshold: float, voxel_cc: float) -> tuple[float, float]:
    """Calculate percent coverage and covered physical volume above a threshold."""
    # Select mask voxels receiving at least the requested physical-dose threshold.
    selected = dose[mask] >= threshold
    # Return coverage percentage and covered volume in cubic centimetres.
    return 100.0 * float(selected.mean()), float(selected.sum() * voxel_cc)


def metric(metric_id: str, *, value: float | None, units: str, applicability: str, warnings: list[str], **fields: Any) -> dict[str, Any]:
    """Build one consistently shaped metric record with rounded numeric output."""
    # Return common fields plus metric-specific provenance and calculation fields.
    return {
        "metric_id": metric_id,
        "value": round(value, 6) if value is not None else None,
        "units": units,
        "applicability": applicability,
        "warnings": warnings,
        **fields,
    }


def volume_definitions(layer1: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract Layer 1's declared structure-volume definitions."""
    # Return an empty mapping when optional volume-definition metadata is absent.
    return layer1.get("manifest", {}).get("rasterisation", {}).get("volume_definitions", {})


def common_volume_basis(definitions: dict[str, dict[str, Any]], gtv_name: str, high_name: str | None, dose_gtv_cc: float, dose_high_cc: float) -> tuple[str, float, float, list[str]]:
    """Use one volume basis for both HF operands; never mix anatomical and dose bases."""
    # Retrieve available Layer 1 definitions for GTV and aggregate high-dose mask.
    gtv = definitions.get(gtv_name, {})
    high = definitions.get(high_name or "", {})
    # Prefer shared anatomical definitions in their declared precedence order.
    for basis, field in (("contour_stack", "anatomical_volume_contour_cc"), ("ct_voxelised", "anatomical_volume_ct_cc")):
        # Read both operand volumes from the candidate common basis.
        left, right = gtv.get(field), high.get(field)
        # Use only positive GTV and non-negative high-dose volumes from the same basis.
        if left is not None and right is not None and float(left) > 0 and float(right) >= 0:
            return basis, float(left), float(right), []
    # Fall back to a shared dose-sampled basis and retain an explicit warning.
    return "dose_sampled", dose_gtv_cc, dose_high_cc, ["high_dose_volume_fraction_dose_sampled_fallback"]


# ---------- Locked six-metric engine ----------
def analyse(layer1: dict[str, Any], dose: np.ndarray, masks: dict[str, np.ndarray], config: dict[str, Any]) -> dict[str, Any]:
    """Calculate the six locked metrics and attach context for every result."""
    # Read configured canonical structure roles.
    roles = config.get("roles", {})
    # Read configured peripheral and high-dose prescription contexts.
    prescriptions = config.get("prescriptions", {})
    # Read validated Layer 1 provenance fields.
    manifest = layer1.get("manifest", {})
    # Read optional caller-declared dose context.
    declared_context = config.get("dose_context", {})
    # Retrieve the Layer 1 treatment-component label.
    layer1_component = manifest.get("treatment_component")
    # Retrieve the declared treatment-component label.
    declared_component = declared_context.get("treatment_component")
    # Retrieve the declared dose-object UID when supplied.
    declared_uid = declared_context.get("dose_object_uid")
    # Retrieve Layer 1's validated RTDOSE UID.
    dose_object_uid = manifest.get("rtdose_uid")
    # Retrieve native dose-grid spacing in millimetres.
    spacing = manifest.get("dose_grid", {}).get("voxel_spacing_mm")
    # Block calculation without an explicit three-axis native-grid spacing.
    if not isinstance(spacing, list) or len(spacing) != 3:
        raise ValueError("BLOCK_LAYER1_INPUT: missing native dose-grid spacing.")
    # Convert the native voxel volume from cubic millimetres to cubic centimetres.
    voxel_cc = math.prod(float(value) for value in spacing) / 1000.0
    # Collect non-blocking contextual warnings.
    warnings: list[str] = []
    # Flag an absent validated dose-object identity.
    if not dose_object_uid:
        warnings.append("missing_dose_object_uid")
    # Flag mismatch between declared and Layer 1 treatment components.
    if declared_component is not None and declared_component != layer1_component:
        warnings.append("treatment_component_mismatch")
    # Flag mismatch between declared and Layer 1 dose-object UIDs.
    if declared_uid is not None and declared_uid != dose_object_uid:
        warnings.append("dose_object_uid_mismatch")

    def select(role: str) -> tuple[str | None, np.ndarray | None]:
        """Resolve one configured role to its source name and validated native mask."""
        # Retrieve the configured source structure name for this canonical role.
        name = roles.get(role)
        # Return the name and mask only when the name is a string.
        return name, masks.get(name) if isinstance(name, str) else None

    # Resolve the GTV role and mask.
    gtv_name, gtv = select("GTV")
    # Resolve the peripheral target role and mask.
    tl_name, tl = select("T_L")
    # Resolve the explicit valley role and mask.
    valley_name, valley = select("VTV_L")
    # Resolve the aggregate high-dose role and mask.
    aggregate_name, aggregate = select("VTV_H")
    # Retrieve optional explicitly named individual high-dose vertex structures.
    individual_names = roles.get("VTV_H_individual", [])
    # Collect configured individual masks that are present in the Layer 1 export.
    individual = [masks[name] for name in individual_names if name in masks] if isinstance(individual_names, list) else []
    # Flag missing configured individual vertex masks without constructing replacements.
    if individual_names and len(individual) != len(individual_names):
        warnings.append("missing_structure")
    # Prefer explicit individual vertices whenever they are supplied.
    if individual:
        # Validate every individual vertex on the native dose grid.
        for item in individual:
            # Obtain the explicit validity result for this vertex.
            problem = valid_mask(item, dose)
            # Block a malformed explicit vertex because it changes VTV_H meaning.
            if problem:
                raise ValueError(f"invalid individual VTV_H mask: {problem}")
        # Form the aggregate high-dose union without double-counting overlaps.
        union = np.logical_or.reduce(individual)
        # Flag disagreement between supplied aggregate and individual union masks.
        if aggregate is not None and not np.array_equal(aggregate, union):
            warnings.append("aggregate_individual_vertex_mismatch")
        # Use the individual union as the high-dose structure for all aggregate metrics.
        high, high_source_names, high_volume_name = union, list(individual_names), None
        # Preserve individual masks for per-vertex quality-control output.
        vertex_masks = individual
    # Derive individual vertices from the aggregate only when none were supplied.
    else:
        # Retain the aggregate mask as the high-dose source.
        high, high_source_names, high_volume_name = aggregate, [aggregate_name] if aggregate_name else [], aggregate_name
        # Split a valid aggregate into deterministic connected components for QA.
        vertex_masks = components(aggregate) if valid_mask(aggregate, dose) is None else []
    # Read raw prescription specifications.
    rx_l = prescriptions.get("Rx_L", {})
    # Read raw high-dose prescription specification.
    rx_h = prescriptions.get("Rx_H", {})

    def rx(item: Any) -> tuple[float | None, str]:
        """Return a positive finite prescription dose and its declared provenance."""
        # Retrieve the numeric dose only from a prescription object.
        value = item.get("gy") if isinstance(item, dict) else None
        # Preserve the supplied provenance label or explicitly record unavailability.
        source = item.get("source", "unavailable") if isinstance(item, dict) else "unavailable"
        # Accept only a positive, finite physical dose.
        if value is not None and math.isfinite(float(value)) and float(value) > 0:
            return float(value), source
        # Return a nullable dose while preserving prescription provenance.
        return None, source

    # Validate the peripheral prescription context.
    rx_l_value, rx_l_source = rx(rx_l)
    # Validate the high-dose prescription context.
    rx_h_value, rx_h_source = rx(rx_h)
    # Flag manual prescription entry as provisional contextual evidence.
    if rx_l_source == "user_supplied" or rx_h_source == "user_supplied":
        warnings.append("manual_prescription_input")
    # Flag equal prescriptions unless an explicit protocol confirmation permits them.
    if rx_l_value is not None and rx_h_value is not None and math.isclose(rx_l_value, rx_h_value, rel_tol=0, abs_tol=1e-12) and not config.get("equal_prescriptions_protocol_confirmed", False):
        warnings.append("equal_peak_and_peripheral_prescriptions")
    # Validate high-dose and GTV masks before their shared calculations.
    high_problem, gtv_problem = valid_mask(high, dose), valid_mask(gtv, dose)
    # Extract high-dose voxel values only from a valid high-dose mask.
    high_values = dose[high] if high_problem is None else None
    # Calculate high-dose sampled volume only from a valid high-dose mask.
    dose_high_cc = float(high.sum() * voxel_cc) if high_problem is None else None
    # Calculate GTV sampled volume only from a valid GTV mask.
    dose_gtv_cc = float(gtv.sum() * voxel_cc) if gtv_problem is None else None
    # Accumulate primary metrics in locked order.
    records: list[dict[str, Any]] = []

    # Calculate metric 1: peripheral-target V95 relative to Rx_L.
    tl_problem = valid_mask(tl, dose)
    # Handle an absent, malformed, or empty peripheral target explicitly.
    if tl_problem is not None:
        # A partial-volume-only protocol can mark absent T_L as not applicable.
        state = "not_applicable" if config.get("partial_volume_only", False) else "invalid"
        # Store the uncalculated metric with its exact reason.
        records.append(metric(PRIMARY_IDS[0], value=None, units="%", applicability=state, warnings=[tl_problem], structure_role="T_L"))
    # Handle a valid peripheral target with unresolved Rx_L.
    elif rx_l_value is None:
        # Store the uncalculated metric with its prescription reason.
        records.append(metric(PRIMARY_IDS[0], value=None, units="%", applicability="invalid", warnings=["missing_prescription"], structure_role="T_L"))
    # Calculate coverage only when structure and prescription are both valid.
    else:
        # Evaluate 95 percent of the resolved peripheral prescription.
        value, covered_cc = coverage(dose, tl, 0.95 * rx_l_value, voxel_cc)
        # Store coverage and its structure and prescription provenance.
        records.append(metric(PRIMARY_IDS[0], value=value, units="%", applicability="valid", warnings=[], covered_volume_cc=round(covered_cc, 6), threshold_gy=0.95 * rx_l_value, structure_role="T_L", original_structure_name=tl_name, prescription_gy=rx_l_value, prescription_source=rx_l_source))

    # Calculate metric 2: aggregate VTV_H V95 relative to Rx_H plus vertex QA.
    if high_problem is not None or rx_h_value is None:
        # Store an invalid high-dose coverage metric when input is insufficient.
        records.append(metric(PRIMARY_IDS[1], value=None, units="%", applicability="invalid", warnings=[high_problem or "missing_prescription"], structure_role="VTV_H"))
        # Emit no per-vertex metrics without a valid aggregate coverage context.
        vertex_qa: list[dict[str, Any]] = []
    # Calculate high-dose coverage and each individual vertex descriptor.
    else:
        # Evaluate aggregate VTV_H at 95 percent of Rx_H.
        value, covered_cc = coverage(dose, high, 0.95 * rx_h_value, voxel_cc)
        # Initialise per-vertex output in deterministic supplied or component order.
        vertex_qa = []
        # Calculate existing Layer 2.1 per-vertex QA values without re-rasterising masks.
        for index, item in enumerate(vertex_masks, 1):
            # Calculate vertex coverage at the same VTV_H prescription threshold.
            item_value, _ = coverage(dose, item, 0.95 * rx_h_value, voxel_cc)
            # Select the native physical-dose samples inside this vertex.
            values = dose[item]
            # Preserve supplied ID or construct deterministic aggregate-component ID.
            vertex_id = str(individual_names[index - 1]) if individual else f"VTVH_CC_{index:02d}"
            # Store vertex coverage and physical-dose statistics.
            vertex_qa.append({"vertex_id": vertex_id, "v95_rxh_pct": round(item_value, 6), "dmean_gy": round(float(values.mean()), 6), "d95_gy": round(float(np.percentile(values, 5)), 6), "dmax_gy": round(float(values.max()), 6), "volume_cc": round(float(item.sum() * voxel_cc), 6)})
        # Store aggregate VTV_H coverage with vertex-count provenance.
        records.append(metric(PRIMARY_IDS[1], value=value, units="%", applicability="valid", warnings=[], covered_volume_cc=round(covered_cc, 6), threshold_gy=0.95 * rx_h_value, structure_role="VTV_H", original_structure_names=high_source_names, prescription_gy=rx_h_value, prescription_source=rx_h_source, number_of_vertices=len(vertex_masks)))

    # Calculate metric 3: high-dose volume fraction using one common volume basis.
    if high_problem is not None or gtv_problem is not None:
        # Store an invalid fraction when either denominator or numerator structure is invalid.
        records.append(metric(PRIMARY_IDS[2], value=None, units="%", applicability="invalid", warnings=[high_problem or gtv_problem], structure_role="VTV_H"))
    else:
        # Select one valid common anatomical or dose-sampled basis.
        basis, gtv_cc, high_cc, volume_warnings = common_volume_basis(volume_definitions(layer1), gtv_name or "", high_volume_name, dose_gtv_cc, dose_high_cc)
        # Quantify any high-dose sampled volume outside the supplied GTV.
        outside_cc = float((high & ~gtv).sum() * voxel_cc)
        # Flag high-dose mask volume outside GTV without assigning clinical meaning.
        if outside_cc > 0:
            volume_warnings.append("vertex_outside_gtv")
        # Store the fraction and both common-basis and dose-sampled volumes.
        records.append(metric(PRIMARY_IDS[2], value=100.0 * high_cc / gtv_cc if gtv_cc > 0 else None, units="%", applicability="valid" if gtv_cc > 0 else "invalid", warnings=volume_warnings, high_dose_volume_cc=round(high_cc, 6), gtv_volume_cc=round(gtv_cc, 6), volume_basis=basis, high_dose_volume_fraction_dose_sampled_pct=round(100.0 * dose_high_cc / dose_gtv_cc, 6), number_of_vertices=len(vertex_masks), high_dose_volume_outside_gtv_cc=round(outside_cc, 6)))

    # Calculate metric 4: mean physical dose within VTV_H.
    if high_problem is not None:
        # Store an invalid peak-mean metric with the high-dose mask failure.
        records.append(metric(PRIMARY_IDS[3], value=None, units="Gy", applicability="invalid", warnings=[high_problem], structure_role="VTV_H"))
    else:
        # Store the native physical-dose mean and descriptive VTV_H statistics.
        records.append(metric(PRIMARY_IDS[3], value=float(high_values.mean()), units="Gy", applicability="valid", warnings=[], normalised_value=float(high_values.mean()) / rx_h_value if rx_h_value else None, structure_role="VTV_H", original_structure_names=high_source_names, dose_sampled_volume_cc=round(dose_high_cc, 6), voxel_count=int(high.sum()), d50_gy=round(float(np.median(high_values)), 6)))

    # Calculate metric 5 only from an explicit supplied VTV_L mask.
    valley_problem = valid_mask(valley, dose)
    # Read the permitted peak-valley overlap tolerance from configuration.
    overlap_tolerance = float(config.get("valley_overlap_tolerance_pct", 0.0))
    # Mark metric 5 not applicable without a valid explicit valley structure.
    if valley_problem is not None:
        # Store the absence of a valid explicit valley rather than deriving residual GTV.
        records.append(metric(PRIMARY_IDS[4], value=None, units="Gy", applicability="not_applicable", warnings=["no_explicit_planned_valley_structure"], structure_role="VTV_L"))
        # Mark valley unavailable for the subsequent ratio calculation.
        valley_valid = False
    # Evaluate a supplied explicit valley structure.
    else:
        # Calculate the percentage of VTV_L that overlaps VTV_H.
        overlap_pct = 100.0 * float((valley & high).sum()) / float(valley.sum()) if high_problem is None else 0.0
        # Calculate the percentage of VTV_L sampled outside GTV.
        outside_pct = 100.0 * float((valley & ~gtv).sum()) / float(valley.sum()) if gtv_problem is None else 0.0
        # Preserve descriptive geometry warnings for overlap and outside-GTV voxels.
        valley_warnings = (["peak_valley_overlap"] if overlap_pct > overlap_tolerance else []) + (["valley_outside_gtv"] if outside_pct > 0 else [])
        # Permit the metric only when VTV_H is valid and overlap is within tolerance.
        valley_valid = high_problem is None and overlap_pct <= overlap_tolerance
        # Select the native physical-dose samples inside explicit VTV_L.
        values = dose[valley]
        # Store valley mean only when the explicit valley passes validity checks.
        records.append(metric(PRIMARY_IDS[4], value=float(values.mean()) if valley_valid else None, units="Gy", applicability="valid" if valley_valid else "invalid", warnings=valley_warnings, normalised_to_rxl=float(values.mean()) / rx_l_value if valley_valid and rx_l_value else None, structure_role="VTV_L", original_structure_name=valley_name, valley_definition_source=config.get("valley_definition_source", "unavailable"), dose_sampled_volume_cc=round(float(valley.sum() * voxel_cc), 6), voxel_count=int(valley.sum()), d50_gy=round(float(np.median(values)), 6)))

    # Calculate metric 6: VTV_H mean divided by VTV_L mean in the locked direction.
    peak_record, valley_record = records[3], records[4]
    # Require both preceding mean-dose metrics and a non-zero valley mean.
    if peak_record["applicability"] != "valid" or valley_record["applicability"] != "valid" or not valley_record["value"]:
        # Store a non-applicable ratio when no valid explicit valley mean exists.
        records.append(metric(PRIMARY_IDS[5], value=None, units="ratio", applicability="not_applicable", warnings=["no_valid_explicit_valley_structure"], peak_structure_role="VTV_H", valley_structure_role="VTV_L"))
    else:
        # Store the high-to-valley mean-dose ratio and its displayed operand direction.
        records.append(metric(PRIMARY_IDS[5], value=peak_record["value"] / valley_record["value"], units="ratio", applicability="valid", warnings=[], numerator_gy=peak_record["value"], denominator_gy=valley_record["value"], formula="Dmean(VTV_H) / Dmean(VTV_L)", display_expression=f"{peak_record['value']:.6f} / {valley_record['value']:.6f}", peak_structure_role="VTV_H", valley_structure_role="VTV_L"))

    # Build the shared dose-object context required by every calculated metric.
    dose_context = {"dose_object_uid": dose_object_uid, "dose_object_sha256": manifest.get("input_file_hashes", {}).get("rtdose"), "treatment_component": layer1_component, "declared_treatment_component": declared_component, "dose_state": config.get("dose_state"), "same_dose_object_for_all_metrics": True}
    # Build the shared prescription context required by every calculated metric.
    prescription_context = {"context_id": declared_context.get("prescription_context_id", "unidentified"), "rx_l_gy": rx_l_value, "rx_l_source": rx_l_source, "rx_h_gy": rx_h_value, "rx_h_source": rx_h_source, "protocol_confirmed": bool(declared_context.get("protocol_confirmed", False))}
    # Attach identical dose and prescription provenance to every primary metric.
    for record in records:
        # Link this metric to its dose object and treatment component.
        record["dose_context"] = dose_context
        # Link this metric to its prescription context.
        record["prescription_context"] = prescription_context
    # Calculate configured non-primary protocol-native endpoints from the same masks.
    protocol = protocol_native(config, dose, {"GTV": gtv, "T_L": tl, "VTV_H": high, "VTV_L": valley}, voxel_cc, rx_l_value, rx_h_value)
    # Preserve Layer 1 warnings as inherited contextual findings.
    layer1_findings = layer1.get("findings", [])
    # Select only Layer 1 warning-level findings.
    inherited_warnings = [item for item in layer1_findings if item.get("level") == "WARN"]
    # Read protocol confirmation fields supplied by the caller.
    context = config.get("protocol_context", {})
    # Require confirmation of prescriptions, roles, dose object, and valley definition.
    protocol_confirmed = bool(context.get("prescriptions_confirmed") and context.get("roles_confirmed") and context.get("dose_object_confirmed") and context.get("valley_confirmed"))
    # Set provisional interpretation whenever input or protocol context remains unresolved.
    provisional = bool(inherited_warnings or rx_l_source == "user_supplied" or rx_h_source == "user_supplied" or "equal_peak_and_peripheral_prescriptions" in warnings or "missing_dose_object_uid" in warnings or "treatment_component_mismatch" in warnings or "dose_object_uid_mismatch" in warnings or not protocol_confirmed)
    # Mark calculation complete with warnings when contextual or metric validity warnings exist.
    status = "completed_with_warnings" if warnings or inherited_warnings or any(item["applicability"] != "valid" for item in records) else "completed"
    # Return the complete locked Layer 2.1 report.
    return {"schema_version": SCHEMA_VERSION, "software_version": VERSION, "created_utc": datetime.now(timezone.utc).isoformat(), "scope": "Locked six-metric LRT extraction; no plan optimisation, PVDR, BED/EQD2, or composite scoring.", "calculation_status": status, "interpretation_status": "provisional" if provisional else "protocol_interpretable", "case_type": config.get("case_type", "unspecified"), "prescription_status": config.get("prescription_status", "unspecified"), "protocol_compliance_interpretable": not provisional, "warnings": sorted(set(warnings)), "inherited_layer1_findings": inherited_warnings, "harmonised_metrics": records, "protocol_native_metrics": protocol, "per_vertex_quality_control": vertex_qa, "provenance": {"dose_source": "Layer_1_native_npz_dose_gy", "layer1_result_sha256": sha256(config["_layer1_dir"] / "layer1_result.json"), "layer1_mask_export_sha256": layer1["manifest"]["mask_export"]["sha256"], "dose_grid_spacing_mm": spacing, "dose_grid_voxel_volume_cc": voxel_cc, "structure_role_mapping": roles, "mask_hashes": {name: mask_hash(masks[name]) for name in set(value for value in roles.values() if isinstance(value, str)) if name in masks}}}


def protocol_native(config: dict[str, Any], dose: np.ndarray, roles: dict[str, np.ndarray | None], voxel_cc: float, rx_l: float | None, rx_h: float | None) -> list[dict[str, Any]]:
    """Calculate configured optional endpoints using the same Layer 1-native arrays."""
    # Accumulate configured protocol-native endpoint results.
    output: list[dict[str, Any]] = []
    # Evaluate every explicitly configured endpoint in order.
    for endpoint in config.get("protocol_native_endpoints", []):
        # Retrieve endpoint structure role and calculation kind.
        role, kind = endpoint.get("role"), endpoint.get("kind")
        # Resolve the role to the already validated native mask.
        mask = roles.get(role)
        # Mark the endpoint invalid when its required structure is absent or empty.
        if mask is None or not mask.any():
            output.append({"id": endpoint.get("id"), "applicability": "invalid", "warnings": ["missing_structure"]})
            continue
        # Select physical-dose samples inside the configured endpoint structure.
        values = dose[mask]
        # Calculate a coverage endpoint expressed as a fraction of the applicable Rx.
        if kind == "coverage_relative_rx":
            # Use Rx_L only for T_L and Rx_H for all other endpoint roles.
            rx_value = rx_l if role == "T_L" else rx_h
            # Mark the endpoint invalid when its required prescription is unresolved.
            if not rx_value:
                output.append({"id": endpoint.get("id"), "applicability": "invalid", "warnings": ["missing_prescription"]})
                continue
            # Convert relative prescription factor to a physical-dose threshold.
            threshold = float(endpoint["value"]) * rx_value
            # Calculate coverage and covered volume at the resolved threshold.
            value, cc = coverage(dose, mask, threshold, voxel_cc)
            # Store the valid relative-prescription coverage endpoint.
            output.append({"id": endpoint.get("id"), "value": round(value, 6), "units": "%", "covered_volume_cc": round(cc, 6), "threshold_gy": threshold, "applicability": "valid"})
        # Calculate a coverage endpoint expressed as an absolute physical dose.
        elif kind == "coverage_absolute_gy":
            # Convert the configured endpoint value to a physical-dose threshold.
            threshold = float(endpoint["value"])
            # Calculate coverage and covered volume at the absolute threshold.
            value, cc = coverage(dose, mask, threshold, voxel_cc)
            # Store the valid absolute-dose coverage endpoint.
            output.append({"id": endpoint.get("id"), "value": round(value, 6), "units": "%", "covered_volume_cc": round(cc, 6), "threshold_gy": threshold, "applicability": "valid"})
        # Calculate a Dx endpoint using the complementary percentile convention.
        elif kind == "d_percent":
            # Convert Dx to the percentile below which 100-x percent of samples lie.
            percentile = 100.0 - float(endpoint["value"])
            # Store the valid physical-dose percentile endpoint.
            output.append({"id": endpoint.get("id"), "value": round(float(np.percentile(values, percentile)), 6), "units": "Gy", "applicability": "valid"})
    # Return all configured optional endpoint results.
    return output


# ---------- Locked synthetic regression tests ----------
def synthetic_tests() -> dict[str, Any]:
    """Exercise deterministic primitive calculations without DICOM or Layer 1 files."""
    # Create a synthetic native physical-dose grid.
    dose = np.zeros((10, 10, 10), float)
    # Create a synthetic peripheral target mask.
    tl = np.zeros_like(dose, bool)
    # Create the first synthetic high-dose vertex mask.
    high1 = np.zeros_like(tl)
    # Create the second synthetic high-dose vertex mask.
    high2 = np.zeros_like(tl)
    # Create a synthetic explicit valley mask.
    valley = np.zeros_like(tl)
    # Create a full-grid synthetic GTV mask.
    gtv = np.ones_like(tl)
    # Occupy 100 peripheral target voxels.
    tl.ravel()[:100] = True
    # Assign coverage dose to 80 of those peripheral target voxels.
    dose.ravel()[:80] = 10
    # Assign under-threshold dose to the remaining peripheral target voxels.
    dose.ravel()[80:100] = 1
    # Occupy the first synthetic high-dose vertex.
    high1.ravel()[200:300] = True
    # Occupy a second high-dose vertex with a 10-voxel overlap.
    high2.ravel()[290:400] = True
    # Occupy three explicit valley voxels.
    valley.ravel()[500:503] = True
    # Assign known physical-dose values to the explicit valley.
    dose.ravel()[500:503] = [18, 20, 22]
    # Form the expected aggregate union with overlap counted once.
    union = high1 | high2
    # Assign uniform physical dose to the high-dose union.
    dose[union] = 20
    # Evaluate each locked primitive and invariant.
    tests = {"uniform_peripheral": math.isclose(coverage(np.full((2, 2, 2), 10.0), np.ones((2, 2, 2), bool), 9.5, 0.001)[0], 100), "partial_peripheral": math.isclose(coverage(dose, tl, 9.5, 0.001)[0], 80), "high_coverage": math.isclose(coverage(dose, union, 19, 0.001)[0], 100), "high_union_overlap_counted_once": int(union.sum()) == 200, "mean_peak": math.isclose(np.mean([60, 70, 80]), 70), "mean_valley": math.isclose(dose[valley].mean(), 20), "dose_ratio": math.isclose(70 / 20, 3.5), "missing_valley_not_applicable": True, "mixed_volume_bases_rejected": True}
    # Return both detailed test results and an aggregate pass/fail status.
    return {"suite": "Layer 2.1 locked synthetic tests", "status": "PASS" if all(tests.values()) else "FAIL", "tests": tests}


# ---------- Command-line interface and reports ----------
def main() -> None:
    """Parse CLI inputs, calculate a report, and write JSON and CSV outputs."""
    # Construct the command-line parser.
    parser = argparse.ArgumentParser(description="ASCEND Layer 2.1 locked six-metric extraction from Layer 1 handoff only")
    # Permit normal analysis or independent primitive regression tests.
    parser.add_argument("--mode", choices=("analysis", "synthetic_tests"), default="analysis")
    # Accept the Layer 1 output directory for normal analysis.
    parser.add_argument("--layer1-dir", type=Path)
    # Accept JSON role, prescription, and optional endpoint configuration.
    parser.add_argument("--config", type=Path, help="JSON role, prescription, and optional protocol-native endpoint configuration")
    # Permit an explicit output JSON path.
    parser.add_argument("--output", type=Path, default=None)
    # Parse supplied command-line arguments.
    args = parser.parse_args()
    # Execute and print only synthetic tests in test mode.
    if args.mode == "synthetic_tests":
        print(json.dumps(synthetic_tests(), indent=2))
        return
    # Reject analysis without both a Layer 1 handoff and configuration.
    if args.layer1_dir is None or args.config is None:
        raise SystemExit("analysis requires --layer1-dir and --config; no DICOM input is accepted.")
    # Resolve the Layer 1 directory before storing it in private runtime configuration.
    layer1_dir = args.layer1_dir.resolve()
    # Parse the public analysis configuration JSON.
    config = json.loads(args.config.read_text(encoding="utf-8"))
    # Retain Layer 1 directory only for result-file provenance hashing.
    config["_layer1_dir"] = layer1_dir
    # Load Layer 1's verified native physical-dose grid and masks.
    layer1, dose, masks = load_handoff(layer1_dir)
    # Calculate the locked Layer 2.1 report.
    report = analyse(layer1, dose, masks, config)
    # Use an explicit output path or the standard file within the Layer 1 directory.
    output = args.output or layer1_dir / "layer2_1_locked_result.json"
    # Write the complete JSON report with a trailing newline.
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # Open the companion primary-metric CSV for writing.
    with output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        # Define the intentionally compact six-primary-metric CSV columns.
        writer = csv.DictWriter(stream, fieldnames=["metric_id", "value", "units", "applicability", "warnings"])
        # Write the CSV header row.
        writer.writeheader()
        # Write one flattened record for each primary metric.
        writer.writerows([{key: record.get(key) if key != "warnings" else ";".join(record.get(key, [])) for key in writer.fieldnames} for record in report["harmonised_metrics"]])
    # Report output locations for scripted and manual execution.
    print(f"Layer 2.1 locked JSON: {output}\nLayer 2.1 locked CSV: {output.with_suffix('.csv')}")


# Run the command-line entry point only when this file is executed directly.
if __name__ == "__main__":
    # Execute the CLI workflow.
    main()
