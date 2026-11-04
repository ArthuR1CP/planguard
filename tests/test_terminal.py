import io

import pytest

from planguard.config import Config
from planguard.report import build_report
from planguard.terminal import render_text, use_color


def test_text_plain(changes_for):
    text = render_text(build_report(changes_for("mixed"), Config()))
    assert "\033[" not in text
    assert text.startswith("planguard v")
    assert "risk: CRITICAL" in text
    assert "🚨 1 cache cluster will be REPLACED" in text
    assert "Plan: 3 to add, 2 to change, 2 to replace, 2 to destroy" in text
    assert "Findings (6)" in text
    assert "Opens port 22/tcp to 0.0.0.0/0  [PG003 sg-open-world]" in text
    assert "`" not in text  # markdown backticks are stripped
    assert "-/+  module.cache.aws_elasticache_replication_group.this" in text
    assert text.rstrip().endswith("✗ FAIL  risk CRITICAL ≥ fail-on HIGH")


def test_text_color(changes_for):
    text = render_text(build_report(changes_for("rds_replace"), Config()), color=True)
    assert "\033[1;31mCRITICAL\033[0m" in text


def test_text_pass_and_fail_on_none(changes_for):
    assert "✓ PASS  risk LOW < fail-on HIGH" in render_text(
        build_report(changes_for("safe"), Config())
    )
    assert "✓ PASS  (fail-on: none)" in render_text(
        build_report(changes_for("rds_replace"), Config(fail_on=None))
    )


def test_text_ignored(changes_for):
    text = render_text(build_report(changes_for("mixed"), Config(ignore=("module.sandbox.*",))))
    assert "Ignored by config (1)" in text
    assert "1 ignored" in text


def test_text_columns_aligned(changes_for):
    report = build_report(changes_for("mixed"), Config())
    lines = render_text(report).splitlines()
    rows = lines[lines.index("Changes (9)") + 1 : lines.index("Changes (9)") + 10]
    by_address = {c.address: c.type for c in report.changes}
    type_columns = set()
    for row in rows:
        address = row.split()[1]
        type_columns.add(row.index(by_address[address], row.index(address) + len(address)))
    assert len(type_columns) == 1


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("env", "stream", "expected"),
    [
        ({}, io.StringIO(), False),
        ({}, _Tty(), True),
        ({"NO_COLOR": "1"}, _Tty(), False),
        ({"FORCE_COLOR": "1"}, io.StringIO(), True),
        ({"TERM": "dumb"}, _Tty(), False),
    ],
)
def test_use_color(monkeypatch, env, stream, expected):
    for key in ("NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert use_color(stream) is expected
