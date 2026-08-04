"""Owner-defined categories collected before the mailbox is read."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app


def _post(client, payload):
    return client.post("/instance-setup/categories", json=payload)


def test_owner_categories_are_slugged_and_keep_their_label(monkeypatch, tmp_path):
    written = {}
    import src.api as api

    monkeypatch.setattr(api, "write_instance_text", lambda kind, text, path, *a, **kw: written.update({kind: text}))

    with TestClient(app) as client:
        response = _post(client, {"categories": [
            {"name": "Banque & Finance", "keywords": ["virement", "rib"]},
            {"name": "Ressources Humaines", "description": "candidatures"},
        ]})

    assert response.status_code == 200
    parsed = response.json()["parsed"]
    names = [c["name"] for c in parsed["categories"]]
    labels = [c["display_name"] for c in parsed["categories"]]
    assert names == ["banque_finance", "ressources_humaines"]
    assert labels == ["Banque & Finance", "Ressources Humaines"]
    assert parsed["categories"][0]["when"]["subject_contains"] == ["virement", "rib"]
    assert "categories" in written


def test_duplicate_and_blank_names_are_dropped(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "write_instance_text", lambda *a, **kw: None)

    with TestClient(app) as client:
        response = _post(client, {"categories": [
            {"name": "Banque"},
            {"name": "  banque  "},
            {"name": "   "},
        ]})

    assert response.status_code == 200
    assert [c["name"] for c in response.json()["parsed"]["categories"]] == ["banque"]


def test_empty_list_is_rejected(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "write_instance_text", lambda *a, **kw: None)

    with TestClient(app) as client:
        assert _post(client, {"categories": []}).status_code == 422
