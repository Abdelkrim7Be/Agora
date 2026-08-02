"""GmailProvider must be a pure pass-through to src.gmail_client.

M4 rewires a dozen modules from direct `gmail_client` imports onto the
provider. That rewiring is only safe if every provider method reaches the same
function with the same arguments it used to be called with — so this asserts
the mapping explicitly rather than trusting the one-liners to stay correct.
"""

from __future__ import annotations

import inspect

import pytest

from src import gmail_client
from src.mail import get_provider
from src.mail.base import MailProvider
from src.mail.gmail import GmailProvider
from src.mail.setting import MAIL_PROVIDERS


class _FakeResource:
    """Stands in for the googleapiclient discovery object."""


@pytest.fixture
def provider():
    return GmailProvider(resource=_FakeResource())


def _record(monkeypatch, name):
    """Replace gmail_client.<name> with a recorder returning a sentinel."""
    calls = {}

    def fake(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return f"<{name}>"

    monkeypatch.setattr(gmail_client, name, fake)
    return calls


# (provider method, gmail_client function, call args, expected positional, expected keyword)
DELEGATIONS = [
    ("fetch_unread", "fetch_unread", (10,), (10,), {"resource": ...}),
    ("list_inbox", "list_inbox", (25,), (25,), {"resource": ...}),
    ("fetch_recent", "fetch_recent", (5,), (5,), {"resource": ...}),
    ("fetch_sent", "fetch_sent", (7,), (7,), {"resource": ...}),
    ("search_messages", "search_messages", ("q", 3), ("q", 3), {"resource": ...}),
    ("list_messages_by_label", "list_messages_by_label", ("L1", 4), ("L1", 4), {"resource": ...}),
    ("current_sync_cursor", "current_history_id", (), (), {"resource": ...}),
    ("fetch_changes_since", "fetch_history_message_refs", ("900",), ("900",), {"resource": ...}),
    ("watch_mailbox", "watch_mailbox", ("topic",), ("topic",), {"resource": ...}),
    ("get_message", "get_message", ("m1",), ("m1",), {"resource": ...}),
    ("fetch_thread", "fetch_thread", ("t1",), ("t1",), {"resource": ...}),
    ("mark_as_read", "mark_as_read", ("m1",), ("m1",), {"resource": ...}),
    ("mark_as_unread", "mark_as_unread", ("m1",), ("m1",), {"resource": ...}),
    ("archive_message", "archive_message", ("m1",), ("m1",), {"resource": ...}),
    ("trash_message", "trash_message", ("m1",), ("m1",), {"resource": ...}),
    ("list_labels", "list_labels", (), (), {"resource": ...}),
    ("ensure_label", "ensure_label", ("Devis",), ("Devis",), {"resource": ...}),
    ("send_message", "send_message", ("a@b.c", "s", "b"), ("a@b.c", "s", "b"), {"resource": ...}),
    ("forward_message", "forward_message", ("m1", "a@b.c", "n"), ("m1", "a@b.c", "n"), {"resource": ...}),
    ("notify_internal_message", "notify_internal_message", ("a@b.c", "s", "n"), ("a@b.c", "s", "n"), {"resource": ...}),
    ("reply_all_message", "reply_all_message", ("m1", "body"), ("m1", "body"), {"resource": ...}),
    ("download_attachment", "download_attachment", ("m1", "a1"), ("m1", "a1"), {"resource": ...}),
]


@pytest.mark.parametrize("method,target,args,expect_args,expect_kwargs", DELEGATIONS)
def test_delegates_to_gmail_client(monkeypatch, provider, method, target, args, expect_args, expect_kwargs):
    calls = _record(monkeypatch, target)

    result = getattr(provider, method)(*args)

    assert result == f"<{target}>"
    assert calls["args"] == expect_args
    assert calls["kwargs"]["resource"] is provider.resource


def test_delegates_keyword_heavy_calls(monkeypatch, provider):
    """The calls whose keyword arguments carry meaning, checked in full."""
    modify = _record(monkeypatch, "modify_labels")
    provider.modify_labels("m1", add_label_ids=["A"], remove_label_ids=["B"], respect_dry_run=False)
    assert modify["args"] == ("m1",)
    assert modify["kwargs"]["add_label_ids"] == ["A"]
    assert modify["kwargs"]["remove_label_ids"] == ["B"]
    assert modify["kwargs"]["respect_dry_run"] is False

    html = _record(monkeypatch, "send_html_message")
    provider.send_html_message("a@b.c", "s", "<p>h</p>", "h", respect_dry_run=False)
    assert html["args"] == ("a@b.c", "s", "<p>h</p>", "h")
    assert html["kwargs"]["respect_dry_run"] is False

    draft = _record(monkeypatch, "create_draft")
    provider.create_draft("a@b.c", "s", "b", thread_id="t9")
    assert draft["args"] == ("a@b.c", "s", "b")
    assert draft["kwargs"]["thread_id"] == "t9"

    batch = _record(monkeypatch, "fetch_messages_batch")
    provider.fetch_messages_batch(["m1", "m2"], fmt="metadata", chunk=10)
    assert batch["args"] == (["m1", "m2"],)
    assert batch["kwargs"]["fmt"] == "metadata"
    assert batch["kwargs"]["chunk"] == 10

    corr = _record(monkeypatch, "fetch_sender_correspondence")
    provider.fetch_sender_correspondence("s@e.com", exclude_thread_id="t1", max_messages=3, max_chars=99)
    assert corr["args"] == ("s@e.com",)
    assert corr["kwargs"]["exclude_thread_id"] == "t1"
    assert corr["kwargs"]["max_messages"] == 3
    assert corr["kwargs"]["max_chars"] == 99

    thread = _record(monkeypatch, "format_thread")
    provider.format_thread([{"id": "m1"}], max_messages=4, max_chars_per_message=50)
    assert thread["args"] == ([{"id": "m1"}],)
    assert thread["kwargs"]["max_messages"] == 4
    assert thread["kwargs"]["max_chars_per_message"] == 50

    normalize = _record(monkeypatch, "gmail_to_email_input")
    provider.to_email_input({"id": "m1"}, thread_messages=[{"id": "m0"}])
    assert normalize["args"] == ({"id": "m1"},)
    assert normalize["kwargs"]["thread_messages"] == [{"id": "m0"}]


def test_cursor_ordering_uses_the_numeric_gmail_comparison(provider):
    assert provider.cursor_is_newer("1002", "1001") is True
    assert provider.cursor_is_newer("1001", "1002") is False


def test_stale_cursor_error_delegates(monkeypatch, provider):
    monkeypatch.setattr(gmail_client, "is_stale_history_error", lambda exc: exc.args[0] == "stale")
    assert provider.is_stale_cursor_error(Exception("stale")) is True
    assert provider.is_stale_cursor_error(Exception("other")) is False


def test_resource_is_built_lazily_and_reused(monkeypatch):
    built = []

    def fake_resource(user_id=None):
        built.append(user_id)
        return _FakeResource()

    monkeypatch.setattr(gmail_client, "gmail_resource", fake_resource)

    p = GmailProvider(user_id="owner@example.com")
    assert built == []  # construction must not touch the token store

    first = p.resource
    second = p.resource
    assert first is second
    assert built == ["owner@example.com"]


def test_probe_reports_the_mailbox(provider, monkeypatch):
    class _Profile:
        def execute(self):
            return {"emailAddress": "ceo@example.com"}

    class _Users:
        def getProfile(self, userId):
            assert userId == "me"
            return _Profile()

    monkeypatch.setattr(provider, "_resource", type("R", (), {"users": lambda self: _Users()})())

    assert provider.probe() == {"ok": True, "mailbox": "ceo@example.com", "error": ""}


def test_probe_never_raises(provider, monkeypatch):
    class _Boom:
        def users(self):
            raise RuntimeError("token expired")

    monkeypatch.setattr(provider, "_resource", _Boom())

    result = provider.probe()
    assert result["ok"] is False
    assert result["mailbox"] == ""
    assert "token expired" in result["error"]


def test_self_address_degrades_to_empty_string(provider, monkeypatch):
    class _Boom:
        def users(self):
            raise RuntimeError("no profile")

    monkeypatch.setattr(provider, "_resource", _Boom())
    assert provider.self_address() == ""


def test_gmail_provider_implements_the_whole_protocol():
    """A missing method would only surface as an AttributeError in production."""
    expected = {
        name
        for name in dir(MailProvider)
        if not name.startswith("_") and callable(getattr(MailProvider, name, None))
    }
    missing = []
    for name in sorted(expected):
        impl = getattr(GmailProvider, name, None)
        if impl is None:
            missing.append(name)
            continue
        contract = inspect.signature(getattr(MailProvider, name))
        actual = inspect.signature(impl)
        assert list(actual.parameters) == list(contract.parameters), (
            f"GmailProvider.{name} signature diverges from the protocol"
        )
    assert missing == []


def test_get_provider_defaults_to_gmail(monkeypatch):
    monkeypatch.setattr("src.mail.get_mail_provider", lambda instance_id: "gmail")
    assert isinstance(get_provider(), GmailProvider)


def test_provider_setting_rejects_unknown_values():
    from src.mail.setting import set_mail_provider

    with pytest.raises(ValueError):
        set_mail_provider("yahoo")
    assert MAIL_PROVIDERS == ("gmail", "outlook")
