"""Kubernetes / Helm rules (PG101-PG106). Registered into the shared rule registry."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from planguard.k8s import PROVIDER, get_path
from planguard.models import Action, Finding, ResourceChange, Risk
from planguard.rules import _self, matches_any, rule

if TYPE_CHECKING:
    from planguard.config import Config

STATEFUL_KINDS: tuple[str, ...] = (
    "PersistentVolumeClaim",
    "PersistentVolume",
    "StatefulSet",
    "Namespace",
    "CustomResourceDefinition",
)
# Replacing these loses data. A recreated StatefulSet keeps its PVCs, so it's PG102 instead.
_DATA_ON_REPLACE = (
    "PersistentVolumeClaim",
    "PersistentVolume",
    "Namespace",
    "CustomResourceDefinition",
)

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "ReplicaSet", "DaemonSet", "Job")
INTERNAL_LB_ANNOTATIONS: dict[str, tuple[str, ...]] = {
    "service.beta.kubernetes.io/aws-load-balancer-internal": ("true", "0.0.0.0/0"),
    "service.beta.kubernetes.io/aws-load-balancer-scheme": ("internal",),
    "networking.gke.io/load-balancer-type": ("internal",),
    "cloud.google.com/load-balancer-type": ("internal",),
    "service.beta.kubernetes.io/azure-load-balancer-internal": ("true",),
    "service.beta.kubernetes.io/oci-load-balancer-internal": ("true",),
}
DANGEROUS_CAPABILITIES = frozenset({"SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE", "ALL"})


def _k8s(change: ResourceChange) -> bool:
    return change.provider == PROVIDER


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --------------------------------------------------------------------------- PG101


@rule(
    "PG101",
    "k8s-stateful-delete",
    Risk.CRITICAL,
    "Kubernetes: delete of a PVC, PV, StatefulSet, Namespace or CRD (a CRD takes every "
    "object of its kind with it), or recreation of a PVC/PV/Namespace/CRD.",
)
def k8s_stateful_delete(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if not _k8s(change):
        return
    kinds = STATEFUL_KINDS + cfg.stateful_kinds
    deleting = change.action is Action.DELETE and matches_any(change.type, kinds)
    recreating = change.action is Action.REPLACE and (
        change.type in _DATA_ON_REPLACE or matches_any(change.type, cfg.stateful_kinds)
    )
    if not (deleting or recreating):
        return
    verb = "DELETED" if deleting else "RECREATED"
    extra = ""
    if change.type == "Namespace":
        extra = ", together with everything in it"
    elif change.type == "CustomResourceDefinition":
        crd_kind = get_path(change.before or change.after, ("spec", "names", "kind"))
        extra = f", along with every {crd_kind or 'custom'} object in the cluster"
    yield _self("PG101").finding(
        change.address,
        f"`{change.address}` will be {verb}{extra}",
        action=change.action.value,
        kind=change.type,
        forces_replacement=change.replace_attributes,
    )


# --------------------------------------------------------------------------- PG102


@rule(
    "PG102",
    "k8s-immutable-change",
    Risk.HIGH,
    "Kubernetes: change to an immutable field (selector, volumeClaimTemplates, clusterIP, "
    "PVC storageClassName, roleRef...). The upgrade fails unless the object is recreated.",
)
def k8s_immutable_change(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if not _k8s(change) or change.action is not Action.REPLACE:
        return
    if change.type in _DATA_ON_REPLACE:
        return  # already CRITICAL under PG101
    fields = ", ".join(f"`{a}`" for a in change.replace_attributes)
    yield _self("PG102").finding(
        change.address,
        f"`{change.address}` changes immutable {fields}: the upgrade fails unless "
        "it is deleted and recreated",
        fields=change.replace_attributes,
    )


# --------------------------------------------------------------------------- PG103


def _is_internal_lb(svc: dict[str, Any]) -> bool:
    annotations = get_path(svc, ("metadata", "annotations")) or {}
    for key, values in INTERNAL_LB_ANNOTATIONS.items():
        if str(annotations.get(key, "")).lower() in values:
            return True
    ranges = get_path(svc, ("spec", "loadBalancerSourceRanges")) or []
    return bool(ranges) and not any(r in ("0.0.0.0/0", "::/0") for r in ranges)


def _service_exposure(svc: dict[str, Any] | None) -> str | None:
    if not svc:
        return None
    stype = get_path(svc, ("spec", "type")) or "ClusterIP"
    if stype == "LoadBalancer" and not _is_internal_lb(svc):
        return "LoadBalancer"
    if stype == "NodePort":
        return "NodePort"
    if get_path(svc, ("spec", "externalIPs")):
        return "externalIPs"
    return None


def _ingress_hosts(ing: dict[str, Any] | None) -> set[str]:
    hosts: set[str] = set()
    for r in _as_list(get_path(ing, ("spec", "rules"))):
        if isinstance(r, dict):
            hosts.add(str(r.get("host") or "*"))
    return hosts


@rule(
    "PG103",
    "k8s-public-exposure",
    Risk.HIGH,
    "Kubernetes: a Service becomes a public LoadBalancer (HIGH) or NodePort (MEDIUM), or an "
    "Ingress serves a new host. Internal load balancer annotations are respected.",
)
def k8s_public_exposure(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if not _k8s(change) or change.action not in (Action.CREATE, Action.UPDATE, Action.REPLACE):
        return
    me = _self("PG103")
    if change.type == "Service":
        new, old = _service_exposure(change.after), _service_exposure(change.before)
        if new and new != old:
            yield me.finding(
                change.address,
                f"`{change.address}` is exposed outside the cluster as {new}",
                risk=Risk.MEDIUM if new == "NodePort" else None,
                exposure=new,
            )
    elif change.type == "Ingress":
        added = sorted(_ingress_hosts(change.after) - _ingress_hosts(change.before))
        if added:
            yield me.finding(
                change.address,
                f"`{change.address}` starts serving {', '.join(added)}",
                hosts=added,
            )


# --------------------------------------------------------------------------- PG104


def _rbac_grants(obj: dict[str, Any] | None) -> set[str]:
    grants: set[str] = set()
    if not obj:
        return grants
    kind = obj.get("kind")
    if kind in ("Role", "ClusterRole"):
        for r in _as_list(obj.get("rules")):
            if not isinstance(r, dict):
                continue
            verbs = [str(v) for v in _as_list(r.get("verbs"))]
            resources = [str(v) for v in _as_list(r.get("resources"))]
            if "*" in verbs or "*" in resources:
                grants.add(f"verbs={','.join(verbs) or '-'} resources={','.join(resources) or '-'}")
    elif kind in ("RoleBinding", "ClusterRoleBinding"):
        if get_path(obj, ("roleRef", "name")) == "cluster-admin":
            grants.add("binds cluster-admin")
        for s in _as_list(obj.get("subjects")):
            if isinstance(s, dict) and s.get("name") in (
                "system:anonymous",
                "system:unauthenticated",
            ):
                grants.add(f"grants access to {s['name']}")
    return grants


@rule(
    "PG104",
    "k8s-rbac-wildcard",
    Risk.HIGH,
    'Kubernetes: Role/ClusterRole with "*" verbs or resources, a binding to cluster-admin, '
    "or a binding for anonymous users.",
)
def k8s_rbac_wildcard(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if not _k8s(change) or change.action not in (Action.CREATE, Action.UPDATE, Action.REPLACE):
        return
    new = sorted(_rbac_grants(change.after) - _rbac_grants(change.before))
    if new:
        yield _self("PG104").finding(
            change.address,
            f"`{change.address}` {'; '.join(new)}",
            grants=new,
        )


# --------------------------------------------------------------------------- PG105


def _replicas(obj: dict[str, Any] | None) -> int | None:
    if not obj:
        return None
    value = get_path(obj, ("spec", "replicas"))
    return 1 if value is None else int(value)


@rule(
    "PG105",
    "k8s-capacity-drop",
    Risk.MEDIUM,
    "Kubernetes: a workload scaled to 0 replicas, or a PodDisruptionBudget removed.",
)
def k8s_capacity_drop(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if not _k8s(change):
        return
    me = _self("PG105")
    if change.type == "PodDisruptionBudget" and change.action is Action.DELETE:
        yield me.finding(change.address, f"`{change.address}` will be DELETED")
    elif (
        change.type in ("Deployment", "StatefulSet", "ReplicaSet")
        and change.action in (Action.UPDATE, Action.REPLACE)
        and (_replicas(change.before) or 0) > 0
        and _replicas(change.after) == 0
    ):
        yield me.finding(
            change.address,
            f"`{change.address}` is scaled from {_replicas(change.before)} to 0 replicas",
        )


# --------------------------------------------------------------------------- PG106


def _pod_spec(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    if not obj:
        return None
    kind = obj.get("kind")
    if kind == "Pod":
        return obj.get("spec")
    if kind == "CronJob":
        return get_path(obj, ("spec", "jobTemplate", "spec", "template", "spec"))
    if kind in WORKLOAD_KINDS:
        return get_path(obj, ("spec", "template", "spec"))
    return None


def _privileges(obj: dict[str, Any] | None) -> set[str]:
    spec = _pod_spec(obj)
    if not isinstance(spec, dict):
        return set()
    found: set[str] = set()
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(flag) is True:
            found.add(flag)
    for vol in _as_list(spec.get("volumes")):
        if isinstance(vol, dict) and isinstance(vol.get("hostPath"), dict):
            found.add(f"hostPath {vol['hostPath'].get('path', '?')}")
    containers = _as_list(spec.get("containers")) + _as_list(spec.get("initContainers"))
    for c in containers:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "?")
        sc = c.get("securityContext") or {}
        if sc.get("privileged") is True:
            found.add(f"privileged container {name}")
        added = {str(x).upper() for x in _as_list(get_path(sc, ("capabilities", "add")))}
        for cap in sorted(added & DANGEROUS_CAPABILITIES):
            found.add(f"{cap} on {name}")
    return found


@rule(
    "PG106",
    "k8s-pod-privileged",
    Risk.MEDIUM,
    "Kubernetes: pods gain privileged containers, hostNetwork/hostPID/hostIPC, hostPath "
    "volumes or capabilities like SYS_ADMIN / NET_ADMIN.",
)
def k8s_pod_privileged(change: ResourceChange, cfg: Config) -> Iterator[Finding]:
    if not _k8s(change) or change.action not in (Action.CREATE, Action.UPDATE, Action.REPLACE):
        return
    new = sorted(_privileges(change.after) - _privileges(change.before))
    if new:
        yield _self("PG106").finding(
            change.address,
            f"`{change.address}` adds {', '.join(new)}",
            privileges=new,
        )
