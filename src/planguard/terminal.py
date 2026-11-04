"""Human-friendly, colored terminal output (stdlib only, plain ANSI escapes)."""

from __future__ import annotations

import os
from typing import IO

from planguard import __version__
from planguard.models import Action, Risk
from planguard.report import MAX_CHANGE_ROWS, Report, sorted_changes, why

_RESET = "\033[0m"
_STYLES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
    "magenta": "35",
}
_RISK_STYLE = {
    Risk.CRITICAL: ("bold", "red"),
    Risk.HIGH: ("red",),
    Risk.MEDIUM: ("yellow",),
    Risk.LOW: ("green",),
    Risk.NONE: ("dim",),
}
# Same symbols Terraform uses in `terraform plan`.
_ACTION_SYMBOL = {
    Action.CREATE: ("+", ("green",)),
    Action.UPDATE: ("~", ("yellow",)),
    Action.REPLACE: ("-/+", ("red",)),
    Action.DELETE: ("-", ("red",)),
    Action.FORGET: ("forget", ("magenta",)),
}


def use_color(stream: IO[str]) -> bool:
    """Color when writing to a terminal, honoring NO_COLOR and FORCE_COLOR."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty() and os.environ.get("TERM") != "dumb"


def render_text(report: Report, color: bool = False) -> str:
    def paint(text: str, *styles: str) -> str:
        if not color or not styles:
            return text
        codes = ";".join(_STYLES[s] for s in styles)
        return f"\033[{codes}m{text}{_RESET}"

    def risk_label(risk: Risk, width: int = 0) -> str:
        return paint(risk.name.ljust(width), *_RISK_STYLE[risk])

    def plain(text: str) -> str:
        return text.replace("`", "")

    out: list[str] = []
    out.append(
        f"{paint('planguard', 'bold')} {paint('v' + __version__, 'dim')}  "
        f"risk: {risk_label(report.risk)}"
    )
    out.append(paint(report.headline, "bold"))
    out.append("")

    c = report.counts
    summary = [
        paint(f"{c[Action.CREATE]} to add", "green"),
        paint(f"{c[Action.UPDATE]} to change", "yellow"),
        paint(f"{c[Action.REPLACE]} to replace", "red"),
        paint(f"{c[Action.DELETE]} to destroy", "red"),
    ]
    if report.ignored:
        summary.append(paint(f"{len(report.ignored)} ignored", "dim"))
    out.append("Plan: " + ", ".join(summary))
    out.append("")

    risky = [f for f in report.findings if f.risk > Risk.LOW]
    notes = [f for f in report.findings if f.risk <= Risk.LOW]
    if risky:
        out.append(paint(f"Findings ({len(risky)})", "bold"))
        for f in risky:
            out.append(f"  {risk_label(f.risk, 8)}  {paint(f.address, 'bold')}")
            out.append(
                f"  {' ' * 8}  {plain(why(f))}  {paint(f'[{f.rule_id} {f.rule_name}]', 'dim')}"
            )
        out.append("")
    if notes:
        out.append(paint(f"Notes ({len(notes)})", "bold"))
        out += [f"  {paint('·', 'dim')} {plain(f.message)}" for f in notes]
        out.append("")

    changed, risk_by_address = sorted_changes(report)
    if changed:
        out.append(paint(f"Changes ({len(changed)})", "bold"))
        shown = changed[:MAX_CHANGE_ROWS]
        addr_width = max(len(ch.address) for ch in shown)
        type_width = max(len(ch.type) for ch in shown)
        for ch in shown:
            symbol, styles = _ACTION_SYMBOL.get(ch.action, (ch.action.value, ()))
            risk = risk_by_address.get(ch.address)
            line = (
                f"  {paint(symbol.rjust(3), *styles)}  {ch.address.ljust(addr_width)}  "
                f"{paint(ch.type.ljust(type_width), 'dim')}"
            )
            if risk:
                line += f"  {risk_label(risk)}"
            out.append(line.rstrip())
        if len(changed) > MAX_CHANGE_ROWS:
            out.append(paint(f"  … {len(changed) - MAX_CHANGE_ROWS} more not shown", "dim"))
        out.append("")

    if report.ignored:
        out.append(paint(f"Ignored by config ({len(report.ignored)})", "bold"))
        out += [paint(f"  {ch.address} ({ch.action.value})", "dim") for ch in report.ignored]
        out.append("")

    if report.fail_on is None:
        out.append(paint("✓ PASS", "bold", "green") + paint("  (fail-on: none)", "dim"))
    elif report.failed:
        out.append(
            paint("✗ FAIL", "bold", "red")
            + f"  risk {report.risk.name} ≥ fail-on {report.fail_on.name}"
        )
    else:
        out.append(
            paint("✓ PASS", "bold", "green")
            + f"  risk {report.risk.name} < fail-on {report.fail_on.name}"
        )
    return "\n".join(out) + "\n"
