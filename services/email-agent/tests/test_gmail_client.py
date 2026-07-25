from __future__ import annotations

import base64
from email import message_from_bytes, policy

from src.config import settings
from src.gmail_client import (
    _extract_message_part,
    archive_message,
    create_draft,
    ensure_label,
    fetch_history_message_refs,
    fetch_messages_batch,
    fetch_sent,
    forward_message,
    format_thread,
    gmail_to_email_input,
    list_inbox,
    list_labels,
    mark_as_read,
    mark_as_unread,
    modify_labels,
    notify_internal_message,
    reply_all_message,
    render_rich_email_html,
    send_message,
    trash_message,
    watch_mailbox,
)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _message(headers: list[dict], payload: dict, msg_id="m1", thread_id="t1") -> dict:
    return {"id": msg_id, "threadId": thread_id, "payload": {**payload, "headers": headers}}


def _decoded(raw: str):
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


def _plain_body(message) -> str:
    part = message.get_body(preferencelist=("plain",)) if message.is_multipart() else message
    return part.get_content().strip()


def _html_body(message) -> str:
    part = message.get_body(preferencelist=("html",))
    return part.get_content() if part else ""


class _Execute:
    def __init__(self, response: dict):
        self.response = response

    def execute(self) -> dict:
        return self.response


class _FakeMessages:
    def __init__(self, messages: dict[str, dict] | None = None, refs: list[dict] | None = None):
        self.messages = messages or {}
        self.refs = refs
        self.calls: list[tuple[str, dict]] = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        refs = self.refs if self.refs is not None else [
            {"id": msg_id, "threadId": message.get("threadId")}
            for msg_id, message in self.messages.items()
        ]
        return _Execute({"messages": refs})

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


class _FakeHistory:
    def __init__(self, pages: list[dict] | None = None):
        self.pages = pages or []
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("pageToken"):
            index = int(kwargs["pageToken"])
        else:
            index = 0
        return _Execute(self.pages[index] if index < len(self.pages) else {})


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
        history_pages: list[dict] | None = None,
        message_refs: list[dict] | None = None,
    ):
        self._messages = _FakeMessages(messages, message_refs)
        self._labels = _FakeLabels(labels)
        self._history = _FakeHistory(history_pages)
        self._drafts = _FakeDrafts()
        self._profile_email = profile_email
        self.watch_calls: list[dict] = []

    def messages(self):
        return self._messages

    def labels(self):
        return self._labels

    def drafts(self):
        return self._drafts

    def history(self):
        return self._history

    def watch(self, **kwargs):
        self.watch_calls.append(kwargs)
        return _Execute({"historyId": "123", "expiration": "999"})

    def getProfile(self, **kwargs):
        return _Execute({"emailAddress": self._profile_email})


class _FakeGmailResource:
    def __init__(
        self,
        labels: list[dict] | None = None,
        messages: dict[str, dict] | None = None,
        profile_email: str = "me@example.com",
        history_pages: list[dict] | None = None,
        message_refs: list[dict] | None = None,
    ):
        self._users = _FakeUsers(labels, messages, profile_email, history_pages, message_refs)

    def users(self):
        return self._users


class _FakeBatch:
    def __init__(self, callback):
        self.callback = callback
        self.requests: list[tuple[str, _Execute]] = []

    def add(self, request, request_id: str):
        self.requests.append((request_id, request))

    def execute(self):
        for request_id, request in self.requests:
            self.callback(request_id, request.execute(), None)


class _FakeBatchGmailResource(_FakeGmailResource):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch_calls = 0

    def new_batch_http_request(self, callback=None):
        self.batch_calls += 1
        return _FakeBatch(callback)


HEADERS = [
    {"name": "From", "value": "Alice <alice@example.com>"},
    {"name": "To", "value": "Me <me@example.com>"},
    {"name": "Subject", "value": "Quick question"},
]


# --- fetch_sent / style sampling ---

def test_fetch_sent_returns_usable_style_samples():
    messages = {
        "m1": _message(
            [
                {"name": "To", "value": "Pat <pat@example.com>"},
                {"name": "Subject", "value": "Project follow-up"},
                {"name": "Date", "value": "Mon, 1 Jun 2026 10:00:00 +0000"},
            ],
            {"body": {"data": _b64("Hi Pat, thanks for the thoughtful notes. I will review and follow up tomorrow. Best, A")}},
            msg_id="m1",
            thread_id="t1",
        ),
        "m2": _message(
            [{"name": "To", "value": "noreply@example.com"}, {"name": "Subject", "value": "Receipt"}],
            {"body": {"data": _b64("This longer automated recipient message should be skipped by sampling.")}},
            msg_id="m2",
            thread_id="t2",
        ),
        "m3": _message(
            [{"name": "To", "value": "Sam <sam@example.com>"}, {"name": "Subject", "value": "Short"}],
            {"body": {"data": _b64("Thanks")}},
            msg_id="m3",
            thread_id="t3",
        ),
    }
    resource = _FakeGmailResource(messages=messages)

    samples = fetch_sent(max_messages=10, resource=resource)

    assert samples == [
        {
            "id": "m1",
            "thread_id": "t1",
            "to": "Pat <pat@example.com>",
            "subject": "Project follow-up",
            "date": "Mon, 1 Jun 2026 10:00:00 +0000",
            "body": "Hi Pat, thanks for the thoughtful notes. I will review and follow up tomorrow. Best, A",
        }
    ]
    assert resource.users().messages().calls[0] == (
        "list",
        {"userId": "me", "q": "in:sent", "maxResults": 10},
    )


def test_fetch_messages_batch_returns_full_messages_in_one_call():
    messages = {
        "m1": _message([{"name": "Subject", "value": "One"}], {}, msg_id="m1", thread_id="t1"),
        "m2": _message([{"name": "Subject", "value": "Two"}], {}, msg_id="m2", thread_id="t2"),
    }
    resource = _FakeBatchGmailResource(messages=messages)

    result = fetch_messages_batch(["m1", "m2"], resource=resource)

    assert resource.batch_calls == 1
    assert set(result) == {"m1", "m2"}
    assert result["m1"]["id"] == "m1"


def test_fetch_messages_batch_chunks_large_id_lists():
    messages = {f"m{i}": _message([], {}, msg_id=f"m{i}", thread_id=f"t{i}") for i in range(5)}
    resource = _FakeBatchGmailResource(messages=messages)

    result = fetch_messages_batch(list(messages), resource=resource, chunk=2)

    assert resource.batch_calls == 3  # 2 + 2 + 1
    assert set(result) == set(messages)


# --- Gmail mutation helpers ---

def test_list_inbox_uses_batch_metadata_fetch():
    messages = {
        "m1": _message(
            [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Mon, 1 Jun 2026 10:00:00 +0000"},
            ],
            {},
            msg_id="m1",
            thread_id="t1",
        )
        | {"labelIds": ["INBOX", "UNREAD"], "snippet": "First"},
        "m2": _message(
            [
                {"name": "From", "value": "Bob <bob@example.com>"},
                {"name": "Subject", "value": "Update"},
                {"name": "Date", "value": "Tue, 2 Jun 2026 10:00:00 +0000"},
            ],
            {},
            msg_id="m2",
            thread_id="t2",
        )
        | {"labelIds": ["INBOX"], "snippet": "Second"},
    }
    resource = _FakeBatchGmailResource(messages=messages)

    rows = list_inbox(10, resource=resource)

    assert resource.batch_calls == 1
    assert rows == [
        {
            "id": "m1",
            "thread_id": "t1",
            "from": "Alice <alice@example.com>",
            "subject": "Hello",
            "snippet": "First",
            "date": "Mon, 1 Jun 2026 10:00:00 +0000",
            "unread": True,
        },
        {
            "id": "m2",
            "thread_id": "t2",
            "from": "Bob <bob@example.com>",
            "subject": "Update",
            "snippet": "Second",
            "date": "Tue, 2 Jun 2026 10:00:00 +0000",
            "unread": False,
        },
    ]


def test_list_inbox_falls_back_to_serial_metadata_fetch():
    messages = {
        "m1": _message(
            [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Mon, 1 Jun 2026 10:00:00 +0000"},
            ],
            {},
            msg_id="m1",
            thread_id="t1",
        )
        | {"labelIds": ["INBOX"], "snippet": "First"},
    }
    resource = _FakeGmailResource(messages=messages)

    assert list_inbox(10, resource=resource) == [
        {
            "id": "m1",
            "thread_id": "t1",
            "from": "Alice <alice@example.com>",
            "subject": "Hello",
            "snippet": "First",
            "date": "Mon, 1 Jun 2026 10:00:00 +0000",
            "unread": False,
        }
    ]


def test_watch_mailbox_registers_inbox_watch(monkeypatch):
    monkeypatch.setattr(settings, "gmail_webhook_topic", "projects/agora/topics/gmail")
    resource = _FakeGmailResource()

    result = watch_mailbox(resource=resource)

    assert result == {"historyId": "123", "expiration": "999"}
    assert resource.users().watch_calls == [
        {
            "userId": "me",
            "body": {
                "topicName": "projects/agora/topics/gmail",
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        }
    ]


def test_fetch_history_message_refs_paginates_and_deduplicates():
    pages = [
        {
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "m1", "threadId": "t1"}},
                        {"message": {"id": "m2", "threadId": "t2"}},
                    ]
                }
            ],
            "nextPageToken": "1",
        },
        {
            "history": [
                {
                    "labelsAdded": [
                        {"message": {"id": "m2", "threadId": "t2"}},
                        {"message": {"id": "m3", "threadId": "t3"}},
                    ]
                }
            ]
        },
    ]
    resource = _FakeGmailResource(history_pages=pages)

    assert fetch_history_message_refs("42", resource=resource) == [
        {"id": "m1", "threadId": "t1"},
        {"id": "m2", "threadId": "t2"},
        {"id": "m3", "threadId": "t3"},
    ]
    assert resource.users().history().calls[0]["startHistoryId"] == "42"
    assert resource.users().history().calls[1]["pageToken"] == "1"


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
    decoded = _decoded(draft_message["raw"])
    assert draft_message["threadId"] == "thread-1"
    assert decoded["To"] == "a@example.com"
    assert decoded["Subject"] == "Subject"
    assert decoded.is_multipart()
    assert _plain_body(decoded) == "Body text"
    assert "<p>Body text</p>" in _html_body(decoded)


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
    decoded = _decoded(sent["raw"])
    assert decoded["To"] == "bob@example.com"
    assert decoded["Subject"] == "Fwd: Quick question"
    content = _plain_body(decoded)
    assert "Please see below" in content
    assert "Forwarded message" in content
    assert "Original body" in content
    assert "<br" in _html_body(decoded) or "<p>" in _html_body(decoded)


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
    decoded = _decoded(sent["raw"])
    # me@example.com (the account itself) is excluded from reply-all recipients.
    assert decoded["To"] == "alice@example.com, carol@example.com"
    assert decoded["Subject"] == "Re: Quick question"
    assert decoded["In-Reply-To"] == "<msg-1@example.com>"
    assert decoded["References"] == "<msg-1@example.com>"
    assert decoded.is_multipart()
    assert _plain_body(decoded) == "Reply body"
    assert "<p>Reply body</p>" in _html_body(decoded)


def test_notify_internal_message_respects_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)

    assert notify_internal_message("ops@example.com", "Route", "FYI") == {
        "dry_run": True,
        "action": "notify_internal_message",
        "to": "ops@example.com",
        "subject": "Route",
    }


def test_notify_internal_message_never_fetches_the_original_message(monkeypatch):
    """The whole point of notify_internal vs forward_email: it sends only the
    caller-supplied note, never re-fetching (and re-sending) the original message."""
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource(messages={})

    result = notify_internal_message("ops@example.com", "Route", "Please handle this.", resource=resource)

    assert result["id"] == "sent-1"
    calls = resource.users().messages().calls
    assert calls == [calls[0]]
    assert calls[0][0] == "send"
    sent = calls[0][1]["body"]
    decoded = _decoded(sent["raw"])
    assert decoded["To"] == "ops@example.com"
    assert decoded["Subject"] == "Route"
    assert _plain_body(decoded) == "Please handle this."


def test_render_rich_email_html_preserves_markdown_structure():
    html = render_rich_email_html("Bonjour,\n\n- Point A\n- Point B\n\n**Merci**")

    assert "<li>Point A</li>" in html
    assert "<li>Point B</li>" in html
    assert "<strong>Merci</strong>" in html
    assert "font-family" in html


def test_send_message_sends_multipart_rich_email(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    resource = _FakeGmailResource()

    result = send_message(
        "client@example.com",
        "Follow-up",
        "Bonjour,\n\nVoici les points :\n\n- A\n- B",
        resource=resource,
    )

    assert result["id"] == "sent-1"
    sent = resource.users().messages().calls[0][1]["body"]
    decoded = _decoded(sent["raw"])
    assert decoded["To"] == "client@example.com"
    assert decoded["Subject"] == "Follow-up"
    assert decoded.is_multipart()
    assert "Voici les points" in _plain_body(decoded)
    assert "<li>A</li>" in _html_body(decoded)
    assert "<li>B</li>" in _html_body(decoded)


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
        "list_unsubscribe": False,
        "precedence_bulk": False,
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
