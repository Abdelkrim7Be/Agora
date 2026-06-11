from __future__ import annotations

import base64

from src.gmail_client import _extract_message_part, format_thread, gmail_to_email_input


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _message(headers: list[dict], payload: dict, msg_id="m1", thread_id="t1") -> dict:
    return {"id": msg_id, "threadId": thread_id, "payload": {**payload, "headers": headers}}


HEADERS = [
    {"name": "From", "value": "Alice <alice@example.com>"},
    {"name": "To", "value": "Me <me@example.com>"},
    {"name": "Subject", "value": "Quick question"},
]


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
