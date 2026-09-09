#!/usr/bin/env python3
"""Generate the publication figure for the locked ASCEND midpoint-iPVDR method.

Outputs a vector PDF and SVG plus a 600 dpi PNG preview.  Geometry and values
are schematic; the mathematical definitions match the ASCEND Layer 2.2 source.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, FancyArrowPatch, PathPatch, Rectangle
from matplotlib.path import Path as MplPath


OUT_DIR = Path(__file__).resolve().parent / "figures"
STEM = "ascend-ipvdr-algorithm-publication"

# Okabe-Ito colour-blind-safe palette. Meaning is also carried by labels,
# outlines, hatching and marker shapes so the figure survives greyscale.
BLUE = "#0072B2"
ORANGE = "#D55E00"
TEAL = "#009E73"
BLACK = "#222222"
MID = "#777777"
LIGHT = "#D9D9D9"
PALE_BLUE = "#DCEEF7"
PALE_ORANGE = "#FCE8DE"
PALE_TEAL = "#DDF3EC"


def setup_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "font.size": 8.0,
            "axes.linewidth": 0.65,
            "lines.linewidth": 1.0,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "mathtext.fontset": "dejavusans",
            "savefig.facecolor": "white",
        }
    )


def panel_label(ax: plt.Axes, letter: str, title: str) -> None:
    ax.text(
        0.01,
        0.985,
        f"({letter})",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        fontweight="bold",
        color=BLACK,
    )
    ax.text(
        0.11,
        0.985,
        title,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.6,
        fontweight="bold",
        color=BLACK,
    )


def gtv_patch() -> PathPatch:
    vertices = np.array(
        [
            (0.45, 1.05),
            (0.25, 2.30),
            (0.85, 3.55),
            (2.20, 4.10),
            (3.85, 3.85),
            (5.20, 3.10),
            (5.45, 1.85),
            (4.55, 0.75),
            (2.95, 0.42),
            (1.35, 0.55),
            (0.45, 1.05),
        ]
    )
    codes = [MplPath.MOVETO] + [MplPath.CURVE3] * (len(vertices) - 2) + [MplPath.CLOSEPOLY]
    return PathPatch(
        MplPath(vertices, codes),
        facecolor="#F6F6F6",
        edgecolor=MID,
        linewidth=1.0,
        linestyle=(0, (4, 2)),
        zorder=0,
    )


def nearest_neighbour_edges(points: np.ndarray) -> list[tuple[int, int]]:
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    edges: set[tuple[int, int]] = set()
    for i in range(len(points)):
        d_min = distances[i].min()
        for j in np.flatnonzero(np.isclose(distances[i], d_min, atol=1.0e-9)):
            edges.add(tuple(sorted((i, int(j)))))
    return sorted(edges)


def draw_graph_panel(ax: plt.Axes) -> None:
    panel_label(ax, "a", "Graph construction")
    ax.add_patch(gtv_patch())
    ax.text(0.62, 3.45, "GTV", color=MID, fontweight="bold")

    points = np.array(
        [
            (1.25, 2.65),
            (2.55, 3.15),
            (4.05, 3.00),
            (1.65, 1.35),
            (3.15, 1.75),
            (4.50, 1.35),
            (3.45, 0.75),
        ]
    )
    edges = nearest_neighbour_edges(points)
    highlighted = (1, 4)

    for i, j in edges:
        colour = ORANGE if (i, j) == highlighted else BLACK
        width = 2.0 if (i, j) == highlighted else 0.9
        zorder = 3 if (i, j) == highlighted else 1
        ax.plot(*zip(points[i], points[j]), color=colour, lw=width, zorder=zorder)

    for index, (x, y) in enumerate(points, start=1):
        ax.add_patch(
            Circle(
                (x, y),
                0.28,
                facecolor=PALE_BLUE,
                edgecolor=BLUE,
                linewidth=1.2,
                hatch="///",
                zorder=4,
            )
        )
        ax.plot(x, y, marker="o", ms=2.6, color=BLACK, zorder=5)
        ax.text(x, y, str(index), ha="center", va="center", fontsize=7.0, color=BLACK, zorder=6)

    ax.annotate(
        "edge $e_{ij}$",
        xy=(2.84, 2.43),
        xytext=(1.70, 3.72),
        arrowprops=dict(arrowstyle="-", lw=0.8, color=ORANGE),
        fontsize=7.1,
        color=ORANGE,
    )
    ax.text(
        2.85,
        0.05,
        "Tied 3D Euclidean neighbours retained",
        ha="center",
        va="bottom",
        fontsize=7.1,
        color=BLACK,
    )
    ax.set_xlim(0, 5.7)
    ax.set_ylim(0, 4.4)
    ax.set_aspect("equal")
    ax.axis("off")


def draw_edge_panel(ax: plt.Axes) -> None:
    panel_label(ax, "b", "Edge calculation")
    left = np.array([0.85, 2.95])
    right = np.array([5.35, 2.95])
    mid = (left + right) / 2

    # Edge and high-dose vertex masks.
    ax.plot([left[0], right[0]], [left[1], right[1]], color=BLACK, lw=1.2, zorder=1)
    for centre, label in [
        (left, "$\\mathbf{c}_i;\;D_{50\\%,i}$"),
        (right, "$\\mathbf{c}_j;\;D_{50\\%,j}$"),
    ]:
        ax.add_patch(
            Circle(
                centre,
                0.55,
                facecolor=PALE_BLUE,
                edgecolor=BLUE,
                linewidth=1.3,
                hatch="///",
                zorder=3,
            )
        )
        ax.plot(*centre, marker="o", ms=3.0, color=BLACK, zorder=4)
        ax.text(centre[0], centre[1] + 0.78, label, ha="center", fontsize=7.5, fontweight="bold")

    # Midpoint sphere is shown as a circular 2-D section through the 3-D sphere.
    sphere = Circle(mid, 0.69, facecolor=PALE_ORANGE, edgecolor=ORANGE, linewidth=1.4, zorder=2)
    ax.add_patch(sphere)
    ax.plot(*mid, marker="+", ms=7, mew=1.2, color=ORANGE, zorder=5)
    ax.text(mid[0], 3.73, "$S_{ij}\;(r=3\\,\\mathrm{mm})$", ha="center", color=ORANGE, fontsize=7.5, fontweight="bold")
    ax.text(mid[0], 2.02, "$\\mathbf{m}_{ij}=(\\mathbf{c}_i+\\mathbf{c}_j)/2$", ha="center", fontsize=7.5)

    # Native-voxel centres in the sampling sphere. Squares distinguish the
    # sampled grid from the continuous sphere boundary.
    offsets = np.array(
        [
            (-0.42, -0.36),
            (0.00, -0.36),
            (0.42, -0.36),
            (-0.42, 0.00),
            (0.00, 0.00),
            (0.42, 0.00),
            (-0.42, 0.36),
            (0.00, 0.36),
            (0.42, 0.36),
        ]
    )
    for dx, dy in offsets:
        ax.add_patch(
            Rectangle(
                (mid[0] + dx - 0.07, mid[1] + dy - 0.07),
                0.14,
                0.14,
                facecolor=ORANGE,
                edgecolor="white",
                linewidth=0.35,
                zorder=4,
            )
        )

    formula = (
        "$D_{\\mathrm{peak},ij}=\\frac{D_{50\\%,i}+D_{50\\%,j}}{2}$"
        "\n"
        "$D_{\\mathrm{valley},ij}=D_{50\\%}(S_{ij})$"
        "\n"
        "$\\mathrm{iPVDR}_{ij}=D_{\\mathrm{peak},ij}/D_{\\mathrm{valley},ij}$"
    )
    ax.text(
        3.1,
        0.82,
        formula,
        ha="center",
        va="center",
        fontsize=7.5,
        linespacing=1.25,
        bbox=dict(boxstyle="round,pad=0.32", fc="white", ec=TEAL, lw=1.0),
    )
    ax.set_xlim(0, 6.2)
    ax.set_ylim(0, 4.4)
    ax.set_aspect("equal")
    ax.axis("off")


def draw_aggregate_panel(ax: plt.Axes) -> None:
    panel_label(ax, "c", "Plan aggregation")
    valid_x = np.array([0.95, 1.55, 2.05, 2.55, 3.10, 3.75, 4.45])
    y = 2.66

    ax.annotate(
        "valid edge values",
        xy=(2.55, y),
        xytext=(2.55, 3.52),
        ha="center",
        fontsize=7.4,
        color=BLACK,
        arrowprops=dict(arrowstyle="-", color=MID, lw=0.7),
    )
    ax.plot([0.65, 4.75], [y, y], color=BLACK, lw=0.8)
    ax.scatter(valid_x, np.full_like(valid_x, y), s=28, facecolor=PALE_TEAL, edgecolor=TEAL, linewidth=1.0, zorder=3)
    for k, x in enumerate(valid_x, start=1):
        ax.text(x, y - 0.35, f"$e_{k}$", ha="center", va="top", fontsize=7.0)

    q1, median, q3 = valid_x[1], valid_x[3], valid_x[5]
    ax.plot([q1, q3], [2.04, 2.04], color=TEAL, lw=5.0, solid_capstyle="butt", alpha=0.25)
    ax.plot([q1, q3], [2.04, 2.04], color=TEAL, lw=0.9)
    ax.plot([median, median], [1.84, 2.88], color=TEAL, lw=1.5)
    ax.text(q1, 1.75, "$Q_1$", ha="center", va="top", fontsize=7.2)
    ax.text(q3, 1.75, "$Q_3$", ha="center", va="top", fontsize=7.2)
    ax.text(median, 3.00, "median", ha="center", va="bottom", fontsize=7.5, color=TEAL, fontweight="bold")

    ax.scatter([4.55], [1.35], marker="x", s=38, color=MID, linewidth=1.3)
    ax.text(4.20, 1.35, "excluded edge", ha="right", va="center", fontsize=7.2, color=MID)
    ax.text(
        2.75,
        0.42,
        "$\\mathrm{iPVDR}_{\\mathrm{plan}}=\\operatorname{median}\\{\\mathrm{iPVDR}_{ij}\\}$\n"
        "$(i,j)\\in E_{\\mathrm{valid}}$",
        ha="center",
        va="center",
        fontsize=8.0,
        linespacing=1.05,
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=TEAL, lw=1.1),
    )
    ax.set_xlim(0, 5.8)
    ax.set_ylim(0, 4.4)
    ax.set_aspect("equal")
    ax.axis("off")


def add_flow_arrow(fig: plt.Figure, x0: float, x1: float) -> None:
    arrow = FancyArrowPatch(
        (x0, 0.525),
        (x1, 0.525),
        transform=fig.transFigure,
        arrowstyle="-|>",
        mutation_scale=9,
        lw=0.8,
        color=MID,
        clip_on=False,
    )
    fig.add_artist(arrow)


def main() -> None:
    setup_style()
    # 15 cm wide: IOP's nominal double-column figure width.  Text is 8--9 pt
    # at final size, within the publisher's recommended 8--12 pt range.
    fig = plt.figure(figsize=(15.0 / 2.54, 5.0 / 2.54), constrained_layout=False)
    grid = fig.add_gridspec(1, 3, width_ratios=[1.02, 1.10, 0.92], wspace=0.18)
    axes = [fig.add_subplot(grid[0, index]) for index in range(3)]
    draw_graph_panel(axes[0])
    draw_edge_panel(axes[1])
    draw_aggregate_panel(axes[2])
    fig.subplots_adjust(left=0.030, right=0.982, top=0.985, bottom=0.02)
    add_flow_arrow(fig, 0.329, 0.350)
    add_flow_arrow(fig, 0.687, 0.708)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_metadata = {
        "Title": "ASCEND Layer 2.2 midpoint-iPVDR algorithm",
        "Author": "ASCEND-LRT project",
        "Subject": "Publication schematic; not patient data",
        "Keywords": "ASCEND, iPVDR, lattice radiotherapy, nearest-neighbour graph",
    }
    svg_metadata = {
        "Title": "ASCEND Layer 2.2 midpoint-iPVDR algorithm",
        "Description": "Publication schematic; not patient data",
        "Creator": "ASCEND-LRT project",
    }
    png_metadata = {
        "Title": "ASCEND Layer 2.2 midpoint-iPVDR algorithm",
        "Author": "ASCEND-LRT project",
        "Description": "Publication schematic; not patient data",
    }
    fig.savefig(OUT_DIR / f"{STEM}.pdf", metadata=pdf_metadata)
    fig.savefig(OUT_DIR / f"{STEM}.svg", metadata=svg_metadata)
    fig.savefig(OUT_DIR / f"{STEM}.png", dpi=600, metadata=png_metadata)
    plt.close(fig)


if __name__ == "__main__":
    main()
