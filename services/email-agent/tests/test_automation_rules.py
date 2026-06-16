from __future__ import annotations

import textwrap
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.automation import (
    DigestConfig,
    RulesConfig,
    build_rule_plan,
    load_rules,
    maybe_emit_daily_digest,
    record_digest_item,
    snooze_label,
)


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
    assert snooze_label("Snoozed", date(2026, 6, 16)) == "Snoozed/2026-06-16"


def test_build_rule_plan_matches_sender_subject_and_labels(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        enabled: true
        rules:
          - name: newsletters
            when:
              sender_domain: ["promo.io"]
              subject_contains: ["digest"]
              labels: ["INBOX"]
            then:
              labels: ["Auto/Newsletters"]
              archive: true
        """,
    )
    email = {
        "author": "Promotions <newsletter@promo.io>",
        "subject": "Weekly digest",
        "labels": ["INBOX", "UNREAD"],
    }

    plan = build_rule_plan(email, load_rules(path))

    assert plan is not None
    assert plan["matched_rules"] == ["newsletters"]
    assert plan["tool_calls"] == [
        {
            "name": "apply_label",
            "args": {"label": "Auto/Newsletters"},
            "id": "rule_0_label_0",
            "type": "tool_call",
        },
        {"name": "archive_email", "args": {}, "id": "rule_0_archive", "type": "tool_call"},
    ]


def test_build_rule_plan_can_prepare_human_gated_response(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        enabled: true
        rules:
          - name: auto ack
            when:
              sender_contains: ["Alice"]
            then:
              respond: Thanks, I received this and will follow up.
        """,
    )
    email = {"author": "Alice <alice@example.com>", "subject": "Question"}

    plan = build_rule_plan(email, load_rules(path))

    assert plan is not None
    assert plan["tool_calls"][0]["name"] == "write_email"
    assert plan["tool_calls"][0]["args"] == {
        "to": "alice@example.com",
        "subject": "Re: Question",
        "content": "Thanks, I received this and will follow up.",
    }


def test_digest_records_configured_status_and_emits_once(tmp_path):
    state_path = tmp_path / "digest_state.json"
    cfg = RulesConfig(digest=DigestConfig(enabled=True, hour=9, statuses=["notify"]))
    email = {"subject": "Build finished", "author": "CI <ci@example.com>"}

    assert record_digest_item(
        cfg,
        "notify",
        email,
        "run-1",
        state_path=state_path,
        now=datetime(2026, 6, 16, 8, 0),
    ) is True
    assert record_digest_item(cfg, "completed", email, "run-2", state_path=state_path) is False

    captured: list[str] = []
    digest = maybe_emit_daily_digest(
        cfg,
        state_path=state_path,
        now=datetime(2026, 6, 16, 9, 0),
        emit=captured.append,
    )

    assert digest is not None
    assert captured == [digest]
    assert "Daily email digest for 2026-06-16" in digest
    assert "[notify] Build finished - CI <ci@example.com> (run run-1)" in digest
    assert maybe_emit_daily_digest(
        cfg,
        state_path=state_path,
        now=datetime(2026, 6, 16, 10, 0),
        emit=captured.append,
    ) is None


def test_digest_waits_until_configured_hour(tmp_path):
    cfg = RulesConfig(digest=DigestConfig(enabled=True, hour=18))
    record_digest_item(
        cfg,
        "notify",
        {"subject": "FYI", "author": "Alice"},
        "run-1",
        state_path=tmp_path / "digest_state.json",
    )

    assert maybe_emit_daily_digest(
        cfg,
        state_path=tmp_path / "digest_state.json",
        now=datetime(2026, 6, 16, 17, 59),
    ) is None
