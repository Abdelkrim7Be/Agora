from __future__ import annotations

from fastapi.testclient import TestClient

import src.graph as graph_module
from src.api import app
from src.memory_summary import (
    _FrenchBullets,
    clear_summary_cache,
    french_display_texts,
    memory_items,
    remove_item,
)


TEXT = "Ignore newsletters\nAlways respond to candidates\n\nEscalate legal topics"


class _FakeBulletsLLM:
    def __init__(self, bullets_factory):
        self._factory = bullets_factory
        self.calls = 0

    def with_structured_output(self, schema):
        assert schema is _FrenchBullets
        return self

    def invoke(self, messages, config=None):
        self.calls += 1
        return self._factory(messages)


def _echo_french(messages):
    lines = [l for l in messages[1]["content"].splitlines() if l.strip()]
    return _FrenchBullets(bullets=[f"FR: {line.split('. ', 1)[1]}" for line in lines])


def test_memory_items_split_and_stable_ids():
    items = memory_items("triage_preferences", TEXT)
    assert [item["text"] for item in items] == [
        "Ignore newsletters", "Always respond to candidates", "Escalate legal topics",
    ]
    again = memory_items("triage_preferences", TEXT)
    assert [i["id"] for i in items] == [i["id"] for i in again]
    other_kind = memory_items("response_preferences", TEXT)
    assert items[0]["id"] != other_kind[0]["id"]


def test_remove_item_removes_exactly_one_line():
    items = memory_items("triage_preferences", TEXT)
    updated = remove_item("triage_preferences", TEXT, items[1]["id"])
    assert "Always respond to candidates" not in updated
    assert "Ignore newsletters" in updated
    assert "Escalate legal topics" in updated


def test_remove_item_unknown_id_returns_none():
    assert remove_item("triage_preferences", TEXT, "nope") is None


def test_french_display_texts_cached_by_content():
    clear_summary_cache()
    llm = _FakeBulletsLLM(_echo_french)
    items = memory_items("triage_preferences", TEXT)
    first = french_display_texts("triage_preferences", items, llm)
    second = french_display_texts("triage_preferences", items, llm)
    assert first == second
    assert first[0] == "FR: Ignore newsletters"
    assert llm.calls == 1


def test_french_display_texts_fall_back_to_raw_on_mismatch():
    clear_summary_cache()
    llm = _FakeBulletsLLM(lambda messages: _FrenchBullets(bullets=["une seule puce"]))
    items = memory_items("triage_preferences", TEXT)
    displays = french_display_texts("triage_preferences", items, llm)
    assert displays == [item["text"] for item in items]


def test_summary_and_delete_endpoints(monkeypatch):
    clear_summary_cache()
    monkeypatch.setattr(graph_module, "llm", _FakeBulletsLLM(_echo_french))
    with TestClient(app) as client:
        client.put(
            "/memory",
            json={
                "triage_preferences": TEXT,
                "response_preferences": "Keep replies short",
            },
        )
        summary = client.get("/memory/summary").json()
        assert len(summary["triage_preferences"]) == 3
        assert summary["triage_preferences"][0]["display_text"].startswith("FR:")

        target = summary["triage_preferences"][1]
        removed = client.delete(
            f"/memory/item?kind=triage_preferences&id={target['id']}"
        ).json()
        assert removed["remaining"] == 2

        after = client.get("/memory").json()
        assert "Always respond to candidates" not in after["triage_preferences"]

        missing = client.delete("/memory/item?kind=triage_preferences&id=nope")
        assert missing.status_code == 404
        invalid = client.delete("/memory/item?kind=bogus&id=nope")
        assert invalid.status_code == 400


def test_delete_memory_item_requires_owner_role():
    with TestClient(app) as client:
        denied = client.delete(
            "/memory/item?kind=triage_preferences&id=abc",
            headers={"X-Agora-Instance-Role": "viewer"},
        )
    assert denied.status_code == 403
