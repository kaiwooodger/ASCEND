"""Versioned export adapters for stored Layer 2.2 extension results."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _serialise(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def write_rows(path: Path, rows: list[dict[str, Any]]) -> Path | None:
    if not rows:
        return None
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: _serialise(row.get(key)) for key in fields} for row in rows])
    return path


def export_layer22_extensions(result: dict[str, Any], destination: str | Path) -> list[Path]:
    """Export already-calculated extension values without recalculation."""
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    extensions = result.get("layer2_2_extensions") or {}
    vertex = extensions.get("vertex_profiles") or {}
    saddle = extensions.get("saddle_graph") or {}
    created: list[Path] = []
    if vertex:
        path = output / "layer2_2_vertex_profiles.json"
        path.write_text(json.dumps(vertex, indent=2), encoding="utf-8")
        created.append(path)
        summary_path = write_rows(output / "layer2_2_vertex_profiles.csv", list(vertex.get("vertices") or []))
        if summary_path:
            created.append(summary_path)
        long_rows = [
            {"vertex_id": vertex_id, **shell}
            for vertex_id, shells in (vertex.get("profiles") or {}).items()
            for shell in shells
        ]
        profile_path = write_rows(output / "layer2_2_vertex_radial_profiles.csv", long_rows)
        if profile_path:
            created.append(profile_path)
    if saddle:
        path = output / "layer2_2_saddle_graph.json"
        path.write_text(json.dumps(saddle, indent=2), encoding="utf-8")
        created.append(path)
        edge_rows = [{key: value for key, value in edge.items() if key != "saddle_path_xyz_mm"} for edge in saddle.get("edges", [])]
        edge_path = write_rows(output / "layer2_2_saddle_edges.csv", edge_rows)
        if edge_path:
            created.append(edge_path)
        path_records = [
            {
                "edge_id": edge.get("edge_id"), "path_index": path_index,
                "x_lps_mm": point[0], "y_lps_mm": point[1], "z_lps_mm": point[2],
            }
            for edge in saddle.get("edges", [])
            for path_index, point in enumerate(edge.get("saddle_path_xyz_mm") or [])
        ]
        paths_file = write_rows(output / "layer2_2_saddle_paths.csv", path_records)
        if paths_file:
            created.append(paths_file)
    if extensions:
        provenance_path = output / "layer2_2_extension_provenance.json"
        provenance_path.write_text(json.dumps({
            key: {"schema_version": value.get("schema_version"), "algorithm_version": value.get("algorithm_version"),
                  "configuration": value.get("configuration"), "provenance": value.get("provenance")}
            for key, value in extensions.items()
            if isinstance(value, dict)
        }, indent=2), encoding="utf-8")
        created.append(provenance_path)
    return created
