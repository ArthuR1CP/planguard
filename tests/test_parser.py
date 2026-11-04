import io

import pytest

from planguard.models import Action
from planguard.parser import PlanError, classify, load_plan, parse_changes
from tests.conftest import make_change, make_plan


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        (["no-op"], Action.NOOP),
        (["create"], Action.CREATE),
        (["read"], Action.READ),
        (["update"], Action.UPDATE),
        (["delete"], Action.DELETE),
        (["delete", "create"], Action.REPLACE),
        (["create", "delete"], Action.REPLACE),
        (["forget"], Action.FORGET),
    ],
)
def test_classify(actions, expected):
    assert classify(actions) is expected


def test_classify_unknown_raises():
    with pytest.raises(PlanError, match="unsupported"):
        classify(["explode"])


def test_parse_rds_replace(changes_for):
    changes = changes_for("rds_replace")
    db = next(c for c in changes if c.address == "aws_db_instance.main")
    assert db.action is Action.REPLACE
    assert db.type == "aws_db_instance"
    assert db.replace_attributes == ["storage_encrypted"]
    assert db.action_reason == "replace_because_cannot_update"
    assert db.tags == {"team": "orders", "env": "prod"}


def test_data_sources_are_skipped(changes_for):
    addresses = [c.address for c in changes_for("safe")]
    assert "data.aws_caller_identity.current" not in addresses
    assert len(addresses) == 4


def test_delete_keeps_before_tags():
    plan = make_plan(
        make_change("aws_lb.x", "aws_lb", ["delete"], before={"tags": {"owner": "net"}})
    )
    (change,) = parse_changes(plan)
    assert change.tags == {"owner": "net"}
    assert change.after is None


def test_replace_attributes_renders_indexes():
    plan = make_plan(
        make_change(
            "aws_instance.a",
            "aws_instance",
            ["delete", "create"],
            {},
            {},
            replace_paths=[["ebs_block_device", 0, "volume_size"], ["ami"], ["ami"]],
        )
    )
    (change,) = parse_changes(plan)
    assert change.replace_attributes == ["ebs_block_device[0].volume_size", "ami"]


def test_empty_plan_without_resource_changes():
    assert parse_changes({"format_version": "1.2", "planned_values": {}}) == []


def test_load_from_stream():
    assert load_plan(io.StringIO('{"format_version": "1.2"}')) == {"format_version": "1.2"}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "must be an object"),
        ('{"foo": 1}', "does not look like a Terraform JSON plan"),
        ('{"format_version": "1.0", "values": {}}', "state, not a plan"),
    ],
)
def test_load_invalid(tmp_path, content, message):
    path = tmp_path / "plan.json"
    path.write_text(content)
    with pytest.raises(PlanError, match=message):
        load_plan(path)


def test_load_binary_plan(tmp_path):
    path = tmp_path / "tfplan"
    path.write_bytes(b"PK\x03\x04\xff\xfe\x00binary")
    with pytest.raises(PlanError, match="terraform show -json"):
        load_plan(path)


def test_load_missing_file(tmp_path):
    with pytest.raises(PlanError, match="not found"):
        load_plan(tmp_path / "nope.json")
