from __future__ import annotations

import json
import textwrap
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.automation import (
    DigestConfig,
    FollowUpConfig,
    LearningConfig,
    SnoozeConfig,
    RulesConfig,
    build_follow_up_plan,
    build_rule_plan,
    due_snooze_labels,
    follow_up_query,
    load_escalation_state,
    load_rules,
    mark_run_escalated,
    maybe_emit_daily_digest,
    record_digest_item,
    snooze_label,
    suggest_rule_from_correction,
    workflow_sla_snapshot,
)
from src.categories import CategoriesConfig, Category, CategoryInstructions


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


def test_build_rule_plan_turns_snooze_days_into_label_and_archive(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        enabled: true
        snooze:
          enabled: true
          label_prefix: Snoozed
        rules:
          - name: snooze newsletters
            when:
              subject_contains: ["later"]
            then:
              snooze_days: 2
        """,
    )

    plan = build_rule_plan(
        {"author": "Alice <alice@example.com>", "subject": "Read later"},
        load_rules(path),
        today=date(2026, 6, 16),
    )

    assert plan is not None
    assert plan["tool_calls"] == [
        {
            "name": "apply_label",
            "args": {"label": "Snoozed/2026-06-18"},
            "id": "rule_0_snooze_label",
            "type": "tool_call",
        },
        {"name": "archive_email", "args": {}, "id": "rule_0_snooze_archive", "type": "tool_call"},
    ]


def test_due_snooze_labels_returns_due_dates_only():
    cfg = RulesConfig(snooze=SnoozeConfig(enabled=True, label_prefix="Snoozed"))
    labels = [
        {"id": "l1", "name": "Snoozed/2026-06-15"},
        {"id": "l2", "name": "Snoozed/2026-06-16"},
        {"id": "l3", "name": "Snoozed/2026-06-17"},
        {"id": "l4", "name": "Other/2026-06-15"},
        {"id": "l5", "name": "Snoozed/not-a-date"},
    ]

    due = due_snooze_labels(labels, cfg, today=date(2026, 6, 16))

    assert [label["id"] for label in due] == ["l1", "l2"]


def test_follow_up_query_uses_label_and_age():
    cfg = RulesConfig(follow_ups=FollowUpConfig(enabled=True, label="Awaiting Reply", after_days=5))

    assert follow_up_query(cfg) == 'label:"Awaiting Reply" older_than:5d'


def test_build_follow_up_plan_creates_human_gated_nudge():
    cfg = RulesConfig(
        follow_ups=FollowUpConfig(
            enabled=True,
            nudge="Checking in on this.",
        )
    )
    email = {
        "to": "Bob <bob@example.com>",
        "subject": "Project update",
    }

    plan = build_follow_up_plan(email, cfg)

    assert plan is not None
    assert plan["matched_rules"] == ["follow_up"]
    assert plan["tool_calls"] == [
        {
            "name": "write_email",
            "args": {
                "to": "bob@example.com",
                "subject": "Re: Project update",
                "content": "Checking in on this.",
            },
            "id": "follow_up_nudge",
            "type": "tool_call",
        }
    ]


def test_rule_suggestions_are_disabled_by_default(tmp_path):
    path = tmp_path / "suggestions.jsonl"

    assert suggest_rule_from_correction(
        RulesConfig(),
        {"author": "Alice <alice@example.com>", "subject": "Question"},
        "ignored_draft",
        state_path=path,
    ) is False
    assert not path.exists()


def test_rule_suggestion_appends_disabled_candidate(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    cfg = RulesConfig(
        learning=LearningConfig(enabled=True, suggestions_path=str(path))
    )

    assert suggest_rule_from_correction(
        cfg,
        {
            "author": "Alice <alice@example.com>",
            "subject": "Project status question",
            "email_id": "m1",
            "gmail_thread_id": "t1",
        },
        "edited_draft",
        {"feedback": "shorter"},
        now=datetime(2026, 6, 16, 12, 30),
    ) is True

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["created_at"] == "2026-06-16T12:30:00"
    assert row["correction_type"] == "edited_draft"
    assert row["source"]["email_id"] == "m1"
    assert row["details"] == {"feedback": "shorter"}
    assert row["suggested_rule"]["enabled"] is False
    assert row["suggested_rule"]["when"] == {
        "sender_domain": ["example.com"],
        "subject_contains": ["project", "status", "question"],
    }
    assert row["suggested_rule"]["then"] == {"notify": True}


def test_forward_edit_suggestion_appends_workflow_candidate(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    cfg = RulesConfig(learning=LearningConfig(enabled=True, suggestions_path=str(path)))

    assert suggest_rule_from_correction(
        cfg,
        {
            "author": "Alice <alice@example.com>",
            "subject": "Payroll question",
            "category": "payroll",
            "category_display_name": "Payroll",
            "priority": "urgent",
            "workflow_owner": "HR",
            "workflow_approver": "hr",
            "workflow_instructions": {"sla": "12h", "escalation": "owner"},
        },
        "edited_draft",
        {"tool": "forward_email", "edited": {"to": ["hr@example.com", "finance"]}},
        now=datetime(2026, 6, 16, 12, 45),
    ) is True

    row = json.loads(path.read_text().splitlines()[0])
    assert row["suggested_workflow"]["name"] == "payroll"
    assert row["suggested_workflow"]["route_to"] == ["hr@example.com", "finance"]
    assert row["suggested_workflow"]["instructions"]["sla"] == "12h"
    assert row["source"]["category"] == "payroll"


def test_workflow_sla_snapshot_marks_overdue_and_uses_escalation_state(tmp_path):
    categories = CategoriesConfig(
        enabled=True,
        categories=[
            Category(
                name="payroll",
                display_name="Payroll",
                instructions=CategoryInstructions(sla="2h", escalation="owner"),
            )
        ],
    )
    state_path = tmp_path / "sla_state.json"
    mark_run_escalated("run-1", "owner@example.com", path=state_path, now=datetime(2026, 6, 16, 10, 30))

    snapshot = workflow_sla_snapshot(
        {"run_id": "run-1", "category": "payroll", "created_at": "2026-06-16T08:00:00+00:00"},
        categories,
        escalation_state=load_escalation_state(state_path),
        now=datetime(2026, 6, 16, 12, 30),
    )

    assert snapshot["sla_label"] == "2h"
    assert snapshot["overdue"] is True
    assert snapshot["overdue_by_seconds"] == 9000
    assert snapshot["escalated_at"] == "2026-06-16T10:30:00+00:00"
    assert snapshot["escalation_target"] == "owner@example.com"


# --- Starter rules and working hours (plan 4.1) ---

def _rules_with_after_hours(**hours):
    from src.automation import (
        AutomationRule,
        RulesConfig,
        RuleThen,
        RuleWhen,
        WorkingHoursConfig,
    )

    return RulesConfig(
        enabled=True,
        working_hours=WorkingHoursConfig(**hours) if hours else WorkingHoursConfig(),
        rules=[
            AutomationRule(
                name="hors heures",
                when=RuleWhen(outside_working_hours=True),
                then=RuleThen(snooze_days=1),
            )
        ],
    )


def test_outside_working_hours_matches_evening_mail():
    from src.automation import build_rule_plan

    config = _rules_with_after_hours()
    # Wednesday 22:10 local — outside the 09:00-18:00 window.
    plan = build_rule_plan(
        {"author": "a@corp.example", "subject": "s", "date": "Wed, 29 Jul 2026 22:10:00 +0200"},
        config,
    )
    assert plan is not None
    assert plan["matched_rules"] == ["hors heures"]


def test_inside_working_hours_does_not_match():
    from src.automation import build_rule_plan

    plan = build_rule_plan(
        {"author": "a@corp.example", "subject": "s", "date": "Wed, 29 Jul 2026 10:30:00 +0200"},
        _rules_with_after_hours(),
    )
    assert plan is None


def test_weekend_counts_as_outside_working_hours():
    from src.automation import build_rule_plan

    # Saturday mid-morning: inside the hour range but not a working day.
    plan = build_rule_plan(
        {"author": "a@corp.example", "subject": "s", "date": "Sat, 1 Aug 2026 10:30:00 +0200"},
        _rules_with_after_hours(),
    )
    assert plan is not None


def test_unparseable_date_never_fires_the_rule():
    from src.automation import build_rule_plan

    for value in ("", None, "not a date"):
        assert build_rule_plan(
            {"author": "a@corp.example", "subject": "s", "date": value},
            _rules_with_after_hours(),
        ) is None


def test_starter_catalogue_flags_what_is_already_applied():
    from src.automation import RulesConfig, apply_starter_rules, starter_rule_catalogue

    config = RulesConfig()
    before = {entry["id"]: entry["applied"] for entry in starter_rule_catalogue(config)}
    assert not any(before.values())

    config, added = apply_starter_rules(config, ["newsletters", "follow_ups"])
    after = {entry["id"]: entry["applied"] for entry in starter_rule_catalogue(config)}
    assert added == ["newsletters", "follow_ups"]
    assert after["newsletters"] is True
    assert after["follow_ups"] is True
    assert after["gmail_categories"] is False
    # A rule subsystem that is off would silently ignore the rule it just added.
    assert config.enabled is True
    assert config.follow_ups.enabled is True


def test_applying_a_starter_rule_twice_does_not_duplicate_it():
    from src.automation import RulesConfig, apply_starter_rules

    config, _ = apply_starter_rules(RulesConfig(), ["newsletters"])
    config, added = apply_starter_rules(config, ["newsletters"])
    assert added == []
    assert [rule.name for rule in config.rules].count("étiqueter et archiver les newsletters") == 1


def test_unknown_starter_rule_is_rejected():
    from src.automation import RulesConfig, apply_starter_rules

    with pytest.raises(ValueError, match="unknown starter rule"):
        apply_starter_rules(RulesConfig(), ["nope"])
