"""Diff two sets of rendered Kubernetes manifests into :class:`ResourceChange` objects.

Input is whatever ``helm template``, ``kustomize build`` or a directory of plain YAML
produces: multi-document YAML, optionally with ``kind: List`` wrappers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from planguard.models import Action, ResourceChange
from planguard.parser import PlanError

PROVIDER = "kubernetes"

CLUSTER_SCOPED_KINDS = frozenset(
    {
        "APIService",
        "CSIDriver",
        "ClusterIssuer",
        "ClusterRole",
        "ClusterRoleBinding",
        "CustomResourceDefinition",
        "IngressClass",
        "MutatingWebhookConfiguration",
        "Namespace",
        "Node",
        "PersistentVolume",
        "PriorityClass",
        "RuntimeClass",
        "StorageClass",
        "ValidatingWebhookConfiguration",
        "VolumeSnapshotClass",
    }
)

# Fields Kubernetes refuses to change in place. Changing one means the apply fails, or the
# object has to be deleted and recreated (e.g. `helm upgrade --force`).
IMMUTABLE_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "Deployment": (("spec", "selector"),),
    "ReplicaSet": (("spec", "selector"),),
    "DaemonSet": (("spec", "selector"),),
    "StatefulSet": (
        ("spec", "selector"),
        ("spec", "volumeClaimTemplates"),
        ("spec", "serviceName"),
        ("spec", "podManagementPolicy"),
    ),
    "Job": (("spec", "selector"), ("spec", "template")),
    "Service": (("spec", "clusterIP"),),
    "PersistentVolumeClaim": (
        ("spec", "storageClassName"),
        ("spec", "accessModes"),
        ("spec", "volumeName"),
        ("spec", "volumeMode"),
        ("spec", "selector"),
    ),
    "RoleBinding": (("roleRef",),),
    "ClusterRoleBinding": (("roleRef",),),
}


class ManifestError(PlanError):
    """Raised when manifests can't be read or don't look like Kubernetes objects."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ManifestError(f"manifest file not found: {path}") from None
    except UnicodeDecodeError:
        raise ManifestError(f"manifest file is not text: {path}") from None


def _files(source: Path) -> list[Path]:
    if source.is_dir():
        return sorted(p for p in source.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file())
    return [source]


def load_manifests(source: str | Path) -> list[dict[str, Any]]:
    """Load every Kubernetes object from a YAML file or a directory of YAML files.

    A missing path whose name is empty or ``/dev/null`` yields no objects, which lets
    callers express "nothing existed before".
    """
    import yaml

    path = Path(source)
    if str(source) in ("", "/dev/null"):
        return []
    if not path.exists():
        raise ManifestError(f"manifest path not found: {path}")

    objects: list[dict[str, Any]] = []
    for file in _files(path):
        try:
            docs = list(yaml.safe_load_all(_read_text(file)))
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {file}: {exc}") from None
        for doc in docs:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ManifestError(
                    f"{file}: expected a Kubernetes object, got {type(doc).__name__}"
                )
            kind = str(doc.get("kind", ""))
            if kind == "List" or (kind.endswith("List") and isinstance(doc.get("items"), list)):
                items = doc.get("items") or []
                objects.extend(i for i in items if isinstance(i, dict))
                continue
            objects.append(doc)
    for obj in objects:
        if not obj.get("kind") or not (obj.get("metadata") or {}).get("name"):
            raise ManifestError(
                "every manifest needs `kind` and `metadata.name` "
                f"(got kind={obj.get('kind')!r}, name={(obj.get('metadata') or {}).get('name')!r})"
            )
    return objects


def address(obj: dict[str, Any]) -> str:
    """``Kind/namespace/name`` for namespaced objects, ``Kind/name`` otherwise."""
    kind = str(obj["kind"])
    meta = obj.get("metadata") or {}
    namespace = meta.get("namespace")
    if kind in CLUSTER_SCOPED_KINDS or not namespace:
        return f"{kind}/{meta['name']}"
    return f"{kind}/{namespace}/{meta['name']}"


def pair_key(obj: dict[str, Any], namespace: str = "") -> tuple[str, str, str]:
    """``(kind, namespace, name)`` used to match an object across the two renders.

    With a release ``namespace``, a missing namespace means that namespace, which is how Helm
    and kubectl apply it. Without one, a missing namespace stays empty, so an object only pairs
    with the same spelling on the other side. Cluster-scoped kinds get an empty namespace.
    """
    kind = str(obj["kind"])
    meta = obj.get("metadata") or {}
    ns = "" if kind in CLUSTER_SCOPED_KINDS else str(meta.get("namespace") or namespace)
    return kind, ns, str(meta["name"])


def _same(before: dict[str, Any], after: dict[str, Any], namespace: str) -> bool:
    """Equal once an explicit release namespace and an implicit one are treated alike."""
    if before == after:
        return True
    if not namespace:
        return False

    def strip(obj: dict[str, Any]) -> dict[str, Any]:
        meta = obj.get("metadata") or {}
        if meta.get("namespace") != namespace:
            return obj
        return {**obj, "metadata": {k: v for k, v in meta.items() if k != "namespace"}}

    return strip(before) == strip(after)


def get_path(obj: dict[str, Any] | None, path: tuple[str, ...]) -> Any:
    cur: Any = obj
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _labels(*objs: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for obj in objs:
        meta = (obj or {}).get("metadata") or {}
        for key in ("labels", "annotations"):
            values = meta.get(key)
            if isinstance(values, dict):
                out.update({str(k): str(v) for k, v in values.items() if v is not None})
    return out


def immutable_changes(kind: str, before: dict[str, Any], after: dict[str, Any]) -> list[list[str]]:
    changed = [
        list(path)
        for path in IMMUTABLE_FIELDS.get(kind, ())
        if get_path(before, path) != get_path(after, path)
    ]
    # ConfigMaps and Secrets marked `immutable: true` can't change their data.
    if kind in ("ConfigMap", "Secret") and before.get("immutable") is True:
        for key in ("data", "stringData", "binaryData"):
            if before.get(key) != after.get(key):
                changed.append([key])
    return changed


def diff_manifests(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    namespace: str = "",
) -> list[ResourceChange]:
    """Pair objects across the two renders and classify each change.

    Objects are paired by kind, name and namespace. When the release ``namespace`` is given,
    an object without a namespace lives in it, so adding ``metadata.namespace: <release
    namespace>`` to a template is not a delete + create. When it is not given (the default),
    an object without a namespace only pairs with an object without one. Displayed addresses
    are unchanged (``address()``).
    """
    old = {pair_key(o, namespace): o for o in before}
    new = {pair_key(o, namespace): o for o in after}
    changes: list[ResourceChange] = []
    for key in old.keys() | new.keys():
        b, a = old.get(key), new.get(key)
        obj = a or b
        assert obj is not None
        addr = address(obj)
        kind = str(obj["kind"])
        replace_paths: list[list[str]] = []
        if b is None:
            action = Action.CREATE
        elif a is None:
            action = Action.DELETE
        elif _same(b, a, namespace):
            action = Action.NOOP
        else:
            replace_paths = immutable_changes(kind, b, a)
            action = Action.REPLACE if replace_paths else Action.UPDATE
        changes.append(
            ResourceChange(
                address=addr,
                type=kind,
                name=str(obj["metadata"]["name"]),
                provider=PROVIDER,
                action=action,
                before=b,
                after=a,
                replace_paths=tuple(tuple(p) for p in replace_paths),
                tags=_labels(b, a),
            )
        )
    changes.sort(key=lambda c: c.address)
    return changes
