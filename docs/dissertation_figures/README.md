# Dissertation Figures

This directory contains editable source and generated PDF figures for the
HFE system control and monitoring logic. The current dissertation set contains
five figures: a combined control/telemetry architecture, the LN auto-mode
logic, a compact measurements/interlocks table, a simplified electrical wiring
overview, and a thermocouple backplate detail.

## Rebuild

Run from the repository root:

```bash
python3 docs/dissertation_figures/build_figures.py --check
```

The command regenerates all files in `generated/`, rewrites `captions.md`, and
validates that every generated file has a valid PDF header.
The wiring overview also produces `generated/04_electrical_wiring_overview.drawio`,
which can be imported into Lucidchart as an editable diagrams.net/draw.io file.

## Files

- `figure_sources.py` is the editable figure source: diagram labels, captions,
  boxes, arrows, colors, and the signal map table.
- `build_figures.py` is the standard-library renderer. It does not depend on
  Mermaid, Graphviz, Inkscape, LaTeX, Node, or third-party Python packages.
- `generated/*.pdf` are vector figures for dissertation insertion.
- `generated/04_electrical_wiring_overview.drawio` is the editable Lucidchart
  import version of the wiring overview.
- `captions.md` contains short figure captions matching the generated files.

The existing process and instrumentation diagram remains at
`clients/web/assets/hfe-pid.svg`; these figures explain control/data logic that
complements that physical layout.
