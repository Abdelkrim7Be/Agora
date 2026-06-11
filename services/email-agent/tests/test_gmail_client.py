from __future__ import annotations

import base64

from src.gmail_client import _extract_message_part, gmail_to_email_input


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
