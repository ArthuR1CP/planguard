import pytest

from planguard.config import Config, ConfigError, config_from_dict, load_config
from planguard.models import Risk


def test_defaults():
    cfg = config_from_dict(None)
    assert cfg == Config()
    assert cfg.fail_on is Risk.HIGH
    assert cfg.max_deletes == 5


def test_full_config(tmp_path):
    path = tmp_path / ".planguard.yml"
    path.write_text(
        """
fail_on: critical
thresholds:
  max_deletes: 2
stateful_types: [aws_opensearch_domain]
compute_types: [aws_batch_*]
ignore:
  - module.sandbox.*
protected_tags:
  criticality: High
public_ports: [80, 443, 8443]
rules:
  PG005: off
  pg002: critical
"""
    )
    cfg = load_config(path)
    assert cfg.fail_on is Risk.CRITICAL
    assert cfg.max_deletes == 2
    assert cfg.stateful_types == ("aws_opensearch_domain",)
    assert cfg.compute_types == ("aws_batch_*",)
    assert cfg.ignore == ("module.sandbox.*",)
    assert cfg.protected_tags == {"criticality": "high"}
    assert cfg.public_ports == frozenset({80, 443, 8443})
    assert cfg.rule_overrides == {"PG005": None, "PG002": Risk.CRITICAL}


def test_fail_on_none():
    assert config_from_dict({"fail_on": "none"}).fail_on is None


def test_autodiscovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_config() == Config()
    (tmp_path / ".planguard.yml").write_text("fail_on: medium\n")
    assert load_config().fail_on is Risk.MEDIUM


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"fail_on": "scary"}, "invalid risk level"),
        ({"failon": "high"}, "unknown config key"),
        ({"thresholds": {"max_delete": 1}}, "unknown threshold"),
        ({"thresholds": {"max_deletes": -1}}, "non-negative"),
        ({"ignore": "x"}, None),
        ({"ignore": [1]}, "list of strings"),
        ({"rules": {"PG999": "off"}}, "unknown rule id"),
        ({"rules": {"PG001": "loud"}}, "invalid risk level"),
        ({"public_ports": ["80"]}, "list of integers"),
        ({"protected_tags": ["x"]}, "mapping"),
        (["not", "a", "mapping"], "mapping"),
    ],
)
def test_invalid(data, message):
    if message is None:  # a single string is accepted as a one-item list
        assert config_from_dict(data).ignore == ("x",)
        return
    with pytest.raises(ConfigError, match=message):
        config_from_dict(data)


def test_invalid_yaml(tmp_path):
    path = tmp_path / "c.yml"
    path.write_text("fail_on: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")
