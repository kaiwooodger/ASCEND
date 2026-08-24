"""Presentation-only Layer 3.2 scalar surfaces and portable 3D exports.

This module consumes stored, hash-verified Layer 3.2 arrays.  It never writes
to a case result and none of its meshes are inputs to a scientific endpoint.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import struct
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile
import zlib

import numpy as np
from skimage.measure import marching_cubes


CONSEQUENCE_SURFACE_COLOURS: dict[float, tuple[int, int, int]] = {
    2.5: (59, 82, 139),
    5.0: (33, 145, 140),
    10.0: (94, 201, 98),
    20.0: (253, 231, 37),
}


@dataclass
class ScalarSurface:
    """Triangle surface in DICOM LPS millimetres with per-vertex display data."""

    vertices_lps_mm: np.ndarray
    faces: np.ndarray
    scalar_values: np.ndarray
    rgb: np.ndarray
    level: float


def _geometry_arrays(geometry: dict[str, Any]) -> dict[str, np.ndarray]:
    required = ("origin", "row_direction", "column_direction", "normal", "offsets", "spacing", "shape")
    missing = [key for key in required if key not in geometry]
    if missing:
        raise ValueError(f"Layer 3.2 crop geometry is missing: {', '.join(missing)}")
    result = {key: np.asarray(geometry[key], dtype=float) for key in required}
    if result["origin"].shape != (3,) or result["spacing"].shape not in {(2,), (3,)}:
        raise ValueError("Layer 3.2 crop origin must contain three values and spacing must contain pixel or z/y/x spacing.")
    if not all(np.isfinite(value).all() for value in result.values()):
        raise ValueError("Layer 3.2 crop geometry must be finite.")
    pixel_spacing = result["spacing"][-2:]
    offsets = result["offsets"]
    frame_spacing = float(np.median(np.abs(np.diff(offsets)))) if len(offsets) > 1 else (
        float(result["spacing"][0]) if len(result["spacing"]) == 3 else 1.0
    )
    result["pixel_spacing"] = pixel_spacing
    result["voxel_spacing_zyx"] = np.asarray([frame_spacing, *pixel_spacing], dtype=float)
    return result


def indices_to_lps(points_zyx: np.ndarray, geometry: dict[str, Any]) -> np.ndarray:
    """Transform floating-point stored-array indices into DICOM patient LPS."""
    values = _geometry_arrays(geometry)
    points = np.asarray(points_zyx, dtype=float)
    offsets = np.interp(points[:, 0], np.arange(len(values["offsets"])), values["offsets"])
    return (
        values["origin"]
        + offsets[:, None] * values["normal"]
        + points[:, 2, None] * values["pixel_spacing"][1] * values["row_direction"]
        + points[:, 1, None] * values["pixel_spacing"][0] * values["column_direction"]
    )


def scalar_range(field: np.ndarray) -> tuple[float, float]:
    """Return a finite stored-field range suitable for display controls."""
    finite = np.asarray(field, dtype=np.float32)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        raise ValueError("Layer 3.2 scalar field has no finite values.")
    return float(np.min(finite)), float(np.max(finite))


def scalar_level(field: np.ndarray, relative_level: float) -> float:
    """Convert a zero-to-one relative display level to a stored scalar value."""
    low, high = scalar_range(field)
    fraction = float(np.clip(relative_level, 0.0, 1.0))
    return low + fraction * (high - low)


def _palette(fraction: float, reverse: bool = False) -> np.ndarray:
    fraction = float(np.clip(1.0 - fraction if reverse else fraction, 0.0, 1.0))
    anchors = np.asarray([
        [20, 39, 93], [20, 111, 154], [37, 181, 164],
        [244, 211, 69], [239, 116, 31], [183, 28, 48],
    ], dtype=float)
    position = fraction * (len(anchors) - 1)
    lower = int(np.floor(position)); upper = min(lower + 1, len(anchors) - 1)
    return np.asarray(np.round(anchors[lower] * (upper - position) + anchors[upper] * (position - lower)), dtype=np.uint8)


def scalar_surface(
    field: np.ndarray,
    geometry: dict[str, Any],
    level: float,
    *,
    display_fraction: float = 0.5,
    reverse_palette: bool = False,
    clip_axis: int | None = None,
    clip_index: int | None = None,
    keep_lower: bool = True,
    step_size: int | None = None,
) -> ScalarSurface:
    """Extract one display isosurface without modifying the stored field."""
    values = np.asarray(field, dtype=np.float32)
    if values.ndim != 3 or not np.isfinite(values).all():
        raise ValueError("Layer 3.2 isosurface input must be one finite three-dimensional field.")
    low, high = scalar_range(values)
    if not low < float(level) < high:
        raise ValueError(f"Isosurface level {level:g} must lie strictly within {low:g} to {high:g}.")
    mask = None
    if clip_axis is not None:
        axis = int(clip_axis)
        if axis not in (0, 1, 2):
            raise ValueError("Layer 3.2 clipping axis must be 0, 1, or 2.")
        index = int(np.clip(clip_index if clip_index is not None else values.shape[axis] // 2, 0, values.shape[axis] - 1))
        mask = np.zeros(values.shape, dtype=bool)
        selection = [slice(None), slice(None), slice(None)]
        selection[axis] = slice(0, index + 1) if keep_lower else slice(index, values.shape[axis])
        mask[tuple(selection)] = True
    if step_size is None:
        step_size = 2 if values.size > 4_000_000 else 1
    vertices, faces, _normals, _samples = marching_cubes(
        values, level=float(level), step_size=max(int(step_size), 1),
        allow_degenerate=False, mask=mask,
    )
    colour = _palette(display_fraction, reverse_palette)
    return ScalarSurface(
        np.ascontiguousarray(indices_to_lps(vertices, geometry), dtype=np.float32),
        np.ascontiguousarray(faces, dtype=np.uint32),
        np.full(len(vertices), float(level), dtype=np.float32),
        np.tile(colour, (len(vertices), 1)),
        float(level),
    )


def multilevel_surfaces(
    field: np.ndarray,
    geometry: dict[str, Any],
    fractions: Iterable[float] = (0.2, 0.4, 0.6, 0.8),
    *,
    reverse_palette: bool = False,
    clip_axis: int | None = None,
    clip_index: int | None = None,
) -> list[ScalarSurface]:
    """Build nested display surfaces at deterministic relative scalar levels."""
    low, high = scalar_range(field)
    if not low < high:
        return []
    surfaces: list[ScalarSurface] = []
    for fraction in fractions:
        try:
            surfaces.append(scalar_surface(
                field, geometry, scalar_level(field, fraction),
                display_fraction=float(fraction), reverse_palette=reverse_palette,
                clip_axis=clip_axis, clip_index=clip_index,
            ))
        except (RuntimeError, ValueError):
            continue
    return surfaces


def consequence_threshold_surfaces(
    reduction_percent_field: np.ndarray,
    geometry: dict[str, Any],
    thresholds_percent: Iterable[float] = (2.5, 5.0, 10.0, 20.0),
    *,
    clip_axis: int | None = None,
    clip_index: int | None = None,
) -> list[ScalarSurface]:
    """Build case-comparable surfaces at absolute model-consequence thresholds."""
    values = np.asarray(reduction_percent_field, dtype=np.float32)
    low, high = scalar_range(values)
    surfaces: list[ScalarSurface] = []
    thresholds = list(map(float, thresholds_percent))
    for index, threshold in enumerate(thresholds):
        if not low < threshold < high:
            continue
        surface = scalar_surface(
            values, geometry, threshold,
            display_fraction=(index + 1) / max(len(thresholds), 1),
            clip_axis=clip_axis, clip_index=clip_index,
        )
        colour = CONSEQUENCE_SURFACE_COLOURS.get(threshold)
        if colour is not None:
            surface.rgb[:] = np.asarray(colour, dtype=np.uint8)
        surfaces.append(surface)
    return surfaces


def equivalent_exposure_h(reduction_percent: float, scaling: float) -> float:
    """Return H corresponding to an additional modelled reduction threshold."""
    reduction = float(reduction_percent) / 100.0
    if not 0.0 < reduction < 1.0 or not math.isfinite(scaling) or scaling <= 0:
        raise ValueError("Reduction must lie between 0 and 100 percent and scaling must be positive.")
    return float(-math.log1p(-reduction) / scaling)


def voxel_volume_cc(geometry: dict[str, Any]) -> float:
    """Return the uniform stored crop voxel volume in cubic centimetres."""
    spacing = _geometry_arrays(geometry)["voxel_spacing_zyx"]
    return float(np.prod(spacing) / 1000.0)


def mask_surface(mask: np.ndarray, geometry: dict[str, Any]) -> ScalarSurface:
    """Extract a stored mask boundary for anatomical overlays and export."""
    values = np.asarray(mask, dtype=np.uint8)
    if values.ndim != 3 or not values.any():
        raise ValueError("Cannot build a surface for an empty Layer 3.2 mask.")
    # Marching cubes previously scanned the complete RTDOSE array even when a
    # structure occupied only a small bounding box.  Crop with a one-voxel
    # context margin and restore the source-array index offset afterwards.  The
    # resulting surface remains in the same DICOM-LPS coordinate system while
    # interactive CAD generation scales with ROI extent instead of dose extent.
    occupied = np.argwhere(values)
    lower = np.maximum(np.min(occupied, axis=0) - 1, 0)
    upper = np.minimum(np.max(occupied, axis=0) + 2, np.asarray(values.shape))
    slices = tuple(slice(int(start), int(stop)) for start, stop in zip(lower, upper))
    cropped = values[slices]
    step = 2 if cropped.size > 4_000_000 else 1
    vertices, faces, _normals, _samples = marching_cubes(
        np.pad(cropped, 1), level=0.5, step_size=step, allow_degenerate=False,
    )
    vertices += lower.astype(float) - 1.0
    colour = np.asarray([210, 220, 231], dtype=np.uint8)
    return ScalarSurface(
        np.ascontiguousarray(indices_to_lps(vertices, geometry), dtype=np.float32),
        np.ascontiguousarray(faces, dtype=np.uint32),
        np.full(len(vertices), 0.5, dtype=np.float32),
        np.tile(colour, (len(vertices), 1)), 0.5,
    )


def crop_corners_lps(geometry: dict[str, Any]) -> np.ndarray:
    """Return the eight model-crop corner points in DICOM patient LPS."""
    shape = np.asarray(_geometry_arrays(geometry)["shape"], dtype=int)
    points = np.asarray([
        [z, y, x]
        for z in (0, max(int(shape[0]) - 1, 0))
        for y in (0, max(int(shape[1]) - 1, 0))
        for x in (0, max(int(shape[2]) - 1, 0))
    ], dtype=float)
    return indices_to_lps(points, geometry)


def combine_surfaces(surfaces: list[ScalarSurface]) -> ScalarSurface:
    """Combine independent shells while retaining per-vertex scalar and colour data."""
    if not surfaces:
        raise ValueError("No Layer 3.2 surfaces are available to combine.")
    vertices: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    scalars: list[np.ndarray] = []
    colours: list[np.ndarray] = []
    offset = 0
    for surface in surfaces:
        vertices.append(surface.vertices_lps_mm)
        faces.append(surface.faces + offset)
        scalars.append(surface.scalar_values)
        colours.append(surface.rgb)
        offset += len(surface.vertices_lps_mm)
    return ScalarSurface(
        np.ascontiguousarray(np.vstack(vertices), dtype=np.float32),
        np.ascontiguousarray(np.vstack(faces), dtype=np.uint32),
        np.ascontiguousarray(np.concatenate(scalars), dtype=np.float32),
        np.ascontiguousarray(np.vstack(colours), dtype=np.uint8),
        float("nan"),
    )


def _binary_xml(values: np.ndarray) -> str:
    raw = np.ascontiguousarray(values).tobytes()
    return base64.b64encode(struct.pack("<Q", len(raw)) + raw).decode("ascii")


def _compressed_binary_xml(values: np.ndarray, block_size: int = 32768) -> str:
    """Encode VTK's block-compressed inline binary representation."""
    raw = memoryview(np.ascontiguousarray(values)).cast("B")
    blocks = [zlib.compress(raw[start:start + block_size], level=6) for start in range(0, len(raw), block_size)]
    if not blocks:
        blocks = [zlib.compress(b"", level=6)]
    last_size = len(raw) - block_size * (len(blocks) - 1)
    header = struct.pack(
        f"<{3 + len(blocks)}Q", len(blocks), block_size, last_size, *[len(block) for block in blocks],
    )
    return base64.b64encode(header + b"".join(blocks)).decode("ascii")


def write_vti(fields: dict[str, np.ndarray], geometry: dict[str, Any], path: Path) -> None:
    """Write all stored crop fields to VTK ImageData with LPS direction metadata."""
    arrays = {name: np.ascontiguousarray(value) for name, value in fields.items()}
    shape = next(iter(arrays.values())).shape
    if any(value.shape != shape for value in arrays.values()):
        raise ValueError("VTI export requires all Layer 3.2 arrays to share one geometry.")
    values = _geometry_arrays(geometry)
    row, column, normal = values["row_direction"], values["column_direction"], values["normal"]
    direction = [row[0], column[0], normal[0], row[1], column[1], normal[1], row[2], column[2], normal[2]]
    voxel_spacing = values["voxel_spacing_zyx"]
    spacing = [voxel_spacing[2], voxel_spacing[1], voxel_spacing[0]]
    extent = f"0 {shape[2]-1} 0 {shape[1]-1} 0 {shape[0]-1}"
    root = ET.Element(
        "VTKFile", type="ImageData", version="1.0", byte_order="LittleEndian",
        header_type="UInt64", compressor="vtkZLibDataCompressor",
    )
    image = ET.SubElement(root, "ImageData", WholeExtent=extent,
                          Origin=" ".join(map(str, values["origin"])),
                          Spacing=" ".join(map(str, spacing)),
                          Direction=" ".join(map(str, direction)))
    piece = ET.SubElement(image, "Piece", Extent=extent)
    point_data = ET.SubElement(piece, "PointData")
    vtk_types = {"f": "Float32", "d": "Float64", "u": "UInt8", "i": "Int32", "b": "UInt8"}
    for name in sorted(arrays):
        value = arrays[name]
        if value.dtype.kind == "b":
            value = value.astype(np.uint8)
        elif value.dtype.kind == "f":
            value = value.astype(np.float32)
        element = ET.SubElement(point_data, "DataArray", type=vtk_types.get(value.dtype.kind, "Float32"),
                                Name=name, format="binary")
        element.text = _compressed_binary_xml(value)
    ET.SubElement(piece, "CellData")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_vtp(surface: ScalarSurface, path: Path) -> None:
    """Write a coloured scalar PolyData surface in DICOM LPS coordinates."""
    root = ET.Element("VTKFile", type="PolyData", version="1.0", byte_order="LittleEndian", header_type="UInt64")
    poly = ET.SubElement(root, "PolyData")
    piece = ET.SubElement(poly, "Piece", NumberOfPoints=str(len(surface.vertices_lps_mm)),
                          NumberOfPolys=str(len(surface.faces)))
    point_data = ET.SubElement(piece, "PointData", Scalars="iso_value")
    scalar = ET.SubElement(point_data, "DataArray", type="Float32", Name="iso_value", format="binary")
    scalar.text = _binary_xml(surface.scalar_values.astype("<f4"))
    rgb = ET.SubElement(point_data, "DataArray", type="UInt8", Name="RGB", NumberOfComponents="3", format="binary")
    rgb.text = _binary_xml(surface.rgb.astype(np.uint8))
    points = ET.SubElement(piece, "Points")
    coordinates = ET.SubElement(points, "DataArray", type="Float32", NumberOfComponents="3", format="binary")
    coordinates.text = _binary_xml(surface.vertices_lps_mm.astype("<f4"))
    polys = ET.SubElement(piece, "Polys")
    connectivity = ET.SubElement(polys, "DataArray", type="UInt32", Name="connectivity", format="binary")
    connectivity.text = _binary_xml(surface.faces.astype("<u4").reshape(-1))
    offsets = ET.SubElement(polys, "DataArray", type="UInt32", Name="offsets", format="binary")
    offsets.text = _binary_xml((np.arange(len(surface.faces), dtype=np.uint32) + 1) * 3)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_ply(surface: ScalarSurface, path: Path) -> None:
    """Write binary PLY with scalar and RGB values on every vertex."""
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment DICOM patient LPS millimetres\n"
        f"element vertex {len(surface.vertices_lps_mm)}\n"
        "property float x\nproperty float y\nproperty float z\nproperty float iso_value\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        f"element face {len(surface.faces)}\nproperty list uchar uint vertex_indices\nend_header\n"
    ).encode("ascii")
    vertex_records = np.empty(len(surface.vertices_lps_mm), dtype=[
        ("position", "<f4", (3,)), ("scalar", "<f4"), ("rgb", "u1", (3,)),
    ])
    vertex_records["position"] = surface.vertices_lps_mm
    vertex_records["scalar"] = surface.scalar_values
    vertex_records["rgb"] = surface.rgb
    face_records = np.empty(len(surface.faces), dtype=[("count", "u1"), ("indices", "<u4", (3,))])
    face_records["count"] = 3; face_records["indices"] = surface.faces
    with path.open("wb") as handle:
        handle.write(header); handle.write(vertex_records.tobytes()); handle.write(face_records.tobytes())


def write_binary_stl(surface: ScalarSurface, path: Path, label: str) -> None:
    """Write geometry-only binary STL for one selected scalar threshold."""
    header = f"ASCEND Layer 3.2 {label}; DICOM LPS mm; geometry only".encode("ascii", "replace")[:80].ljust(80, b" ")
    with path.open("wb") as handle:
        handle.write(header); handle.write(struct.pack("<I", len(surface.faces)))
        for start in range(0, len(surface.faces), 100_000):
            triangles = np.asarray(surface.vertices_lps_mm[surface.faces[start:start + 100_000]], dtype="<f4")
            normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            lengths = np.linalg.norm(normals, axis=1); valid = lengths > 0; normals[valid] /= lengths[valid, None]
            records = np.zeros(len(triangles), dtype=[
                ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2"),
            ])
            records["normal"] = normals; records["vertices"] = triangles
            handle.write(records.tobytes())


def _aligned(parts: list[bytes], value: bytes) -> tuple[int, int]:
    offset = sum(len(part) for part in parts)
    padding = (-offset) % 4
    if padding:
        parts.append(b"\x00" * padding); offset += padding
    parts.append(value)
    return offset, len(value)


def write_glb(surface: ScalarSurface, path: Path) -> None:
    """Write a portable glTF binary mesh with normalized vertex colours."""
    parts: list[bytes] = []
    position_offset, position_length = _aligned(parts, surface.vertices_lps_mm.astype("<f4").tobytes())
    rgba = np.column_stack((surface.rgb, np.full(len(surface.rgb), 255, dtype=np.uint8)))
    colour_offset, colour_length = _aligned(parts, rgba.tobytes())
    index_offset, index_length = _aligned(parts, surface.faces.astype("<u4").reshape(-1).tobytes())
    binary = b"".join(parts)
    minimum = np.min(surface.vertices_lps_mm, axis=0).tolist(); maximum = np.max(surface.vertices_lps_mm, axis=0).tolist()
    document = {
        "asset": {"version": "2.0", "generator": "ASCEND Layer 3.2", "extras": {
            "coordinateSystem": "DICOM patient LPS", "units": "mm",
        }},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": position_offset, "byteLength": position_length, "target": 34962},
            {"buffer": 0, "byteOffset": colour_offset, "byteLength": colour_length, "target": 34962},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": index_length, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(surface.vertices_lps_mm), "type": "VEC3", "min": minimum, "max": maximum},
            {"bufferView": 1, "componentType": 5121, "count": len(surface.rgb), "type": "VEC4", "normalized": True},
            {"bufferView": 2, "componentType": 5125, "count": int(surface.faces.size), "type": "SCALAR"},
        ],
        "materials": [{"name": "Layer 3.2 scalar heat map", "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.65}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2, "material": 0}]}],
        "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0,
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((-len(json_chunk)) % 4); binary += b"\x00" * ((-len(binary)) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    with path.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, total))
        handle.write(struct.pack("<I4s", len(json_chunk), b"JSON")); handle.write(json_chunk)
        handle.write(struct.pack("<I4s", len(binary), b"BIN\x00")); handle.write(binary)


def write_3mf(surface: ScalarSurface, path: Path) -> None:
    """Write a millimetre-scale coloured 3MF suitable for capable slicers."""
    colours, inverse = np.unique(surface.rgb, axis=0, return_inverse=True)
    model = ET.Element("model", unit="millimeter", lang="en-US", xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02")
    metadata = ET.SubElement(model, "metadata", name="Title"); metadata.text = "ASCEND Layer 3.2 scalar isosurfaces"
    resources = ET.SubElement(model, "resources")
    materials = ET.SubElement(resources, "basematerials", id="2")
    for index, colour in enumerate(colours):
        ET.SubElement(materials, "base", name=f"Scalar colour {index + 1}",
                      displaycolor=f"#{colour[0]:02X}{colour[1]:02X}{colour[2]:02X}FF")
    object_element = ET.SubElement(resources, "object", id="1", type="model")
    mesh = ET.SubElement(object_element, "mesh"); vertices = ET.SubElement(mesh, "vertices")
    for point in surface.vertices_lps_mm:
        ET.SubElement(vertices, "vertex", x=f"{point[0]:.7g}", y=f"{point[1]:.7g}", z=f"{point[2]:.7g}")
    triangles = ET.SubElement(mesh, "triangles")
    for face in surface.faces:
        colour_index = int(inverse[int(face[0])])
        ET.SubElement(triangles, "triangle", v1=str(int(face[0])), v2=str(int(face[1])), v3=str(int(face[2])),
                      pid="2", p1=str(colour_index))
    build = ET.SubElement(model, "build"); ET.SubElement(build, "item", objectid="1")
    model_bytes = ET.tostring(model, encoding="utf-8", xml_declaration=True)
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>'''
    relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>'''
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("3D/3dmodel.model", model_bytes)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "field"


def export_spatial_package(
    fields: dict[str, np.ndarray],
    masks: dict[str, np.ndarray],
    geometry: dict[str, Any],
    selected_field: str,
    folder: str | Path,
    *,
    nonlocal_scaling: float | None = None,
) -> list[Path]:
    """Export full scalar volume and absolute model-consequence surfaces."""
    if selected_field not in fields:
        raise ValueError(f"Layer 3.2 export field is unavailable: {selected_field}")
    target = Path(folder); target.mkdir(parents=True, exist_ok=True)
    consequence_field = "additional_modelled_survival_reduction_percent"
    legacy_compatibility = nonlocal_scaling is None
    if consequence_field not in fields or legacy_compatibility:
        consequence_field = selected_field
    thresholds = (2.5, 5.0, 10.0, 20.0)
    if consequence_field == "additional_modelled_survival_reduction_percent":
        surfaces = consequence_threshold_surfaces(fields[consequence_field], geometry, thresholds)
    else:
        surfaces = multilevel_surfaces(fields[consequence_field], geometry)
    if not surfaces:
        raise ValueError("Layer 3.2 consequence field has no exportable absolute-threshold surface.")
    combined = combine_surfaces(surfaces); safe_field = _safe_name(consequence_field)
    outputs: list[Path] = []
    vti = target / "layer3_2_full_scalar_volume_LPS.vti"
    write_vti({**fields, **{name: np.asarray(mask, dtype=np.uint8) for name, mask in masks.items()}}, geometry, vti); outputs.append(vti)
    for extension, writer in (("vtp", write_vtp), ("ply", write_ply), ("glb", write_glb), ("3mf", write_3mf)):
        path = target / f"{safe_field}_absolute_surfaces.{extension}"; writer(combined, path); outputs.append(path)
    levels: list[dict[str, Any]] = []
    for index, surface in enumerate(surfaces, 1):
        exposure_h = (
            equivalent_exposure_h(surface.level, float(nonlocal_scaling))
            if consequence_field == "additional_modelled_survival_reduction_percent" and nonlocal_scaling
            else None
        )
        h_suffix = f"_H_{exposure_h:.1f}" if exposure_h is not None else ""
        stl = target / f"additional_reduction_{surface.level:g}pct{h_suffix}.stl"
        write_binary_stl(surface, stl, f"{surface.level:g}% additional modelled survival reduction"); outputs.append(stl)
        levels.append({
            "index": index,
            "additional_modelled_survival_reduction_percent": surface.level,
            "equivalent_cumulative_mediator_exposure_h": exposure_h,
            "label": (
                f"{surface.level:g}% additional modelled survival reduction"
                + (f" — H = {exposure_h:.1f}" if exposure_h is not None else "")
            ),
            "stl": stl.name,
            "rgb": surface.rgb[0].tolist(),
        })
    mask_files: dict[str, str] = {}
    for name, mask in sorted(masks.items()):
        if not np.asarray(mask, dtype=bool).any():
            continue
        surface = mask_surface(mask, geometry); path = target / f"{_safe_name(name)}_mask_LPS_mm.stl"
        write_binary_stl(surface, path, f"{name} stored mask"); outputs.append(path); mask_files[name] = path.name
    manifest = target / "layer3_2_spatial_export_manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "ASCEND-L3.2-spatial-export-v2",
        "coordinate_system": "DICOM patient LPS", "units": "mm",
        "selected_field": selected_field,
        "selected_display_field": selected_field,
        "surface_field": consequence_field,
        "stored_field_range": list(scalar_range(fields[consequence_field])),
        "full_scalar_volume": vti.name,
        "coloured_surface_formats": {
            "VTP": f"{safe_field}_absolute_surfaces.vtp", "PLY": f"{safe_field}_absolute_surfaces.ply",
            "GLB": f"{safe_field}_absolute_surfaces.glb", "3MF": f"{safe_field}_absolute_surfaces.3mf",
        },
        "absolute_consequence_surfaces": levels,
        "stl_isosurfaces": levels,
        "anatomical_mask_stl": mask_files,
        "model_crop_geometry": geometry,
        "limitations": [
            "STL contains threshold geometry only and cannot store scalar values or colours.",
            "The exported modelled field is limited to the stored GTV-plus-margin model crop.",
            "Layer 3.2 is provisional and not a clinical outcome or OAR-compliance model.",
            "Intersecting shells are not Boolean-unioned; validate printable geometry in the target slicer.",
        ],
    }, indent=2), encoding="utf-8"); outputs.append(manifest)
    return outputs
