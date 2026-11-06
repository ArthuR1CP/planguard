import pytest

from planguard.config import Config, config_from_dict
from planguard.k8s import ManifestError, address, diff_manifests, load_manifests
from planguard.models import Action, Risk
from planguard.rules import evaluate, overall_risk
from tests.conftest import FIXTURES

K8S = FIXTURES / "k8s"


def obj(kind, name, namespace=None, api="v1", **body):
    meta = {"name": name}
    if namespace:
        meta["namespace"] = namespace
    if "labels" in body:
        meta["labels"] = body.pop("labels")
    if "annotations" in body:
        meta["annotations"] = body.pop("annotations")
    return {"apiVersion": api, "kind": kind, "metadata": meta, **body}


def run(before, after, cfg=None):
    changes = diff_manifests(before, after)
    return evaluate(changes, cfg or Config())[2]


def ids(findings):
    return sorted((f.rule_id, f.address, f.risk.name) for f in findings)


def deploy(name="web", replicas=2, selector=None, containers=None, **spec):
    return obj(
        "Deployment",
        name,
        api="apps/v1",
        spec={
            "replicas": replicas,
            "selector": {"matchLabels": selector or {"app": name}},
            "template": {"spec": {"containers": containers or [{"name": "c", "image": "x"}]}},
            **spec,
        },
    )


# ---------------------------------------------------------------- loading


def test_load_multi_doc_list_and_dir(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "kind: ConfigMap\napiVersion: v1\nmetadata: {name: a}\n---\n---\n"
        "kind: List\napiVersion: v1\nitems:\n"
        "  - {kind: Secret, apiVersion: v1, metadata: {name: s}}\n"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.yml").write_text("kind: Service\napiVersion: v1\nmetadata: {name: b, namespace: x}\n")
    (tmp_path / "notes.txt").write_text("ignored")
    objs = load_manifests(tmp_path)
    assert sorted(address(o) for o in objs) == ["ConfigMap/a", "Secret/s", "Service/x/b"]


def test_load_empty_sources():
    assert load_manifests("/dev/null") == []
    assert load_manifests("") == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("kind: [unclosed\n", "invalid YAML"),
        ("- just\n- a list\n", "expected a Kubernetes object"),
        ("kind: ConfigMap\nmetadata: {}\n", "needs `kind` and `metadata.name`"),
    ],
)
def test_load_invalid(tmp_path, content, message):
    path = tmp_path / "m.yaml"
    path.write_text(content)
    with pytest.raises(ManifestError, match=message):
        load_manifests(path)


def test_load_missing(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifests(tmp_path / "nope.yaml")


def test_address_cluster_scoped_ignores_namespace():
    assert address(obj("ClusterRole", "r", namespace="x")) == "ClusterRole/r"
    assert address(obj("ConfigMap", "c", namespace="x")) == "ConfigMap/x/c"


# ---------------------------------------------------------------- diff


def test_diff_classification():
    cm = obj("ConfigMap", "same", data={"a": "1"})
    changes = diff_manifests(
        [cm, obj("ConfigMap", "gone"), deploy("web"), deploy("api")],
        [
            cm,
            obj("ConfigMap", "new"),
            deploy("web", replicas=3),
            deploy("api", selector={"x": "y"}),
        ],
    )
    actions = {c.address: c.action for c in changes}
    assert actions == {
        "ConfigMap/same": Action.NOOP,
        "ConfigMap/gone": Action.DELETE,
        "ConfigMap/new": Action.CREATE,
        "Deployment/web": Action.UPDATE,
        "Deployment/api": Action.REPLACE,
    }
    api = next(c for c in changes if c.address == "Deployment/api")
    assert api.replace_attributes == ["spec.selector"]
    assert api.provider == "kubernetes"


def test_immutable_configmap_data():
    before = obj("ConfigMap", "c", immutable=True, data={"a": "1"})
    after = obj("ConfigMap", "c", immutable=True, data={"a": "2"})
    (change,) = diff_manifests([before], [after])
    assert change.action is Action.REPLACE


# ---------------------------------------------------------------- helm fixture end to end


def test_helm_chart_fixture():
    before = load_manifests(K8S / "helm_shop_v1.yaml")
    after = load_manifests(K8S / "helm_shop_v2.yaml")
    analyzed, _, findings = evaluate(diff_manifests(before, after), Config())
    assert overall_risk(analyzed, findings) is Risk.CRITICAL
    assert ids(findings) == [
        ("PG006", "PersistentVolumeClaim/shop-cache", "HIGH"),
        ("PG007", "StatefulSet/shop-db", "LOW"),
        ("PG101", "PersistentVolumeClaim/shop-cache", "CRITICAL"),
        ("PG102", "StatefulSet/shop-db", "HIGH"),
        ("PG103", "Ingress/shop-web", "HIGH"),
        ("PG103", "Service/shop-web", "HIGH"),
        ("PG104", "ClusterRoleBinding/shop-ops-admin", "HIGH"),
        ("PG105", "Deployment/shop-worker", "MEDIUM"),
        ("PG105", "PodDisruptionBudget/shop-web", "MEDIUM"),
        ("PG106", "Deployment/shop-web", "MEDIUM"),
    ]


def test_same_render_is_noop():
    docs = load_manifests(K8S / "helm_shop_v1.yaml")
    changes = diff_manifests(docs, docs)
    assert {c.action for c in changes} == {Action.NOOP}
    assert run(docs, docs) == []


# ---------------------------------------------------------------- PG101


@pytest.mark.parametrize(
    "kind", ["PersistentVolumeClaim", "StatefulSet", "Namespace", "PersistentVolume"]
)
def test_pg101_delete(kind):
    assert ids(run([obj(kind, "x")], [])) == [("PG101", f"{kind}/x", "CRITICAL")]


def test_pg101_crd_and_namespace_messages():
    crd = obj(
        "CustomResourceDefinition",
        "certificates.cert-manager.io",
        api="apiextensions.k8s.io/v1",
        spec={"names": {"kind": "Certificate"}},
    )
    (f,) = run([crd], [])
    assert "every Certificate object" in f.message
    (f,) = run([obj("Namespace", "prod")], [])
    assert "everything in it" in f.message


def test_pg101_pvc_recreate_is_critical_not_pg102():
    before = obj("PersistentVolumeClaim", "d", spec={"storageClassName": "gp2"})
    after = obj("PersistentVolumeClaim", "d", spec={"storageClassName": "gp3"})
    found = ids(run([before], [after]))
    assert ("PG101", "PersistentVolumeClaim/d", "CRITICAL") in found
    assert not any(rule == "PG102" for rule, _, _ in found)


def test_pg101_custom_stateful_kinds():
    cluster = obj("Cluster", "pg", api="postgresql.cnpg.io/v1")
    assert run([cluster], []) == []
    cfg = config_from_dict({"stateful_kinds": ["Cluster"]})
    assert ids(run([cluster], [], cfg)) == [("PG101", "Cluster/pg", "CRITICAL")]


# ---------------------------------------------------------------- PG102


def test_pg102_role_binding_roleref():
    before = obj("RoleBinding", "rb", roleRef={"kind": "Role", "name": "a"})
    after = obj("RoleBinding", "rb", roleRef={"kind": "Role", "name": "b"})
    found = run([before], [after])
    assert ("PG102", "RoleBinding/rb", "HIGH") in ids(found)


# ---------------------------------------------------------------- PG103


def svc(stype="ClusterIP", **meta_and_spec):
    annotations = meta_and_spec.pop("annotations", None)
    body = {"spec": {"type": stype, **meta_and_spec}}
    return (
        obj("Service", "web", annotations=annotations, **body)
        if annotations
        else obj("Service", "web", **body)
    )


def test_pg103_load_balancer_and_nodeport():
    assert ids(run([svc()], [svc("LoadBalancer")])) == [("PG103", "Service/web", "HIGH")]
    assert ids(run([svc()], [svc("NodePort")])) == [("PG103", "Service/web", "MEDIUM")]
    assert ids(run([], [svc("LoadBalancer")])) == [("PG103", "Service/web", "HIGH")]


@pytest.mark.parametrize(
    "extra",
    [
        {"annotations": {"service.beta.kubernetes.io/aws-load-balancer-scheme": "internal"}},
        {"annotations": {"networking.gke.io/load-balancer-type": "Internal"}},
        {"annotations": {"service.beta.kubernetes.io/azure-load-balancer-internal": "true"}},
        {"loadBalancerSourceRanges": ["10.0.0.0/8"]},
    ],
)
def test_pg103_internal_lb_is_fine(extra):
    assert run([svc()], [svc("LoadBalancer", **extra)]) == []


def test_pg103_existing_lb_not_reflagged():
    assert run([svc("LoadBalancer")], [svc("LoadBalancer", ports=[{"port": 81}])]) == []


def test_pg103_ingress_hosts():
    def ing(*hosts):
        return obj(
            "Ingress", "i", api="networking.k8s.io/v1", spec={"rules": [{"host": h} for h in hosts]}
        )

    (f,) = run([ing("a.example.com")], [ing("a.example.com", "b.example.com")])
    assert f.details["hosts"] == ["b.example.com"]
    assert run([ing("a.example.com")], [ing("a.example.com")]) == []


# ---------------------------------------------------------------- PG104


def test_pg104_role_wildcards_and_bindings():
    role = obj(
        "ClusterRole", "r", rules=[{"apiGroups": [""], "resources": ["*"], "verbs": ["get"]}]
    )
    assert ids(run([], [role])) == [("PG104", "ClusterRole/r", "HIGH")]
    # Already there before: not reported again.
    assert run([role], [role | {"metadata": {"name": "r", "labels": {"x": "y"}}}]) == []

    anon = obj(
        "RoleBinding",
        "anon",
        namespace="n",
        roleRef={"kind": "Role", "name": "view"},
        subjects=[{"kind": "Group", "name": "system:anonymous"}],
    )
    (f,) = run([], [anon])
    assert "system:anonymous" in f.message


def test_pg104_narrow_role_is_fine():
    role = obj("Role", "r", rules=[{"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}])
    assert run([], [role]) == []


# ---------------------------------------------------------------- PG105


def test_pg105_scale_to_zero_and_pdb():
    assert ids(run([deploy(replicas=3)], [deploy(replicas=0)])) == [
        ("PG105", "Deployment/web", "MEDIUM")
    ]
    assert run([deploy(replicas=3)], [deploy(replicas=1)]) == []
    pdb = obj("PodDisruptionBudget", "p", api="policy/v1")
    assert ids(run([pdb], [])) == [("PG105", "PodDisruptionBudget/p", "MEDIUM")]


# ---------------------------------------------------------------- PG106


def test_pg106_privileges():
    risky = deploy(
        containers=[
            {"name": "a", "image": "x", "securityContext": {"privileged": True}},
            {
                "name": "b",
                "image": "x",
                "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
            },
        ],
    )
    risky["spec"]["template"]["spec"]["hostNetwork"] = True
    risky["spec"]["template"]["spec"]["volumes"] = [{"name": "h", "hostPath": {"path": "/var/run"}}]
    (f,) = run([deploy()], [risky])
    assert f.details["privileges"] == [
        "NET_ADMIN on b",
        "hostNetwork",
        "hostPath /var/run",
        "privileged container a",
    ]
    assert run([risky], [risky]) == []


def test_pg106_cronjob_pod_spec():
    cron = obj(
        "CronJob",
        "c",
        api="batch/v1",
        spec={"jobTemplate": {"spec": {"template": {"spec": {"hostPID": True, "containers": []}}}}},
    )
    assert ids(run([], [cron])) == [("PG106", "CronJob/c", "MEDIUM")]


# ---------------------------------------------------------------- shared rules


def test_terraform_rules_ignore_kubernetes():
    # A k8s Service named like a Terraform type must not trigger PG001-PG005.
    found = run([obj("aws_db_instance", "x")], [])
    assert found == []


def test_mass_delete_counts_kubernetes():
    before = [obj("ConfigMap", f"c{i}") for i in range(3)]
    found = run(before, [], Config(max_deletes=2))
    assert ("PG004", "(plan)", "HIGH") in ids(found)
