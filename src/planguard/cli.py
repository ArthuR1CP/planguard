"""Command-line interface: ``planguard analyze plan.json``."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from planguard import __version__
from planguard.config import ConfigError, load_config, parse_fail_on
from planguard.parser import PlanError, load_plan, parse_changes
from planguard.report import build_report, render_json, render_markdown
from planguard.rules import RULES

EXIT_OK = 0
EXIT_RISK = 1
EXIT_ERROR = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planguard",
        description="Flag risky changes in a Terraform plan before merge.",
    )
    parser.add_argument("--version", action="version", version=f"planguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze",
        help="analyze a JSON plan (from `terraform show -json tfplan`)",
        description="Analyze a JSON plan. Exit codes: 0 ok, 1 risk >= fail-on, 2 error.",
    )
    analyze.add_argument("plan", help="path to the JSON plan, or '-' for stdin")
    analyze.add_argument(
        "-c", "--config", help="path to .planguard.yml (default: ./.planguard.yml if present)"
    )
    analyze.add_argument(
        "-f", "--format", choices=("md", "json"), default="md", help="stdout format (default: md)"
    )
    analyze.add_argument(
        "--fail-on",
        metavar="LEVEL",
        help="exit 1 when risk >= LEVEL: low|medium|high|critical|none (default: high)",
    )
    analyze.add_argument("--output", metavar="FILE", help="also write the Markdown report to FILE")
    analyze.add_argument("--json-output", metavar="FILE", help="also write the JSON report to FILE")

    sub.add_parser("rules", help="list built-in rules")
    return parser


def _analyze(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.fail_on:
        cfg = dataclasses.replace(cfg, fail_on=parse_fail_on(args.fail_on))
    plan = load_plan(sys.stdin if args.plan == "-" else args.plan)
    report = build_report(parse_changes(plan), cfg)

    markdown = render_markdown(report)
    as_json = render_json(report)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
    if args.json_output:
        Path(args.json_output).write_text(as_json, encoding="utf-8")
    sys.stdout.write(as_json if args.format == "json" else markdown)
    return EXIT_RISK if report.failed else EXIT_OK


def _rules() -> int:
    width = max(len(r.name) for r in RULES)
    for r in RULES:
        print(f"{r.id}  {r.name:<{width}}  {r.default_risk.name:<8}  {r.description}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "rules":
            return _rules()
        return _analyze(args)
    except (PlanError, ConfigError) as exc:
        print(f"planguard: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"planguard: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
