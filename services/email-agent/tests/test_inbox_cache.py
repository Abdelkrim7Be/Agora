"""The Messages view serves a cached Gmail listing.

Listing the inbox is a network round trip to Google, measured around four
seconds for fifty messages against the live account. Paying it on every visit is
what made the view feel slow; these tests pin that it is paid once per window,
that verdicts stay fresh anyway, and that mutating the mailbox invalidates it.
"""

from __future__ import annotations

import pytest

from tests.conftest import patch_provider
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

    def _list_inbox(limit):
        calls.append(limit)
        return [{"id": "m1", "thread_id": "t1", "from": "a@b.com", "subject": "Hi", "snippet": "", "labels": []}]

    patch_provider(
        monkeypatch, api, list_inbox=_list_inbox, mark_as_read=lambda msg_id: None
    )
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


def test_sent_mailbox_uses_separate_cache(monkeypatch):
    inbox_calls: list[int] = []
    sent_calls: list[int] = []

    monkeypatch.setattr(api, "list_runs", lambda *a, **kw: [])
    patch_provider(
        monkeypatch,
        api,
        list_inbox=lambda limit: inbox_calls.append(limit)
        or [{"id": "in-1", "thread_id": "t1", "from": "a@b.com", "subject": "Reçu", "snippet": "", "unread": False}],
        fetch_sent=lambda limit: sent_calls.append(limit)
        or [{"id": "sent-1", "thread_id": "t2", "to": "c@d.com", "subject": "Envoyé", "date": "today", "body": "message envoyé assez long pour le tableau"}],
    )

    with TestClient(app) as client:
        inbox = client.get("/inbox?limit=25")
        sent = client.get("/inbox?limit=25&mailbox=sent")
        sent_again = client.get("/inbox?limit=25&mailbox=sent")

    assert inbox.status_code == sent.status_code == sent_again.status_code == 200
    assert inbox.json()["messages"][0]["id"] == "in-1"
    assert sent.json()["messages"][0]["id"] == "sent-1"
    assert len(inbox_calls) == 1
    assert len(sent_calls) == 1


def test_force_agent_marks_unread_clears_run_and_processes(monkeypatch):
    marked: list[str] = []
    deleted: list[list[str]] = []

    async def _process(graph, msg_id, provider, rules_config, message=None):
        return (msg_id, "pending_approval", "run-new")

    patch_provider(monkeypatch, api, mark_as_unread=lambda msg_id: marked.append(msg_id))
    monkeypatch.setattr(api, "find_run_by_email", lambda *a, **kw: {"run_id": "run-old"})
    monkeypatch.setattr(api, "delete_runs", lambda run_ids, **kw: deleted.append(run_ids) or len(run_ids))
    monkeypatch.setattr(api, "process_message_with_retry", _process)

    with TestClient(app) as client:
        response = client.post("/inbox/m1/force-agent")

    assert response.status_code == 200, response.text
    assert marked == ["m1"]
    assert deleted == [["run-old"]]
    assert response.json()["outcome"]["run_id"] == "run-new"


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


def test_an_expired_listing_is_served_immediately_instead_of_blocking(monkeypatch):
    """Past its TTL the cached listing is stale, not wrong. Blocking the view on a
    live Gmail round trip to confirm that is what made opening Messages feel like
    the app had hung — especially as the poller invalidates the cache each cycle."""
    import time as _time
    from src import api

    key = ("someone", "an-instance", "inbox", 25)
    monkeypatch.setattr(api, "_INBOX_CACHE", {})
    monkeypatch.setattr(api, "_INBOX_CACHE_TTL_SECONDS", 60.0)
    monkeypatch.setattr(api, "_INBOX_STALE_SECONDS", 900.0)
    # No shared backend in this test: exercise the local half.
    monkeypatch.setattr(api, "cache_get_json", lambda _key: None)
    monkeypatch.setattr(api, "cache_set_json", lambda *a, **k: False)

    api._inbox_cache_put(key, [{"id": "m1"}])

    fresh, stale = api._inbox_cache_get(key)
    assert fresh == [{"id": "m1"}] and stale is False

    # Age it past the TTL but inside the stale window.
    stored_at, messages = api._INBOX_CACHE[key]
    api._INBOX_CACHE[key] = (stored_at - 120, messages)
    served, stale = api._inbox_cache_get(key)
    assert served == [{"id": "m1"}]
    assert stale is True, "an expired-but-recent listing must still be served"

    # Past the stale window it is dropped and the caller must fetch.
    api._INBOX_CACHE[key] = (_time.time() - 5000, messages)
    assert api._inbox_cache_get(key) == (None, False)


def test_only_one_background_refresh_runs_per_listing(monkeypatch):
    from src import api

    key = ("someone", "an-instance", "inbox", 25)
    monkeypatch.setattr(api, "_INBOX_REFRESHING", set())
    scheduled = []
    monkeypatch.setattr(api.asyncio, "create_task", lambda coro: scheduled.append(coro) or coro.close())

    api._schedule_inbox_refresh(key, "inbox", 25)
    api._schedule_inbox_refresh(key, "inbox", 25)

    # The second visit while a refresh is in flight must not queue another
    # Gmail round trip behind the first.
    assert len(scheduled) == 1
