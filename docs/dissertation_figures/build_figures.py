#!/usr/bin/env python3
"""Render dissertation control/monitoring PDF figures from editable sources.

This script intentionally uses only the Python standard library so the figure
set can be rebuilt on a clean lab workstation without Graphviz, Mermaid,
Inkscape, LaTeX, Node, or third-party Python packages.
"""

from __future__ import annotations

import argparse
import html
import math
import sys
import textwrap
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True

from figure_sources import FIGURES, PALETTE


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "generated"
FONT_FAMILY = "Arial, Helvetica, sans-serif"
DEFAULT_STROKE = "#59636e"
TITLE_COLOR = "#17212b"
CAPTION_COLOR = "#48525f"
MM_TO_PT = 72.0 / 25.4


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return (0.0, 0.0, 0.0)
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def palette(group: str | None) -> dict[str, str]:
    if group and group in PALETTE:
        return PALETTE[group]
    return {"fill": "#ffffff", "stroke": DEFAULT_STROKE, "text": "#222222", "label": group or ""}


def approx_width(text: str, size: float) -> float:
    width = 0.0
    for ch in text:
        if ch in "il.,:;|!":
            width += 0.28 * size
        elif ch in "MW@#%":
            width += 0.82 * size
        elif ch in "→←↑↓≤≥":
            width += 0.62 * size
        elif ch == " ":
            width += 0.32 * size
        else:
            width += 0.55 * size
    return width


def rich_segments(text: str) -> list[tuple[str, float, float]]:
    """Return text segments with size and baseline adjustments for _{...}."""
    segments: list[tuple[str, float, float]] = []
    idx = 0
    while idx < len(text):
        marker = text.find("_{", idx)
        if marker < 0:
            if idx < len(text):
                segments.append((text[idx:], 1.0, 0.0))
            break
        if marker > idx:
            segments.append((text[idx:marker], 1.0, 0.0))
        end = text.find("}", marker + 2)
        if end < 0:
            segments.append((text[marker:], 1.0, 0.0))
            break
        subscript = text[marker + 2 : end]
        if subscript:
            segments.append((subscript, 0.72, 0.22))
        idx = end + 1
    return segments


def rich_width(text: str, size: float) -> float:
    return sum(approx_width(segment, size * scale) for segment, scale, _ in rich_segments(text))


def wrap_lines(text: object, max_width: float, size: float) -> list[str]:
    raw = "" if text is None else str(text)
    lines: list[str] = []
    max_chars = max(8, int(max_width / max(size * 0.52, 1)))
    for part in raw.splitlines() or [""]:
        part = part.strip()
        if not part:
            lines.append("")
            continue
        if rich_width(part, size) <= max_width:
            lines.append(part)
            continue
        wrapped = textwrap.wrap(
            part,
            width=max_chars,
            break_long_words=False,
            break_on_hyphens=False,
        )
        lines.extend(wrapped or [part])
    return lines


def arrow_head(points: list[list[float]], size: float = 13.0) -> list[tuple[float, float]]:
    if len(points) < 2:
        return []
    x2, y2 = points[-1]
    x1, y1 = points[-2]
    while len(points) > 2 and abs(x2 - x1) < 1e-6 and abs(y2 - y1) < 1e-6:
        points = points[:-1]
        x2, y2 = points[-1]
        x1, y1 = points[-2]
    angle = math.atan2(y2 - y1, x2 - x1)
    left = (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6))
    right = (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6))
    return [(x2, y2), left, right]


def path_midpoint(points: list[list[float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return tuple(points[0])  # type: ignore[return-value]
    lengths: list[float] = []
    total = 0.0
    for a, b in zip(points, points[1:]):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        lengths.append(length)
        total += length
    target = total / 2.0
    walked = 0.0
    for idx, length in enumerate(lengths):
        if walked + length >= target and length > 0:
            a = points[idx]
            b = points[idx + 1]
            frac = (target - walked) / length
            return (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
        walked += length
    return tuple(points[-1])  # type: ignore[return-value]


class SvgCanvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.items: list[str] = []

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        stroke: str = DEFAULT_STROKE,
        stroke_width: float = 2.0,
        radius: float = 10.0,
    ) -> None:
        self.items.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'rx="{radius:.2f}" ry="{radius:.2f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{stroke_width:.2f}"/>'
        )

    def line(
        self,
        points: list[list[float]],
        color: str = DEFAULT_STROKE,
        width: float = 2.4,
        dashed: bool = False,
    ) -> None:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        dash = ' stroke-dasharray="8 7"' if dashed else ""
        self.items.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="{width:.2f}" stroke-linecap="round" stroke-linejoin="round"{dash}/>'
        )

    def polygon(
        self,
        points: Iterable[tuple[float, float]],
        fill: str,
        stroke: str | None = None,
        stroke_width: float = 0.0,
    ) -> None:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        if stroke and stroke_width:
            self.items.append(
                f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="{stroke_width:.2f}"/>'
            )
        else:
            self.items.append(f'<polygon points="{pts}" fill="{fill}"/>')

    def text(
        self,
        x: float,
        y: float,
        lines: list[str],
        size: float,
        color: str,
        weight: str = "400",
        anchor: str = "start",
        line_height: float = 1.22,
    ) -> None:
        if not lines:
            return
        if any("_{" in line for line in lines):
            for idx, line in enumerate(lines):
                line_width = rich_width(line, size)
                line_x = x
                if anchor == "middle":
                    line_x = x - line_width / 2.0
                elif anchor == "end":
                    line_x = x - line_width
                baseline_y = y + size + idx * size * line_height
                cur_x = line_x
                for segment, scale, dy_factor in rich_segments(line):
                    if not segment:
                        continue
                    seg_size = size * scale
                    self.items.append(
                        f'<text x="{cur_x:.2f}" y="{baseline_y + size * dy_factor:.2f}" '
                        f'fill="{color}" font-family="{FONT_FAMILY}" font-size="{seg_size:.2f}" '
                        f'font-weight="{weight}" text-anchor="start">{escape(segment)}</text>'
                    )
                    cur_x += approx_width(segment, seg_size)
            return
        self.items.append(
            f'<text x="{x:.2f}" y="{y + size:.2f}" fill="{color}" '
            f'font-family="{FONT_FAMILY}" font-size="{size:.2f}" '
            f'font-weight="{weight}" text-anchor="{anchor}">'
        )
        for idx, line in enumerate(lines):
            dy = 0 if idx == 0 else size * line_height
            self.items.append(
                f'<tspan x="{x:.2f}" dy="{dy:.2f}">{escape(line)}</tspan>'
            )
        self.items.append("</text>")

    def output(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img">\n'
            "<defs>\n"
            "<style><![CDATA[\n"
            "text { dominant-baseline: auto; }\n"
            "]]></style>\n"
            "</defs>\n"
            f'<rect x="0" y="0" width="{self.width}" height="{self.height}" fill="#ffffff"/>\n'
            + "\n".join(self.items)
            + "\n</svg>\n"
        )


class PdfCanvas:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self.ops: list[str] = []

    def _y(self, y: float) -> float:
        return self.height - y

    def _rect_y(self, y: float, h: float) -> float:
        return self.height - y - h

    def _set_stroke(self, color: str) -> str:
        r, g, b = hex_to_rgb(color)
        return f"{r:.4f} {g:.4f} {b:.4f} RG"

    def _set_fill(self, color: str) -> str:
        r, g, b = hex_to_rgb(color)
        return f"{r:.4f} {g:.4f} {b:.4f} rg"

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        stroke: str = DEFAULT_STROKE,
        stroke_width: float = 2.0,
        radius: float = 0.0,
    ) -> None:
        del radius
        self.ops.append(
            "q\n"
            f"{self._set_fill(fill)}\n{self._set_stroke(stroke)}\n{stroke_width:.2f} w\n"
            f"{x:.2f} {self._rect_y(y, h):.2f} {w:.2f} {h:.2f} re B\n"
            "Q"
        )

    def line(
        self,
        points: list[list[float]],
        color: str = DEFAULT_STROKE,
        width: float = 2.4,
        dashed: bool = False,
    ) -> None:
        if len(points) < 2:
            return
        dash = "[8 7] 0 d" if dashed else "[] 0 d"
        parts = [
            "q",
            self._set_stroke(color),
            f"{width:.2f} w",
            dash,
            f"{points[0][0]:.2f} {self._y(points[0][1]):.2f} m",
        ]
        for x, y in points[1:]:
            parts.append(f"{x:.2f} {self._y(y):.2f} l")
        parts.extend(["S", "Q"])
        self.ops.append("\n".join(parts))

    def polygon(
        self,
        points: Iterable[tuple[float, float]],
        fill: str,
        stroke: str | None = None,
        stroke_width: float = 0.0,
    ) -> None:
        pts = list(points)
        if len(pts) < 3:
            return
        parts = [
            "q",
            self._set_fill(fill),
            self._set_stroke(stroke or fill),
            f"{stroke_width:.2f} w",
            f"{pts[0][0]:.2f} {self._y(pts[0][1]):.2f} m",
        ]
        for x, y in pts[1:]:
            parts.append(f"{x:.2f} {self._y(y):.2f} l")
        parts.extend(["h B" if stroke and stroke_width else "h f", "Q"])
        self.ops.append("\n".join(parts))

    def text(
        self,
        x: float,
        y: float,
        lines: list[str],
        size: float,
        color: str,
        weight: str = "400",
        anchor: str = "start",
        line_height: float = 1.22,
    ) -> None:
        if not lines:
            return
        font = "F2" if str(weight) in {"600", "700", "bold"} else "F1"
        symbol_codes = {"→": 174, "≤": 163, "≥": 179}

        def emit_text(parts: list[str], draw_font: str, draw_size: float, draw_x: float, draw_y: float, text: str) -> None:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            parts.append(
                f"BT /{draw_font} {draw_size:.2f} Tf {draw_x:.2f} {self._y(draw_y):.2f} Td ({safe}) Tj ET"
            )

        parts = ["q", self._set_fill(color)]
        for idx, line in enumerate(lines):
            line_x = x
            if anchor == "middle":
                line_x = x - rich_width(line, size) / 2.0
            elif anchor == "end":
                line_x = x - rich_width(line, size)
            baseline = y + size + idx * size * line_height
            cur_x = line_x
            for segment, scale, dy_factor in rich_segments(line):
                if not segment:
                    continue
                seg_size = size * scale
                chunk = ""
                for ch in segment:
                    if ch in symbol_codes:
                        if chunk:
                            emit_text(parts, font, seg_size, cur_x, baseline + size * dy_factor, chunk)
                            cur_x += approx_width(chunk, seg_size)
                            chunk = ""
                        parts.append(
                            f"BT /F3 {seg_size:.2f} Tf {cur_x:.2f} {self._y(baseline + size * dy_factor):.2f} Td "
                            f"(\\{symbol_codes[ch]:03o}) Tj ET"
                        )
                        cur_x += approx_width(ch, seg_size)
                    else:
                        chunk += ch
                if chunk:
                    emit_text(parts, font, seg_size, cur_x, baseline + size * dy_factor, chunk)
                    cur_x += approx_width(chunk, seg_size)
        parts.append("Q")
        self.ops.append("\n".join(parts))

    def output(self) -> bytes:
        stream = "\n".join(self.ops).encode("latin-1", errors="replace")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.width:.2f} {self.height:.2f}] "
                "/Resources << /Font << /F1 4 0 R /F2 5 0 R /F3 6 0 R >> >> /Contents 7 0 R >>"
            ).encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Symbol >>",
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        ]
        chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
        offsets = [0]
        for idx, obj in enumerate(objects, start=1):
            offsets.append(sum(len(chunk) for chunk in chunks))
            chunks.append(f"{idx} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
        xref_offset = sum(len(chunk) for chunk in chunks)
        chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        chunks.append(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            chunks.append(f"{off:010d} 00000 n \n".encode("ascii"))
        chunks.append(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_offset}\n%%EOF\n"
            ).encode("ascii")
        )
        return b"".join(chunks)


class TransformedPdfCanvas:
    """Scale and crop source drawing coordinates onto a target PDF page."""

    def __init__(self, target: PdfCanvas, scale: float, offset_x: float, offset_y: float) -> None:
        self.target = target
        self.scale = scale
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.width = target.width
        self.height = target.height

    def _x(self, x: float) -> float:
        return (x - self.offset_x) * self.scale

    def _y(self, y: float) -> float:
        return (y - self.offset_y) * self.scale

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        stroke: str = DEFAULT_STROKE,
        stroke_width: float = 2.0,
        radius: float = 0.0,
    ) -> None:
        self.target.rect(
            self._x(x),
            self._y(y),
            w * self.scale,
            h * self.scale,
            fill,
            stroke=stroke,
            stroke_width=stroke_width * self.scale,
            radius=radius * self.scale,
        )

    def line(
        self,
        points: list[list[float]],
        color: str = DEFAULT_STROKE,
        width: float = 2.4,
        dashed: bool = False,
    ) -> None:
        self.target.line(
            [[self._x(x), self._y(y)] for x, y in points],
            color=color,
            width=width * self.scale,
            dashed=dashed,
        )

    def polygon(
        self,
        points: Iterable[tuple[float, float]],
        fill: str,
        stroke: str | None = None,
        stroke_width: float = 0.0,
    ) -> None:
        self.target.polygon(
            [(self._x(x), self._y(y)) for x, y in points],
            fill=fill,
            stroke=stroke,
            stroke_width=stroke_width * self.scale,
        )

    def text(
        self,
        x: float,
        y: float,
        lines: list[str],
        size: float,
        color: str,
        weight: str = "400",
        anchor: str = "start",
        line_height: float = 1.22,
    ) -> None:
        self.target.text(
            self._x(x),
            self._y(y),
            lines,
            size * self.scale,
            color,
            weight=weight,
            anchor=anchor,
            line_height=line_height,
        )


def draw_text_block(
    canvas: SvgCanvas | PdfCanvas,
    x: float,
    y: float,
    w: float,
    text: object,
    size: float,
    color: str,
    weight: str = "400",
    anchor: str = "start",
    line_height: float = 1.22,
) -> float:
    lines = wrap_lines(text, w, size)
    canvas.text(x, y, lines, size, color, weight=weight, anchor=anchor, line_height=line_height)
    return len(lines) * size * line_height


def draw_box(canvas: SvgCanvas | PdfCanvas, element: dict) -> None:
    group = element.get("group")
    colors = palette(group)
    if element.get("type") == "note":
        group = "note"
        colors = palette("note")
    x, y, w, h = (float(element[k]) for k in ("x", "y", "w", "h"))
    fill = element.get("fill", colors["fill"])
    stroke = element.get("stroke", colors["stroke"])
    text_color = element.get("text", colors["text"])
    dashed_border = bool(element.get("dashed_border", False))
    canvas.rect(x, y, w, h, fill=fill, stroke=fill if dashed_border else stroke, stroke_width=0.0 if dashed_border else 2.2, radius=10)
    if dashed_border:
        canvas.line([[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]], color=stroke, width=2.2, dashed=True)
    pad = float(element.get("pad", 15))
    label = element.get("label", "")
    body = element.get("body", "")
    label_size = float(element.get("label_size", 23))
    body_size = float(element.get("body_size", 18))
    if element.get("center_text"):
        text_w = w - 2 * pad
        line_height = float(element.get("line_height", 1.16))
        label_lines = wrap_lines(label, text_w, label_size)
        body_lines = wrap_lines(body, text_w, body_size) if body else []
        label_h = len(label_lines) * label_size * line_height
        body_h = len(body_lines) * body_size * line_height
        gap = 9 if body_lines else 0
        cur_y = y + (h - label_h - gap - body_h) / 2
        canvas.text(x + w / 2, cur_y, label_lines, label_size, text_color, weight="700", anchor="middle", line_height=line_height)
        if body_lines:
            canvas.text(x + w / 2, cur_y + label_h + gap, body_lines, body_size, text_color, anchor="middle", line_height=line_height)
        return
    used = draw_text_block(canvas, x + pad, y + pad, w - 2 * pad, label, label_size, text_color, weight="700")
    if body:
        draw_text_block(canvas, x + pad, y + pad + used + 9, w - 2 * pad, body, body_size, text_color)


def draw_flow_node(canvas: SvgCanvas | PdfCanvas, element: dict) -> None:
    group = element.get("group")
    colors = palette(group)
    x, y, w, h = (float(element[k]) for k in ("x", "y", "w", "h"))
    fill = element.get("fill", colors["fill"])
    stroke = element.get("stroke", colors["stroke"])
    text_color = element.get("text", colors["text"])
    stroke_width = float(element.get("stroke_width", 2.2))
    kind = element.get("type")

    if kind == "decision":
        points = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
        text_w = float(element.get("text_w", w * 0.58))
    elif kind == "io":
        slant = float(element.get("slant", min(34, w * 0.14)))
        points = [(x + slant, y), (x + w, y), (x + w - slant, y + h), (x, y + h)]
        text_w = float(element.get("text_w", w - 2 * slant - 20))
    elif kind == "terminator":
        r = h / 2
        points = [
            (x + r, y),
            (x + w - r, y),
            (x + w - r / 2, y + h * 0.15),
            (x + w, y + h / 2),
            (x + w - r / 2, y + h * 0.85),
            (x + w - r, y + h),
            (x + r, y + h),
            (x + r / 2, y + h * 0.85),
            (x, y + h / 2),
            (x + r / 2, y + h * 0.15),
        ]
        text_w = float(element.get("text_w", w - h - 10))
    else:
        points = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        text_w = float(element.get("text_w", w - 30))

    canvas.polygon(points, fill=fill, stroke=stroke, stroke_width=stroke_width)

    label = element.get("label", "")
    body = element.get("body", "")
    label_size = float(element.get("label_size", 22))
    body_size = float(element.get("body_size", 16))
    gap = float(element.get("text_gap", 8 if body else 0))
    line_height = float(element.get("line_height", 1.16))
    label_lines = wrap_lines(label, text_w, label_size)
    body_lines = wrap_lines(body, text_w, body_size) if body else []
    label_h = len(label_lines) * label_size * line_height
    body_h = len(body_lines) * body_size * line_height
    total_h = label_h + (gap if body_lines else 0) + body_h
    cur_y = y + (h - total_h) / 2
    canvas.text(x + w / 2, cur_y, label_lines, label_size, text_color, weight="700", anchor="middle", line_height=line_height)
    if body_lines:
        canvas.text(x + w / 2, cur_y + label_h + gap, body_lines, body_size, text_color, anchor="middle", line_height=line_height)


def draw_panel(canvas: SvgCanvas | PdfCanvas, element: dict) -> None:
    x, y, w, h = (float(element[k]) for k in ("x", "y", "w", "h"))
    canvas.rect(
        x,
        y,
        w,
        h,
        fill=element.get("fill", "#ffffff"),
        stroke=element.get("stroke", DEFAULT_STROKE),
        stroke_width=float(element.get("stroke_width", 2.0)),
        radius=float(element.get("radius", 10.0)),
    )


def draw_arrow(canvas: SvgCanvas | PdfCanvas, element: dict) -> None:
    group = element.get("group")
    colors = palette(group)
    color = element.get("color", colors["stroke"] if group else DEFAULT_STROKE)
    points = [[float(x), float(y)] for x, y in element["points"]]
    dashed = bool(element.get("dashed", False))
    canvas.line(points, color=color, width=float(element.get("width", 2.6)), dashed=dashed)
    head = arrow_head([p[:] for p in points], size=float(element.get("head_size", 14)))
    canvas.polygon(head, fill=color)
    label = element.get("label")
    if label:
        mx, my = path_midpoint(points)
        ox = float(element.get("label_dx", 0))
        oy = float(element.get("label_dy", -26))
        size = float(element.get("label_size", 15))
        lines = wrap_lines(label, float(element.get("label_w", 210)), size)
        inferred_w = max((rich_width(line, size) for line in lines), default=0) + 16
        label_w = float(element.get("label_box_w", inferred_w))
        label_h = len(lines) * (size * 1.2) + 8
        canvas.rect(mx + ox - label_w / 2, my + oy - 16, label_w, label_h, fill="#ffffff", stroke="#d7dde3", stroke_width=1.0, radius=6)
        canvas.text(mx + ox, my + oy - 12, lines, size, "#303942", weight="600", anchor="middle", line_height=1.12)


def draw_wire(canvas: SvgCanvas | PdfCanvas, element: dict, show_label: bool = True) -> None:
    group = element.get("group")
    colors = palette(group)
    color = element.get("color", colors["stroke"] if group else DEFAULT_STROKE)
    points = [[float(x), float(y)] for x, y in element["points"]]
    canvas.line(points, color=color, width=float(element.get("width", 2.4)), dashed=bool(element.get("dashed", False)))
    label = element.get("label")
    if show_label and label:
        mx, my = path_midpoint(points)
        ox = float(element.get("label_dx", 0))
        oy = float(element.get("label_dy", -24))
        size = float(element.get("label_size", 14))
        lines = wrap_lines(label, float(element.get("label_w", 170)), size)
        inferred_w = max((rich_width(line, size) for line in lines), default=0) + 16
        label_w = float(element.get("label_box_w", inferred_w))
        label_h = len(lines) * (size * 1.15) + 8
        canvas.rect(mx + ox - label_w / 2, my + oy - 16, label_w, label_h, fill="#ffffff", stroke="#d7dde3", stroke_width=1.0, radius=6)
        canvas.text(mx + ox, my + oy - 12, lines, size, "#303942", weight="600", anchor="middle", line_height=1.08)


def draw_legend(canvas: SvgCanvas | PdfCanvas, element: dict) -> None:
    x = float(element["x"])
    y = float(element["y"])
    items = element.get("items", [])
    font_size = float(element.get("font_size", 14))
    swatch_w = float(element.get("swatch_w", 22))
    swatch_h = float(element.get("swatch_h", 14))
    gap = float(element.get("gap", 28))
    text_gap = float(element.get("text_gap", 8))
    swatch_dy = float(element.get("swatch_dy", 0))
    item_w = element.get("item_w")
    item_align = element.get("item_align", "left")
    sample_style = element.get("sample_style", "box")
    sample_width = float(element.get("sample_width", swatch_w))
    sample_thickness = float(element.get("sample_thickness", 3.0))
    cur_x = x
    for idx, group in enumerate(items):
        if item_w is not None:
            cur_x = x + idx * float(item_w)
        colors = palette(group)
        label = colors.get("label", str(group))
        sample_w = sample_width if sample_style == "line" else swatch_w
        label_w = rich_width(label, font_size)
        if item_w is not None and item_align == "center":
            cur_x += max(0.0, (float(item_w) - (sample_w + text_gap + label_w)) / 2.0)
        if sample_style == "line":
            sample_y = y + swatch_dy + swatch_h / 2.0
            canvas.line([[cur_x, sample_y], [cur_x + sample_w, sample_y]], color=colors["stroke"], width=sample_thickness)
        else:
            canvas.rect(cur_x, y + swatch_dy, swatch_w, swatch_h, fill=colors["fill"], stroke=colors["stroke"], stroke_width=1.5, radius=3)
        label_y = y + (swatch_h - font_size) / 2.0
        canvas.text(cur_x + sample_w + text_gap, label_y, [label], font_size, "#34404a", weight="600")
        if item_w is None:
            cur_x += sample_w + text_gap + label_w + gap


def draw_table(canvas: SvgCanvas | PdfCanvas, element: dict) -> None:
    x = float(element["x"])
    y = float(element["y"])
    w = float(element["w"])
    row_h = float(element.get("row_h", 82))
    font_size = float(element.get("font_size", 17))
    columns = element["columns"]
    rows = element["rows"]
    header_h = float(element.get("header_h", 52))
    total_h = header_h + row_h * len(rows)
    canvas.rect(x, y, w, total_h, fill="#ffffff", stroke="#6d7782", stroke_width=2.0, radius=6)
    canvas.rect(x, y, w, header_h, fill="#26323d", stroke="#26323d", stroke_width=1.0, radius=6)

    cur_x = x
    for label, col_w in columns:
        canvas.text(cur_x + 12, y + 12, [str(label)], 18, "#ffffff", weight="700")
        cur_x += float(col_w)
        canvas.line([[cur_x, y], [cur_x, y + total_h]], color="#c8d0d8", width=1.0)

    for row_idx, row in enumerate(rows):
        row_y = y + header_h + row_idx * row_h
        fill = "#f8fafc" if row_idx % 2 == 0 else "#ffffff"
        canvas.rect(x, row_y, w, row_h, fill=fill, stroke="#e0e6ec", stroke_width=0.8, radius=0)
        cur_x = x
        for col_idx, cell in enumerate(row):
            col_w = float(columns[col_idx][1])
            size = font_size
            max_lines = max(2, int((row_h - 16) / (size * 1.12)))
            lines = wrap_lines(cell, col_w - 22, size)
            if len(lines) > max_lines:
                size = max(12, size - 2)
                lines = wrap_lines(cell, col_w - 22, size)
            if len(lines) > max_lines:
                lines = lines[: max_lines - 1] + [lines[max_lines - 1].rstrip(".") + "..."]
            color = "#17212b" if col_idx == 0 else "#303942"
            weight = "700" if col_idx == 0 else "400"
            canvas.text(cur_x + 12, row_y + 10, lines, size, color, weight=weight, line_height=1.12)
            cur_x += col_w
        canvas.line([[x, row_y], [x + w, row_y]], color="#dce3e9", width=0.8)


class DrawioCanvas:
    """Minimal diagrams.net/draw.io writer for Lucidchart import."""

    def __init__(self, width: int, height: int, name: str) -> None:
        self.width = width
        self.height = height
        self.next_id = 2
        self.mxfile = ET.Element(
            "mxfile",
            {
                "host": "app.diagrams.net",
                "modified": "1970-01-01T00:00:00Z",
                "agent": "Codex",
                "version": "24.7.17",
                "type": "device",
            },
        )
        diagram = ET.SubElement(self.mxfile, "diagram", {"id": self._id("diagram"), "name": name})
        self.model = ET.SubElement(
            diagram,
            "mxGraphModel",
            {
                "dx": str(width),
                "dy": str(height),
                "grid": "1",
                "gridSize": "10",
                "guides": "1",
                "tooltips": "1",
                "connect": "1",
                "arrows": "1",
                "fold": "1",
                "page": "1",
                "pageScale": "1",
                "pageWidth": str(width),
                "pageHeight": str(height),
                "math": "0",
                "shadow": "0",
            },
        )
        self.root = ET.SubElement(self.model, "root")
        ET.SubElement(self.root, "mxCell", {"id": "0"})
        ET.SubElement(self.root, "mxCell", {"id": "1", "parent": "0"})

    def _id(self, prefix: str = "cell") -> str:
        value = f"{prefix}-{self.next_id}"
        self.next_id += 1
        return value

    def add_vertex(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        value: str,
        style: str,
        cell_id: str | None = None,
    ) -> None:
        cell = ET.SubElement(
            self.root,
            "mxCell",
            {
                "id": cell_id or self._id(),
                "value": value,
                "style": style,
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": f"{x:.2f}",
                "y": f"{y:.2f}",
                "width": f"{w:.2f}",
                "height": f"{h:.2f}",
                "as": "geometry",
            },
        )

    def add_edge(
        self,
        points: list[list[float]],
        color: str,
        width: float,
        dashed: bool = False,
        rounded: bool = False,
    ) -> None:
        if len(points) < 2:
            return
        dash_style = "dashed=1;dashPattern=8 7;" if dashed else ""
        style = (
            "edgeStyle=orthogonalEdgeStyle;orthogonalLoop=1;jettySize=auto;html=1;"
            f"rounded={1 if rounded else 0};startArrow=none;endArrow=none;"
            f"strokeColor={color};strokeWidth={width:.2f};{dash_style}"
        )
        cell = ET.SubElement(
            self.root,
            "mxCell",
            {
                "id": self._id("edge"),
                "value": "",
                "style": style,
                "edge": "1",
                "parent": "1",
            },
        )
        geometry = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        ET.SubElement(geometry, "mxPoint", {"x": f"{points[0][0]:.2f}", "y": f"{points[0][1]:.2f}", "as": "sourcePoint"})
        ET.SubElement(geometry, "mxPoint", {"x": f"{points[-1][0]:.2f}", "y": f"{points[-1][1]:.2f}", "as": "targetPoint"})
        if len(points) > 2:
            array = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in points[1:-1]:
                ET.SubElement(array, "mxPoint", {"x": f"{x:.2f}", "y": f"{y:.2f}"})

    def output(self) -> bytes:
        ET.indent(self.mxfile, space="  ")
        return ET.tostring(self.mxfile, encoding="utf-8", xml_declaration=True)


def drawio_value(label: object, body: object = "", label_size: float = 20, body_size: float = 13) -> str:
    safe_label = html.escape(str(label)).replace("\n", "<br>")
    safe_body = html.escape(str(body)).replace("\n", "<br>")
    parts = [
        f'<div style="font-size:{label_size:.0f}px;font-weight:700;line-height:1.15">{safe_label}</div>'
    ]
    if safe_body:
        parts.append(f'<div style="font-size:{body_size:.0f}px;line-height:1.18;margin-top:8px">{safe_body}</div>')
    return "".join(parts)


def drawio_text_value(text: object, size: float, bold: bool = False) -> str:
    weight = "700" if bold else "400"
    safe_text = html.escape(str(text)).replace("\n", "<br>")
    return f'<div style="font-size:{size:.0f}px;font-weight:{weight};line-height:1.2">{safe_text}</div>'


def drawio_box(canvas: DrawioCanvas, element: dict) -> None:
    colors = palette("note" if element.get("type") == "note" else element.get("group"))
    x, y, w, h = (float(element[k]) for k in ("x", "y", "w", "h"))
    fill = element.get("fill", colors["fill"])
    stroke = element.get("stroke", colors["stroke"])
    text_color = element.get("text", colors["text"])
    label_size = float(element.get("label_size", 20))
    body_size = float(element.get("body_size", 13))
    pad = float(element.get("pad", 15))
    value = drawio_value(element.get("label", ""), element.get("body", ""), label_size, body_size)
    style = (
        "rounded=1;whiteSpace=wrap;html=1;arcSize=8;absoluteArcSize=1;"
        f"fillColor={fill};strokeColor={stroke};strokeWidth=2.2;fontColor={text_color};"
        f"align=left;verticalAlign=top;spacingLeft={pad:.0f};spacingRight={pad:.0f};spacingTop={pad:.0f};"
    )
    canvas.add_vertex(x, y, w, h, value, style)


def drawio_wire(canvas: DrawioCanvas, element: dict) -> None:
    colors = palette(element.get("group"))
    color = element.get("color", colors["stroke"])
    points = [[float(x), float(y)] for x, y in element["points"]]
    canvas.add_edge(
        points,
        color=color,
        width=float(element.get("width", 2.4)),
        dashed=bool(element.get("dashed", False)),
    )


def drawio_legend(canvas: DrawioCanvas, element: dict) -> None:
    x = float(element["x"])
    y = float(element["y"])
    cur_x = x
    for group in element.get("items", []):
        colors = palette(group)
        label = colors.get("label", str(group))
        rect_style = (
            f"rounded=0;whiteSpace=wrap;html=1;fillColor={colors['fill']};"
            f"strokeColor={colors['stroke']};strokeWidth=1.5;"
        )
        canvas.add_vertex(cur_x, y, 22, 14, "", rect_style)
        text_style = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;fontColor=#34404a;"
        canvas.add_vertex(cur_x + 30, y - 4, approx_width(label, 14) + 8, 24, drawio_text_value(label, 14, bold=True), text_style)
        cur_x += 30 + approx_width(label, 14) + 28


def drawio_title(canvas: DrawioCanvas, figure: dict) -> None:
    width = int(figure["size"][0])
    title_style = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;whiteSpace=wrap;fontColor=#17212b;"
    canvas.add_vertex(45, 26, width - 90, 44, drawio_text_value(figure["title"], 30, bold=True), title_style)
    caption = figure.get("caption", "")
    separator_y = 112
    if caption:
        cap_lines = wrap_lines(caption, min(width - 90, 1000), 16)
        cap_h = max(44, len(cap_lines) * 20)
        cap_style = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;whiteSpace=wrap;fontColor=#48525f;"
        canvas.add_vertex(45, 78, min(width - 90, 1000), cap_h, drawio_text_value(caption, 16), cap_style)
        separator_y = max(separator_y, 78 + cap_h + 14)
    canvas.add_edge([[45, separator_y], [width - 45, separator_y]], color="#ccd4dc", width=1.3)


def render_drawio_figure(figure: dict) -> DrawioCanvas:
    width, height = (int(v) for v in figure["size"])
    canvas = DrawioCanvas(width, height, str(figure["title"]))
    if figure.get("show_title", True):
        drawio_title(canvas, figure)
    for element in figure.get("elements", []):
        if element.get("type") == "wire":
            drawio_wire(canvas, element)
    for element in figure.get("elements", []):
        kind = element.get("type")
        if kind == "wire":
            continue
        if kind in {"box", "note"}:
            drawio_box(canvas, element)
        elif kind == "legend":
            drawio_legend(canvas, element)
        elif kind in {"arrow", "table"}:
            # The Lucidchart import target is currently used for the wiring overview.
            continue
        else:
            raise ValueError(f"Unsupported draw.io element type {kind!r} in {figure.get('slug')}")
    return canvas


def draw_title(canvas: SvgCanvas | PdfCanvas, figure: dict) -> None:
    width = int(figure["size"][0])
    title_h = draw_text_block(canvas, 45, 26, width - 90, figure["title"], 30, TITLE_COLOR, weight="700")
    caption = figure.get("caption", "")
    separator_y = 112
    if caption:
        caption_y = 26 + title_h + 8
        caption_h = draw_text_block(canvas, 45, caption_y, min(width - 90, 1000), caption, 16, CAPTION_COLOR)
        separator_y = max(separator_y, caption_y + caption_h + 14)
    canvas.line([[45, separator_y], [width - 45, separator_y]], color="#ccd4dc", width=1.3)


def render_figure(figure: dict, canvas: SvgCanvas | PdfCanvas) -> None:
    if figure.get("show_title", True):
        draw_title(canvas, figure)
    show_wire_labels = bool(figure.get("show_wire_labels", True))
    for element in figure.get("elements", []):
        if element.get("type") == "panel":
            draw_panel(canvas, element)
    for element in figure.get("elements", []):
        if element.get("type") == "wire":
            draw_wire(canvas, element, show_label=show_wire_labels)
    for element in figure.get("elements", []):
        kind = element.get("type")
        if kind == "wire":
            continue
        if kind in {"box", "note"}:
            draw_box(canvas, element)
        elif kind in {"decision", "io", "terminator"}:
            draw_flow_node(canvas, element)
        elif kind == "arrow":
            draw_arrow(canvas, element)
        elif kind == "wire":
            draw_wire(canvas, element)
        elif kind == "legend":
            draw_legend(canvas, element)
        elif kind == "table":
            draw_table(canvas, element)
        elif kind == "panel":
            continue
        else:
            raise ValueError(f"Unsupported element type {kind!r} in {figure.get('slug')}")


def render_pdf_figure(figure: dict) -> PdfCanvas:
    width, height = (float(v) for v in figure["size"])
    pdf_width_mm = figure.get("pdf_width_mm")
    pdf_viewbox = figure.get("pdf_viewbox")
    if pdf_width_mm and pdf_viewbox:
        view_x, view_y, view_w, view_h = (float(v) for v in pdf_viewbox)
        pdf_width = float(pdf_width_mm) * MM_TO_PT
        scale = pdf_width / view_w
        pdf = PdfCanvas(pdf_width, view_h * scale)
        render_figure(figure, TransformedPdfCanvas(pdf, scale, view_x, view_y))
        return pdf

    pdf = PdfCanvas(width, height)
    render_figure(figure, pdf)
    return pdf


def write_captions(figures: list[dict]) -> None:
    lines = ["# Dissertation Figure Captions", ""]
    for fig in figures:
        slug = fig["slug"]
        title = fig["title"]
        caption = fig.get("caption", "")
        lines.append(f"## {slug}: {title}")
        lines.append("")
        lines.append(caption)
        lines.append("")
        lines.append(f"- PDF: `generated/{slug}.pdf`")
        if fig.get("drawio"):
            lines.append(f"- Lucidchart import: `generated/{slug}.drawio`")
        lines.append("")
    (ROOT / "captions.md").write_text("\n".join(lines), encoding="utf-8")


def build() -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old_path in OUT_DIR.iterdir():
        if old_path.suffix in {".svg", ".pdf", ".drawio"}:
            old_path.unlink()
    outputs: list[Path] = []
    for figure in FIGURES:
        slug = figure["slug"]

        pdf = render_pdf_figure(figure)
        pdf_path = OUT_DIR / f"{slug}.pdf"
        pdf_path.write_bytes(pdf.output())
        outputs.append(pdf_path)

        if figure.get("drawio"):
            drawio_path = OUT_DIR / f"{slug}.drawio"
            drawio_path.write_bytes(render_drawio_figure(figure).output())
            outputs.append(drawio_path)

    write_captions(FIGURES)
    outputs.append(ROOT / "captions.md")
    return outputs


def validate(paths: list[Path]) -> None:
    for path in paths:
        if path.suffix == ".pdf":
            with path.open("rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise ValueError(f"{path} does not have a PDF header")
        elif path.suffix == ".drawio":
            ET.parse(path)
        elif path.suffix == ".md":
            if not path.read_text(encoding="utf-8").strip():
                raise ValueError(f"{path} is empty")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate generated PDF outputs after rendering")
    args = parser.parse_args(argv)

    outputs = build()
    if args.check:
        validate(outputs)
    for path in outputs:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
