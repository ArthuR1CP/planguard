"""Load and validate ``.planguard.yml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from planguard.models import Risk

DEFAULT_CONFIG_NAMES = (".planguard.yml", ".planguard.yaml")

_TOP_LEVEL_KEYS = {
    "fail_on",
    "thresholds",
    "stateful_types",
    "stateful_kinds",
    "compute_types",
    "ignore",
    "protected_tags",
    "public_ports",
    "rules",
}
_THRESHOLD_KEYS = {"max_deletes"}


class ConfigError(Exception):
    """Raised for an unreadable or invalid configuration file."""


@dataclass(frozen=True)
class Config:
    fail_on: Risk | None = Risk.HIGH
    """Fail when overall risk >= this level. ``None`` never fails."""
    max_deletes: int = 5
    stateful_types: tuple[str, ...] = ()
    """Extra stateful Terraform type globs, added to the built-in list."""
    stateful_kinds: tuple[str, ...] = ()
    """Extra stateful Kubernetes kinds (globs), added to the built-in list."""
    compute_types: tuple[str, ...] = ()
    """Extra compute type globs, added to the built-in list."""
    ignore: tuple[str, ...] = ()
    """Resource address globs excluded from analysis."""
    protected_tags: dict[str, str] = field(
        default_factory=lambda: {"planguard:protected": "true", "planguard/protected": "true"}
    )
    """Tags (Terraform) or labels/annotations (Kubernetes) that mark a resource as protected."""
    public_ports: frozenset[int] = frozenset({80, 443})
    rule_overrides: dict[str, Risk | None] = field(default_factory=dict)
    """Per-rule risk override; ``None`` disables the rule."""


def parse_fail_on(value: Any) -> Risk | None:
    if value is None or str(value).strip().lower() in ("none", "never", "off", "false"):
        return None
    try:
        return Risk.parse(str(value))
    except ValueError as exc:
        raise ConfigError(f"fail_on: {exc}") from None


def _str_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key}: expected a list of strings")
    return tuple(value)


def config_from_dict(data: dict[str, Any] | None) -> Config:
    from planguard.rules import RULES  # local import: rules depends on config

    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(sorted(unknown))}")

    kwargs: dict[str, Any] = {}
    if "fail_on" in data:
        kwargs["fail_on"] = parse_fail_on(data["fail_on"])

    thresholds = data.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        raise ConfigError("thresholds: expected a mapping")
    if set(thresholds) - _THRESHOLD_KEYS:
        bad = ", ".join(sorted(set(thresholds) - _THRESHOLD_KEYS))
        raise ConfigError(f"unknown threshold(s): {bad}")
    if "max_deletes" in thresholds:
        value = thresholds["max_deletes"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError("thresholds.max_deletes: expected a non-negative integer")
        kwargs["max_deletes"] = value

    for key in ("stateful_types", "stateful_kinds", "compute_types", "ignore"):
        if key in data:
            kwargs[key] = _str_list(data, key)

    if "protected_tags" in data:
        tags = data["protected_tags"] or {}
        if not isinstance(tags, dict):
            raise ConfigError("protected_tags: expected a mapping of tag key to value")
        kwargs["protected_tags"] = {str(k): str(v).lower() for k, v in tags.items()}

    if "public_ports" in data:
        ports = data["public_ports"] or []
        if not isinstance(ports, list) or not all(
            isinstance(p, int) and not isinstance(p, bool) for p in ports
        ):
            raise ConfigError("public_ports: expected a list of integers")
        kwargs["public_ports"] = frozenset(ports)

    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        raise ConfigError("rules: expected a mapping of rule id to level or 'off'")
    known = {r.id for r in RULES}
    overrides: dict[str, Risk | None] = {}
    for rule_id, level in rules.items():
        rule_id = str(rule_id).upper()
        if rule_id not in known:
            raise ConfigError(f"rules: unknown rule id {rule_id!r}")
        # YAML parses a bare `off` as False.
        if level is False or str(level).strip().lower() == "off":
            overrides[rule_id] = None
        else:
            try:
                overrides[rule_id] = Risk.parse(str(level))
            except ValueError as exc:
                raise ConfigError(f"rules.{rule_id}: {exc}") from None
    kwargs["rule_overrides"] = overrides
    return Config(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """Load config from ``path``, or from ``.planguard.yml`` in the cwd if present."""
    import yaml

    if path is None:
        for name in DEFAULT_CONFIG_NAMES:
            if Path(name).is_file():
                path = name
                break
        else:
            return Config()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from None
    return config_from_dict(data)
