"""Build the developer-facing ASCEND software architecture figure.

The figure deliberately documents software components, integrity controls,
persistence, and delivery. It excludes scientific algorithms and equations.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_STEM = ROOT / "docs" / "figures" / "ascend-software-architecture"

NAVY = "#17324D"
BLUE = "#2E6F9E"
BLUE_LIGHT = "#EAF3F8"
TEAL = "#2D7B78"
TEAL_LIGHT = "#E8F4F2"
GOLD = "#B47821"
GOLD_LIGHT = "#FBF2E2"
PURPLE = "#71558F"
PURPLE_LIGHT = "#F1ECF6"
GREY_700 = "#52606D"
GREY_500 = "#7B8790"
GREY_300 = "#C8D0D6"
GREY_100 = "#F4F6F7"
WHITE = "#FFFFFF"


def rounded_box(
    ax,
    xy,
    width,
    height,
    *,
    facecolor=WHITE,
    edgecolor=GREY_300,
    linewidth=1.2,
    linestyle="-",
    radius=0.12,
    zorder=2,
):
    patch = FancyBboxPatch(
        xy,
        width,
        height,
        boxstyle=f"round,pad=0.02,rounding_size={radius}",
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=linewidth,
        linestyle=linestyle,
        zorder=zorder,
    )
    ax.add_patch(patch)
    return patch


def text_box(
    ax,
    x,
    y,
    width,
    height,
    title,
    subtitle="",
    *,
    code="",
    facecolor=WHITE,
    edgecolor=GREY_300,
    title_color=NAVY,
    title_size=9.0,
    subtitle_size=7.2,
    linestyle="-",
):
    rounded_box(
        ax,
        (x, y),
        width,
        height,
        facecolor=facecolor,
        edgecolor=edgecolor,
        linestyle=linestyle,
    )
    center_x = x + width / 2
    if subtitle and code:
        title_y, subtitle_y, code_y = y + height * 0.70, y + height * 0.43, y + height * 0.16
    elif subtitle:
        title_y, subtitle_y, code_y = y + height * 0.65, y + height * 0.31, None
    else:
        title_y, subtitle_y, code_y = y + height * 0.51, None, None

    ax.text(
        center_x,
        title_y,
        title,
        ha="center",
        va="center",
        fontsize=title_size,
        fontweight="bold",
        color=title_color,
        zorder=3,
    )
    if subtitle_y is not None:
        ax.text(
            center_x,
            subtitle_y,
            subtitle,
            ha="center",
            va="center",
            fontsize=subtitle_size,
            color=GREY_700,
            linespacing=1.25,
            zorder=3,
        )
    if code_y is not None:
        ax.text(
            center_x,
            code_y,
            code,
            ha="center",
            va="center",
            fontsize=6.6,
            family="monospace",
            color=GREY_700,
            zorder=3,
        )


def band(ax, y, height, label, color):
    ax.add_patch(Rectangle((0.25, y), 15.50, height, facecolor=GREY_100, edgecolor="none", zorder=0))
    ax.add_patch(Rectangle((0.25, y), 0.10, height, facecolor=color, edgecolor="none", zorder=1))
    ax.text(
        0.54,
        y + height / 2,
        label.upper(),
        rotation=90,
        ha="center",
        va="center",
        fontsize=7.0,
        fontweight="bold",
        color=color,
        zorder=3,
    )


def arrow(
    ax,
    start,
    end,
    *,
    color=GREY_500,
    linewidth=1.15,
    linestyle="-",
    connectionstyle="arc3",
    mutation_scale=9,
    zorder=1,
):
    patch = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=mutation_scale,
        linewidth=linewidth,
        linestyle=linestyle,
        color=color,
        connectionstyle=connectionstyle,
        shrinkA=2,
        shrinkB=2,
        zorder=zorder,
    )
    ax.add_patch(patch)
    return patch


def bidirectional(ax, start, end, *, color=NAVY, linewidth=1.1):
    arrow(ax, start, end, color=color, linewidth=linewidth)
    offset = 0.11 if start[1] == end[1] else 0
    reverse_start = (end[0], end[1] - offset)
    reverse_end = (start[0], start[1] - offset)
    arrow(ax, reverse_start, reverse_end, color=color, linewidth=linewidth)


def build_figure():
    fig, ax = plt.subplots(figsize=(13.6, 8.2))
    fig.patch.set_facecolor(WHITE)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10.2)
    ax.axis("off")

    ax.text(
        0.30,
        9.91,
        "ASCEND developer architecture",
        ha="left",
        va="center",
        fontsize=17,
        fontweight="bold",
        color=NAVY,
    )
    ax.text(
        0.30,
        9.60,
        "Software components, integrity controls, persistence, and delivery",
        ha="left",
        va="center",
        fontsize=9.5,
        color=GREY_700,
    )

    band(ax, 8.48, 0.84, "External inputs", BLUE)
    band(ax, 7.31, 0.84, "Presentation", PURPLE)
    band(ax, 5.70, 1.30, "Application core", NAVY)
    band(ax, 3.75, 1.66, "Infrastructure", TEAL)
    band(ax, 2.47, 0.98, "Persistence", GOLD)
    band(ax, 0.84, 1.32, "Engineering lifecycle", BLUE)

    input_specs = [
        (1.00, 4.20, "DICOM-RT source", "Study files selected by the user"),
        (5.56, 4.10, "Configuration", "ROI bindings  •  options  •  parameters"),
        (10.02, 4.92, "Reference resources", "TPS/DVH files  •  meshes  •  fixtures"),
    ]
    for x, width, title, subtitle in input_specs:
        text_box(
            ax,
            x,
            8.60,
            width,
            0.58,
            title,
            subtitle,
            facecolor=BLUE_LIGHT,
            edgecolor=BLUE,
            title_size=9.1,
            subtitle_size=7.2,
        )

    adapters = [
        (1.00, 4.10, "Qt desktop GUI", "PySide6 pages, presenters, viewers", "ascend/gui"),
        (5.56, 4.10, "Command-line adapter", "batch operation and automation", "ascend/cli.py"),
        (10.12, 4.82, "Browser adapter", "optional localhost client", "ascend/web"),
    ]
    for x, width, title, subtitle, code in adapters:
        text_box(
            ax,
            x,
            7.43,
            width,
            0.58,
            title,
            subtitle,
            code=code,
            facecolor=PURPLE_LIGHT,
            edgecolor=PURPLE,
            title_size=8.9,
            subtitle_size=7.0,
        )

    # A shared boundary means every adapter can submit any supported input.
    for input_x in (3.10, 7.61, 12.48):
        ax.plot([input_x, input_x], [8.58, 8.30], color=BLUE, linewidth=1.05, zorder=1)
    ax.plot([3.10, 12.48], [8.30, 8.30], color=BLUE, linewidth=1.05, zorder=1)
    for adapter_x in (3.05, 7.61, 12.53):
        arrow(ax, (adapter_x, 8.30), (adapter_x, 8.03), color=BLUE, linewidth=1.05)

    text_box(
        ax,
        1.00,
        5.96,
        6.40,
        0.79,
        "ApplicationController",
        "case lifecycle  •  command routing  •  dependency invalidation  •  export coordination",
        code="ascend/app/controller.py",
        facecolor=WHITE,
        edgecolor=NAVY,
        title_size=10.2,
        subtitle_size=7.3,
    )
    text_box(
        ax,
        7.78,
        5.96,
        3.20,
        0.79,
        "Runtime state",
        "stage  •  message  •  busy/error state",
        code="ascend/app/state.py",
        facecolor=WHITE,
        edgecolor=NAVY,
        title_size=8.9,
        subtitle_size=7.0,
    )
    text_box(
        ax,
        11.35,
        5.96,
        3.60,
        0.79,
        "Domain contracts",
        "ASCENDCase  •  configuration  •  run/status records",
        code="ascend/models",
        facecolor=WHITE,
        edgecolor=NAVY,
        title_size=8.9,
        subtitle_size=7.0,
    )
    rounded_box(ax, (1.00, 5.76), 13.95, 0.14, facecolor=NAVY, edgecolor=NAVY, radius=0.04)
    ax.text(
        7.98,
        5.83,
        "APPLICATION SERVICE INTERFACE — controller invokes services; interfaces only submit commands and present stored results",
        ha="center",
        va="center",
        fontsize=6.9,
        fontweight="bold",
        color=WHITE,
        zorder=3,
    )

    for x in (3.05, 7.61, 12.53):
        arrow(ax, (x, 7.41), (4.20, 6.77), color=PURPLE, linewidth=1.1)
    bidirectional(ax, (7.40, 6.39), (7.78, 6.39), color=NAVY)
    bidirectional(ax, (10.98, 6.39), (11.35, 6.39), color=NAVY)

    infrastructure = [
        (
            1.00,
            3.04,
            "DICOM gateway",
            "header discovery  •  UID-chain selection\nROI identity resolution  •  metadata geometry",
            "ascend/dicom",
        ),
        (
            4.29,
            3.28,
            "Integrity and provenance",
            "SHA-256 file hash  •  canonical configuration hash\nrun identifier  •  version and Git commit",
            "ascend/validation/provenance.py",
        ),
        (
            7.82,
            3.27,
            "Cache and publication",
            "content-addressed cache key  •  manifest verification\nstaging directory  •  atomic rename",
            "ascend/layer1/cache.py",
        ),
        (
            11.34,
            3.61,
            "Output adapters",
            "view models and 2D/3D rendering\nJSON/CSV/archive export from stored results",
            "ascend/visualization + ascend/reporting",
        ),
    ]
    for x, width, title, subtitle, code in infrastructure:
        text_box(
            ax,
            x,
            3.94,
            width,
            1.17,
            title,
            subtitle,
            code=code,
            facecolor=TEAL_LIGHT,
            edgecolor=TEAL,
            title_size=8.8,
            subtitle_size=6.9,
        )

    for x in (2.52, 5.93, 9.46, 13.15):
        arrow(ax, (4.20, 5.74), (x, 5.13), color=NAVY, linewidth=1.0)
    arrow(ax, (7.57, 4.53), (7.80, 4.53), color=TEAL, linewidth=1.1)
    arrow(ax, (11.09, 4.53), (11.32, 4.53), color=TEAL, linewidth=1.1)

    text_box(
        ax,
        1.00,
        2.67,
        4.42,
        0.59,
        "Relocation-safe case record",
        "ascend_case.json  •  configuration  •  statuses  •  artifact paths",
        code="ascend/models/case.py",
        facecolor=GOLD_LIGHT,
        edgecolor=GOLD,
        title_size=8.6,
        subtitle_size=6.9,
    )
    text_box(
        ax,
        5.72,
        2.67,
        4.05,
        0.59,
        "Case workspace",
        "raw/  •  validated/  •  derived/  •  cache/  •  logs/",
        code="case_root",
        facecolor=GOLD_LIGHT,
        edgecolor=GOLD,
        title_size=8.6,
        subtitle_size=7.0,
    )
    text_box(
        ax,
        10.07,
        2.67,
        4.88,
        0.59,
        "Portable deliverables",
        "exports/  •  JSON schema payloads  •  CSV derivatives  •  checksums",
        code="ascend/reporting/export.py",
        facecolor=GOLD_LIGHT,
        edgecolor=GOLD,
        title_size=8.6,
        subtitle_size=6.9,
    )

    arrow(ax, (3.21, 3.92), (3.21, 3.28), color=GOLD, linestyle="--", linewidth=1.0)
    arrow(ax, (5.93, 3.92), (4.65, 3.28), color=GOLD, linestyle="--", linewidth=1.0)
    arrow(ax, (9.46, 3.92), (7.74, 3.28), color=GOLD, linestyle="--", linewidth=1.0)
    arrow(ax, (13.15, 3.92), (12.51, 3.28), color=GOLD, linestyle="--", linewidth=1.0)

    lifecycle = [
        (1.00, 2.70, "Source repository", "Python package  •  tests  •  schemas", "pyproject.toml + Git"),
        (3.98, 2.80, "Continuous integration", "compile  •  Ruff  •  mypy  •  security", ".github/workflows/tests.yml"),
        (7.08, 2.95, "Cross-platform verification", "Linux  •  Windows  •  macOS  •  Python matrix", "pytest + validation harnesses"),
        (10.33, 2.15, "Package build", "wheel + source archive", "python -m build"),
        (12.78, 2.17, "Tagged release", "checksums + immutable assets", "release.yml"),
    ]
    for x, width, title, subtitle, code in lifecycle:
        text_box(
            ax,
            x,
            1.17,
            width,
            0.71,
            title,
            subtitle,
            code=code,
            facecolor=BLUE_LIGHT,
            edgecolor=BLUE,
            title_size=8.2,
            subtitle_size=6.7,
        )
    for start, end in [
        ((3.70, 1.52), (3.96, 1.52)),
        ((6.78, 1.52), (7.06, 1.52)),
        ((10.03, 1.52), (10.31, 1.52)),
        ((12.48, 1.52), (12.76, 1.52)),
    ]:
        arrow(ax, start, end, color=BLUE, linewidth=1.2)

    arrow(ax, (1.02, 0.49), (1.47, 0.49), color=NAVY, linewidth=1.3)
    ax.text(1.58, 0.49, "runtime call or data flow", ha="left", va="center", fontsize=7.3, color=GREY_700)
    arrow(ax, (4.20, 0.49), (4.65, 0.49), color=GOLD, linewidth=1.1, linestyle="--")
    ax.text(4.76, 0.49, "persistence or integrity record", ha="left", va="center", fontsize=7.3, color=GREY_700)
    ax.text(
        14.95,
        0.49,
        "Boundary rule: GUI, CLI, and browser code do not own processing logic.",
        ha="right",
        va="center",
        fontsize=7.4,
        fontweight="bold",
        color=NAVY,
    )

    plt.subplots_adjust(left=0.015, right=0.985, top=0.985, bottom=0.02)
    return fig


def main() -> None:
    OUTPUT_STEM.parent.mkdir(parents=True, exist_ok=True)
    fig = build_figure()
    document_metadata = {
        "Title": "ASCEND developer architecture",
        "Author": "ASCEND project",
        "Subject": "Software component architecture, integrity, persistence, and delivery",
        "Keywords": "ASCEND, software architecture, provenance, SHA-256, GUI, CI",
    }
    fig.savefig(
        OUTPUT_STEM.with_suffix(".svg"),
        bbox_inches="tight",
        facecolor=WHITE,
        metadata={"Title": document_metadata["Title"], "Description": document_metadata["Subject"]},
    )
    fig.savefig(
        OUTPUT_STEM.with_suffix(".pdf"),
        bbox_inches="tight",
        facecolor=WHITE,
        metadata=document_metadata,
    )
    fig.savefig(
        OUTPUT_STEM.with_suffix(".png"),
        dpi=300,
        bbox_inches="tight",
        facecolor=WHITE,
        metadata={"Title": document_metadata["Title"], "Description": document_metadata["Subject"]},
    )
    plt.close(fig)


if __name__ == "__main__":
    main()
