from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from src.automation import RulesConfig, load_rules, snooze_label


def _write_yaml(tmp_path, body: str):
    path = tmp_path / "rules.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_missing_rules_file_defaults_off(tmp_path):
    cfg = load_rules(tmp_path / "missing.yaml")

    assert cfg == RulesConfig()
    assert cfg.enabled is False


def test_load_rules_parses_rule_model(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        enabled: true
        rules:
          - name: newsletters
            when:
              sender_domain: ["example.com"]
              subject_contains: ["digest"]
              labels: ["INBOX"]
            then:
              labels: ["Auto/Newsletters"]
              archive: true
        digest:
          enabled: true
          hour: 9
        """,
    )

    cfg = load_rules(path)

    assert cfg.enabled is True
    assert cfg.rules[0].name == "newsletters"
    assert cfg.rules[0].when.sender_domain == ["example.com"]
    assert cfg.rules[0].then.labels == ["Auto/Newsletters"]
    assert cfg.rules[0].then.archive is True
    assert cfg.digest.enabled is True
    assert cfg.digest.hour == 9


def test_blank_response_rule_fails_validation(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        enabled: true
        rules:
          - name: bad response
            then:
              respond: "  "
        """,
    )

    with pytest.raises(ValidationError):
        load_rules(path)


def test_snooze_label_uses_iso_date():
    assert snooze_label("Snoozed", __import__("datetime").date(2026, 6, 16)) == "Snoozed/2026-06-16"
