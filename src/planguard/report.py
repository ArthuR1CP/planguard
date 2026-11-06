"""Build the analysis report and render it as Markdown or JSON (see terminal.py for text)."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from planguard import __version__
from planguard.config import Config
from planguard.models import Action, Finding, ResourceChange, Risk
from planguard.rules import RULES, evaluate, overall_risk

MARKER = "<!-- planguard:sticky-comment -->"
MAX_CHANGE_ROWS = 250
"""Cap on rows in the collapsible change list (GitHub comments are limited to 65k chars)."""

_ACTION_ORDER = [
    Action.CREATE,
    Action.UPDATE,
    Action.REPLACE,
    Action.DELETE,
    Action.FORGET,
    Action.NOOP,
]
_ACTION_ICON = {
    Action.CREATE: "➕",
    Action.UPDATE: "✏️",
    Action.REPLACE: "♻️",
    Action.DELETE: "🗑️",
    Action.FORGET: "👋",
    Action.NOOP: "·",
    Action.READ: "📖",
}
_CACHE_HINT = re.compile(r"(elasticache|redis|memorydb)")
_DB_HINT = re.compile(
    r"(_db_|_rds_|dynamodb|_sql_|database|cosmosdb|docdb|neptune"
    r"|redshift|spanner|bigtable|bigquery|alloydb|timestream|_mssql_|_mysql_|_postgresql_)"
)
_BUCKET_HINT = re.compile(r"(_s3_bucket$|storage_bucket|storage_account|storage_container)")
_VOLUME_HINT = re.compile(r"(_ebs_volume|_disk$|file_system|filestore|storage_share)")


@dataclass
class Report:
    changes: list[ResourceChange]
    ignored: list[ResourceChange]
    findings: list[Finding]
    risk: Risk
    fail_on: Risk | None
    config: Config = field(repr=False, default_factory=Config)

    @property
    def failed(self) -> bool:
        return self.fail_on is not None and self.risk >= self.fail_on

    @property
    def counts(self) -> Counter[Action]:
        return Counter(c.action for c in self.changes)

    @property
    def headline(self) -> str:
        return headline(self)


def build_report(changes: list[ResourceChange], cfg: Config) -> Report:
    analyzed, ignored, findings = evaluate(changes, cfg)
    return Report(
        changes=analyzed,
        ignored=ignored,
        findings=findings,
        risk=overall_risk(analyzed, findings),
        fail_on=cfg.fail_on,
        config=cfg,
    )


# --------------------------------------------------------------------------- headline


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _noun(rtype: str) -> tuple[str, str]:
    if _CACHE_HINT.search(rtype):
        return "cache cluster", "cache clusters"
    if _DB_HINT.search(rtype):
        return "database", "databases"
    if _BUCKET_HINT.search(rtype):
        return "bucket", "buckets"
    if _VOLUME_HINT.search(rtype):
        return "volume", "volumes"
    return "stateful resource", "stateful resources"


def _summary_line(report: Report) -> str:
    c = report.counts
    parts = [
        f"{c[Action.CREATE]} to add",
        f"{c[Action.UPDATE]} to change",
        f"{c[Action.REPLACE]} to replace",
        f"{c[Action.DELETE]} to destroy",
    ]
    return ", ".join(parts)


def headline(report: Report) -> str:
    """One line answering "what is the scariest thing in this plan?"."""
    if report.risk is Risk.NONE:
        return "✅ No changes."
    top = [f for f in report.findings if f.risk == report.risk and f.rule_id != "PG007"]
    if not top or report.risk is Risk.LOW:
        return f"✅ No risky changes ({_summary_line(report)})."

    types = {c.address: c.type for c in report.changes}
    parts: list[str] = []
    by_rule: dict[str, list[Finding]] = defaultdict(list)
    for f in top:
        by_rule[f.rule_id].append(f)

    if "PG001" in by_rule:
        groups: Counter[tuple[str, str, str]] = Counter()
        for f in by_rule.pop("PG001"):
            singular, plural = _noun(types.get(f.address, ""))
            verb = "REPLACED" if f.details.get("action") == Action.REPLACE.value else "DESTROYED"
            groups[(singular, plural, verb)] += 1
        for (singular, plural, verb), n in groups.items():
            parts.append(f"{_plural(n, singular, plural)} will be {verb}")
    if "PG101" in by_rule:
        kinds: Counter[tuple[str, str]] = Counter()
        for f in by_rule.pop("PG101"):
            verb = "DELETED" if f.details.get("action") == Action.DELETE.value else "RECREATED"
            kinds[(str(f.details.get("kind", "resource")), verb)] += 1
        for (kind, verb), n in kinds.items():
            parts.append(f"{_plural(n, kind)} will be {verb}")
    phrases = {
        "PG002": lambda n: f"{_plural(n, 'IAM grant')} with wildcard permissions",
        "PG003": lambda n: f"{_plural(n, 'security rule')} open to the internet",
        "PG004": lambda _n: by_rule["PG004"][0].message,
        "PG005": lambda n: f"{_plural(n, 'compute resource')} will be REPLACED",
        "PG006": lambda n: f"{_plural(n, 'protected resource')} will change",
        "PG102": lambda n: f"{_plural(n, 'object')} must be recreated (immutable fields)",
        "PG103": lambda n: f"{_plural(n, 'new public endpoint')}",
        "PG104": lambda n: f"{_plural(n, 'RBAC grant')} with wildcards or cluster-admin",
        "PG105": lambda n: f"{_plural(n, 'capacity drop')}",
        "PG106": lambda n: f"{_plural(n, 'pod spec')} gaining host or privileged access",
    }
    for rule_id, items in by_rule.items():
        phrase = phrases.get(rule_id)
        parts.append(phrase(len(items)) if phrase else f"{len(items)} × {items[0].rule_name}")

    icon = "🚨" if report.risk is Risk.CRITICAL else "⚠️"
    return f"{icon} " + "; ".join(parts)


# --------------------------------------------------------------------------- markdown


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def why(finding: Finding) -> str:
    """The finding message without the address, which is shown in its own column."""
    text = finding.message.replace(f"`{finding.address}` ", "", 1)
    text = text.replace(f" `{finding.address}`", "", 1)
    return text[:1].upper() + text[1:]


def sorted_changes(report: Report) -> tuple[list[ResourceChange], dict[str, Risk]]:
    """Real changes (no no-ops), riskiest first, plus the highest risk per address."""
    risk_by_address: dict[str, Risk] = {}
    for f in report.findings:
        risk_by_address[f.address] = max(risk_by_address.get(f.address, Risk.NONE), f.risk)
    order = {a: i for i, a in enumerate(_ACTION_ORDER)}
    changed = [ch for ch in report.changes if ch.action not in (Action.NOOP, Action.READ)]
    changed.sort(
        key=lambda ch: (
            -risk_by_address.get(ch.address, Risk.NONE),
            order.get(ch.action, 99),
            ch.address,
        )
    )
    return changed, risk_by_address


def render_markdown(report: Report) -> str:
    lines: list[str] = [
        MARKER,
        f"## 🛡️ planguard: {report.risk.emoji} {report.risk.name}",
        "",
        f"### {report.headline}",
        "",
    ]
    c = report.counts
    lines.append(
        f"**Plan:** ➕ {c[Action.CREATE]} to add · ✏️ {c[Action.UPDATE]} to change · "
        f"♻️ {c[Action.REPLACE]} to replace · 🗑️ {c[Action.DELETE]} to destroy"
        + (f" · 🙈 {len(report.ignored)} ignored" if report.ignored else "")
    )
    lines.append("")

    risky = [f for f in report.findings if f.risk > Risk.LOW]
    info = [f for f in report.findings if f.risk <= Risk.LOW]
    if risky:
        lines += ["| Risk | Resource | Rule | Why |", "|---|---|---|---|"]
        for f in risky:
            lines.append(
                f"| {f.risk.emoji} **{f.risk.name}** | `{_cell(f.address)}` "
                f"| {f.rule_id} {f.rule_name} | {_cell(why(f))} |"
            )
        lines.append("")
    if info:
        lines += [f"<details><summary>ℹ️ {_plural(len(info), 'note')}</summary>", ""]
        lines += [f"- {_cell(f.message)}" for f in info]
        lines += ["", "</details>", ""]

    changed, risk_by_address = sorted_changes(report)
    if changed:
        lines += [
            f"<details><summary>All changes ({len(changed)})</summary>",
            "",
            "| Action | Resource | Type | Risk |",
            "|---|---|---|---|",
        ]
        for ch in changed[:MAX_CHANGE_ROWS]:
            risk = risk_by_address.get(ch.address)
            risk_cell = f"{risk.emoji} {risk.name}" if risk else ""
            lines.append(
                f"| {_ACTION_ICON[ch.action]} {ch.action.value} | `{_cell(ch.address)}` "
                f"| `{ch.type}` | {risk_cell} |"
            )
        if len(changed) > MAX_CHANGE_ROWS:
            lines.append(f"| … | _{len(changed) - MAX_CHANGE_ROWS} more not shown_ | | |")
        lines += ["", "</details>", ""]

    if report.ignored:
        lines += [
            f"<details><summary>Ignored by config ({len(report.ignored)})</summary>",
            "",
        ]
        lines += [f"- `{_cell(ch.address)}` ({ch.action.value})" for ch in report.ignored]
        lines += ["", "</details>", ""]

    fail_on = report.fail_on.name.lower() if report.fail_on else "none"
    status = "❌ failing" if report.failed else "✅ passing"
    lines.append(
        f"<sub>{status} · fail-on: `{fail_on}` · "
        f"[planguard](https://github.com/ArthuR1CP/planguard) v{__version__}</sub>"
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- json


def to_dict(report: Report) -> dict[str, Any]:
    c = report.counts
    return {
        "version": __version__,
        "risk": report.risk.name,
        "fail_on": report.fail_on.name if report.fail_on else None,
        "failed": report.failed,
        "headline": report.headline,
        "summary": {
            "create": c[Action.CREATE],
            "update": c[Action.UPDATE],
            "replace": c[Action.REPLACE],
            "delete": c[Action.DELETE],
            "forget": c[Action.FORGET],
            "no_op": c[Action.NOOP],
            "ignored": len(report.ignored),
        },
        "findings": [f.to_dict() for f in report.findings],
        "changes": [
            {
                "address": ch.address,
                "type": ch.type,
                "action": ch.action.value,
                "forces_replacement": ch.replace_attributes,
            }
            for ch in report.changes
            if ch.action is not Action.NOOP
        ],
        "ignored": [ch.address for ch in report.ignored],
        "rules": [{"id": r.id, "name": r.name, "default_risk": r.default_risk.name} for r in RULES],
    }


def render_json(report: Report) -> str:
    return json.dumps(to_dict(report), indent=2, ensure_ascii=False) + "\n"
