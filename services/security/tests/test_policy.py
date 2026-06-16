from __future__ import annotations

import pytest

from src.policy import PolicyConfig, load_policy


def test_default_policy_loads():
    policy = load_policy()
    assert policy.default == "deny"
    assert "Done" in policy.tools
    assert policy.tools["Done"].decision == "allow"
    assert "write_email" in policy.tools
    assert policy.tools["write_email"].decision == "hitl"
    assert policy.tools["write_email"].limits is not None
    assert policy.tools["write_email"].limits.max_per_run == 1
    assert policy.tools["write_email"].limits.max_per_day == 100
    assert policy.tools["write_email"].limits.max_content_chars == 20000


def test_bare_policy_config_defaults_to_deny():
    policy = PolicyConfig()
    assert policy.default == "deny"
    assert policy.tools == {}


def test_relative_policy_path_resolves_from_service_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    policy = load_policy("policy.yaml")

    assert policy.default == "deny"
    assert policy.tools["write_email"].decision == "hitl"


def test_missing_policy_file_raises():
    with pytest.raises(FileNotFoundError):
        load_policy("/nonexistent/path/policy.yaml")


def test_empty_policy_file_raises(tmp_path):
    empty = tmp_path / "policy.yaml"
    empty.write_text("")
    with pytest.raises(ValueError):
        load_policy(empty)
