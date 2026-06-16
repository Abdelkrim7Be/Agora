from __future__ import annotations

import pytest

from src.capabilities import approval_required, load_capabilities
from src.config import load_config


def _names(tools) -> list[str]:
    return [t.name for t in tools]


def test_email_only_loads_correct_tools():
    tools, prompt = load_capabilities({"email": True})
    assert "write_email" in _names(tools)
    assert "Done" in _names(tools)
    assert "write_email" in prompt


def test_calendar_enabled_adds_meeting_tools():
    tools, prompt = load_capabilities({"email": True, "calendar": True})
    names = _names(tools)
    assert "write_email" in names
    assert "Done" in names
    assert "schedule_meeting" in names
    assert "check_calendar_availability" in names
    assert "schedule_meeting" in prompt


def test_calendar_disabled_excludes_meeting_tools():
    tools, _ = load_capabilities({"email": True, "calendar": False})
    names = _names(tools)
    assert "schedule_meeting" not in names
    assert "check_calendar_availability" not in names


def test_inbox_enabled_adds_organization_tools():
    tools, prompt = load_capabilities({"email": True, "inbox": True})
    names = _names(tools)
    assert "write_email" in names
    assert "apply_label" in names
    assert "remove_label" in names
    assert "archive_email" in names
    assert "mark_read" in names
    assert "mark_unread" in names
    assert "trash_email" in names
    assert "email_id" not in prompt


def test_inbox_disabled_excludes_organization_tools():
    tools, _ = load_capabilities({"email": True, "inbox": False})
    names = _names(tools)
    assert "apply_label" not in names
    assert "archive_email" not in names
    assert "trash_email" not in names


def test_inbox_trash_requires_approval():
    assert "trash_email" in approval_required({"inbox": True})
    assert "archive_email" not in approval_required({"inbox": True})


def test_drafts_enabled_adds_create_draft_tool():
    tools, prompt = load_capabilities({"email": True, "drafts": True})
    names = _names(tools)
    assert "write_email" in names
    assert "create_draft" in names
    assert "thread_id" not in prompt


def test_drafts_disabled_excludes_create_draft_tool():
    tools, _ = load_capabilities({"email": True, "drafts": False})
    assert "create_draft" not in _names(tools)


def test_create_draft_does_not_require_approval():
    assert "create_draft" not in approval_required({"drafts": True})


def test_unknown_capability_fails_loud():
    with pytest.raises(ValueError, match="Unknown capability"):
        load_capabilities({"bogus": True})


def test_committed_config_yields_email_tools():
    cfg = load_config()
    tools, _ = load_capabilities(cfg.capabilities)
    assert len(tools) > 0
    assert "write_email" in _names(tools)
