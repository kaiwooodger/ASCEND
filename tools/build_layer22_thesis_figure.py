"""Build the unified thesis figure for ASCEND Layer 2.2 iPVDR and saddle analysis."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Ellipse


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output" / "pdf"
PDF_PATH = OUTPUT_DIR / "ASCEND_Layer22_iPVDR_Saddle_Thesis_Figure.pdf"
PNG_PATH = OUTPUT_DIR / "ASCEND_Layer22_iPVDR_Saddle_Thesis_Figure.png"

NAVY = "#16324F"
BLUE = "#1667A8"
TEAL = "#008C95"
ORANGE = "#D66A00"
RED = "#C9252D"
GREY = "#5E6B75"


def _style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "mathtext.fontset": "stix",
            "font.size": 9.2,
            "axes.edgecolor": "#9AA7B1",
            "axes.linewidth": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )


def _spatial_field(ax: mpl.axes.Axes) -> dict[str, np.ndarray]:
    ci = np.array([-20.0, -1.5])
    cj = np.array([15.0, 3.0])
    midpoint = 0.5 * (ci + cj)

    x = np.linspace(-32.0, 28.0, 520)
    y = np.linspace(-13.0, 16.0, 300)
    xx, yy = np.meshgrid(x, y)
    dose = (
        2.0
        + 21.0 * np.exp(-0.5 * (((xx - ci[0]) / 6.2) ** 2 + ((yy - ci[1]) / 5.2) ** 2))
        + 15.7 * np.exp(-0.5 * (((xx - cj[0]) / 7.0) ** 2 + ((yy - cj[1]) / 5.8) ** 2))
    )
    dose /= float(np.max(dose))
    gtv = (((xx + 2.5) / 30.0) ** 2 + ((yy - 1.0) / 13.5) ** 2) <= 1.0
    field = np.ma.masked_where(~gtv, dose)
    levels = np.linspace(0.08, 1.0, 13)
    contours = ax.contourf(xx, yy, field, levels=levels, cmap="turbo", alpha=0.80, antialiased=True, zorder=1)
    ax.contour(xx, yy, field, levels=levels[1::2], colors="white", linewidths=0.55, alpha=0.46, zorder=2)
    ax.add_patch(Ellipse((-2.5, 1.0), 60.0, 27.0, fill=False, edgecolor="#667681", lw=1.25, zorder=5))

    graph_nodes = {
        "V01": np.array([-28.0, 7.0]),
        "V02": np.array([-12.0, 13.0]),
        "V03": np.array([6.0, 13.0]),
        "V04": np.array([25.0, 7.0]),
        "V05": np.array([24.0, -7.0]),
        "V06": np.array([-10.0, -11.0]),
        "V07": np.array([-28.0, -7.0]),
    }
    graph_edges = [("V01", "V02"), ("V02", "V03"), ("V03", "V04"), ("V04", "V05"), ("V05", "V06"), ("V06", "V07"), ("V07", "V01")]
    for first, second in graph_edges:
        segment = np.vstack([graph_nodes[first], graph_nodes[second]])
        ax.plot(*segment.T, color="#72828D", lw=0.85, alpha=0.42, zorder=4)
    for name, point in graph_nodes.items():
        ax.scatter(*point, s=30, c="#5D91B5", edgecolors="white", linewidths=0.55, alpha=0.75, zorder=6)
        ax.text(*point, name, fontsize=5.4, color="white", ha="center", va="center", zorder=7)

    t = np.linspace(0.0, 1.0, 180)
    straight = ci[None, :] * (1.0 - t[:, None]) + cj[None, :] * t[:, None]
    path = straight.copy()
    path[:, 1] += 2.8 * np.sin(np.pi * t) - 0.8 * np.sin(2.0 * np.pi * t)
    saddle_t = 0.45
    saddle_index = int(np.argmin(np.abs(t - saddle_t)))
    saddle = path[saddle_index]

    direction = cj - ci
    normal = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction)
    for sign in (-1.0, 1.0):
        offset = straight + sign * 3.0 * normal[None, :]
        ax.plot(*offset.T, color=TEAL, lw=0.8, alpha=0.74, zorder=8)
    ax.add_patch(Circle(ci, 3.0, facecolor="none", edgecolor=TEAL, lw=0.8, alpha=0.74, zorder=8))
    ax.add_patch(Circle(cj, 3.0, facecolor="none", edgecolor=TEAL, lw=0.8, alpha=0.74, zorder=8))

    ax.plot(*straight.T, color="#344650", lw=1.7, ls=(0, (4, 2.4)), zorder=10)
    ax.plot(*path.T, color=ORANGE, lw=2.5, zorder=11)
    for centre in (ci, cj):
        ax.add_patch(Circle(centre, 2.4, facecolor=RED, edgecolor="white", lw=1.0, alpha=0.87, zorder=12))
    for centre, colour, linestyle in ((midpoint, TEAL, "-"), (saddle, ORANGE, "--")):
        ax.add_patch(Circle(centre, 3.0, facecolor="white", alpha=0.22, edgecolor=colour, lw=1.55, linestyle=linestyle, zorder=13))
        ax.scatter(*centre, s=48, c=colour, edgecolors="white", linewidths=0.8, zorder=14)

    ax.annotate(r"$D_{50}(\mathrm{VTV}_{H,i})$", xy=ci, xytext=(-27.5, -9.8), color=NAVY,
                arrowprops={"arrowstyle": "->", "color": NAVY, "lw": 1.0})
    ax.annotate(r"$D_{50}(\mathrm{VTV}_{H,j})$", xy=cj, xytext=(18.5, 11.2), ha="center", color=NAVY,
                arrowprops={"arrowstyle": "->", "color": NAVY, "lw": 1.0})
    ax.annotate(r"geometric midpoint $\mathbf{m}_{ij}$" + "\n" + r"$S_m=B(\mathbf{m}_{ij},3\,\mathrm{mm})$", xy=midpoint,
                xytext=(-8.0, -8.6), ha="center", color=TEAL,
                arrowprops={"arrowstyle": "->", "color": TEAL, "lw": 1.0})
    ax.annotate(r"dose saddle $\mathbf{s}_{ij}$" + "\n" + r"$S_s=B(\mathbf{s}_{ij},3\,\mathrm{mm})$", xy=saddle,
                xytext=(4.2, 10.8), ha="center", color=ORANGE,
                arrowprops={"arrowstyle": "->", "color": ORANGE, "lw": 1.0})
    ax.text(-29.0, 10.5, r"validated $\mathrm{GTV}$", color=GREY,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 1.0})
    ax.text(-1.5, 14.9, r"$G=(\mathcal{V},\mathcal{E}),\quad e_{ij}\in\mathcal{E}_{\mathrm{valid}}$", color=NAVY, fontsize=10.2, ha="center")
    ax.text(-4.0, 6.6, r"corridor $C_{ij}$, radius $R_c=3\,\mathrm{mm}$", color=TEAL, fontsize=8.2, ha="center")
    ax.text(0.0, 2.0, r"$d_{ij}=\|\mathbf{c}_j-\mathbf{c}_i\|_2$", color="#344650", fontsize=8.5, rotation=7, ha="center")

    cax = ax.inset_axes([0.555, 0.657, 0.010, 0.218])
    cb = plt.colorbar(contours, cax=cax)
    cb.set_label(r"synthetic $D/D_{\max}$", fontsize=7.1)
    cb.set_ticks([0.1, 0.5, 0.9])
    cb.ax.tick_params(labelsize=6.8, length=2)
    return {"ci": ci, "cj": cj, "midpoint": midpoint, "saddle": saddle}


def _dose_trace(ax: mpl.axes.Axes, points: dict[str, np.ndarray]) -> None:
    ell = np.linspace(0.0, 1.0, 900)
    dose = (
        2.2
        + 20.5 * np.exp(-0.5 * ((ell - 0.075) / 0.125) ** 2)
        + 15.3 * np.exp(-0.5 * ((ell - 0.91) / 0.165) ** 2)
        + 0.30 * ell
    )
    x_profile = points["ci"][0] + (points["cj"][0] - points["ci"][0]) * ell
    y_base = -25.0
    y_profile = y_base + 10.7 * dose / 25.0
    saddle_l, midpoint_l = 0.45, 0.50
    saddle_x = float(points["ci"][0] + (points["cj"][0] - points["ci"][0]) * saddle_l)
    midpoint_x = float(points["ci"][0] + (points["cj"][0] - points["ci"][0]) * midpoint_l)
    saddle_index = int(np.argmin(np.abs(ell - saddle_l)))
    midpoint_index = int(np.argmin(np.abs(ell - midpoint_l)))

    ax.fill_between(x_profile, y_base, y_profile, color="#DCEAF3", alpha=0.78, zorder=2)
    ax.plot(x_profile, y_profile, color=NAVY, lw=2.0, zorder=4)
    ax.plot([points["ci"][0], points["cj"][0]], [y_base, y_base], color="#7A8993", lw=0.8)
    for spatial_point, xp, colour, linestyle in (
        (points["ci"], points["ci"][0], BLUE, ":"),
        (points["saddle"], saddle_x, ORANGE, "-"),
        (points["midpoint"], midpoint_x, TEAL, "--"),
        (points["cj"], points["cj"][0], BLUE, ":"),
    ):
        trace_top = float(np.interp(xp, x_profile, y_profile))
        ax.plot([spatial_point[0], xp], [spatial_point[1] - 3.2, trace_top], color=colour, lw=0.75, ls=linestyle, alpha=0.62, zorder=3)

    ax.scatter(saddle_x, y_profile[saddle_index], s=43, c=ORANGE, edgecolors="white", linewidths=0.8, zorder=6)
    ax.scatter(midpoint_x, y_profile[midpoint_index], s=38, c=TEAL, edgecolors="white", linewidths=0.8, zorder=6)
    ax.text(points["ci"][0], -13.7, r"$D(\gamma_{ij}^{*}(\ell))$", color=NAVY, fontsize=9.5)
    ax.text(points["ci"][0], -26.6, r"$\ell=0$", color=BLUE, ha="center", fontsize=7.8)
    ax.text(points["cj"][0], -26.6, r"$\ell=1$", color=BLUE, ha="center", fontsize=7.8)
    ax.text(saddle_x - 0.7, -26.6, r"$\ell_s=0.45$", color=ORANGE, ha="right", fontsize=7.8)
    ax.text(midpoint_x + 0.7, -26.6, r"$\ell_m=0.50$", color=TEAL, ha="left", fontsize=7.8)
    ax.annotate(r"raw bottleneck $D(\mathbf{s}_{ij})$", xy=(saddle_x, y_profile[saddle_index]),
                xytext=(saddle_x - 7.0, -15.8), color=ORANGE, fontsize=8.2,
                arrowprops={"arrowstyle": "->", "color": ORANGE, "lw": 0.9})
    ax.annotate(r"midpoint sample", xy=(midpoint_x, y_profile[midpoint_index]),
                xytext=(midpoint_x + 4.0, -19.3), color=TEAL, fontsize=8.2,
                arrowprops={"arrowstyle": "->", "color": TEAL, "lw": 0.9})
    ax.text(-29.0, -23.7, "absorbed dose", color=GREY, rotation=90, va="center", fontsize=7.6)
    ax.annotate("", xy=(points["cj"][0] + 1.2, y_base), xytext=(points["ci"][0] - 1.2, y_base),
                arrowprops={"arrowstyle": "->", "color": "#7A8993", "lw": 0.9})
    ax.text(-2.5, -27.6, r"normalized path coordinate $\ell$", color=GREY, ha="center", fontsize=7.8)


def _equation_derivation(ax: mpl.axes.Axes) -> None:
    ax.plot([33.3, 33.3], [-18.5, 27.7], color="#CBD5DB", lw=1.2)
    ax.text(35.0, 28.3, "Edge-local derivation", color=NAVY, fontsize=12.3, fontweight="bold", va="bottom")
    ax.text(35.0, 26.3, "native RTDOSE voxels; no interpolation", color=GREY, fontsize=7.6)
    rows = [
        (23.0, "Geometry", BLUE, r"$\mathbf{m}_{ij}=\frac{\mathbf{c}_i+\mathbf{c}_j}{2},\quad d_{ij}=\|\mathbf{c}_j-\mathbf{c}_i\|_2$"),
        (17.0, "Endpoint peak", BLUE, r"$D_{\mathrm{peak},ij}=\frac{D_{50}(\mathrm{VTV}_{H,i})+D_{50}(\mathrm{VTV}_{H,j})}{2}$"),
        (10.5, "Midpoint valley", TEAL, r"$S_m=B(\mathbf{m}_{ij},3\,\mathrm{mm})\cap(\mathrm{GTV}\setminus\mathrm{VTV}_H)$" + "\n" + r"$D_{m,ij}=\operatorname{med}_{\mathbf{x}\in S_m}D(\mathbf{x})$"),
        (2.5, "Topographic saddle", ORANGE, r"$C_{ij}=\operatorname{capsule}(\mathbf{c}_i,\mathbf{c}_j;R_c)\cap(\mathrm{GTV}\setminus\mathrm{VTV}_H)$" + "\n" + r"$\gamma_{ij}^{*}=\arg\max_{\gamma\in\mathcal{P}_{ij}(C_{ij})}\min_{\mathbf{x}\in\gamma}D(\mathbf{x})$" + "\n" + r"$\mathbf{s}_{ij}=\arg\min_{\mathbf{x}\in\gamma_{ij}^{*}}D(\mathbf{x})$"),
        (-7.5, "Saddle-local median", ORANGE, r"$S_s=B(\mathbf{s}_{ij},3\,\mathrm{mm})\cap(\mathrm{GTV}\setminus\mathrm{VTV}_H)$" + "\n" + r"$D_{s,ij}=\operatorname{med}_{\mathbf{x}\in S_s}D(\mathbf{x})$"),
        (-15.0, "Contrast", NAVY, r"$\mathrm{iPVDR}_{ij}=\frac{D_{\mathrm{peak},ij}}{D_{m,ij}},\qquad \mathrm{sPVDR}_{ij}=\frac{D_{\mathrm{peak},ij}}{D_{s,ij}}$" + "\n" + r"$\Delta D_{ij}=D_{m,ij}-D_{s,ij},\quad \Delta\mathrm{PVDR}_{ij}=\mathrm{sPVDR}_{ij}-\mathrm{iPVDR}_{ij}$"),
    ]
    for y, heading, colour, formula in rows:
        ax.scatter(33.3, y + 0.4, s=35, c=colour, edgecolors="white", linewidths=0.7, zorder=10)
        ax.text(35.0, y + 1.4, heading, color=colour, fontsize=8.4, fontweight="bold", va="center")
        ax.text(35.0, y - 0.2, formula, color="#18242D", fontsize=8.6, va="top", linespacing=2.0)
    ax.text(50.5, -22.6,
            r"$\mathrm{iPVDR}_{\mathrm{plan}}=\operatorname{median}_{(i,j)\in\mathcal{E}_{\mathrm{valid}}}\{\mathrm{iPVDR}_{ij}\}$",
            color=NAVY, fontsize=10.7, fontweight="bold", ha="center", va="center",
            bbox={"boxstyle": "round,pad=0.42", "facecolor": "#EDF4F7", "edgecolor": TEAL, "linewidth": 1.0})


def build() -> None:
    _style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(16.0, 9.5))
    fig.subplots_adjust(left=0.03, right=0.985, top=0.88, bottom=0.11)
    ax.set_xlim(-34.0, 70.0)
    ax.set_ylim(-28.5, 31.5)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    points = _spatial_field(ax)
    _dose_trace(ax, points)
    _equation_derivation(ax)
    legend = [
        Line2D([0], [0], color="#344650", lw=1.7, ls=(0, (4, 2.4)), label=r"locked edge $e_{ij}$"),
        Line2D([0], [0], color=ORANGE, lw=2.5, label=r"widest path $\gamma_{ij}^{*}$"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=TEAL, markeredgecolor="white", label="midpoint"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=ORANGE, markeredgecolor="white", label="saddle"),
    ]
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(0.035, 0.985), ncol=4, frameon=False, fontsize=8.0)

    fig.suptitle("ASCEND Layer 2.2: unified midpoint iPVDR and dose-topographic saddle analysis",
                 x=0.035, y=0.965, ha="left", color=NAVY, fontsize=19, fontweight="bold")
    fig.text(0.035, 0.925,
             "A single edge of the locked nearest-neighbour graph links spatial sampling, the deterministic dose path, and plan-level aggregation",
             ha="left", color=GREY, fontsize=10.4)
    fig.text(0.035, 0.035,
             "Conceptual, non-patient illustration using synthetic Gaussian dose fields. ASCEND evaluates native RTDOSE voxels within the validated GTV and outside all high-dose vertex masks.\n"
             "The saddle path uses 26-connectivity and maximises its minimum dose. Midpoint iPVDR remains the locked primary Layer 2.2 endpoint; saddle quantities are complementary diagnostics.",
             ha="left", va="bottom", color="#4C5962", fontsize=8.2)

    metadata = {
        "Title": "ASCEND Layer 2.2 unified midpoint iPVDR and dose-topographic saddle analysis",
        "Author": "ASCEND project",
        "Subject": "Unified thesis-ready conceptual mathematical figure",
        "Keywords": "ASCEND, Layer 2.2, iPVDR, saddle, radiotherapy, lattice",
    }
    fig.savefig(PDF_PATH, dpi=300, metadata=metadata)
    fig.savefig(PNG_PATH, dpi=240, metadata={"Software": "ASCEND reproducible figure builder"})
    plt.close(fig)
    print(PDF_PATH)
    print(PNG_PATH)


if __name__ == "__main__":
    build()
