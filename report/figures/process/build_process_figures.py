#!/usr/bin/env python3
"""Generate print-ready process diagrams for the dissertation.

The figures deliberately use normalized axes rather than a diagram package so
that every element remains vector text and geometry in the exported PDFs.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

OUT = Path(__file__).resolve().parent

NAVY = "#1F4E79"
TEAL = "#2A7F62"
ORANGE = "#C97A2B"
RED = "#B2473E"
INK = "#24313B"
MID = "#66737D"
GRAY = "#D6DDE2"
LIGHT_GRAY = "#F3F5F6"
LIGHT_BLUE = "#EAF1F7"
LIGHT_TEAL = "#EAF5F1"
LIGHT_ORANGE = "#FAF0E6"
LIGHT_RED = "#F9ECEA"
WHITE = "#FFFFFF"


mpl.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "STIXGeneral", "DejaVu Serif"],
        "font.size": 8.5,
        "text.color": INK,
        "axes.edgecolor": INK,
        "axes.linewidth": 0.8,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.facecolor": WHITE,
        "figure.facecolor": WHITE,
    }
)


def canvas(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height))
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    return fig, ax


def box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    *,
    edge: str = NAVY,
    face: str = WHITE,
    fontsize: float = 8.2,
    weight: str = "normal",
    radius: float = 0.015,
    linewidth: float = 1.05,
    zorder: int = 3,
    text_color: str = INK,
    ha: str = "center",
    pad_x: float = 0.012,
):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.006,rounding_size={radius}",
        linewidth=linewidth,
        edgecolor=edge,
        facecolor=face,
        zorder=zorder,
    )
    ax.add_patch(patch)
    tx = x + w / 2 if ha == "center" else x + pad_x
    ax.text(
        tx,
        y + h / 2,
        text,
        ha=ha,
        va="center",
        fontsize=fontsize,
        fontweight=weight,
        color=text_color,
        linespacing=1.12,
        zorder=zorder + 1,
    )
    return patch


def arrow(
    ax,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str = MID,
    lw: float = 1.0,
    style: str = "-|>",
    connectionstyle: str = "arc3",
    zorder: int = 2,
):
    patch = FancyArrowPatch(
        start,
        end,
        arrowstyle=style,
        mutation_scale=9,
        linewidth=lw,
        color=color,
        connectionstyle=connectionstyle,
        shrinkA=0,
        shrinkB=0,
        zorder=zorder,
    )
    ax.add_patch(patch)
    return patch


def line(ax, xs, ys, *, color=MID, lw=0.9, ls="-", zorder=1):
    ax.plot(xs, ys, color=color, lw=lw, ls=ls, zorder=zorder)


def pill(ax, x, y, w, text, *, face=NAVY, fontsize=7.2, text_color=WHITE):
    return box(
        ax,
        x,
        y,
        w,
        0.055,
        text,
        edge=face,
        face=face,
        fontsize=fontsize,
        weight="bold",
        radius=0.027,
        linewidth=0.8,
        text_color=text_color,
    )


def branch_label(ax, x, y, text, *, color, face=WHITE):
    """Draw a legible connector label without colliding with lines or boxes."""
    ax.text(
        x,
        y,
        text,
        ha="center",
        va="center",
        fontsize=7.0,
        fontweight="bold",
        color=color,
        zorder=8,
        bbox={
            "boxstyle": "round,pad=0.18",
            "facecolor": face,
            "edgecolor": "none",
            "alpha": 0.96,
        },
    )


def save(fig, filename: str):
    fig.savefig(OUT / filename, format="pdf", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def economic_eligibility_tree():
    fig, ax = canvas(7.15, 5.1)

    # Two evidence lanes make the observed/estimated distinction explicit.
    ax.add_patch(Rectangle((0.01, 0.65), 0.98, 0.32, facecolor=LIGHT_BLUE, edgecolor="none"))
    ax.add_patch(Rectangle((0.01, 0.025), 0.98, 0.59, facecolor=LIGHT_ORANGE, edgecolor="none"))
    pill(ax, 0.025, 0.90, 0.19, "OBSERVED EVIDENCE", face=NAVY)
    pill(ax, 0.34, 0.545, 0.31, "ASSUMPTION-BASED ESTIMATES", face=ORANGE)

    # Observed evidence is deliberately separated from assumption-based value.
    box(
        ax,
        0.04,
        0.72,
        0.20,
        0.13,
        "All reconstructed\nworkflow runs",
        edge=NAVY,
        face=WHITE,
        weight="bold",
        fontsize=8.4,
    )
    box(
        ax,
        0.34,
        0.80,
        0.20,
        0.10,
        "Reliability totals\nAll statuses included",
        edge=NAVY,
        face=WHITE,
        fontsize=7.8,
        weight="bold",
    )
    box(
        ax,
        0.34,
        0.67,
        0.20,
        0.10,
        "Observed-cost evidence\nPartial sum + n/N coverage",
        edge=NAVY,
        face=WHITE,
        fontsize=7.6,
        weight="bold",
    )
    box(
        ax,
        0.63,
        0.705,
        0.32,
        0.13,
        "Incomplete coverage\nWithhold complete totals;\nnever convert missing values to zero",
        edge=GRAY,
        face=WHITE,
        fontsize=7.2,
        linewidth=0.9,
    )
    arrow(ax, (0.24, 0.785), (0.34, 0.85), color=NAVY)
    arrow(ax, (0.24, 0.785), (0.34, 0.72), color=NAVY)

    # Benefit eligibility.
    box(
        ax,
        0.03,
        0.29,
        0.20,
        0.15,
        "Completed + business success\nor approved quality?",
        edge=ORANGE,
        face=WHITE,
        weight="bold",
        fontsize=7.7,
    )
    arrow(ax, (0.14, 0.72), (0.13, 0.44), color=ORANGE)
    box(
        ax,
        0.29,
        0.39,
        0.21,
        0.12,
        "Cycle baseline + duration\nCycle-time difference\n(not labour)",
        edge=TEAL,
        face=LIGHT_TEAL,
        weight="bold",
        fontsize=7.4,
    )
    box(
        ax,
        0.29,
        0.20,
        0.21,
        0.12,
        "Human-effort evidence\nBaseline + agent effort\n+ labour rate",
        edge=ORANGE,
        face=WHITE,
        weight="bold",
        fontsize=7.2,
    )
    arrow(ax, (0.23, 0.38), (0.29, 0.45), color=TEAL)
    arrow(ax, (0.23, 0.33), (0.29, 0.26), color=TEAL)
    branch_label(ax, 0.26, 0.415, "YES", color=TEAL, face=LIGHT_ORANGE)
    branch_label(ax, 0.26, 0.295, "YES", color=TEAL, face=LIGHT_ORANGE)

    box(
        ax,
        0.53,
        0.21,
        0.18,
        0.11,
        "Labour-value benefit\nReport only when complete",
        edge=TEAL,
        face=LIGHT_TEAL,
        weight="bold",
        fontsize=7.2,
    )
    arrow(ax, (0.50, 0.26), (0.53, 0.265), color=TEAL)
    box(
        ax,
        0.53,
        0.045,
        0.18,
        0.095,
        "Benefit fixed at zero\nRun costs still retained",
        edge=MID,
        face=LIGHT_GRAY,
        weight="bold",
        fontsize=7.1,
    )
    line(ax, [0.13, 0.13, 0.50], [0.29, 0.095, 0.095], color=MID, lw=1.0)
    arrow(ax, (0.50, 0.095), (0.53, 0.095), color=MID)
    branch_label(ax, 0.13, 0.205, "NO", color=MID, face=LIGHT_ORANGE)

    # Portfolio-value gate. Both input routes remain left of the output boxes.
    box(
        ax,
        0.76,
        0.30,
        0.20,
        0.14,
        "All-run cost coverage\nand benefit terms complete?",
        edge=ORANGE,
        face=WHITE,
        weight="bold",
        fontsize=7.4,
    )
    line(ax, [0.71, 0.72, 0.72], [0.265, 0.265, 0.385], color=ORANGE, lw=1.0)
    arrow(ax, (0.72, 0.385), (0.76, 0.385), color=ORANGE)
    line(ax, [0.71, 0.735, 0.735], [0.095, 0.095, 0.335], color=MID, lw=1.0)
    arrow(ax, (0.735, 0.335), (0.76, 0.335), color=MID)
    box(
        ax,
        0.76,
        0.075,
        0.105,
        0.10,
        "Report\nportfolio\nnet value",
        edge=TEAL,
        face=LIGHT_TEAL,
        weight="bold",
        fontsize=6.8,
    )
    box(
        ax,
        0.88,
        0.075,
        0.10,
        0.10,
        "Withhold\nportfolio\nnet value",
        edge=ORANGE,
        face=WHITE,
        weight="bold",
        fontsize=6.8,
    )
    arrow(ax, (0.815, 0.30), (0.8125, 0.175), color=TEAL)
    arrow(ax, (0.91, 0.30), (0.93, 0.175), color=ORANGE)
    branch_label(ax, 0.813, 0.235, "YES", color=TEAL, face=LIGHT_ORANGE)
    branch_label(ax, 0.93, 0.235, "NO", color=ORANGE, face=LIGHT_ORANGE)

    save(fig, "economic_eligibility_tree.pdf")


def enterprise_data_boundary():
    fig, ax = canvas(7.15, 4.9)

    # Repository boundary and zones.
    ax.add_patch(
        Rectangle(
            (0.015, 0.06),
            0.215,
            0.88,
            facecolor=LIGHT_ORANGE,
            edgecolor=ORANGE,
            linewidth=1.0,
            linestyle=(0, (4, 3)),
        )
    )
    ax.add_patch(
        Rectangle(
            (0.26, 0.06),
            0.725,
            0.88,
            facecolor="#FAFBFC",
            edgecolor=NAVY,
            linewidth=1.0,
            linestyle=(0, (4, 3)),
        )
    )
    pill(ax, 0.035, 0.865, 0.175, "OUTSIDE REPOSITORY", face=ORANGE, fontsize=6.8)
    pill(ax, 0.285, 0.865, 0.245, "CONTROLLED EVALUATION AREA", face=NAVY, fontsize=6.8)
    ax.text(
        0.243,
        0.50,
        "DATA\nBOUNDARY",
        ha="center",
        va="center",
        rotation=90,
        fontsize=7.0,
        color=MID,
        fontweight="bold",
        linespacing=1.0,
    )

    box(
        ax,
        0.045,
        0.55,
        0.155,
        0.19,
        "OpenTelemetry\nOTLP/JSON export",
        edge=ORANGE,
        face=WHITE,
        fontsize=8.5,
        weight="bold",
    )
    ax.text(
        0.122,
        0.47,
        "Source remains under\nenterprise custody",
        ha="center",
        va="center",
        fontsize=7.5,
        color=INK,
        linespacing=1.15,
    )
    box(
        ax,
        0.042,
        0.21,
        0.162,
        0.13,
        "NOT RETAINED\nin derived package",
        edge=ORANGE,
        face=LIGHT_ORANGE,
        fontsize=7.8,
        weight="bold",
    )

    box(
        ax,
        0.29,
        0.60,
        0.17,
        0.17,
        "Offline adapter\n\nMask every input / output",
        edge=NAVY,
        face=LIGHT_BLUE,
        fontsize=8.1,
        weight="bold",
    )
    arrow(ax, (0.20, 0.645), (0.29, 0.685), color=NAVY, lw=1.1)
    ax.text(
        0.375,
        0.545,
        "Default retained value:\n[REDACTED] + type + length\nOptional keyed HMAC only",
        ha="center",
        va="center",
        fontsize=7.4,
        color=MID,
        linespacing=1.15,
    )

    box(
        ax,
        0.50,
        0.58,
        0.185,
        0.22,
        (
            "Preparation evidence\n\nSource profile\nCoverage profile\n"
            "Redaction report\nTrace-audit sample"
        ),
        edge=NAVY,
        face=WHITE,
        fontsize=7.9,
        weight="bold",
    )
    arrow(ax, (0.46, 0.685), (0.50, 0.685), color=NAVY)

    # Approval gate (diamond) with a separate, explicit reject path.
    cx, cy, rw, rh = 0.775, 0.69, 0.085, 0.125
    diamond = Polygon(
        [(cx, cy + rh), (cx + rw, cy), (cx, cy - rh), (cx - rw, cy)],
        closed=True,
        facecolor=LIGHT_ORANGE,
        edgecolor=ORANGE,
        linewidth=1.1,
        zorder=3,
    )
    ax.add_patch(diamond)
    ax.text(
        cx,
        cy,
        "Manual approval\ntrace = run and\nbusiness status",
        ha="center",
        va="center",
        fontsize=7.3,
        fontweight="bold",
        linespacing=1.05,
        zorder=4,
    )
    arrow(ax, (0.685, 0.69), (cx - rw, cy), color=ORANGE)

    box(
        ax,
        0.86,
        0.61,
        0.105,
        0.16,
        "Canonical\nevents",
        edge=TEAL,
        face=LIGHT_TEAL,
        fontsize=8.2,
        weight="bold",
    )
    arrow(ax, (cx + rw, cy), (0.86, 0.69), color=TEAL)
    ax.text(0.865, 0.745, "APPROVE", fontsize=6.7, color=TEAL, fontweight="bold", ha="left")

    box(
        ax,
        0.69,
        0.29,
        0.17,
        0.15,
        "Coverage / gap\nreport only\nEvaluation stops",
        edge=RED,
        face=LIGHT_RED,
        fontsize=8.0,
        weight="bold",
    )
    arrow(ax, (cx, cy - rh), (0.775, 0.44), color=RED)
    ax.text(0.785, 0.505, "REJECT", fontsize=6.7, color=RED, fontweight="bold", ha="left")

    box(
        ax,
        0.48,
        0.25,
        0.16,
        0.16,
        "Safety-gated\nevaluator",
        edge=NAVY,
        face=LIGHT_BLUE,
        fontsize=8.5,
        weight="bold",
    )
    # Route the approved path around (not through) the rejection-only report.
    line(ax, [0.912, 0.955, 0.955, 0.56], [0.61, 0.56, 0.18, 0.18], color=NAVY, lw=1.05, zorder=2)
    arrow(ax, (0.56, 0.18), (0.56, 0.25), color=NAVY, lw=1.05)
    ax.text(
        0.925,
        0.195,
        "APPROVED PATH",
        fontsize=6.5,
        color=NAVY,
        fontweight="bold",
        ha="right",
        va="bottom",
    )

    box(
        ax,
        0.28,
        0.19,
        0.16,
        0.27,
        (
            "Retained outputs\n\nAnalytical tables\nScorecard / report\n"
            "Exclusions / warnings\nProvenance hashes"
        ),
        edge=TEAL,
        face=LIGHT_TEAL,
        fontsize=7.8,
        weight="bold",
    )
    arrow(ax, (0.48, 0.33), (0.44, 0.33), color=TEAL)
    ax.text(
        0.62,
        0.135,
        "No raw input/output body crosses into analytical outputs.",
        ha="center",
        va="center",
        fontsize=7.5,
        color=MID,
        fontweight="bold",
    )

    save(fig, "enterprise_data_boundary.pdf")


def expanded_enterprise_data_boundary():
    fig, ax = canvas(7.15, 4.65)

    ax.add_patch(
        Rectangle(
            (0.015, 0.08), 0.20, 0.84, facecolor=LIGHT_ORANGE, edgecolor=ORANGE, linewidth=1.0
        )
    )
    ax.add_patch(
        Rectangle((0.235, 0.08), 0.46, 0.84, facecolor=LIGHT_BLUE, edgecolor=NAVY, linewidth=1.0)
    )
    ax.add_patch(
        Rectangle((0.715, 0.08), 0.27, 0.84, facecolor=LIGHT_TEAL, edgecolor=TEAL, linewidth=1.0)
    )
    pill(ax, 0.035, 0.845, 0.16, "SOURCE CUSTODY", face=ORANGE, fontsize=6.8)
    pill(ax, 0.255, 0.845, 0.25, "ENTERPRISE EVIDENCE ONBOARDING", face=NAVY, fontsize=6.6)
    pill(ax, 0.735, 0.845, 0.19, "CANONICAL EVALUATION", face=TEAL, fontsize=6.7)

    box(
        ax,
        0.045,
        0.57,
        0.14,
        0.17,
        "OpenTelemetry\nOTLP/JSON export\n\nraw values remain\noutside bundle",
        edge=ORANGE,
        face=WHITE,
        fontsize=7.6,
        weight="bold",
    )
    box(
        ax,
        0.045,
        0.28,
        0.14,
        0.15,
        "Documented source\nstructures\n+ audit sample",
        edge=ORANGE,
        face=LIGHT_ORANGE,
        fontsize=7.4,
    )

    box(
        ax,
        0.265,
        0.61,
        0.15,
        0.15,
        "Raw-value-free\nsource profile\npaths • types • n/N",
        edge=NAVY,
        face=WHITE,
        fontsize=7.5,
        weight="bold",
    )
    box(
        ax,
        0.455,
        0.61,
        0.19,
        0.15,
        "Mapping specification\ndirect • lookup • derive\ntest + semantic approval",
        edge=NAVY,
        face=WHITE,
        fontsize=7.4,
        weight="bold",
    )
    arrow(ax, (0.185, 0.655), (0.265, 0.685), color=NAVY)
    arrow(ax, (0.415, 0.685), (0.455, 0.685), color=NAVY)

    assessment_boxes = [
        (0.265, "Mapping\nreadiness"),
        (0.365, "Analytical\nreadiness"),
        (0.465, "Governance\nsafety"),
        (0.565, "Claim\nstrength"),
    ]
    for x, label in assessment_boxes:
        box(
            ax,
            x,
            0.345,
            0.085,
            0.12,
            label,
            edge=NAVY,
            face=LIGHT_BLUE,
            fontsize=6.9,
            weight="bold",
        )
    arrow(ax, (0.55, 0.61), (0.455, 0.475), color=NAVY)
    ax.text(
        0.455,
        0.505,
        "SEPARATE ASSESSMENTS",
        ha="center",
        va="center",
        fontsize=6.6,
        color=NAVY,
        fontweight="bold",
    )

    box(
        ax,
        0.29,
        0.12,
        0.33,
        0.135,
        (
            "Mapping-friction profile\niterations • decisions • issues\n"
            "review • cycle • recorded effort"
        ),
        edge=ORANGE,
        face=LIGHT_ORANGE,
        fontsize=7.0,
        weight="bold",
    )
    arrow(ax, (0.455, 0.345), (0.455, 0.255), color=ORANGE)

    box(
        ax,
        0.735,
        0.61,
        0.22,
        0.15,
        "Tier classifier\nblocked • mapping-only\npartial • complete",
        edge=TEAL,
        face=WHITE,
        fontsize=7.7,
        weight="bold",
    )
    arrow(ax, (0.65, 0.405), (0.735, 0.65), color=TEAL)
    box(
        ax,
        0.735,
        0.35,
        0.22,
        0.14,
        "Canonical events\n+ quarantined-run register\n+ metric n/N",
        edge=TEAL,
        face=WHITE,
        fontsize=7.3,
    )
    arrow(ax, (0.845, 0.61), (0.845, 0.49), color=TEAL)
    box(
        ax,
        0.735,
        0.15,
        0.22,
        0.11,
        "Permitted tier-specific\noutputs",
        edge=TEAL,
        face=LIGHT_TEAL,
        fontsize=7.5,
        weight="bold",
    )
    arrow(ax, (0.845, 0.35), (0.845, 0.26), color=TEAL)

    save(fig, "enterprise_data_boundary.pdf")


def mapping_friction_dimensions():
    fig, ax = canvas(7.15, 5.0)
    pill(ax, 0.025, 0.91, 0.25, "MAPPING-FRICTION DIMENSIONS", face=NAVY, fontsize=6.8)
    pill(ax, 0.445, 0.91, 0.18, "DECISION POINTS", face=ORANGE, fontsize=6.8)
    pill(ax, 0.79, 0.91, 0.17, "READINESS EFFECT", face=TEAL, fontsize=6.8)

    dimensions = [
        ("Signal progress", "mapped • observed\ntested • approved"),
        ("Transformation", "direct • coerce • lookup\nderive • aggregate • manual"),
        ("Semantic work", "boundary • business status\nreviewer + evidence"),
        ("Iteration and issues", "versions • decisions\nopened / resolved / unresolved"),
        ("Source burden", "formats • field shape\nmanual-review n/N"),
        ("Time and effort", "elapsed cycle time\nrecorded active effort n/N"),
    ]
    ys = [0.77, 0.64, 0.51, 0.38, 0.25, 0.12]
    for (title, detail), y in zip(dimensions, ys, strict=True):
        box(
            ax,
            0.03,
            y - 0.05,
            0.355,
            0.105,
            f"{title}\n{detail}",
            edge=NAVY,
            face=WHITE,
            fontsize=7.2,
            weight="bold",
            ha="left",
            pad_x=0.015,
        )
        arrow(ax, (0.385, y), (0.445, y), color=MID)

    decisions = [
        (0.70, "Are restricted values\nsafely omitted?", RED),
        (0.48, "Are run boundaries and\nbusiness status approved?", ORANGE),
        (0.26, "Is at least one metric\nfamily valid for n/N?", NAVY),
    ]
    for y, text, color in decisions:
        cx, rw, rh = 0.56, 0.105, 0.09
        ax.add_patch(
            Polygon(
                [(cx, y + rh), (cx + rw, y), (cx, y - rh), (cx - rw, y)],
                closed=True,
                facecolor=WHITE,
                edgecolor=color,
                linewidth=1.1,
                zorder=3,
            )
        )
        ax.text(
            cx,
            y,
            text,
            ha="center",
            va="center",
            fontsize=6.9,
            color=INK,
            fontweight="bold",
            linespacing=1.05,
            zorder=4,
        )
    line(ax, [0.445, 0.445, 0.455], [0.12, 0.77, 0.70], color=MID, lw=0.8)
    arrow(ax, (0.665, 0.70), (0.77, 0.70), color=RED)
    arrow(ax, (0.665, 0.48), (0.77, 0.48), color=ORANGE)
    arrow(ax, (0.665, 0.26), (0.77, 0.26), color=NAVY)

    box(
        ax,
        0.77,
        0.63,
        0.19,
        0.14,
        "NO → BLOCKED\nSafety is not degraded",
        edge=RED,
        face=LIGHT_RED,
        fontsize=7.6,
        weight="bold",
    )
    box(
        ax,
        0.77,
        0.41,
        0.19,
        0.14,
        "NO → MAPPING-ONLY\nResolve semantics first",
        edge=ORANGE,
        face=LIGHT_ORANGE,
        fontsize=7.4,
        weight="bold",
    )
    box(
        ax,
        0.77,
        0.19,
        0.19,
        0.14,
        "YES → ANALYTICS\nPartial or complete\nNO → MAPPING-ONLY",
        edge=TEAL,
        face=LIGHT_TEAL,
        fontsize=7.0,
        weight="bold",
    )
    save(fig, "mapping_friction_dimensions.pdf")


def evidence_tier_publication():
    fig, ax = canvas(7.15, 5.05)
    pill(ax, 0.025, 0.915, 0.21, "TIER CLASSIFICATION", face=NAVY, fontsize=6.8)

    questions = [
        (
            0.06,
            0.69,
            "Privacy, integrity, identity,\n"
            "aggregation, pagination and\npopulation assignment safe?",
            RED,
        ),
        (0.29, 0.69, "Required mapping and\nworkflow semantics\nverified?", ORANGE),
        (0.52, 0.69, "At least one requested\nmetric family valid\nfor an explicit n/N?", NAVY),
        (0.75, 0.69, "All requested preconditions\nmet and zero\nquarantined runs?", TEAL),
    ]
    for x, y, label, color in questions:
        box(ax, x, y, 0.18, 0.17, label, edge=color, face=WHITE, fontsize=7.1, weight="bold")
    for x1, x2 in [(0.24, 0.29), (0.47, 0.52), (0.70, 0.75)]:
        arrow(ax, (x1, 0.775), (x2, 0.775), color=MID)
        ax.text(
            (x1 + x2) / 2, 0.795, "YES", fontsize=6.3, ha="center", color=TEAL, fontweight="bold"
        )

    tier_cards = [
        (
            0.055,
            0.36,
            "BLOCKED",
            "Diagnostics + readiness only\nNo analytical scorecard",
            RED,
            LIGHT_RED,
        ),
        (
            0.285,
            0.36,
            "MAPPING-ONLY",
            "Source profile + mapping\nreadiness; no analytics",
            ORANGE,
            LIGHT_ORANGE,
        ),
        (
            0.53,
            0.36,
            "PARTIAL",
            "Tier-labelled scorecard\nvalid metrics with n/N",
            NAVY,
            LIGHT_BLUE,
        ),
        (
            0.775,
            0.36,
            "COMPLETE",
            "All requested outputs\nwith complete preconditions",
            TEAL,
            LIGHT_TEAL,
        ),
    ]
    for x, y, title, detail, edge, face in tier_cards:
        box(
            ax,
            x,
            y,
            0.19,
            0.17,
            f"{title}\n{detail}",
            edge=edge,
            face=face,
            fontsize=7.2,
            weight="bold",
        )
    arrow(ax, (0.15, 0.69), (0.15, 0.53), color=RED)
    ax.text(0.16, 0.60, "NO", fontsize=6.2, color=RED, fontweight="bold")
    arrow(ax, (0.38, 0.69), (0.38, 0.53), color=ORANGE)
    ax.text(0.39, 0.60, "NO", fontsize=6.2, color=ORANGE, fontweight="bold")
    # With no usable metric family, the safe output tier is mapping-only.
    # Partial requires a usable metric and unmet complete-tier preconditions.
    line(ax, [0.61, 0.61, 0.50, 0.50], [0.69, 0.59, 0.59, 0.445], color=ORANGE)
    arrow(ax, (0.50, 0.445), (0.481, 0.445), color=ORANGE)
    ax.text(0.62, 0.64, "NO", fontsize=6.2, color=ORANGE, fontweight="bold")
    arrow(ax, (0.84, 0.69), (0.87, 0.53), color=TEAL)
    ax.text(0.87, 0.60, "YES", fontsize=6.2, color=TEAL, fontweight="bold")
    arrow(ax, (0.80, 0.69), (0.66, 0.53), color=NAVY, connectionstyle="arc3,rad=0.18")
    ax.text(0.735, 0.60, "NO", fontsize=6.2, color=NAVY, fontweight="bold")

    ax.add_patch(
        Rectangle((0.035, 0.08), 0.93, 0.18, facecolor=LIGHT_GRAY, edgecolor=GRAY, linewidth=0.9)
    )
    ax.text(
        0.055, 0.205, "INDEPENDENT CLAIM ASSESSMENT", fontsize=7.0, color=INK, fontweight="bold"
    )
    ax.text(
        0.055,
        0.145,
        "Evidence tier answers: which outputs are safely supportable from this population?",
        fontsize=7.5,
        color=INK,
    )
    ax.text(
        0.055,
        0.105,
        "Claim strength answers: what interpretation can the evaluation design support?",
        fontsize=7.5,
        color=INK,
    )
    ax.text(0.68, 0.145, "Complete ≠ causal", fontsize=8.0, color=RED, fontweight="bold")
    ax.text(
        0.68,
        0.105,
        "Synthetic remains conformance/readiness",
        fontsize=7.3,
        color=NAVY,
        fontweight="bold",
    )
    save(fig, "evidence_tier_publication.pdf")


def workflow_reconstruction_timeline():
    fig, ax = canvas(7.15, 5.0)

    pill(ax, 0.02, 0.91, 0.22, "ACCEPTED RECONSTRUCTION", face=TEAL)
    line(ax, [0.08, 0.92], [0.72, 0.72], color=NAVY, lw=1.8, zorder=1)

    events = [
        (0.09, "run_started", "t0", TEAL),
        (0.30, "step events", "ordered", NAVY),
        (0.49, "tool call", "dedup: call_id", NAVY),
        (0.68, "retry", "dedup: step + attempt", NAVY),
        (0.91, "one terminal", "run_completed OR\nrun_failed", TEAL),
    ]
    for x, title, sub, color in events:
        ax.scatter([x], [0.72], s=58, color=WHITE, edgecolor=color, linewidth=1.5, zorder=3)
        ax.text(
            x, 0.79, title, ha="center", va="bottom", fontsize=8.0, fontweight="bold", color=color
        )
        ax.text(x, 0.655, sub, ha="center", va="top", fontsize=7.2, color=MID)
    arrow(ax, (0.10, 0.72), (0.90, 0.72), color=NAVY, lw=1.2)

    box(
        ax,
        0.045,
        0.50,
        0.205,
        0.105,
        "duration = terminal time\n- start time",
        edge=TEAL,
        face=LIGHT_TEAL,
        fontsize=7.3,
        weight="bold",
    )
    box(
        ax,
        0.28,
        0.50,
        0.205,
        0.105,
        "status derives from\nterminal semantics",
        edge=TEAL,
        face=LIGHT_TEAL,
        fontsize=7.3,
        weight="bold",
    )
    box(
        ax,
        0.515,
        0.50,
        0.205,
        0.105,
        "token / cost increments\nsummed once per event",
        edge=NAVY,
        face=LIGHT_BLUE,
        fontsize=7.15,
        weight="bold",
    )
    box(
        ax,
        0.75,
        0.50,
        0.205,
        0.105,
        "undeclared capability =\nnull / unsupported",
        edge=NAVY,
        face=LIGHT_BLUE,
        fontsize=7.15,
        weight="bold",
    )

    pill(ax, 0.02, 0.39, 0.27, "INVALID RUN PATTERNS", face=RED)
    ax.text(
        0.98,
        0.425,
        "Quarantine when safely run-local; otherwise block",
        ha="right",
        va="center",
        fontsize=6.8,
        color=RED,
        fontweight="bold",
    )

    def rejected_card(x, y, heading, detail, event_labels):
        w, h = 0.455, 0.155
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor=LIGHT_RED,
                edgecolor=RED,
                linewidth=1.0,
                zorder=2,
            )
        )
        ax.text(
            x + 0.015,
            y + h - 0.035,
            heading,
            ha="left",
            va="center",
            fontsize=7.8,
            fontweight="bold",
            color=RED,
            zorder=4,
        )
        xs = [
            x + 0.025 + i * (w - 0.05) / max(1, len(event_labels) - 1)
            for i in range(len(event_labels))
        ]
        ly = y + 0.065
        if len(xs) > 1:
            line(ax, [xs[0], xs[-1]], [ly, ly], color=RED, lw=0.9, zorder=2)
            arrow(ax, (xs[0], ly), (xs[-1], ly), color=RED, lw=0.8, zorder=2)
        for px, label in zip(xs, event_labels, strict=True):
            ax.scatter([px], [ly], s=25, color=WHITE, edgecolor=RED, linewidth=1.0, zorder=4)
            ax.text(px, ly - 0.025, label, ha="center", va="top", fontsize=6.0, color=INK, zorder=4)
        ax.text(
            x + w - 0.012,
            y + h - 0.035,
            detail,
            ha="right",
            va="center",
            fontsize=6.4,
            color=MID,
            zorder=4,
        )

    rejected_card(0.025, 0.205, "Missing terminal", "No duration or status", ["start", "step", "?"])
    rejected_card(
        0.52,
        0.205,
        "Conflicting terminals",
        "Exactly one required",
        ["start", "complete", "failed"],
    )
    rejected_card(
        0.025, 0.02, "Post-terminal event", "Boundary is violated", ["start", "complete", "event"]
    )
    rejected_card(
        0.52,
        0.02,
        "Configuration drift",
        "One config tuple per run",
        ["cfg A", "cfg B", "terminal"],
    )

    save(fig, "workflow_reconstruction_timeline.pdf")


def implementation_data_flow():
    """Show the package boundary, major components, schemas, and published outputs."""

    fig, ax = canvas(7.15, 4.55)
    ax.add_patch(Rectangle((0.012, 0.70), 0.976, 0.275, facecolor=LIGHT_BLUE, edgecolor="none"))
    ax.add_patch(Rectangle((0.012, 0.31), 0.976, 0.325, facecolor=LIGHT_TEAL, edgecolor="none"))
    ax.add_patch(Rectangle((0.012, 0.025), 0.976, 0.225, facecolor=LIGHT_ORANGE, edgecolor="none"))
    pill(ax, 0.025, 0.905, 0.19, "INPUT CONTRACTS", face=NAVY, fontsize=7.1)
    pill(ax, 0.025, 0.565, 0.22, "TOOLKIT COMPONENTS", face=TEAL, fontsize=7.1)
    pill(ax, 0.025, 0.18, 0.22, "PUBLISHED EVIDENCE", face=ORANGE, fontsize=7.1)

    inputs = [
        (0.06, "Evaluation manifest\nWorkflow configuration"),
        (0.36, "OTLP/JSON export or\nsource profile"),
        (0.66, "Mapping specification\nOptional activity log"),
    ]
    for x, label in inputs:
        box(ax, x, 0.745, 0.25, 0.105, label, edge=NAVY, face=WHITE, fontsize=7.8, weight="bold")

    components = [
        (0.025, 0.14, "Preparation\nand mapping"),
        (0.185, 0.14, "Schema and\npolicy validation"),
        (0.345, 0.14, "Run\nreconstruction"),
        (0.505, 0.14, "Readiness and\nmetric engine"),
        (0.665, 0.14, "Claim\ngovernance"),
        (0.825, 0.15, "Transactional\npublication"),
    ]
    for x, w, label in components:
        box(ax, x, 0.39, w, 0.105, label, edge=TEAL, face=WHITE, fontsize=7.1, weight="bold")
    for (x, w, _), (nx, _, _) in zip(components[:-1], components[1:], strict=True):
        arrow(ax, (x + w, 0.442), (nx - 0.007, 0.442), color=TEAL, lw=1.1)

    for x in (0.185, 0.455, 0.725):
        arrow(ax, (x, 0.745), (x, 0.505), color=NAVY, lw=1.0)

    ax.text(
        0.5,
        0.335,
        "Typed schemas  •  canonical events  •  explicit denominators  •  tier and claim gates",
        ha="center",
        va="center",
        fontsize=7.2,
        color=MID,
    )

    outputs = [
        (0.03, 0.205, "Readiness and\ndiagnostics"),
        (0.255, 0.205, "Tier-labelled\nscorecard/report"),
        (0.48, 0.205, "Evaluation package\nand checksum index"),
        (0.75, 0.215, "verify-package\nhash recomputation"),
    ]
    for x, w, label in outputs:
        box(ax, x, 0.07, w, 0.075, label, edge=ORANGE, face=WHITE, fontsize=6.9, weight="bold")
    for x in (0.13, 0.355, 0.582):
        arrow(ax, (0.9, 0.39), (x, 0.155), color=ORANGE, lw=0.9, connectionstyle="arc3,rad=0.08")
    arrow(ax, (0.685, 0.107), (0.74, 0.107), color=ORANGE, lw=1.0)

    save(fig, "implementation_data_flow.pdf")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    economic_eligibility_tree()
    expanded_enterprise_data_boundary()
    mapping_friction_dimensions()
    evidence_tier_publication()
    workflow_reconstruction_timeline()
    implementation_data_flow()


if __name__ == "__main__":
    main()
