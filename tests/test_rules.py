import json

import pytest

from planguard.config import Config
from planguard.models import Risk
from planguard.parser import parse_changes
from planguard.rules import RULES, evaluate, overall_risk
from tests.conftest import make_change, make_plan


def ids(findings):
    return sorted((f.rule_id, f.address, f.risk.name) for f in findings)


def run(*changes, cfg=None):
    return evaluate(parse_changes(make_plan(*changes)), cfg or Config())[2]


# ---------------------------------------------------------------- fixtures end to end


def test_safe_plan_has_no_findings(changes_for):
    changes = changes_for("safe")
    analyzed, ignored, findings = evaluate(changes, Config())
    assert findings == []
    assert ignored == []
    assert overall_risk(analyzed, findings) is Risk.LOW


def test_rds_replace_is_critical(findings_for):
    findings = findings_for("rds_replace")
    assert ids(findings) == [
        ("PG001", "aws_db_instance.main", "CRITICAL"),
        ("PG007", "aws_db_instance.main", "LOW"),
    ]
    assert "storage_encrypted" in findings[0].message
    assert findings[0].details["forces_replacement"] == ["storage_encrypted"]


def test_s3_delete_is_critical(findings_for):
    findings = findings_for("s3_delete")
    assert ids(findings) == [("PG001", "aws_s3_bucket.invoices", "CRITICAL")]
    assert "DESTROYED" in findings[0].message


def test_iam_wildcard(findings_for):
    assert ids(findings_for("iam_wildcard")) == [
        ("PG002", "aws_iam_policy.ci_deploy", "HIGH"),
        ("PG002", "aws_iam_role_policy.reader", "MEDIUM"),
        ("PG002", "aws_iam_role_policy_attachment.admin", "HIGH"),
    ]


def test_sg_open_world(findings_for):
    findings = findings_for("sg_open_world")
    assert ids(findings) == [
        ("PG003", "aws_security_group.web", "HIGH"),
        ("PG003", "aws_vpc_security_group_ingress_rule.db", "HIGH"),
    ]
    web = next(f for f in findings if f.address == "aws_security_group.web")
    # The pre-existing 443 rule is not re-reported, only the new port 22.
    assert web.details["exposures"] == ["port 22/tcp to 0.0.0.0/0"]


def test_mixed_plan(findings_for):
    assert ids(findings_for("mixed")) == [
        ("PG001", "module.cache.aws_elasticache_replication_group.this", "CRITICAL"),
        ("PG001", "module.sandbox.aws_s3_bucket.scratch", "CRITICAL"),
        ("PG002", "aws_iam_role_policy.logs", "MEDIUM"),
        ("PG003", "aws_security_group_rule.ssh", "HIGH"),
        ("PG005", "aws_instance.web[0]", "MEDIUM"),
        ("PG006", "aws_lb.public", "MEDIUM"),
        ("PG007", "aws_instance.web[0]", "LOW"),
        ("PG007", "module.cache.aws_elasticache_replication_group.this", "LOW"),
    ]


def test_findings_sorted_by_risk(findings_for):
    risks = [f.risk for f in findings_for("mixed")]
    assert risks == sorted(risks, reverse=True)


# ---------------------------------------------------------------- config interaction


def test_ignore_glob(findings_for, changes_for):
    cfg = Config(ignore=("module.sandbox.*",))
    _, ignored, findings = evaluate(changes_for("mixed"), cfg)
    assert [c.address for c in ignored] == ["module.sandbox.aws_s3_bucket.scratch"]
    assert all(not f.address.startswith("module.sandbox") for f in findings)


def test_rule_off_and_override(findings_for):
    cfg = Config(rule_overrides={"PG007": None, "PG003": Risk.CRITICAL})
    findings = findings_for("mixed", cfg)
    assert not any(f.rule_id == "PG007" for f in findings)
    assert {f.risk for f in findings if f.rule_id == "PG003"} == {Risk.CRITICAL}


def test_extra_stateful_and_compute_types():
    change = make_change("null_resource.db", "null_resource", ["delete", "create"], {}, {})
    assert ids(run(change, cfg=Config(rule_overrides={"PG007": None}))) == []
    assert ids(run(change, cfg=Config(stateful_types=("null_*",))))[0][0] == "PG001"
    assert ("PG005", "null_resource.db", "MEDIUM") in ids(
        run(change, cfg=Config(compute_types=("null_resource",)))
    )


def test_mass_delete_threshold(changes_for):
    changes = changes_for("mixed")
    destructive = sum(c.action.destructive for c in changes)
    findings = evaluate(changes, Config(max_deletes=destructive - 1))[2]
    mass = [f for f in findings if f.rule_id == "PG004"]
    assert len(mass) == 1 and mass[0].risk is Risk.HIGH
    assert mass[0].details == {"count": destructive, "threshold": destructive - 1}
    assert not any(f.rule_id == "PG004" for f in evaluate(changes, Config())[2])


# ---------------------------------------------------------------- PG001 / PG005


@pytest.mark.parametrize(
    "rtype",
    [
        "aws_rds_cluster",
        "aws_dynamodb_table",
        "aws_efs_file_system",
        "aws_elasticache_cluster",
        "google_sql_database_instance",
        "azurerm_mssql_database",
        "azurerm_postgresql_flexible_server",
    ],
)
def test_stateful_types_delete(rtype):
    assert ids(run(make_change(f"{rtype}.x", rtype, ["delete"], {}, None))) == [
        ("PG001", f"{rtype}.x", "CRITICAL")
    ]


def test_stateful_create_and_update_are_fine():
    assert run(make_change("aws_db_instance.a", "aws_db_instance", ["create"], None, {})) == []
    assert run(make_change("aws_db_instance.a", "aws_db_instance", ["update"], {}, {})) == []


def test_replace_by_request_message():
    findings = run(
        make_change("aws_instance.a", "aws_instance", ["delete", "create"], {}, {}),
        cfg=Config(),
    )
    assert any("reason not reported" in f.message for f in findings)
    plan = make_plan(make_change("aws_instance.a", "aws_instance", ["create", "delete"], {}, {}))
    plan["resource_changes"][0]["action_reason"] = "replace_by_request"
    findings = evaluate(parse_changes(plan), Config())[2]
    assert any("-replace" in f.message for f in findings)


# ---------------------------------------------------------------- PG002


def _policy(*statements):
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


def test_iam_unchanged_policy_not_reflagged():
    doc = _policy({"Effect": "Allow", "Action": "*", "Resource": "*"})
    change = make_change(
        "aws_iam_policy.p",
        "aws_iam_policy",
        ["update"],
        {"policy": doc, "tags": {}},
        {"policy": doc, "tags": {"a": "b"}},
    )
    assert run(change) == []


def test_iam_service_wildcard_and_deny_ignored():
    allow = _policy({"Effect": "Allow", "Action": ["s3:*"], "Resource": "arn:aws:s3:::b/*"})
    deny = _policy({"Effect": "Deny", "Action": "*", "Resource": "*"})
    assert ids(
        run(make_change("aws_iam_policy.a", "aws_iam_policy", ["create"], None, {"policy": allow}))
    ) == [("PG002", "aws_iam_policy.a", "HIGH")]
    assert (
        run(make_change("aws_iam_policy.d", "aws_iam_policy", ["create"], None, {"policy": deny}))
        == []
    )


def test_iam_not_action_and_inline_role_policy():
    doc = _policy({"Effect": "Allow", "NotAction": "iam:*", "Resource": "*"})
    change = make_change(
        "aws_iam_role.r",
        "aws_iam_role",
        ["create"],
        None,
        {"inline_policy": [{"name": "x", "policy": doc}]},
    )
    findings = run(change)
    assert ids(findings) == [("PG002", "aws_iam_role.r", "HIGH")]
    assert "NotAction" in findings[0].message


def test_iam_unknown_policy_is_skipped():
    change = make_change("aws_iam_policy.a", "aws_iam_policy", ["create"], None, {"policy": None})
    assert run(change) == []


# ---------------------------------------------------------------- PG003


def _sgr(from_port, to_port, protocol="tcp", cidr="0.0.0.0/0", rtype="ingress"):
    return make_change(
        "aws_security_group_rule.r",
        "aws_security_group_rule",
        ["create"],
        None,
        {
            "type": rtype,
            "from_port": from_port,
            "to_port": to_port,
            "protocol": protocol,
            "cidr_blocks": [cidr],
        },
    )


@pytest.mark.parametrize(
    ("args", "flagged"),
    [
        ((443, 443), False),
        ((80, 80), False),
        ((80, 443), True),
        ((22, 22), True),
        ((0, 0, "-1"), True),
        ((-1, -1, "icmp"), False),
        ((22, 22, "tcp", "10.0.0.0/8"), False),
        ((22, 22, "tcp", "0.0.0.0/0", "egress"), False),
        ((3389, 3389, "tcp", "::/0"), True),
    ],
)
def test_sg_rule_ports(args, flagged):
    assert bool(run(_sgr(*args))) is flagged


def test_sg_public_ports_configurable():
    assert run(_sgr(8080, 8080), cfg=Config(public_ports=frozenset({8080}))) == []


def test_gcp_firewall_and_azure_nsg():
    gcp = make_change(
        "google_compute_firewall.ssh",
        "google_compute_firewall",
        ["create"],
        None,
        {
            "direction": "INGRESS",
            "source_ranges": ["0.0.0.0/0"],
            "allow": [{"protocol": "tcp", "ports": ["22", "443"]}],
        },
    )
    nsg = make_change(
        "azurerm_network_security_rule.rdp",
        "azurerm_network_security_rule",
        ["create"],
        None,
        {
            "direction": "Inbound",
            "access": "Allow",
            "protocol": "Tcp",
            "source_address_prefix": "*",
            "destination_port_range": "3389",
        },
    )
    findings = run(gcp, nsg)
    assert ids(findings) == [
        ("PG003", "azurerm_network_security_rule.rdp", "HIGH"),
        ("PG003", "google_compute_firewall.ssh", "HIGH"),
    ]
    gcp_finding = next(f for f in findings if f.address.startswith("google"))
    assert gcp_finding.details["exposures"] == ["port 22/tcp to 0.0.0.0/0"]


# ---------------------------------------------------------------- PG006


def test_protected_tag_destroy_is_high():
    change = make_change(
        "aws_lb.x", "aws_lb", ["delete"], {"tags": {"planguard:protected": "TRUE"}}
    )
    assert ids(run(change)) == [("PG006", "aws_lb.x", "HIGH")]


def test_protected_tag_custom_wildcard_value():
    cfg = Config(protected_tags={"criticality": "*"})
    change = make_change(
        "aws_lb.x",
        "aws_lb",
        ["update"],
        {"tags": {"criticality": "tier1"}},
        {"tags": {"criticality": "tier1"}},
    )
    assert ids(run(change, cfg=cfg)) == [("PG006", "aws_lb.x", "MEDIUM")]


# ---------------------------------------------------------------- misc


def test_rule_ids_unique_and_documented():
    assert len({r.id for r in RULES}) == len(RULES)
    assert all(r.description for r in RULES)


def test_overall_risk_none_for_noop_plan():
    changes = parse_changes(make_plan(make_change("aws_sns_topic.a", "aws_sns_topic", ["no-op"])))
    assert overall_risk(changes, []) is Risk.NONE


def test_real_terraform_output(changes_for):
    """Plan produced by real `terraform show -json` (Terraform 1.11) on examples/."""
    cfg = Config(stateful_types=("local_file",), compute_types=("null_resource",))
    analyzed, _, findings = evaluate(changes_for("real_terraform_local"), cfg)
    assert ids(findings) == [
        ("PG001", "local_file.database", "CRITICAL"),
        ("PG005", "null_resource.app_server", "MEDIUM"),
        ("PG007", "local_file.database", "LOW"),
        ("PG007", "null_resource.app_server", "LOW"),
    ]
    db = next(c for c in analyzed if c.address == "local_file.database")
    assert db.replace_attributes == ["content"]
