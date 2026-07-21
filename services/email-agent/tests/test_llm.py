from __future__ import annotations

from types import SimpleNamespace

import pytest

from src import cost_tracker
from src.llm import clear_llm_profile_cache, get_llm, get_llm_model_name, load_llm_profile


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    clear_llm_profile_cache()
    yield
    clear_llm_profile_cache()


def test_get_llm_uses_default_local_profile(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROFILE", raising=False)
    monkeypatch.delenv("AGENT_LLM_CONFIG_PATH", raising=False)
    monkeypatch.setattr("src.llm.settings.llm_profile", "local")
    monkeypatch.setattr("src.llm.settings.llm_config_path", "")
    captured = {}

    def fake_init_chat_model(model_name, **kwargs):
        captured["model_name"] = model_name
        captured["kwargs"] = kwargs
        return SimpleNamespace(model_name=model_name, kwargs=kwargs)

    monkeypatch.setattr("src.llm.init_chat_model", fake_init_chat_model)

    model = get_llm("triage")

    assert model.model_name == "openai:qwen2.5:3b-8k"
    assert captured["kwargs"]["base_url"] == "http://localhost:11434/v1"


def test_loads_prod_profile_mapping(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROFILE", "prod")

    profile = load_llm_profile()

    assert profile.endpoint == "http://litellm:4000/v1"
    assert profile.roles["triage"] == "openai:agora-triage"
    assert profile.roles["triage"] != profile.roles["draft"]
    assert profile.fallbacks["draft"] == ["openai:agora-draft-backup", "anthropic:agora-draft-anthropic-backup"]
    assert get_llm_model_name("draft") == "openai:agora-draft"


def test_loads_local_ollama_profile_mapping(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROFILE", "local")

    profile = load_llm_profile()

    assert profile.endpoint == "http://localhost:11434/v1"
    assert profile.roles["triage"] == "openai:qwen2.5:3b-8k"
    assert set(profile.roles) == {"triage", "draft", "reason", "memory_style"}
    assert profile.fallbacks == {}
    assert get_llm_model_name("draft") == "openai:qwen2.5:3b-8k"


def test_prod_fallback_uses_backup_model(monkeypatch, tmp_path):
    path = tmp_path / "llm.test.yaml"
    path.write_text(
        "endpoint: http://litellm:4000/v1\n"
        "temperature: 0.0\n"
        "roles:\n"
        "  triage: openai:primary-triage\n"
        "  draft: openai:primary-draft\n"
        "  reason: openai:primary-reason\n"
        "  memory_style: openai:primary-memory\n"
        "fallbacks:\n"
        "  draft:\n"
        "    - openai:backup-draft\n",
        encoding="utf-8",
    )

    calls: list[str] = []

    class FakeModel:
        def __init__(self, model_name: str):
            self.model_name = model_name

        def invoke(self, _messages, config=None):
            calls.append(self.model_name)
            if self.model_name == "openai:primary-draft":
                raise RuntimeError("primary unavailable")
            return {"served_by": self.model_name, "config": config}

        def bind_tools(self, *_args, **_kwargs):
            return self

        def with_structured_output(self, *_args, **_kwargs):
            return self

    monkeypatch.setattr("src.llm.init_chat_model", lambda model_name, **_kwargs: FakeModel(model_name))

    llm = get_llm("draft", config_path=path)
    result = llm.invoke([{"role": "user", "content": "hello"}], config={"metadata": {"run_id": "r1"}})

    assert result["served_by"] == "openai:backup-draft"
    assert calls == ["openai:primary-draft", "openai:backup-draft"]


def test_profile_flip_changes_models_without_code_change(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROFILE", "safe")

    safe_profile = load_llm_profile()

    assert safe_profile.endpoint == "http://localhost:11434/v1"
    assert safe_profile.roles["draft"] == "openai:qwen2.5:3b-8k"
    assert safe_profile.max_tokens == 400


def test_unknown_role_raises_clear_error():
    with pytest.raises(ValueError, match="Unsupported LLM role 'unknown'"):
        get_llm_model_name("unknown")


def test_compute_cost_unknown_model_warns_and_defaults_zero(caplog):
    caplog.set_level("WARNING")

    cost = cost_tracker.compute_cost("openai:missing-model", 1500, 500)

    assert cost == 0.0
    assert "No pricing configured for model 'openai:missing-model'" in caplog.text


def test_profile_backed_price_alias_keeps_known_rate(monkeypatch):
    monkeypatch.setattr(
        cost_tracker,
        "load_llm_profile",
        lambda profile_name=None: SimpleNamespace(
            roles={
                "triage": "custom:llama-3.3-70b-versatile",
                "draft": "custom:llama-3.3-70b-versatile",
                "reason": "custom:llama-3.3-70b-versatile",
                "memory_style": "custom:llama-3.3-70b-versatile",
            }
        ),
    )
    monkeypatch.setattr(cost_tracker, "active_profile_name", lambda: "dev")
    cost = cost_tracker.compute_cost("custom:llama-3.3-70b-versatile", 1_000_000, 0)

    assert cost == 0.54



def test_prod_profile_includes_multi_provider_fallbacks(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROFILE", "prod")
    profile = load_llm_profile()
    assert profile.fallbacks["draft"][-1] == "anthropic:agora-draft-anthropic-backup"
