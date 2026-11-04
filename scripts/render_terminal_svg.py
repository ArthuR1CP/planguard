"""Render planguard's colored terminal output as an SVG "screenshot" for the README.

Usage:
    FORCE_COLOR=1 planguard analyze tests/fixtures/mixed.json -f text \
        | python scripts/render_terminal_svg.py "planguard analyze plan.json" \
        > docs/assets/terminal.svg
"""

from __future__ import annotations

import re
import sys
from html import escape

BG, FG, DIM = "#0d1117", "#e6edf3", "#8b949e"
COLORS = {"31": "#ff7b72", "32": "#3fb950", "33": "#d29922", "35": "#d2a8ff", "36": "#79c0ff"}
CHAR_W, LINE_H, PAD_X, TOP = 8.45, 21, 22, 64
ANSI = re.compile(r"\033\[([\d;]*)m")


def _nbsp(text: str) -> str:
    """Escape and keep runs of spaces: SVG renderers collapse normal whitespace."""
    return escape(text).replace(" ", "\u00a0")


def spans(line: str) -> list[tuple[str, dict[str, bool | str]]]:
    out: list[tuple[str, dict[str, bool | str]]] = []
    style: dict[str, bool | str] = {}
    pos = 0
    for m in ANSI.finditer(line):
        if m.start() > pos:
            out.append((line[pos : m.start()], dict(style)))
        codes = m.group(1).split(";") if m.group(1) else ["0"]
        for code in codes:
            if code == "0":
                style = {}
            elif code == "1":
                style["bold"] = True
            elif code == "2":
                style["dim"] = True
            elif code in COLORS:
                style["color"] = COLORS[code]
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], dict(style)))
    return out


def render(text: str, command: str) -> str:
    lines = text.rstrip("\n").split("\n")
    plain_width = max(len(ANSI.sub("", ln)) for ln in [*lines, "$ " + command])
    width = int(PAD_X * 2 + plain_width * CHAR_W)
    height = TOP + (len(lines) + 1) * LINE_H + 18

    rows = [
        f'<text x="{PAD_X}" y="{TOP}"><tspan fill="{COLORS["32"]}">$</tspan> '
        f'<tspan fill="{FG}">{_nbsp(command)}</tspan></text>'
    ]
    for i, line in enumerate(lines, start=1):
        parts = []
        for chunk, st in spans(line):
            attrs = [f'fill="{st.get("color") or (DIM if st.get("dim") else FG)}"']
            if st.get("bold"):
                attrs.append('font-weight="700"')
            parts.append(f"<tspan {' '.join(attrs)}>{_nbsp(chunk)}</tspan>")
        rows.append(f'<text x="{PAD_X}" y="{TOP + i * LINE_H}">{"".join(parts)}</text>')

    dots = "".join(
        f'<circle cx="{22 + i * 20}" cy="22" r="6" fill="{c}"/>'
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" role="img" aria-label="planguard terminal output">
  <rect width="{width}" height="{height}" rx="10" fill="{BG}"/>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="none" \
stroke="#30363d"/>
  {dots}
  <text x="{width / 2}" y="27" fill="{DIM}" font-size="12" text-anchor="middle" \
font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">planguard: bash</text>
  <g font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace" \
font-size="14">
    {chr(10).join("    " + r for r in rows).strip()}
  </g>
</svg>
"""


if __name__ == "__main__":
    sys.stdout.write(render(sys.stdin.read(), sys.argv[1] if len(sys.argv) > 1 else "planguard"))
