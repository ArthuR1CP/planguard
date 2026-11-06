import json

from planguard import report as report_mod
from planguard.config import Config
from planguard.models import Risk
from planguard.parser import parse_changes
from planguard.report import MARKER, build_report, headline, render_json, render_markdown
from tests.conftest import make_change, make_plan


def build(changes_for, name, **cfg):
    return build_report(changes_for(name), Config(**cfg))


def test_headline_rds(changes_for):
    assert headline(build(changes_for, "rds_replace")) == "🚨 1 database will be REPLACED"


def test_headline_s3(changes_for):
    assert headline(build(changes_for, "s3_delete")) == "🚨 1 bucket will be DESTROYED"


def test_headline_mixed(changes_for):
    assert headline(build(changes_for, "mixed")) == (
        "🚨 1 cache cluster will be REPLACED; 1 bucket will be DESTROYED"
    )


def test_headline_high_and_safe(changes_for):
    assert headline(build(changes_for, "sg_open_world")) == (
        "⚠️ 2 security rules open to the internet"
    )
    assert headline(build(changes_for, "iam_wildcard")) == (
        "⚠️ 2 IAM grants with wildcard permissions"
    )
    assert headline(build(changes_for, "safe")) == (
        "✅ No risky changes (2 to add, 1 to change, 0 to replace, 0 to destroy)."
    )


def test_headline_no_changes():
    changes = parse_changes(make_plan(make_change("aws_sns_topic.a", "aws_sns_topic", ["no-op"])))
    assert headline(build_report(changes, Config())) == "✅ No changes."


def test_markdown_structure(changes_for):
    md = render_markdown(build(changes_for, "mixed", ignore=("module.sandbox.*",)))
    assert md.startswith(MARKER)
    assert "## 🛡️ planguard: 🔴 CRITICAL" in md
    assert "### 🚨 1 cache cluster will be REPLACED" in md
    assert "| Will be REPLACED (forces replacement: `at_rest_encryption_enabled`) |" in md
    assert "| 🔴 **CRITICAL** | `module.cache.aws_elasticache_replication_group.this`" in md
    assert "<details><summary>All changes (" in md
    assert "Ignored by config (1)" in md
    assert "🙈 1 ignored" in md
    assert "❌ failing" in md
    # Critical rows come before medium rows.
    assert md.index("**CRITICAL**") < md.index("**MEDIUM**")


def test_markdown_safe_passes(changes_for):
    md = render_markdown(build(changes_for, "safe"))
    assert "✅ passing" in md
    assert "| Risk | Resource |" not in md


def test_markdown_escapes_pipes():
    change = make_change('aws_s3_bucket.b["a|b"]', "aws_s3_bucket", ["delete"], {}, None)
    md = render_markdown(build_report(parse_changes(make_plan(change)), Config()))
    assert 'aws_s3_bucket.b["a\\|b"]' in md


def test_markdown_truncates_long_change_list(monkeypatch):
    monkeypatch.setattr(report_mod, "MAX_CHANGE_ROWS", 3)
    changes = [make_change(f"aws_sns_topic.t{i}", "aws_sns_topic", ["create"]) for i in range(5)]
    md = render_markdown(build_report(parse_changes(make_plan(*changes)), Config()))
    assert "_2 more not shown_" in md


def test_json_output(changes_for):
    data = json.loads(render_json(build(changes_for, "rds_replace", fail_on=Risk.CRITICAL)))
    assert data["risk"] == "CRITICAL"
    assert data["failed"] is True
    assert data["fail_on"] == "CRITICAL"
    assert data["summary"]["replace"] == 1
    assert data["findings"][0] == {
        "rule_id": "PG001",
        "rule": "stateful-destroy",
        "risk": "CRITICAL",
        "address": "aws_db_instance.main",
        "message": (
            "`aws_db_instance.main` will be REPLACED (forces replacement: `storage_encrypted`)"
        ),
        "details": {"action": "replace", "forces_replacement": ["storage_encrypted"]},
    }
    assert {c["address"] for c in data["changes"]} == {
        "aws_db_instance.main",
        "aws_db_parameter_group.main",
    }


def test_fail_on_none_never_fails(changes_for):
    assert build(changes_for, "rds_replace", fail_on=None).failed is False


def test_headline_kubernetes():
    from planguard.k8s import diff_manifests, load_manifests

    k8s = "tests/fixtures/k8s/"
    changes = diff_manifests(
        load_manifests(k8s + "helm_shop_v1.yaml"), load_manifests(k8s + "helm_shop_v2.yaml")
    )
    report = build_report(changes, Config(rule_overrides={"PG101": None, "PG006": None}))
    assert headline(report) == (
        "⚠️ 1 RBAC grant with wildcards or cluster-admin; 2 new public endpoints; "
        "1 object must be recreated (immutable fields)"
    )
