"""Risk rules. Each rule inspects one resource change (or the whole plan) and yields findings."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Literal

from planguard.models import Action, Finding, ResourceChange, Risk

if TYPE_CHECKING:
    from planguard.config import Config

# --------------------------------------------------------------------------- type lists

STATEFUL_TYPES: tuple[str, ...] = (
    # AWS
    "aws_db_instance",
    "aws_db_cluster_snapshot",
    "aws_rds_cluster",
    "aws_rds_cluster_instance",
    "aws_rds_global_cluster",
    "aws_dynamodb_table",
    "aws_dynamodb_global_table",
    "aws_s3_bucket",
    "aws_efs_file_system",
    "aws_ebs_volume",
    "aws_elasticache_*",
    "aws_memorydb_cluster",
    "aws_redshift_cluster",
    "aws_redshiftserverless_namespace",
    "aws_docdb_cluster",
    "aws_neptune_cluster",
    "aws_opensearch_domain",
    "aws_elasticsearch_domain",
    "aws_msk_cluster",
    "aws_kinesis_stream",
    "aws_kms_key",
    "aws_backup_vault",
    "aws_timestream_database",
    "aws_timestream_table",
    "aws_fsx_*_file_system",
    # Google Cloud
    "google_sql_database_instance",
    "google_sql_database",
    "google_storage_bucket",
    "google_bigquery_dataset",
    "google_bigquery_table",
    "google_spanner_instance",
    "google_spanner_database",
    "google_bigtable_instance",
    "google_bigtable_table",
    "google_redis_instance",
    "google_filestore_instance",
    "google_compute_disk",
    "google_firestore_database",
    "google_alloydb_cluster",
    "google_kms_crypto_key",
    # Azure
    "azurerm_*_database",
    "azurerm_mssql_server",
    "azurerm_mssql_managed_instance",
    "azurerm_postgresql_server",
    "azurerm_postgresql_flexible_server",
    "azurerm_mysql_server",
    "azurerm_mysql_flexible_server",
    "azurerm_cosmosdb_account",
    "azurerm_cosmosdb_*_database",
    "azurerm_storage_account",
    "azurerm_storage_container",
    "azurerm_storage_share",
    "azurerm_redis_cache",
    "azurerm_managed_disk",
    "azurerm_key_vault",
)

COMPUTE_TYPES: tuple[str, ...] = (
    "aws_instance",
    "aws_launch_template",
    "aws_launch_configuration",
    "aws_autoscaling_group",
    "aws_eks_cluster",
    "aws_eks_node_group",
    "aws_ecs_cluster",
    "google_compute_instance",
    "google_compute_instance_template",
    "google_compute_instance_group_manager",
    "google_container_cluster",
    "google_container_node_pool",
    "azurerm_*virtual_machine",
    "azurerm_*virtual_machine_scale_set",
    "azurerm_kubernetes_cluster",
    "azurerm_kubernetes_cluster_node_pool",
)

IAM_POLICY_TYPES = frozenset(
    {"aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy"}
)
IAM_ATTACHMENT_TYPES = frozenset(
    {
        "aws_iam_role_policy_attachment",
        "aws_iam_user_policy_attachment",
        "aws_iam_group_policy_attachment",
        "aws_iam_policy_attachment",
    }
)
ADMIN_POLICY_SUFFIXES = (":policy/AdministratorAccess", ":policy/IAMFullAccess")

WORLD_V4 = ipaddress.ip_network("0.0.0.0/0")
WORLD_V6 = ipaddress.ip_network("::/0")


def matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(fnmatchcase(value, p) for p in patterns)


def is_stateful(rtype: str, cfg: Config) -> bool:
    return matches_any(rtype, STATEFUL_TYPES) or matches_any(rtype, cfg.stateful_types)


def is_compute(rtype: str, cfg: Config) -> bool:
    return matches_any(rtype, COMPUTE_TYPES) or matches_any(rtype, cfg.compute_types)


def _verb(action: Action) -> str:
    return "REPLACED" if action is Action.REPLACE else "DESTROYED"


def _replace_suffix(change: ResourceChange) -> str:
    attrs = change.replace_attributes
    if attrs:
        return " (forces replacement: " + ", ".join(f"`{a}`" for a in attrs) + ")"
    if change.action_reason == "replace_by_request":
        return " (replacement requested with -replace)"
    return ""


# --------------------------------------------------------------------------- rule registry


ResourceCheck = Callable[[ResourceChange, "Config"], Iterator[Finding]]
PlanCheck = Callable[[list[ResourceChange], "Config"], Iterator[Finding]]


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    default_risk: Risk
    scope: Literal["resource", "plan"]
    description: str
    check: Callable[..., Iterator[Finding]]

    def finding(
        self,
        address: str,
        message: str,
        risk: Risk | None = None,
        **details: Any,
    ) -> Finding:
        return Finding(
            rule_id=self.id,
            rule_name=self.name,
            risk=risk if risk is not None else self.default_risk,
            address=address,
            message=message,
            details=details,
        )


RULES: list[Rule] = []


def rule(
    id: str,
    name: str,
    risk: Risk,
    description: str,
    scope: Literal["resource", "plan"] = "resource",
) -> Callable[[Callable[..., Iterator[Finding]]], Callable[..., Iterator[Finding]]]:
    def register(fn: Callable[..., Iterator[Finding]]) -> Callable[..., Iterator[Finding]]:
        RULES.append(Rule(id, name, risk, scope, description, fn))
        return fn

    return register


def _self(rule_id: str) -> Rule:
    return next(r for r in RULES if r.id == rule_id)


# --------------------------------------------------------------------------- PG001


@rule(
    "PG001",
    "stateful-destroy",
    Risk.CRITICAL,
    "Delete or replace of a stateful resource (databases, buckets, volumes, keys).",
)
def stateful_destroy(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if change.action.destructive and is_stateful(change.type, cfg):
        yield _self("PG001").finding(
            change.address,
            f"`{change.address}` will be {_verb(change.action)}{_replace_suffix(change)}",
            action=change.action.value,
            forces_replacement=change.replace_attributes,
        )


# --------------------------------------------------------------------------- PG002


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _policy_issues(document: Any) -> tuple[list[str], list[str]]:
    """Return (action_wildcards, resource_wildcards) found in Allow statements."""
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except json.JSONDecodeError:
            return [], []
    if not isinstance(document, dict):
        return [], []
    action_hits: list[str] = []
    resource_hits: list[str] = []
    for idx, stmt in enumerate(_as_list(document.get("Statement"))):
        if not isinstance(stmt, dict) or stmt.get("Effect", "Allow") != "Allow":
            continue
        sid = stmt.get("Sid") or f"#{idx}"
        actions = [str(a) for a in _as_list(stmt.get("Action"))]
        wild = [a for a in actions if a == "*" or a.endswith(":*")]
        if wild:
            action_hits.append(f"{sid}: Action {', '.join(wild)}")
        elif "NotAction" in stmt:
            action_hits.append(f"{sid}: Allow with NotAction")
        resources = [str(r) for r in _as_list(stmt.get("Resource"))]
        if "*" in resources:
            resource_hits.append(f"{sid}: Resource *")
    return action_hits, resource_hits


def _policies(values: dict[str, Any] | None, rtype: str) -> list[Any]:
    if not values:
        return []
    if rtype == "aws_iam_role":
        return [
            p.get("policy") for p in _as_list(values.get("inline_policy")) if isinstance(p, dict)
        ]
    return [values.get("policy")]


@rule(
    "PG002",
    "iam-wildcard",
    Risk.HIGH,
    "IAM policy grants wildcard actions (HIGH) or wildcard resources (MEDIUM), "
    "or attaches AdministratorAccess.",
)
def iam_wildcard(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    me = _self("PG002")
    if change.action not in (Action.CREATE, Action.UPDATE, Action.REPLACE) or not change.after:
        return

    if change.type in IAM_ATTACHMENT_TYPES:
        arn = str(change.after.get("policy_arn") or "")
        before_arn = str((change.before or {}).get("policy_arn") or "")
        if arn.endswith(ADMIN_POLICY_SUFFIXES) and arn != before_arn:
            yield me.finding(
                change.address,
                f"`{change.address}` attaches `{arn.rsplit('/', 1)[-1]}`",
                policy_arn=arn,
            )
        return

    if change.type not in IAM_POLICY_TYPES and change.type != "aws_iam_role":
        return
    after = _policies(change.after, change.type)
    before = _policies(change.before, change.type)
    if after == before:
        return  # policy document unchanged; don't re-flag existing grants
    old_actions: set[str] = set()
    old_resources: set[str] = set()
    for doc in before:
        a, r = _policy_issues(doc)
        old_actions.update(a)
        old_resources.update(r)
    actions: list[str] = []
    resources: list[str] = []
    for doc in after:
        a, r = _policy_issues(doc)
        actions += [x for x in a if x not in old_actions]
        resources += [x for x in r if x not in old_resources]
    if actions:
        yield me.finding(
            change.address,
            f"`{change.address}` grants wildcard actions ({'; '.join(actions)})",
            statements=actions + resources,
        )
    elif resources:
        yield me.finding(
            change.address,
            f"`{change.address}` grants access to all resources ({'; '.join(resources)})",
            risk=Risk.MEDIUM,
            statements=resources,
        )


# --------------------------------------------------------------------------- PG003


def _is_world(cidr: Any) -> bool:
    if not isinstance(cidr, str):
        return False
    if cidr.strip().lower() in ("*", "internet", "any"):
        return True
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return False
    return net in (WORLD_V4, WORLD_V6)


def _parse_port_range(spec: Any) -> tuple[int, int] | None:
    """Parse '22', '1000-2000', '*' or an int. None means all ports."""
    if spec is None:
        return None
    text = str(spec).strip()
    if text in ("", "*", "-1"):
        return None
    if "-" in text:
        lo, hi = text.split("-", 1)
        return int(lo), int(hi)
    return int(text), int(text)


_ALL_PROTOCOLS = ("-1", "all", "*")
_ICMP = ("icmp", "icmpv6", "1", "58")


@dataclass(frozen=True)
class _Exposure:
    protocol: str
    ports: tuple[int, int] | None  # None = all ports
    source: str

    def render(self) -> str:
        if self.ports is None:
            port = "all ports"
        elif self.ports[0] == self.ports[1]:
            port = f"port {self.ports[0]}"
        else:
            port = f"ports {self.ports[0]}-{self.ports[1]}"
        proto = "all protocols" if self.protocol in _ALL_PROTOCOLS else self.protocol
        return f"{port}/{proto} to {self.source}"


def _aws_rule_exposures(rule_values: dict[str, Any], cidr_keys: tuple[str, ...]) -> list[_Exposure]:
    proto = str(rule_values.get("protocol", rule_values.get("ip_protocol", "-1"))).lower()
    if proto in _ALL_PROTOCOLS:
        ports = None
    else:
        lo, hi = rule_values.get("from_port"), rule_values.get("to_port")
        ports = None if lo is None or hi is None or int(lo) < 0 else (int(lo), int(hi))
        if ports == (0, 65535):
            ports = None
    out = []
    for key in cidr_keys:
        for cidr in _as_list(rule_values.get(key)):
            if _is_world(cidr):
                out.append(_Exposure(proto, ports, str(cidr)))
    return out


def _exposures(rtype: str, values: dict[str, Any] | None) -> list[_Exposure]:
    if not values:
        return []
    if rtype == "aws_security_group":
        out: list[_Exposure] = []
        for ingress in _as_list(values.get("ingress")):
            if isinstance(ingress, dict):
                out += _aws_rule_exposures(ingress, ("cidr_blocks", "ipv6_cidr_blocks"))
        return out
    if rtype == "aws_security_group_rule":
        if values.get("type") != "ingress":
            return []
        return _aws_rule_exposures(values, ("cidr_blocks", "ipv6_cidr_blocks"))
    if rtype == "aws_vpc_security_group_ingress_rule":
        return _aws_rule_exposures(values, ("cidr_ipv4", "cidr_ipv6"))
    if rtype == "google_compute_firewall":
        if str(values.get("direction") or "INGRESS").upper() != "INGRESS":
            return []
        sources = [s for s in _as_list(values.get("source_ranges")) if _is_world(s)]
        out = []
        for allow in _as_list(values.get("allow")):
            if not isinstance(allow, dict):
                continue
            proto = str(allow.get("protocol", "all")).lower()
            port_specs = _as_list(allow.get("ports")) or [None]
            for src in sources:
                for spec in port_specs:
                    out.append(_Exposure(proto, _parse_port_range(spec), str(src)))
        return out
    if rtype == "azurerm_network_security_rule":
        if (
            str(values.get("direction", "")).lower() != "inbound"
            or str(values.get("access", "")).lower() != "allow"
        ):
            return []
        sources = [
            values.get("source_address_prefix"),
            *_as_list(values.get("source_address_prefixes")),
        ]
        proto = str(values.get("protocol", "*")).lower()
        port_specs = [
            values.get("destination_port_range"),
            *_as_list(values.get("destination_port_ranges")),
        ]
        port_specs = [p for p in port_specs if p not in (None, "")] or ["*"]
        return [
            _Exposure(proto, _parse_port_range(spec), str(src))
            for src in sources
            if _is_world(src)
            for spec in port_specs
        ]
    return []


@rule(
    "PG003",
    "sg-open-world",
    Risk.HIGH,
    "Ingress from 0.0.0.0/0 or ::/0 on ports other than the public ones (80/443 by default).",
)
def sg_open_world(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if change.action not in (Action.CREATE, Action.UPDATE, Action.REPLACE):
        return
    existing = set(_exposures(change.type, change.before))
    risky = []
    for exp in _exposures(change.type, change.after):
        if exp in existing or exp.protocol in _ICMP:
            continue
        if (
            exp.ports is not None
            and exp.ports[0] == exp.ports[1]
            and exp.ports[0] in cfg.public_ports
        ):
            continue
        if exp not in risky:
            risky.append(exp)
    if risky:
        yield _self("PG003").finding(
            change.address,
            f"`{change.address}` opens {', '.join(e.render() for e in risky)}",
            exposures=[e.render() for e in risky],
        )


# --------------------------------------------------------------------------- PG004


@rule(
    "PG004",
    "mass-delete",
    Risk.HIGH,
    "More resources destroyed or replaced than `thresholds.max_deletes` (default 5).",
    scope="plan",
)
def mass_delete(changes: list[ResourceChange], cfg: Config) -> Iterator[Finding]:
    destroyed = [c.address for c in changes if c.action.destructive]
    if len(destroyed) > cfg.max_deletes:
        yield _self("PG004").finding(
            "(plan)",
            f"{len(destroyed)} resources will be destroyed or replaced "
            f"(threshold: {cfg.max_deletes})",
            count=len(destroyed),
            threshold=cfg.max_deletes,
        )


# --------------------------------------------------------------------------- PG005


@rule(
    "PG005",
    "compute-replace",
    Risk.MEDIUM,
    "Replacement of compute (instances, launch templates, node groups, clusters).",
)
def compute_replace(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if (
        change.action is Action.REPLACE
        and is_compute(change.type, cfg)
        and not is_stateful(change.type, cfg)
    ):
        yield _self("PG005").finding(
            change.address,
            f"`{change.address}` will be REPLACED{_replace_suffix(change)}",
            forces_replacement=change.replace_attributes,
        )


# --------------------------------------------------------------------------- PG006


def _protected_tag(change: ResourceChange, cfg: Config) -> str | None:
    for key, expected in cfg.protected_tags.items():
        if key in change.tags and (expected == "*" or change.tags[key].lower() == expected):
            return f"{key}={change.tags[key]}"
    return None


@rule(
    "PG006",
    "protected-tag",
    Risk.MEDIUM,
    "Any change to a resource carrying a protected tag (HIGH if it is destroyed or replaced).",
)
def protected_tag(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if change.action not in (Action.UPDATE, Action.DELETE, Action.REPLACE):
        return
    tag = _protected_tag(change, cfg)
    if tag:
        verb = _verb(change.action) if change.action.destructive else "modified"
        yield _self("PG006").finding(
            change.address,
            f"protected resource `{change.address}` ({tag}) will be {verb}",
            risk=Risk.HIGH if change.action.destructive else None,
            tag=tag,
        )


# --------------------------------------------------------------------------- PG007


@rule(
    "PG007",
    "forces-replacement",
    Risk.LOW,
    "Lists the attributes that force each replacement (from `replace_paths`).",
)
def forces_replacement(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if change.action is Action.REPLACE:
        attrs = change.replace_attributes
        reason = (
            "forced by " + ", ".join(f"`{a}`" for a in attrs)
            if attrs
            else "requested with -replace"
            if change.action_reason == "replace_by_request"
            else "reason not reported by Terraform"
        )
        yield _self("PG007").finding(
            change.address,
            f"`{change.address}` replacement {reason}",
            forces_replacement=attrs,
        )


# --------------------------------------------------------------------------- engine


def evaluate(
    changes: list[ResourceChange], cfg: Config
) -> tuple[list[ResourceChange], list[ResourceChange], list[Finding]]:
    """Run all enabled rules.

    Returns ``(analyzed_changes, ignored_changes, findings)``; findings are sorted by
    risk (highest first), then address.
    """
    ignored: list[ResourceChange] = []
    analyzed: list[ResourceChange] = []
    for c in changes:
        (ignored if matches_any(c.address, cfg.ignore) else analyzed).append(c)
    findings: list[Finding] = []
    for r in RULES:
        override = cfg.rule_overrides.get(r.id, r.default_risk)
        if override is None:
            continue
        if r.scope == "plan":
            produced: Iterable[Finding] = r.check(analyzed, cfg)
        else:
            produced = (f for c in analyzed for f in r.check(c, cfg))
        for f in produced:
            if r.id in cfg.rule_overrides:
                f = Finding(f.rule_id, f.rule_name, override, f.address, f.message, f.details)
            findings.append(f)
    findings.sort(key=lambda f: (-f.risk, f.address, f.rule_id))
    return analyzed, ignored, findings


def overall_risk(changes: list[ResourceChange], findings: list[Finding]) -> Risk:
    if findings:
        return max(f.risk for f in findings)
    if any(c.action not in (Action.NOOP, Action.READ) for c in changes):
        return Risk.LOW
    return Risk.NONE
