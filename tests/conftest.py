from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from planguard.config import Config
from planguard.models import Finding, ResourceChange
from planguard.parser import load_plan, parse_changes
from planguard.rules import evaluate

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_path() -> Callable[[str], Path]:
    return lambda name: FIXTURES / f"{name}.json"


@pytest.fixture
def changes_for() -> Callable[[str], list[ResourceChange]]:
    return lambda name: parse_changes(load_plan(FIXTURES / f"{name}.json"))


@pytest.fixture
def findings_for() -> Callable[..., list[Finding]]:
    def run(name: str, cfg: Config | None = None) -> list[Finding]:
        changes = parse_changes(load_plan(FIXTURES / f"{name}.json"))
        return evaluate(changes, cfg or Config())[2]

    return run


def make_plan(*changes: dict[str, Any]) -> dict[str, Any]:
    return {"format_version": "1.2", "resource_changes": list(changes)}


def make_change(
    address: str,
    rtype: str,
    actions: list[str],
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    **change_extra: Any,
) -> dict[str, Any]:
    return {
        "address": address,
        "mode": "managed",
        "type": rtype,
        "name": address.rsplit(".", 1)[-1],
        "provider_name": "registry.terraform.io/hashicorp/aws",
        "change": {"actions": actions, "before": before, "after": after, **change_extra},
    }


def write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data))
    return path
