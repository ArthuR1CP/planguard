"""Parse ``terraform show -json`` output into :class:`ResourceChange` objects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Any

from planguard.models import Action, ResourceChange


class PlanError(Exception):
    """Raised when the input is not a usable Terraform JSON plan."""


_ACTIONS: dict[tuple[str, ...], Action] = {
    ("no-op",): Action.NOOP,
    ("create",): Action.CREATE,
    ("read",): Action.READ,
    ("update",): Action.UPDATE,
    ("delete",): Action.DELETE,
    ("delete", "create"): Action.REPLACE,
    ("create", "delete"): Action.REPLACE,
    ("forget",): Action.FORGET,
}

_TAG_KEYS = ("tags", "tags_all", "labels")


def classify(actions: list[str] | tuple[str, ...]) -> Action:
    """Map Terraform's ``change.actions`` list to a single :class:`Action`."""
    key = tuple(actions)
    if key in _ACTIONS:
        return _ACTIONS[key]
    raise PlanError(f"unsupported change actions: {list(actions)}")


def load_plan(source: str | Path | IO[str]) -> dict[str, Any]:
    """Read and decode a JSON plan from a path, ``"-"`` (stdin) or a file object."""
    try:
        if hasattr(source, "read"):
            text = source.read()  # type: ignore[union-attr]
        else:
            text = Path(source).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PlanError(f"plan file not found: {source}") from None
    except UnicodeDecodeError:
        raise PlanError(
            "plan file is not text; did you pass the binary plan? "
            "Run `terraform show -json tfplan > plan.json` first."
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(
            f"plan is not valid JSON ({exc.msg} at line {exc.lineno}). "
            "Run `terraform show -json tfplan > plan.json` first."
        ) from None
    if not isinstance(data, dict):
        raise PlanError("plan JSON must be an object")
    if "resource_changes" not in data and "format_version" not in data:
        raise PlanError("input does not look like a Terraform JSON plan (no resource_changes)")
    if "values" in data and "resource_changes" not in data and "planned_values" not in data:
        raise PlanError("input looks like Terraform state, not a plan")
    return data


def _tags(*blocks: dict[str, Any] | None) -> dict[str, str]:
    tags: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key in _TAG_KEYS:
            value = block.get(key)
            if isinstance(value, dict):
                tags.update({str(k): str(v) for k, v in value.items() if v is not None})
    return tags


def parse_changes(plan: dict[str, Any]) -> list[ResourceChange]:
    """Return managed-resource changes from a decoded plan. Data sources are skipped."""
    raw = plan.get("resource_changes") or []
    if not isinstance(raw, list):
        raise PlanError("resource_changes must be a list")

    changes: list[ResourceChange] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("mode", "managed") != "managed":
            continue
        change = item.get("change") or {}
        try:
            address = item["address"]
            rtype = item["type"]
        except KeyError as exc:
            raise PlanError(f"resource change is missing {exc.args[0]!r}") from None
        before = change.get("before")
        after = change.get("after")
        replace_paths = tuple(
            tuple(p) for p in (change.get("replace_paths") or []) if isinstance(p, list)
        )
        changes.append(
            ResourceChange(
                address=address,
                type=rtype,
                name=item.get("name", ""),
                provider=item.get("provider_name", ""),
                action=classify(change.get("actions") or ["no-op"]),
                before=before if isinstance(before, dict) else None,
                after=after if isinstance(after, dict) else None,
                replace_paths=replace_paths,
                action_reason=item.get("action_reason"),
                tags=_tags(before, after),
            )
        )
    return changes
