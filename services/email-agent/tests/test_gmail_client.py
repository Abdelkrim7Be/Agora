from __future__ import annotations

import base64
from email import message_from_bytes, policy

from src.config import settings
from src.gmail_client import (
    _extract_message_part,
    archive_message,
    create_draft,
    ensure_label,
    forward_message,
    format_thread,
    gmail_to_email_input,
    list_labels,
    mark_as_read,
    mark_as_unread,
    modify_labels,
    reply_all_message,
    trash_message,
)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _message(headers: list[dict], payload: dict, msg_id="m1", thread_id="t1") -> dict:
    return {"id": msg_id, "threadId": thread_id, "payload": {**payload, "headers": headers}}


class _Execute:
    def __init__(self, response: dict):
        self.response = response

    def execute(self) -> dict:
        return self.response


class _FakeMessages:
    def __init__(self, messages: dict[str, dict] | None = None):
        self.messages = messages or {}
        self.calls: list[tuple[str, dict]] = []

    def modify(self, **kwargs):
        self.calls.append(("modify", kwargs))
        return _Execute({"id": kwargs["id"], **kwargs["body"]})

    def trash(self, **kwargs):
        self.calls.append(("trash", kwargs))
        return _Execute({"id": kwargs["id"], "labelIds": ["TRASH"]})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Execute(self.messages[kwargs["id"]])

    def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        return _Execute({"id": "sent-1", **kwargs["body"]})


class _FakeLabels:
    def __init__(self, labels: list[dict] | None = None):
        self.labels = labels or []
        self.calls: list[tuple[str, dict]] = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Execute({"labels": self.labels})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        created = {"id": f"Label_{len(self.labels) + 1}", "name": kwargs["body"]["name"]}
        self.labels.append(created)
        return _Execute(created)


class _FakeDrafts:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Execute({"id": "draft-1", **kwargs["body"]})


class _FakeUsers:
    def __init__(
        self,
        labels: list[dict] | None = None,
        messages: dict[str, dict] | None = None,
        profile_email: str = "me@example.com",
    ):
        self._messages = _FakeMessages(messages)
        self._labels = _FakeLabels(labels)
        self._drafts = _FakeDrafts()
        self._profile_email = profile_email

    def messages(self):
        return self._messages

    def labels(self):
        return self._labels

    def drafts(self):
        return self._drafts

    def getProfile(self, **kwargs):
        return _Execute({"emailAddress": self._profile_email})


class _FakeGmailResource:
    def __init__(
        self,
        labels: list[dict] | None = None,
        messages: dict[str, dict] | None = None,
        profile_email: str = "me@example.com",
    ):
        self._users = _FakeUsers(labels, messages, profile_email)

    def users(self):
        return self._users


HEADERS = [
    {"name": "From", "value": "Alice <alice@example.com>"},
    {"name": "To", "value": "Me <me@example.com>"},
    {"name": "Subject", "value": "Quick question"},
]


# --- Gmail mutation helpers ---

def test_modify_labels_dry_run_does_not_touch_resource(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    result = modify_labels("msg-1", add_label_ids=["L1"], remove_label_ids=["UNREAD"])

    assert result == {
        "dry_run": True,
        "action": "modify_labels",
        "message_id": "msg-1",
        "add_label_ids": ["L1"],
        "remove_label_ids": ["UNREAD"],
    }


def test_modify_labels_calls_gmail_api(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource()

    result = modify_labels(
        "msg-1",
        add_label_ids=["Label_1"],
        remove_label_ids=["UNREAD"],
        resource=resource,
    )

    assert result == {
        "id": "msg-1",
        "addLabelIds": ["Label_1"],
        "removeLabelIds": ["UNREAD"],
    }
    assert resource.users().messages().calls == [
        (
            "modify",
            {
                "userId": "me",
                "id": "msg-1",
                "body": {"addLabelIds": ["Label_1"], "removeLabelIds": ["UNREAD"]},
            },
        )
    ]


def test_mark_as_read_runs_even_in_dry_run(monkeypatch):
    # Poller housekeeping must not be suppressed by dry-run, or poll_once would
    # reprocess the same unread emails on every cycle.
    monkeypatch.setattr(settings, "dry_run", True)
    resource = _FakeGmailResource()

    mark_as_read("msg-read", resource=resource)

    assert resource.users().messages().calls == [
        (
            "modify",
            {
                "userId": "me",
                "id": "msg-read",
                "body": {"addLabelIds": [], "removeLabelIds": ["UNREAD"]},
            },
        )
    ]


def test_read_and_archive_helpers_use_label_mutation(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource()

    mark_as_read("msg-read", resource=resource)
    mark_as_unread("msg-unread", resource=resource)
    archive_message("msg-archive", resource=resource)

    assert resource.users().messages().calls == [
        (
            "modify",
            {
                "userId": "me",
                "id": "msg-read",
                "body": {"addLabelIds": [], "removeLabelIds": ["UNREAD"]},
            },
        ),
        (
            "modify",
            {
                "userId": "me",
                "id": "msg-unread",
                "body": {"addLabelIds": ["UNREAD"], "removeLabelIds": []},
            },
        ),
        (
            "modify",
            {
                "userId": "me",
                "id": "msg-archive",
                "body": {"addLabelIds": [], "removeLabelIds": ["INBOX"]},
            },
        ),
    ]


def test_trash_message_respects_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    assert trash_message("msg-1") == {
        "dry_run": True,
        "action": "trash_message",
        "message_id": "msg-1",
    }


def test_trash_message_calls_gmail_api(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource()

    result = trash_message("msg-1", resource=resource)

    assert result == {"id": "msg-1", "labelIds": ["TRASH"]}
    assert resource.users().messages().calls == [("trash", {"userId": "me", "id": "msg-1"})]


def test_list_labels_returns_labels(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource(labels=[{"id": "Label_1", "name": "Clients"}])

    assert list_labels(resource=resource) == [{"id": "Label_1", "name": "Clients"}]
    assert resource.users().labels().calls == [("list", {"userId": "me"})]


def test_ensure_label_reuses_existing_label(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource(labels=[{"id": "Label_1", "name": "Clients"}])

    assert ensure_label("Clients", resource=resource) == "Label_1"
    assert resource.users().labels().calls == [("list", {"userId": "me"})]


def test_ensure_label_creates_missing_label(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource()

    assert ensure_label("Auto/Ignored", resource=resource) == "Label_1"
    assert resource.users().labels().calls == [
        ("list", {"userId": "me"}),
        (
            "create",
            {
                "userId": "me",
                "body": {
                    "name": "Auto/Ignored",
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            },
        ),
    ]


def test_ensure_label_dry_run_returns_stable_id(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    assert ensure_label("Auto/Ignored") == "dry-run-label:Auto/Ignored"


def test_create_draft_respects_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    assert create_draft("a@example.com", "Subject", "Body", thread_id="thread-1") == {
        "dry_run": True,
        "action": "create_draft",
        "to": "a@example.com",
        "subject": "Subject",
        "thread_id": "thread-1",
    }


def test_create_draft_calls_gmail_api_with_encoded_message(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource()

    result = create_draft(
        "a@example.com", "Subject", "Body text", thread_id="thread-1", resource=resource
    )

    assert result["id"] == "draft-1"
    draft_message = resource.users().drafts().calls[0][1]["body"]["message"]
    decoded = message_from_bytes(
        base64.urlsafe_b64decode(draft_message["raw"]), policy=policy.default
    )
    assert draft_message["threadId"] == "thread-1"
    assert decoded["To"] == "a@example.com"
    assert decoded["Subject"] == "Subject"
    assert decoded.get_content().strip() == "Body text"


def test_forward_message_respects_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    assert forward_message("msg-1", "bob@example.com", "FYI") == {
        "dry_run": True,
        "action": "forward_message",
        "message_id": "msg-1",
        "to": "bob@example.com",
    }


def test_forward_message_fetches_original_and_sends_encoded_forward(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    original = _message(
        HEADERS + [
            {"name": "Date", "value": "Tue"},
            {"name": "Message-ID", "value": "<msg-1@example.com>"},
        ],
        {"body": {"data": _b64("Original body")}},
        msg_id="msg-1",
        thread_id="thread-1",
    )
    resource = _FakeGmailResource(messages={"msg-1": original})

    result = forward_message("msg-1", "bob@example.com", "Please see below", resource=resource)

    assert result["id"] == "sent-1"
    calls = resource.users().messages().calls
    assert calls[0] == ("get", {"userId": "me", "id": "msg-1"})
    assert calls[1][0] == "send"
    sent = calls[1][1]["body"]
    decoded = message_from_bytes(base64.urlsafe_b64decode(sent["raw"]), policy=policy.default)
    assert decoded["To"] == "bob@example.com"
    assert decoded["Subject"] == "Fwd: Quick question"
    content = decoded.get_content()
    assert "Please see below" in content
    assert "Forwarded message" in content
    assert "Original body" in content


def test_reply_all_message_respects_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    assert reply_all_message("msg-1", "Reply body") == {
        "dry_run": True,
        "action": "reply_all_message",
        "message_id": "msg-1",
    }


def test_reply_all_message_fetches_original_and_sends_thread_reply(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    original = _message(
        [
            {"name": "From", "value": "Alice <alice@example.com>"},
            {"name": "To", "value": "Me <me@example.com>"},
            {"name": "Cc", "value": "Carol <carol@example.com>"},
            {"name": "Subject", "value": "Re: Quick question"},
            {"name": "Message-ID", "value": "<msg-1@example.com>"},
        ],
        {"body": {"data": _b64("Original body")}},
        msg_id="msg-1",
        thread_id="thread-1",
    )
    resource = _FakeGmailResource(messages={"msg-1": original})

    result = reply_all_message("msg-1", "Reply body", resource=resource)

    assert result["id"] == "sent-1"
    calls = resource.users().messages().calls
    assert calls[0] == ("get", {"userId": "me", "id": "msg-1"})
    assert calls[1][0] == "send"
    sent = calls[1][1]["body"]
    assert sent["threadId"] == "thread-1"
    decoded = message_from_bytes(base64.urlsafe_b64decode(sent["raw"]), policy=policy.default)
    # me@example.com (the account itself) is excluded from reply-all recipients.
    assert decoded["To"] == "alice@example.com, carol@example.com"
    assert decoded["Subject"] == "Re: Quick question"
    assert decoded["In-Reply-To"] == "<msg-1@example.com>"
    assert decoded["References"] == "<msg-1@example.com>"
    assert decoded.get_content().strip() == "Reply body"


# --- _extract_message_part ---

def test_extract_simple_plain_body():
    payload = {"body": {"data": _b64("hello world")}}
    assert _extract_message_part(payload) == "hello world"


def test_extract_prefers_text_plain_over_html():
    payload = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("plain text")}},
        ]
    }
    assert _extract_message_part(payload) == "plain text"


def test_extract_falls_back_to_html():
    payload = {"parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>only html</p>")}}]}
    assert _extract_message_part(payload) == "<p>only html</p>"


def test_extract_recurses_into_nested_multipart():
    payload = {
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/plain", "body": {"data": _b64("nested body")}}],
            }
        ]
    }
    assert _extract_message_part(payload) == "nested body"


def test_extract_returns_empty_when_no_body():
    assert _extract_message_part({"mimeType": "image/png"}) == ""


# --- gmail_to_email_input ---

def test_gmail_to_email_input_maps_all_fields():
    msg = _message(HEADERS, {"body": {"data": _b64("the body text")}}, msg_id="abc", thread_id="thr9")
    result = gmail_to_email_input(msg)
    assert result == {
        "author": "Alice <alice@example.com>",
        "to": "Me <me@example.com>",
        "subject": "Quick question",
        "email_thread": "the body text",
        "email_id": "abc",
        "gmail_thread_id": "thr9",
        "attachments": [],
        "labels": [],
    }


def test_gmail_to_email_input_defaults_missing_headers():
    msg = _message([], {"body": {"data": _b64("body")}}, msg_id="x", thread_id="y")
    result = gmail_to_email_input(msg)
    assert result["author"] == "Unknown Sender"
    assert result["to"] == "Unknown Recipient"
    assert result["subject"] == "No Subject"


# --- format_thread / thread-aware mapping (S10) ---

def _thread_msg(author: str, date: str, body: str, internal_date: str | None = None) -> dict:
    msg = {
        "payload": {
            "headers": [
                {"name": "From", "value": author},
                {"name": "Date", "value": date},
            ],
            "body": {"data": _b64(body)},
        }
    }
    if internal_date is not None:
        msg["internalDate"] = internal_date
    return msg


def test_format_thread_renders_all_messages_chronologically():
    msgs = [
        _thread_msg("alice@x.com", "Mon", "first message"),
        _thread_msg("me@x.com", "Tue", "my reply"),
        _thread_msg("alice@x.com", "Wed", "follow up"),
    ]
    out = format_thread(msgs)
    assert out.index("first message") < out.index("my reply") < out.index("follow up")
    assert out.count("---") == 2  # two separators between three blocks
    assert "From: alice@x.com" in out and "Date: Tue" in out


def test_format_thread_caps_to_most_recent_messages():
    msgs = [_thread_msg("a@x.com", f"d{i}", f"body {i}") for i in range(5)]
    out = format_thread(msgs, max_messages=2)
    assert "body 0" not in out and "body 2" not in out
    assert "body 3" in out and "body 4" in out


def test_format_thread_truncates_long_bodies():
    msgs = [_thread_msg("a@x.com", "d", "x" * 5000)]
    out = format_thread(msgs, max_chars_per_message=100)
    assert "…[truncated]" in out
    assert len(out) < 500


def test_format_thread_sorts_by_internal_date():
    # Supplied out of order; internalDate must drive chronological rendering.
    msgs = [
        _thread_msg("a@x.com", "Wed", "third", internal_date="3000"),
        _thread_msg("a@x.com", "Mon", "first", internal_date="1000"),
        _thread_msg("a@x.com", "Tue", "second", internal_date="2000"),
    ]
    out = format_thread(msgs)
    assert out.index("first") < out.index("second") < out.index("third")


def test_format_thread_non_positive_cap_means_unlimited():
    msgs = [_thread_msg("a@x.com", f"d{i}", f"body {i}") for i in range(5)]
    out = format_thread(msgs, max_messages=0)
    assert all(f"body {i}" in out for i in range(5))


def test_gmail_to_email_input_uses_full_thread_when_provided():
    trigger = _message(HEADERS, {"body": {"data": _b64("latest message")}}, msg_id="abc", thread_id="thr9")
    thread = [
        _thread_msg("alice@x.com", "Mon", "original question"),
        _thread_msg("me@x.com", "Tue", "my earlier answer"),
    ]
    result = gmail_to_email_input(trigger, thread_messages=thread)
    # Headers still from the triggering message; thread carries prior turns.
    assert result["subject"] == "Quick question"
    assert result["email_id"] == "abc"
    assert "original question" in result["email_thread"]
    assert "my earlier answer" in result["email_thread"]
