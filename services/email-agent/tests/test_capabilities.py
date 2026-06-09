from __future__ import annotations

import pytest

from src.capabilities import load_capabilities
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


def test_unknown_capability_fails_loud():
    with pytest.raises(ValueError, match="Unknown capability"):
        load_capabilities({"bogus": True})


def test_committed_config_yields_email_tools():
    cfg = load_config()
    tools, _ = load_capabilities(cfg.capabilities)
    assert len(tools) > 0
    assert "write_email" in _names(tools)
