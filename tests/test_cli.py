import io
import json
import subprocess
import sys

import pytest

from planguard import __version__
from planguard.cli import main


def test_analyze_markdown_fails_on_critical(fixture_path, capsys):
    code = main(["analyze", str(fixture_path("rds_replace"))])
    out = capsys.readouterr().out
    assert code == 1
    assert "1 database will be REPLACED" in out


def test_analyze_safe_passes(fixture_path, capsys):
    assert main(["analyze", str(fixture_path("safe"))]) == 0


def test_fail_on_flag_overrides(fixture_path, capsys):
    plan = str(fixture_path("sg_open_world"))
    assert main(["analyze", plan]) == 1
    assert main(["analyze", plan, "--fail-on", "critical"]) == 0
    assert main(["analyze", str(fixture_path("safe")), "--fail-on", "low"]) == 1
    assert main(["analyze", str(fixture_path("rds_replace")), "--fail-on", "none"]) == 0


def test_json_format_and_outputs(fixture_path, tmp_path, capsys):
    md_path, json_path = tmp_path / "r.md", tmp_path / "r.json"
    code = main(
        [
            "analyze",
            str(fixture_path("iam_wildcard")),
            "--format",
            "json",
            "--output",
            str(md_path),
            "--json-output",
            str(json_path),
        ]
    )
    stdout = json.loads(capsys.readouterr().out)
    assert code == 1
    assert stdout["risk"] == "HIGH"
    assert json.loads(json_path.read_text()) == stdout
    assert "planguard: 🟠 HIGH" in md_path.read_text()


def test_config_file(fixture_path, tmp_path, capsys):
    cfg = tmp_path / ".planguard.yml"
    cfg.write_text("fail_on: critical\nrules:\n  PG001: medium\n")
    assert main(["analyze", str(fixture_path("rds_replace")), "-c", str(cfg)]) == 0


def test_stdin(fixture_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(fixture_path("safe").read_text()))
    assert main(["analyze", "-", "-f", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["risk"] == "LOW"


@pytest.mark.parametrize(
    "args",
    [
        ["analyze", "missing.json"],
        ["analyze", "PLAN", "--fail-on", "scary"],
        ["analyze", "PLAN", "--config", "missing.yml"],
    ],
)
def test_errors_exit_2(fixture_path, capsys, args):
    args = [str(fixture_path("safe")) if a == "PLAN" else a for a in args]
    assert main(args) == 2
    assert "planguard: error:" in capsys.readouterr().err


def test_rules_command(capsys):
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "PG001" in out and "stateful-destroy" in out


def test_module_entrypoint(fixture_path):
    result = subprocess.run(
        [sys.executable, "-m", "planguard", "--version"], capture_output=True, text=True
    )
    assert result.stdout.strip() == f"planguard {__version__}"


def test_default_format_is_md_when_piped(fixture_path, capsys):
    main(["analyze", str(fixture_path("safe"))])
    assert capsys.readouterr().out.startswith("<!-- planguard")


def test_default_format_is_text_on_tty(fixture_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    main(["analyze", str(fixture_path("safe")), "--no-color"])
    out = capsys.readouterr().out
    assert out.startswith("planguard v") and "\033[" not in out


def test_text_format_flag(fixture_path, capsys):
    main(["analyze", str(fixture_path("rds_replace")), "-f", "text"])
    assert "✗ FAIL" in capsys.readouterr().out
