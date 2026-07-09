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


def test_get_llm_uses_default_dev_profile(monkeypatch):
    captured = {}

    def fake_init_chat_model(model_name, **kwargs):
        captured["model_name"] = model_name
        captured["kwargs"] = kwargs
        return SimpleNamespace(model_name=model_name, kwargs=kwargs)

    monkeypatch.setattr("src.llm.init_chat_model", fake_init_chat_model)

    model = get_llm("triage")

    assert model.model_name == "groq:llama-3.3-70b-versatile"
    assert captured == {
        "model_name": "groq:llama-3.3-70b-versatile",
        "kwargs": {"temperature": 0.0},
    }


def test_loads_prod_profile_mapping(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROFILE", "prod")

    profile = load_llm_profile()

    assert profile.endpoint == "http://litellm:4000/v1"
    assert profile.roles["triage"] == "openai:agora-triage"
    assert get_llm_model_name("draft") == "openai:agora-draft"


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
