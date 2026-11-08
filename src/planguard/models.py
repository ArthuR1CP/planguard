"""Shared data types used by the parser, rules and report modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class Action(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    REPLACE = "replace"
    NOOP = "no-op"
    READ = "read"
    FORGET = "forget"

    @property
    def destructive(self) -> bool:
        return self in (Action.DELETE, Action.REPLACE)


class Risk(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: str | Risk) -> Risk:
        if isinstance(value, Risk):
            return value
        try:
            return cls[str(value).strip().upper()]
        except KeyError:
            choices = ", ".join(r.name.lower() for r in cls)
            raise ValueError(f"invalid risk level {value!r} (expected one of: {choices})") from None

    @property
    def label(self) -> str:
        return self.name

    @property
    def emoji(self) -> str:
        return {
            Risk.NONE: "⚪",
            Risk.LOW: "🟢",
            Risk.MEDIUM: "🟡",
            Risk.HIGH: "🟠",
            Risk.CRITICAL: "🔴",
        }[self]


@dataclass(frozen=True)
class ResourceChange:
    address: str
    type: str
    name: str
    provider: str
    action: Action
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    replace_paths: tuple[tuple[str | int, ...], ...] = ()
    action_reason: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    create_before_destroy: bool = False
    """True for a replace where the new object is created before the old one is destroyed."""

    @property
    def replace_attributes(self) -> list[str]:
        """Human-readable attribute paths that force replacement, e.g. ``ingress[0].cidr``."""
        out: list[str] = []
        for path in self.replace_paths:
            text = ""
            for part in path:
                text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
            if text and text not in out:
                out.append(text)
        return out


@dataclass(frozen=True)
class Finding:
    rule_id: str
    rule_name: str
    risk: Risk
    address: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule": self.rule_name,
            "risk": self.risk.name,
            "address": self.address,
            "message": self.message,
            "details": self.details,
        }
