"""Deterministic, collision-free colours for biological anatomy overlays."""

from __future__ import annotations

import colorsys
from collections.abc import Iterable


_ROLE_COLOURS = {
    "Region: Whole GTV": "#f4d77a",
    "Region: Vertices": "#20c7df",
    "Region: Valleys": "#8067e8",
    "Region: Other GTV": "#7792e6",
}


def _oar_colour(index: int) -> str:
    """Return one reproducible high-contrast RGB colour for an OAR index."""
    # The golden-angle sequence avoids neighbouring hues. Alternating
    # saturation/value levels expands the collision-free space without
    # assigning the same RGB triplet to two ordinary clinical OAR sets.
    hue = (0.031 + index * 0.6180339887498949) % 1.0
    saturation = (0.82, 0.67, 0.91)[index % 3]
    value = (0.96, 0.84, 0.91)[(index // 3) % 3]
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def anatomy_colour_map(names: Iterable[str]) -> dict[str, str]:
    """Assign stable role colours and a unique colour to every named OAR."""
    ordered = sorted(dict.fromkeys(map(str, names)))
    oars = [name for name in ordered if name.startswith("OAR:")]
    result = {name: _ROLE_COLOURS.get(name, "#8fb5ce") for name in ordered}
    used = set(_ROLE_COLOURS.values())
    for index, name in enumerate(oars):
        candidate_index = index
        colour = _oar_colour(candidate_index)
        while colour in used:
            candidate_index += len(oars) + 1
            colour = _oar_colour(candidate_index)
        result[name] = colour
        used.add(colour)
    return result


def anatomy_colour(name: str, names: Iterable[str]) -> str:
    """Resolve a single colour using the complete scene name set."""
    return anatomy_colour_map(names).get(str(name), "#8fb5ce")
