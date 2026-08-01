"""The Messages view serves a cached Gmail listing.

Listing the inbox is a network round trip to Google, measured around four
seconds for fifty messages against the live account. Paying it on every visit is
what made the view feel slow; these tests pin that it is paid once per window,
that verdicts stay fresh anyway, and that mutating the mailbox invalidates it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.api import app


@pytest.fixture(autouse=True)
def _clear_cache():
    api._inbox_cache_clear()
    yield
    api._inbox_cache_clear()


@pytest.fixture
def gmail_calls(monkeypatch):
    calls: list[int] = []

    def _list_inbox(limit, resource=None):
        calls.append(limit)
        return [{"id": "m1", "thread_id": "t1", "from": "a@b.com", "subject": "Hi", "snippet": "", "labels": []}]

    monkeypatch.setattr(api, "gmail_resource", lambda *a, **kw: object())
    monkeypatch.setattr(api, "list_inbox", _list_inbox)
    monkeypatch.setattr(api, "list_runs", lambda *a, **kw: [])
    return calls


def test_second_read_is_served_from_cache(gmail_calls):
    with TestClient(app) as client:
        first = client.get("/inbox?limit=25")
        second = client.get("/inbox?limit=25")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["messages"] == second.json()["messages"]
    assert len(gmail_calls) == 1


def test_refresh_forces_a_re_read(gmail_calls):
    with TestClient(app) as client:
        client.get("/inbox?limit=25")
        client.get("/inbox?limit=25&refresh=true")

    assert len(gmail_calls) == 2


def test_verdicts_are_reattached_even_on_a_cache_hit(gmail_calls, monkeypatch):
    with TestClient(app) as client:
        first = client.get("/inbox?limit=25")
        assert first.json()["messages"][0].get("run_status") is None

        # A run lands between the two reads; the cached listing must still show it.
        monkeypatch.setattr(
            api,
            "list_runs",
            lambda *a, **kw: [{"run_id": "r1", "email_id": "m1", "status": "pending_approval",
                               "classification": "respond"}],
        )
        second = client.get("/inbox?limit=25")

    assert len(gmail_calls) == 1
    assert second.json()["messages"][0]["run_status"] == "pending_approval"


def test_mailbox_mutation_drops_the_cache(gmail_calls, monkeypatch):
    monkeypatch.setattr(api, "archive_email_message", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(api, "mark_as_read", lambda *a, **kw: None, raising=False)

    with TestClient(app) as client:
        client.get("/inbox?limit=25")
        client.post("/inbox/m1/read")
        client.get("/inbox?limit=25")

    assert len(gmail_calls) == 2


def test_a_zero_ttl_disables_caching(gmail_calls, monkeypatch):
    monkeypatch.setattr(api, "_INBOX_CACHE_TTL_SECONDS", 0)

    with TestClient(app) as client:
        client.get("/inbox?limit=25")
        client.get("/inbox?limit=25")

    assert len(gmail_calls) == 2
