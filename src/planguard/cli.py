"""Command-line interface: ``planguard analyze plan.json``."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from planguard import __version__
from planguard.config import Config, ConfigError, load_config, parse_fail_on
from planguard.k8s import diff_manifests, load_manifests
from planguard.parser import PlanError, load_plan, parse_changes
from planguard.report import Report, build_report, render_json, render_markdown
from planguard.rules import RULES
from planguard.terminal import render_text, use_color

EXIT_OK = 0
EXIT_RISK = 1
EXIT_ERROR = 2


def _add_report_options(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument(
        "-c", "--config", help="path to .planguard.yml (default: ./.planguard.yml if present)"
    )
    cmd.add_argument(
        "-f",
        "--format",
        choices=("text", "md", "json"),
        help="stdout format (default: text in a terminal, md when piped)",
    )
    cmd.add_argument(
        "--no-color", action="store_true", help="disable colors in text output (or set NO_COLOR)"
    )
    cmd.add_argument(
        "--fail-on",
        metavar="LEVEL",
        help="exit 1 when risk >= LEVEL: low|medium|high|critical|none (default: high)",
    )
    cmd.add_argument("--output", metavar="FILE", help="also write the Markdown report to FILE")
    cmd.add_argument("--json-output", metavar="FILE", help="also write the JSON report to FILE")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planguard",
        description="Flag destructive infrastructure changes (Terraform plans, Kubernetes/Helm "
        "manifests) before they merge. Exit codes: 0 ok, 1 risk >= fail-on, 2 error.",
    )
    parser.add_argument("--version", action="version", version=f"planguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze",
        help="analyze a Terraform JSON plan (from `terraform show -json tfplan`)",
        description="Analyze a Terraform JSON plan. Exit codes: 0 ok, 1 risk >= fail-on, 2 error.",
    )
    analyze.add_argument("plan", help="path to the JSON plan, or '-' for stdin")
    _add_report_options(analyze)

    k8s = sub.add_parser(
        "k8s",
        help="compare two sets of Kubernetes manifests (helm template, kustomize build, YAML)",
        description="Compare rendered Kubernetes manifests before and after a change. "
        "Each side is a YAML file (multi-document is fine) or a directory of YAML files; "
        "use /dev/null for 'nothing existed'. Exit codes: 0 ok, 1 risk >= fail-on, 2 error.",
    )
    k8s.add_argument("before", help="manifests before the change (file, directory or /dev/null)")
    k8s.add_argument("after", help="manifests after the change (file, directory or /dev/null)")
    _add_report_options(k8s)

    sub.add_parser("rules", help="list built-in rules")
    return parser


def _emit(report: Report, args: argparse.Namespace) -> int:
    markdown = render_markdown(report)
    as_json = render_json(report)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
    if args.json_output:
        Path(args.json_output).write_text(as_json, encoding="utf-8")
    fmt = args.format or ("text" if sys.stdout.isatty() else "md")
    if fmt == "json":
        sys.stdout.write(as_json)
    elif fmt == "md":
        sys.stdout.write(markdown)
    else:
        sys.stdout.write(render_text(report, color=not args.no_color and use_color(sys.stdout)))
    return EXIT_RISK if report.failed else EXIT_OK


def _config(args: argparse.Namespace) -> Config:
    cfg = load_config(args.config)
    if args.fail_on:
        cfg = dataclasses.replace(cfg, fail_on=parse_fail_on(args.fail_on))
    return cfg


def _analyze(args: argparse.Namespace) -> int:
    cfg = _config(args)
    plan = load_plan(sys.stdin if args.plan == "-" else args.plan)
    return _emit(build_report(parse_changes(plan), cfg), args)


def _k8s(args: argparse.Namespace) -> int:
    cfg = _config(args)
    changes = diff_manifests(load_manifests(args.before), load_manifests(args.after))
    return _emit(build_report(changes, cfg), args)


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
        if args.command == "k8s":
            return _k8s(args)
        return _analyze(args)
    except (PlanError, ConfigError) as exc:
        print(f"planguard: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"planguard: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
