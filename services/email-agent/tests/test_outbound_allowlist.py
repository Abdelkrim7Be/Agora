"""The last-resort outbound recipient guard in the Gmail send helpers.

AGENT_OUTBOUND_ALLOWLIST sits below the security service, below dry-run and below
any model decision: it is checked inside the functions that actually hand a
message to the Gmail API. These tests pin that a live test run cannot reach a
mailbox outside the configured set no matter which send path is taken.
"""

from __future__ import annotations

import pytest

from src import gmail_client
from src.config import settings


ALLOWED = "agora.test.inbox@gmail.com"
OTHER_ALLOWED = "thomas.lefevre@example.com"
STRANGER = "someone.else@gmail.com"


class _RecordingResource:
    """Stands in for the Gmail API resource and records what was sent."""

    def __init__(self):
        self.sent: list[dict] = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):  # noqa: N803 - Google client kwarg name
        self.sent.append(body)
        return self

    def execute(self):
        return {"id": "sent-1"}


@pytest.fixture
def allowlisted(monkeypatch):
    monkeypatch.setattr(settings, "outbound_allowlist", frozenset({ALLOWED, OTHER_ALLOWED}))
    monkeypatch.setattr(settings, "dry_run", False)


def test_send_to_an_allowlisted_address_goes_through(allowlisted, monkeypatch):
    resource = _RecordingResource()
    monkeypatch.setattr(gmail_client, "record_gmail_call", lambda *a, **k: None)

    gmail_client._send_email_message(
        to=ALLOWED, subject="Test", body="Hello", resource=resource
    )

    assert len(resource.sent) == 1


def test_send_to_an_unlisted_address_is_blocked(allowlisted, monkeypatch):
    resource = _RecordingResource()
    monkeypatch.setattr(gmail_client, "record_gmail_call", lambda *a, **k: None)

    with pytest.raises(gmail_client.OutboundRecipientBlocked) as exc:
        gmail_client._send_email_message(
            to=STRANGER, subject="Test", body="Hello", resource=resource
        )

    assert STRANGER in str(exc.value)
    assert resource.sent == []


def test_one_unlisted_recipient_blocks_the_whole_send(allowlisted, monkeypatch):
    # reply_all derives a recipient list from the thread; a single outsider on it
    # must stop the send rather than being quietly dropped.
    resource = _RecordingResource()
    monkeypatch.setattr(gmail_client, "record_gmail_call", lambda *a, **k: None)

    with pytest.raises(gmail_client.OutboundRecipientBlocked):
        gmail_client._send_email_message(
            to=[ALLOWED, STRANGER], subject="Test", body="Hello", resource=resource
        )

    assert resource.sent == []


def test_display_name_form_is_matched_on_the_address(allowlisted, monkeypatch):
    resource = _RecordingResource()
    monkeypatch.setattr(gmail_client, "record_gmail_call", lambda *a, **k: None)

    gmail_client._send_email_message(
        to=f"Agora Test <{ALLOWED}>", subject="Test", body="Hello", resource=resource
    )
    assert len(resource.sent) == 1

    with pytest.raises(gmail_client.OutboundRecipientBlocked):
        gmail_client._send_email_message(
            to=f"Someone Else <{STRANGER}>", subject="Test", body="Hello", resource=resource
        )


def test_campaign_html_send_is_guarded_too(allowlisted, monkeypatch):
    # send_html_message builds its own MIME message instead of delegating to
    # _send_email_message, so it needs its own check.
    resource = _RecordingResource()
    monkeypatch.setattr(gmail_client, "record_gmail_call", lambda *a, **k: None)

    with pytest.raises(gmail_client.OutboundRecipientBlocked):
        gmail_client.send_html_message(
            to=STRANGER,
            subject="Campaign",
            html="<p>Hi</p>",
            text="Hi",
            resource=resource,
            respect_dry_run=False,
        )

    assert resource.sent == []


def test_empty_allowlist_leaves_sending_unrestricted(monkeypatch):
    # Production default: the guard is inert unless explicitly configured.
    monkeypatch.setattr(settings, "outbound_allowlist", frozenset())
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(gmail_client, "record_gmail_call", lambda *a, **k: None)
    resource = _RecordingResource()

    gmail_client._send_email_message(
        to="anyone@anywhere.com", subject="Test", body="Hello", resource=resource
    )

    assert len(resource.sent) == 1
