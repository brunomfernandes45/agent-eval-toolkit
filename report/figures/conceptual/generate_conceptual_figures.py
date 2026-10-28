#!/usr/bin/env python3
"""Generate the dissertation's conceptual diagrams as vector PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, pi, sin
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

OUT_DIR = Path(__file__).resolve().parent

NAVY = "#1F4E79"
TEAL = "#2A7F62"
ORANGE = "#C97A2B"
GRAY = "#5F6B75"
DARK = "#26323B"
MID = "#AAB3BA"
LIGHT_GRAY = "#F2F4F5"
LIGHT_NAVY = "#EAF1F7"
LIGHT_TEAL = "#EAF4F0"
LIGHT_ORANGE = "#FAF1E8"
WHITE = "#FFFFFF"

ARIAL_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
ARIAL_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
if ARIAL_REGULAR.exists() and ARIAL_BOLD.exists():
    pdfmetrics.registerFont(TTFont("FigureSans", str(ARIAL_REGULAR)))
    pdfmetrics.registerFont(TTFont("FigureSans-Bold", str(ARIAL_BOLD)))
    REGULAR_FONT = "FigureSans"
    BOLD_FONT = "FigureSans-Bold"
else:
    # Standard PDF fonts preserve portability if the preferred local fonts are
    # unavailable when a contributor regenerates the diagrams.
    REGULAR_FONT = "Helvetica"
    BOLD_FONT = "Helvetica-Bold"


@dataclass
class Page:
    """Small normalized-coordinate wrapper around ReportLab's vector canvas."""

    filename: str
    height_inches: float

    def __post_init__(self) -> None:
        self.width = 7.1 * inch
        self.height = self.height_inches * inch
        self.canvas = pdfcanvas.Canvas(
            str(OUT_DIR / self.filename),
            pagesize=(self.width, self.height),
            pageCompression=1,
            invariant=1,
            initialFontName=REGULAR_FONT,
        )
        self.canvas.setTitle(Path(self.filename).stem.replace("_", " "))
        self.canvas.setCreator("ReportLab")
        self.canvas.setFillColor(HexColor(WHITE))
        self.canvas.rect(0, 0, self.width, self.height, fill=1, stroke=0)

    def x(self, value: float) -> float:
        return value * self.width

    def y(self, value: float) -> float:
        return value * self.height

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        size: float = 8.0,
        color: str = DARK,
        bold: bool = False,
        align: str = "center",
        leading: float = 1.14,
    ) -> None:
        lines = value.split("\n")
        font = BOLD_FONT if bold else REGULAR_FONT
        self.canvas.setFont(font, size)
        self.canvas.setFillColor(HexColor(color))
        line_step = size * leading
        baseline = self.y(y) + ((len(lines) - 1) * line_step) / 2 - size * 0.34
        for index, line in enumerate(lines):
            line_y = baseline - index * line_step
            if align == "left":
                self.canvas.drawString(self.x(x), line_y, line)
            elif align == "right":
                self.canvas.drawRightString(self.x(x), line_y, line)
            else:
                self.canvas.drawCentredString(self.x(x), line_y, line)

    def line(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        color: str = GRAY,
        width: float = 1.0,
        dash: tuple[float, ...] | None = None,
    ) -> None:
        self.canvas.setStrokeColor(HexColor(color))
        self.canvas.setLineWidth(width)
        self.canvas.setDash(dash or ())
        self.canvas.line(self.x(start[0]), self.y(start[1]), self.x(end[0]), self.y(end[1]))
        self.canvas.setDash()

    def arrow(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        color: str = GRAY,
        width: float = 1.15,
        head: float = 5.0,
        both: bool = False,
    ) -> None:
        x1, y1 = self.x(start[0]), self.y(start[1])
        x2, y2 = self.x(end[0]), self.y(end[1])
        self.canvas.setStrokeColor(HexColor(color))
        self.canvas.setFillColor(HexColor(color))
        self.canvas.setLineWidth(width)
        self.canvas.line(x1, y1, x2, y2)
        self._arrow_head(x1, y1, x2, y2, color, head)
        if both:
            self._arrow_head(x2, y2, x1, y1, color, head)

    def _arrow_head(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: str,
        head: float,
    ) -> None:
        angle = atan2(y2 - y1, x2 - x1)
        left = (
            x2 - head * cos(angle - pi / 6),
            y2 - head * sin(angle - pi / 6),
        )
        right = (
            x2 - head * cos(angle + pi / 6),
            y2 - head * sin(angle + pi / 6),
        )
        path = self.canvas.beginPath()
        path.moveTo(x2, y2)
        path.lineTo(*left)
        path.lineTo(*right)
        path.close()
        self.canvas.setFillColor(HexColor(color))
        self.canvas.drawPath(path, fill=1, stroke=0)

    def rounded_box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        title: str,
        body: str | None = None,
        face: str = WHITE,
        edge: str = NAVY,
        title_color: str = DARK,
        body_color: str = GRAY,
        linewidth: float = 1.1,
        radius: float = 5.0,
        title_size: float = 8.6,
        body_size: float = 7.5,
        align: str = "center",
    ) -> None:
        self.canvas.setFillColor(HexColor(face))
        self.canvas.setStrokeColor(HexColor(edge))
        self.canvas.setLineWidth(linewidth)
        self.canvas.roundRect(
            self.x(x),
            self.y(y),
            self.x(w),
            self.y(h),
            radius,
            fill=1,
            stroke=1,
        )
        text_x = x + w / 2 if align == "center" else x + 0.014
        if body:
            self.text(
                text_x,
                y + h * 0.69,
                title,
                size=title_size,
                color=title_color,
                bold=True,
                align=align,
                leading=1.05,
            )
            self.text(
                text_x,
                y + h * 0.29,
                body,
                size=body_size,
                color=body_color,
                align=align,
                leading=1.12,
            )
        else:
            self.text(
                text_x,
                y + h / 2,
                title,
                size=title_size,
                color=title_color,
                bold=True,
                align=align,
                leading=1.08,
            )

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        *,
        face: str = WHITE,
        edge: str = NAVY,
        width: float = 1.1,
    ) -> None:
        self.canvas.setFillColor(HexColor(face))
        self.canvas.setStrokeColor(HexColor(edge))
        self.canvas.setLineWidth(width)
        radius_points = radius * self.width
        self.canvas.circle(self.x(x), self.y(y), radius_points, fill=1, stroke=1)

    def polygon(
        self,
        points: list[tuple[float, float]],
        *,
        face: str,
        edge: str,
        width: float = 1.1,
    ) -> None:
        path = self.canvas.beginPath()
        path.moveTo(self.x(points[0][0]), self.y(points[0][1]))
        for x, y in points[1:]:
            path.lineTo(self.x(x), self.y(y))
        path.close()
        self.canvas.setFillColor(HexColor(face))
        self.canvas.setStrokeColor(HexColor(edge))
        self.canvas.setLineWidth(width)
        self.canvas.drawPath(path, fill=1, stroke=1)

    def header(self, x: float, label: str) -> None:
        self.text(x, 0.94, label.upper(), size=7.4, color=GRAY, bold=True)

    def save(self) -> None:
        self.canvas.showPage()
        self.canvas.save()


def thesis_research_map() -> None:
    page = Page("thesis_research_map.pdf", 5.2)

    page.header(0.155, "Problem dimension")
    page.header(0.44, "Research question")
    page.header(0.745, "Framework response")

    rows = [
        (
            "Architecture",
            "Ambiguous agent categories",
            "RQ1",
            "What must be recorded\nfor fair comparison?",
            "FM1  Architecture taxonomy",
            "Comparable deployment description",
            NAVY,
            LIGHT_NAVY,
        ),
        (
            "Metrics",
            "Weak evidence and\nhidden mapping work",
            "RQ2",
            "Which signals and mapping\nwork make evidence usable?",
            "FM2  Metrics and Telemetry",
            "Mapping friction, evidence and coverage",
            TEAL,
            LIGHT_TEAL,
        ),
        (
            "Evaluation design",
            "Claims exceed design strength",
            "RQ3",
            "Which designs support\nwhich claims?",
            "FM3  Evaluation Designs",
            "Explicit baselines and claim limits",
            NAVY,
            LIGHT_NAVY,
        ),
        (
            "Governance",
            "Privacy and audit gaps",
            "RQ4",
            "Which controls make results\ndefensible?",
            "FM4  Validity and Governance",
            "Traceable, privacy-aware evidence",
            TEAL,
            LIGHT_TEAL,
        ),
    ]
    row_y = [0.755, 0.605, 0.455, 0.305]

    for y, row in zip(row_y, rows, strict=True):
        p_title, p_body, rq, rq_body, fm_title, fm_body, accent, light = row
        page.rounded_box(
            0.025,
            y,
            0.26,
            0.105,
            title=p_title,
            body=p_body,
            face=LIGHT_GRAY,
            edge=MID,
            title_size=8.5,
            body_size=7.2,
        )
        page.rounded_box(
            0.335,
            y,
            0.21,
            0.105,
            title=rq,
            body=rq_body,
            face=WHITE,
            edge=accent,
            title_color=accent,
            title_size=8.9,
            body_size=7.0,
        )
        page.rounded_box(
            0.595,
            y,
            0.34,
            0.105,
            title=fm_title,
            body=fm_body,
            face=light,
            edge=accent,
            title_size=8.4,
            body_size=7.1,
        )
        page.arrow((0.286, y + 0.0525), (0.330, y + 0.0525), color=accent)
        page.arrow((0.546, y + 0.0525), (0.590, y + 0.0525), color=accent)

    spine_x = 0.962
    centers = [y + 0.0525 for y in row_y]
    for center_y in centers:
        page.line((0.936, center_y), (spine_x, center_y), color=GRAY, width=0.95)
    page.line((spine_x, centers[-1]), (spine_x, centers[0]), color=GRAY, width=0.95)
    page.line((spine_x, centers[-1]), (spine_x, 0.20), color=GRAY, width=0.95)
    page.arrow((spine_x, 0.20), (0.737, 0.20), color=NAVY, width=1.25)

    page.rounded_box(
        0.27,
        0.15,
        0.46,
        0.10,
        title="Executable evaluation toolkit",
        body="Onboarding, tiering, metrics and evidence packages",
        face=NAVY,
        edge=NAVY,
        title_color=WHITE,
        body_color=WHITE,
        title_size=9.2,
        body_size=7.2,
    )
    page.arrow((0.50, 0.148), (0.50, 0.112), color=TEAL, width=1.35)
    page.rounded_box(
        0.27,
        0.025,
        0.46,
        0.085,
        title="Future naturalistic enterprise pilot",
        body="Framework utility and actual onboarding friction with approved telemetry",
        face=LIGHT_TEAL,
        edge=TEAL,
        title_color=TEAL,
        title_size=9.0,
        body_size=7.2,
    )
    page.save()


def architecture_profile() -> None:
    page = Page("agentic_architecture_spectrum.pdf", 4.4)

    page.text(
        0.5,
        0.94,
        "FM1 architecture profile",
        size=10.2,
        color=NAVY,
        bold=True,
    )
    page.text(
        0.5,
        0.885,
        "Four independent questions; detailed mechanisms remain qualifiers",
        size=7.6,
        color=GRAY,
    )

    rows = [
        (
            "1",
            "Execution and control",
            "What may the system execute,\nand where must a person decide?",
            "Advisory  |  supervised\nBounded autonomous  |  policy-automated",
            NAVY,
            LIGHT_NAVY,
        ),
        (
            "2",
            "Agent organisation\nand coordination",
            "How many decision-making roles act,\nand how are they coordinated?",
            "Single agent  |  central orchestration\nDistributed network",
            TEAL,
            LIGHT_TEAL,
        ),
        (
            "3",
            "State and knowledge",
            "How long does usable state persist,\nand what source or store backs it?",
            "Stateless  |  run-scoped\nCross-run persistent",
            ORANGE,
            LIGHT_ORANGE,
        ),
        (
            "4",
            "Interaction surface",
            "Which human, tool, workflow,\nor agent boundaries are crossed?",
            "Human UI  |  tool/API\nWorkflow/event bus  |  agent-to-agent",
            GRAY,
            LIGHT_GRAY,
        ),
    ]

    y_positions = [0.69, 0.51, 0.33, 0.15]
    for y, (number, title, question, values, accent, light) in zip(y_positions, rows, strict=True):
        page.circle(0.065, y + 0.065, 0.025, face=WHITE, edge=accent, width=1.35)
        page.text(0.065, y + 0.065, number, size=8.0, color=accent, bold=True)
        page.rounded_box(
            0.105,
            y,
            0.25,
            0.13,
            title=title,
            face=light,
            edge=accent,
            title_color=accent,
            title_size=8.5,
        )
        page.rounded_box(
            0.385,
            y,
            0.285,
            0.13,
            title=question,
            face=WHITE,
            edge=MID,
            title_size=7.6,
            linewidth=0.95,
        )
        page.rounded_box(
            0.70,
            y,
            0.27,
            0.13,
            title=values,
            face=WHITE,
            edge=accent,
            title_color=DARK,
            title_size=6.9,
            linewidth=1.0,
        )

    page.text(0.23, 0.84, "DIMENSION", size=7.1, color=GRAY, bold=True)
    page.text(0.53, 0.84, "ASSIGNMENT QUESTION", size=7.1, color=GRAY, bold=True)
    page.text(0.835, 0.84, "TYPICAL PROFILE VALUES", size=7.1, color=GRAY, bold=True)
    page.text(
        0.5,
        0.065,
        "Workflow and run boundaries remain evaluation context, not an architecture dimension.",
        size=7.2,
        color=GRAY,
    )
    page.save()


def literature_synthesis() -> None:
    page = Page("literature_framework_synthesis.pdf", 4.8)

    page.header(0.155, "Literature stream")
    page.header(0.50, "Research gap")
    page.header(0.845, "Framework response")

    lanes = [
        (
            "Agent architectures\n& multi-agent systems",
            "Ambiguous categories and\nweak deployment comparability",
            "FM1",
            "Architecture taxonomy",
            NAVY,
            LIGHT_NAVY,
        ),
        (
            "Operational evaluation\n& agent benchmarks",
            "Model-centric scores omit\nworkflow mapping and coverage",
            "FM2",
            "Metrics and Telemetry\nOnboarding contract",
            TEAL,
            LIGHT_TEAL,
        ),
        (
            "Productivity studies\n& field evidence",
            "Baselines, comparators and\nclaim strength are inconsistent",
            "FM3",
            "Evaluation Designs",
            NAVY,
            LIGHT_NAVY,
        ),
        (
            "Responsible AI,\nobservability & governance",
            "Privacy, traceability and\nrepeatability remain fragmented",
            "FM4",
            "Validity and Governance",
            TEAL,
            LIGHT_TEAL,
        ),
    ]
    row_y = [0.735, 0.545, 0.355, 0.165]

    for y, lane in zip(row_y, lanes, strict=True):
        stream, gap, fm, response, accent, light = lane
        page.rounded_box(
            0.025,
            y,
            0.26,
            0.125,
            title=stream,
            face=LIGHT_GRAY,
            edge=MID,
            title_size=8.1,
            linewidth=1.0,
        )
        page.rounded_box(
            0.365,
            y,
            0.27,
            0.125,
            title=gap,
            face=LIGHT_ORANGE,
            edge=ORANGE,
            title_size=7.9,
            linewidth=1.0,
        )
        page.rounded_box(
            0.705,
            y,
            0.27,
            0.125,
            title=fm,
            body=response,
            face=light,
            edge=accent,
            title_color=accent,
            title_size=9.0,
            body_size=7.4,
        )
        page.arrow((0.287, y + 0.0625), (0.36, y + 0.0625), color=ORANGE)
        page.arrow((0.637, y + 0.0625), (0.70, y + 0.0625), color=accent)

    # A shared bracket makes clear that all four responses form the integrated
    # framework; the bottom response is not treated as the sole contributor.
    response_centers = [y + 0.0625 for y in row_y]
    spine_x = 0.992
    for center_y in response_centers:
        page.line((0.977, center_y), (spine_x, center_y), color=TEAL, width=0.9)
    page.line(
        (spine_x, response_centers[-1]), (spine_x, response_centers[0]), color=TEAL, width=0.9
    )
    page.line((spine_x, response_centers[-1]), (spine_x, 0.055), color=TEAL, width=0.9)
    page.arrow((spine_x, 0.055), (0.852, 0.055), color=TEAL, width=1.15)
    page.rounded_box(
        0.325,
        0.018,
        0.52,
        0.073,
        title="Integrated, workflow-run evaluation framework",
        face=WHITE,
        edge=TEAL,
        title_color=TEAL,
        title_size=8.3,
        linewidth=1.0,
    )
    page.save()


def framework_overview() -> None:
    page = Page("framework_overview.pdf", 4.9)

    page.header(0.115, "Evaluation context")
    page.header(0.505, "Mutually constraining components")
    page.header(0.895, "Evaluation outputs")

    input_nodes = [
        "Enterprise source\nstructure",
        "Workflow boundary",
        "Baseline + comparator",
        "Telemetry, window +\nprivacy constraints",
    ]
    input_y = [0.73, 0.57, 0.41, 0.25]
    for label, y in zip(input_nodes, input_y, strict=True):
        page.rounded_box(
            0.025,
            y,
            0.18,
            0.105,
            title=label,
            face=LIGHT_GRAY,
            edge=MID,
            title_size=7.7,
            linewidth=0.95,
        )

    modules = [
        (
            0.275,
            0.675,
            "FM1",
            "Architecture taxonomy",
            "What system is evaluated?",
            NAVY,
            LIGHT_NAVY,
        ),
        (
            0.545,
            0.675,
            "FM2",
            "Metrics and Telemetry",
            "What can be mapped\nand observed?",
            TEAL,
            LIGHT_TEAL,
        ),
        (0.275, 0.285, "FM3", "Evaluation Designs", "What may be claimed?", NAVY, LIGHT_NAVY),
        (
            0.545,
            0.285,
            "FM4",
            "Validity and Governance",
            "What may be retained\nand trusted?",
            TEAL,
            LIGHT_TEAL,
        ),
    ]
    for x, y, fm, name, question, accent, light in modules:
        page.rounded_box(
            x,
            y,
            0.18,
            0.17,
            title=f"{fm}\n{name}",
            body=question,
            face=light,
            edge=accent,
            title_size=7.6,
            body_size=7.0,
        )

    page.polygon(
        [(0.50, 0.625), (0.555, 0.565), (0.50, 0.505), (0.445, 0.565)],
        face=LIGHT_ORANGE,
        edge=ORANGE,
        width=1.1,
    )
    page.text(
        0.50,
        0.565,
        "Tier\n& claim\nboundary",
        size=6.7,
        color=DARK,
        bold=True,
        leading=0.98,
    )

    page.arrow((0.438, 0.675), (0.47, 0.61), color=GRAY, width=0.9, head=4.0, both=True)
    page.arrow((0.562, 0.675), (0.53, 0.61), color=GRAY, width=0.9, head=4.0, both=True)
    page.arrow((0.438, 0.455), (0.47, 0.52), color=GRAY, width=0.9, head=4.0, both=True)
    page.arrow((0.562, 0.455), (0.53, 0.52), color=GRAY, width=0.9, head=4.0, both=True)

    page.arrow((0.207, 0.545), (0.268, 0.545), color=NAVY, width=1.4, head=6.0)
    page.arrow((0.732, 0.545), (0.793, 0.545), color=TEAL, width=1.4, head=6.0)

    page.rounded_box(
        0.79,
        0.57,
        0.19,
        0.265,
        title="Tiered outputs",
        body="Mapping-only evidence\nPartial or complete metrics\nCoverage + diagnostics",
        face=LIGHT_NAVY,
        edge=NAVY,
        title_size=8.5,
        body_size=6.6,
    )
    page.rounded_box(
        0.79,
        0.25,
        0.19,
        0.265,
        title="Onboarding & controls",
        body="Mapping friction + issues\nApprovals + quarantine\nHashes + redaction",
        face=LIGHT_TEAL,
        edge=TEAL,
        title_size=8.5,
        body_size=6.8,
    )

    page.save()


def main() -> None:
    thesis_research_map()
    architecture_profile()
    literature_synthesis()
    framework_overview()


if __name__ == "__main__":
    main()
