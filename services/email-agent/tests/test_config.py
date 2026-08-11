from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.config import AgentConfig, load_config
from src.config import validate_gmail_webhook_config, validate_live_send_config, validate_model_redaction
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
        category_section="",
    )
    assert "UNIQUE_BACKGROUND_MARKER" in triage
    assert "UNIQUE_TRIAGE_MARKER" in triage

    agent = agent_system_prompt.format(
        tools_prompt="(tools)",
        background=cfg.agent.background,
        response_preferences=cfg.agent.response_preferences,
        writing_style=cfg.agent.writing_style_default,
        reply_language="",
        workflow_instructions_section="",
    )
    assert "UNIQUE_BACKGROUND_MARKER" in agent
    assert "UNIQUE_PREFS_MARKER" in agent


def test_platform_settings_default_to_single_user_dev():
    from src.config import settings

    assert settings.database_url == ""
    assert settings.redis_url == ""
    assert settings.storage_backend == "sqlite"
    assert settings.run_registry_backend == "json"
    assert settings.tenant_mode == "single"
    assert settings.default_user_id == "default"
    assert settings.gmail_webhook_enabled is False
    assert settings.gmail_webhook_topic == ""
    assert settings.gmail_webhook_secret == ""
    assert settings.poll_max_retries == 3
    assert settings.poll_backoff_base_seconds == 2
    assert settings.roles_path == "roles.yaml"
    assert settings.contacts_path == "contacts.yaml"
    assert settings.llm_streaming_enabled is True
    assert settings.polling_fallback_enabled is True


def test_model_redaction_can_be_disabled_for_local_profiles(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROFILE", raising=False)
    config = SimpleNamespace(llm_profile="local", redact_for_model=False)

    validate_model_redaction(config)


def test_model_redaction_is_required_for_hosted_profiles(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROFILE", raising=False)
    config = SimpleNamespace(llm_profile="prod", redact_for_model=False)

    with pytest.raises(RuntimeError, match="AGENT_REDACT_FOR_MODEL=false is not allowed"):
        validate_model_redaction(config)


def test_model_redaction_accepts_hosted_profile_when_enabled(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROFILE", raising=False)
    config = SimpleNamespace(llm_profile="dev", redact_for_model=True)

    validate_model_redaction(config)


def test_gmail_webhook_requires_topic_and_secret():
    config = SimpleNamespace(
        gmail_webhook_enabled=True,
        gmail_webhook_topic="",
        gmail_webhook_secret="",
        polling_fallback_enabled=True,
    )

    with pytest.raises(RuntimeError, match="GMAIL_WEBHOOK_TOPIC, GMAIL_WEBHOOK_SECRET"):
        validate_gmail_webhook_config(config)


def test_gmail_webhook_keeps_polling_fallback_enabled():
    config = SimpleNamespace(
        gmail_webhook_enabled=True,
        gmail_webhook_topic="projects/acme/topics/gmail-push",
        gmail_webhook_secret="secret",
        polling_fallback_enabled=False,
    )

    with pytest.raises(RuntimeError, match="GMAIL_POLLING_FALLBACK_ENABLED"):
        validate_gmail_webhook_config(config)


def test_gmail_webhook_accepts_complete_push_config():
    config = SimpleNamespace(
        gmail_webhook_enabled=True,
        gmail_webhook_topic="projects/acme/topics/gmail-push",
        gmail_webhook_secret="secret",
        polling_fallback_enabled=True,
    )

    validate_gmail_webhook_config(config)


def test_live_send_allowed_in_dry_run_with_no_allowlist():
    config = SimpleNamespace(dry_run=True, outbound_allowlist=frozenset())

    validate_live_send_config(config)


def test_live_send_requires_allowlist():
    config = SimpleNamespace(dry_run=False, outbound_allowlist=frozenset())

    with pytest.raises(RuntimeError, match="AGENT_DRY_RUN=false requires a non-empty AGENT_OUTBOUND_ALLOWLIST"):
        validate_live_send_config(config)


def test_live_send_accepts_dry_run_false_with_allowlist():
    config = SimpleNamespace(dry_run=False, outbound_allowlist=frozenset({"test@example.com"}))

    validate_live_send_config(config)


def test_dlq_settings_default_to_local_backend():
    from src.config import settings
    assert settings.dlq_backend in {"json", "postgres", "redis"}
    assert settings.dlq_path == "logs/dlq.json"
