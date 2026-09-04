from __future__ import annotations

import pytest

from tests.conftest import patch_provider
from fastapi.testclient import TestClient

import src.api as api_module
import src.graph as graph_module
from src.api import app
from src.persona import PersonaSuggestion, suggest_persona


class _FakeStructuredLLM:
    def __init__(self, suggestion: PersonaSuggestion):
        self._suggestion = suggestion
        self.prompts: list = []

    def with_structured_output(self, schema):
        assert schema is PersonaSuggestion
        return self

    def invoke(self, messages, config=None):
        self.prompts.append(messages)
        return self._suggestion


SENT = [
    {"to": "candidat@example.com", "subject": "Re: candidature", "body": "Bonjour,\n...\nKarim Martin\nChargé RH — Acme"},
]
RECEIVED = [
    {"from": "candidat@example.com", "subject": "Candidature stage"},
    {"from": "fournisseur@example.com", "subject": "Facture mars"},
]

SUGGESTION = PersonaSuggestion(
    prenom="Karim", nom="Martin", fonction="Chargé RH", entreprise="Acme",
    repond_a=["candidats", "fournisseurs"],
)


def test_suggest_persona_builds_prompt_from_both_sources():
    llm = _FakeStructuredLLM(SUGGESTION)
    result = suggest_persona(SENT, RECEIVED, llm)
    assert result.prenom == "Karim"
    prompt_text = str(llm.prompts[0])
    assert "SENT samples" in prompt_text
    assert "RECEIVED messages" in prompt_text
    assert "Candidature stage" in prompt_text


def test_suggest_persona_requires_some_samples():
    with pytest.raises(ValueError):
        suggest_persona([], [], _FakeStructuredLLM(SUGGESTION))


def test_suggest_persona_normalizes_tone_and_language():
    raw = PersonaSuggestion(
        prenom="Karim", ton=" Chaleureux ", mission="Gérer les demandes RH", langue_reponse="FR",
    )
    result = suggest_persona(SENT, RECEIVED, _FakeStructuredLLM(raw))
    assert result.ton == "chaleureux"
    assert result.langue_reponse == "fr"
    assert result.mission == "Gérer les demandes RH"


def test_suggest_persona_drops_invalid_tone_and_language():
    raw = PersonaSuggestion(prenom="Karim", ton="sarcastique", langue_reponse="klingon")
    result = suggest_persona(SENT, RECEIVED, _FakeStructuredLLM(raw))
    assert result.ton == ""
    assert result.langue_reponse == ""


@pytest.fixture
def suggest_env(monkeypatch):
    patch_provider(
        monkeypatch,
        api_module,
        fetch_sent=lambda max_samples: SENT,
        list_inbox=lambda limit: RECEIVED,
    )
    monkeypatch.setattr(graph_module, "llm", _FakeStructuredLLM(SUGGESTION))


def test_suggest_endpoint_returns_suggestion_without_writing(suggest_env, monkeypatch, tmp_path):
    import src.instance_config as ic
    import src.persona as persona_module

    monkeypatch.setattr(ic.settings, "database_url", "")
    monkeypatch.setattr(persona_module, "DEFAULT_PERSONA_PATH", tmp_path / "persona.yaml")

    with TestClient(app) as client:
        payload = client.post("/persona/suggest").json()
        assert payload["suggestion"]["prenom"] == "Karim"
        assert payload["suggestion"]["repond_a"] == ["candidats", "fournisseurs"]
        assert payload["sent_sample_count"] == 1

        # Nothing was persisted: the stored persona is still empty.
        persona = client.get("/persona").json()
        assert persona["identite"]["prenom"] == ""


def test_suggest_endpoint_reports_gmail_outage(monkeypatch):
    def boom():
        raise RuntimeError("no token")

    patch_provider(monkeypatch, api_module, fetch_sent=boom)
    with TestClient(app) as client:
        response = client.post("/persona/suggest")
    assert response.status_code == 503
    assert "Gmail" in response.json()["detail"]


def test_suggest_endpoint_requires_owner_role(suggest_env):
    with TestClient(app) as client:
        denied = client.post(
            "/persona/suggest", headers={"X-Agora-Instance-Role": "viewer"}
        )
    assert denied.status_code == 403
