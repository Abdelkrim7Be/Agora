from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from src.config import AgentConfig, load_config
from src.prompts import agent_system_prompt, triage_system_prompt


def _write_yaml(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_load_config_parses_yaml(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        agent:
          background: I'm a test persona.
          triage_instructions: respond to direct questions.
          response_preferences: be concise.
        """,
    )
    cfg = load_config(path)
    assert isinstance(cfg, AgentConfig)
    assert cfg.agent.background == "I'm a test persona."
    assert cfg.agent.triage_instructions == "respond to direct questions."


def test_load_config_defaults_auto_organize_off(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        agent:
          background: I'm a test persona.
          triage_instructions: respond to direct questions.
          response_preferences: be concise.
        """,
    )

    cfg = load_config(path)

    assert cfg.auto_organize.enabled is False
    assert cfg.auto_organize.ignored_label == "Auto/Ignored"


def test_load_config_parses_auto_organize(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        agent:
          background: I'm a test persona.
          triage_instructions: respond to direct questions.
          response_preferences: be concise.
        auto_organize:
          enabled: true
          ignored_label: Auto/Skip
        """,
    )

    cfg = load_config(path)

    assert cfg.auto_organize.enabled is True
    assert cfg.auto_organize.ignored_label == "Auto/Skip"


def test_default_config_loads():
    """The committed config.yaml at the service root is valid."""
    cfg = load_config()
    assert cfg.agent.background.strip()
    assert cfg.agent.triage_instructions.strip()
    assert cfg.agent.response_preferences.strip()


def test_missing_file_fails_loud(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_empty_file_fails_loud(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("")
    with pytest.raises(ValueError):
        load_config(path)


def test_blank_field_fails_loud(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        agent:
          background: ""
          triage_instructions: something
          response_preferences: something
        """,
    )
    with pytest.raises(ValidationError):
        load_config(path)


def test_missing_field_fails_loud(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        agent:
          background: only this one
        """,
    )
    with pytest.raises(ValidationError):
        load_config(path)


def test_config_values_flow_into_prompts(tmp_path):
    """The injected config values appear in the formatted prompts (no LLM call)."""
    path = _write_yaml(
        tmp_path,
        """
        agent:
          background: UNIQUE_BACKGROUND_MARKER
          triage_instructions: UNIQUE_TRIAGE_MARKER
          response_preferences: UNIQUE_PREFS_MARKER
        """,
    )
    cfg = load_config(path)

    triage = triage_system_prompt.format(
        background=cfg.agent.background,
        triage_instructions=cfg.agent.triage_instructions,
    )
    assert "UNIQUE_BACKGROUND_MARKER" in triage
    assert "UNIQUE_TRIAGE_MARKER" in triage

    agent = agent_system_prompt.format(
        tools_prompt="(tools)",
        background=cfg.agent.background,
        response_preferences=cfg.agent.response_preferences,
    )
    assert "UNIQUE_BACKGROUND_MARKER" in agent
    assert "UNIQUE_PREFS_MARKER" in agent


def test_platform_settings_default_to_single_user_dev():
    from src.config import settings

    assert settings.database_url == ""
    assert settings.redis_url == ""
    assert settings.storage_backend == "sqlite"
    assert settings.tenant_mode == "single"
    assert settings.default_user_id == "default"
    assert settings.gmail_webhook_enabled is False
    assert settings.polling_fallback_enabled is True
