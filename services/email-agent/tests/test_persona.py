from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.persona import Persona, PersonaIdentite, PersonaPerimetre, compile_persona


def _full_persona() -> Persona:
    return Persona(
        identite=PersonaIdentite(
            prenom="Karim", nom="Martin", fonction="Chargé RH",
            entreprise="Acme", langue_reponse="fr",
        ),
        mission="répondre aux candidats et aux demandes RH",
        perimetre=PersonaPerimetre(
            repond_a=["candidats", "demandes RH internes"],
            ne_repond_jamais_a=["newsletters", "démarchage commercial"],
            escalade_vers="direction@acme.example",
        ),
        ton="chaleureux",
    )


@pytest.fixture
def file_backend(monkeypatch, tmp_path):
    import src.instance_config as ic
    import src.persona as persona_module

    monkeypatch.setattr(ic.settings, "database_url", "")
    monkeypatch.setattr(persona_module, "DEFAULT_PERSONA_PATH", tmp_path / "persona.yaml")
    return tmp_path


def test_compile_persona_produces_french_instructions():
    background, triage, response = compile_persona(_full_persona())

    assert "Karim Martin" in background
    assert "Chargé RH chez Acme" in background
    assert "répondre aux candidats et aux demandes RH" in background

    assert "candidats, demandes RH internes" in triage
    assert "newsletters, démarchage commercial" in triage
    assert "direction@acme.example" in triage
    assert "notify" in triage

    assert "chaleureux" in response
    assert "Toujours répondre en français." in response


def test_compile_persona_is_deterministic():
    assert compile_persona(_full_persona()) == compile_persona(_full_persona())


def test_empty_persona_is_empty():
    assert Persona().is_empty() is True
    assert _full_persona().is_empty() is False


def test_persona_round_trip_endpoint(file_backend, monkeypatch, tmp_path):
    # Redirect the config kind too so compilation does not rewrite the repo config.yaml.
    import src.api as api_module

    monkeypatch.setattr(api_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    with TestClient(app) as client:
        saved = client.put("/persona", json=_full_persona().model_dump()).json()
        assert saved["identite"]["prenom"] == "Karim"
        assert "Karim Martin" in saved["compiled"]["background"]

        fetched = client.get("/persona").json()
        assert fetched["mission"] == "répondre aux candidats et aux demandes RH"
        assert fetched["ton"] == "chaleureux"
        assert "compiled" in fetched


def test_put_persona_compiles_into_agent_config(file_backend, monkeypatch, tmp_path):
    import src.api as api_module
    import shutil
    from src.config import DEFAULT_CONFIG_PATH as REAL_CONFIG_PATH

    config_copy = tmp_path / "config.yaml"
    shutil.copyfile(REAL_CONFIG_PATH, config_copy)
    monkeypatch.setattr(api_module, "DEFAULT_CONFIG_PATH", config_copy)
    import src.config as config_pkg
    monkeypatch.setattr(config_pkg, "DEFAULT_CONFIG_PATH", config_copy)

    with TestClient(app) as client:
        client.put("/persona", json=_full_persona().model_dump())
        cfg = client.get("/config").json()
    assert "Karim Martin" in cfg["agent"]["background"]
    assert "newsletters" in cfg["agent"]["triage_instructions"]
    assert "Toujours répondre en français." in cfg["agent"]["response_preferences"]


def test_put_empty_persona_leaves_config_untouched(file_backend, monkeypatch, tmp_path):
    import src.api as api_module
    import shutil
    from src.config import DEFAULT_CONFIG_PATH as REAL_CONFIG_PATH

    config_copy = tmp_path / "config.yaml"
    shutil.copyfile(REAL_CONFIG_PATH, config_copy)
    monkeypatch.setattr(api_module, "DEFAULT_CONFIG_PATH", config_copy)
    import src.config as config_pkg
    monkeypatch.setattr(config_pkg, "DEFAULT_CONFIG_PATH", config_copy)

    with TestClient(app) as client:
        before = client.get("/config").json()
        client.put("/persona", json=Persona().model_dump())
        after = client.get("/config").json()
    assert after["agent"]["background"] == before["agent"]["background"]


def test_put_persona_requires_owner_role(file_backend):
    with TestClient(app) as client:
        denied = client.put(
            "/persona",
            json=Persona().model_dump(),
            headers={"X-Agora-Instance-Role": "viewer"},
        )
        assert denied.status_code == 403


def test_persona_rejects_unknown_tone():
    with pytest.raises(ValueError):
        Persona(ton="agressif")
