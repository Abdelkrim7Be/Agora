from __future__ import annotations

import base64

from src.gmail_client import extract_attachments, format_attachments, gmail_to_email_input
from src.utils import format_email_markdown


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode("ascii")


# --- extract_attachments ---

def test_extract_attachments_finds_pdf():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "filename": "", "body": {"data": _b64("body")}},
            {
                "mimeType": "application/pdf",
                "filename": "invoice.pdf",
                "body": {"attachmentId": "att_abc", "size": 86016},
            },
        ],
    }
    result = extract_attachments(payload)
    assert len(result) == 1
    assert result[0]["filename"] == "invoice.pdf"
    assert result[0]["mime_type"] == "application/pdf"
    assert result[0]["size"] == 86016
    assert result[0]["attachment_id"] == "att_abc"


def test_extract_attachments_nested_multipart():
    """Attachment nested inside multipart/related inside multipart/mixed."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/related",
                "parts": [
                    {
                        "mimeType": "application/zip",
                        "filename": "archive.zip",
                        "body": {"attachmentId": "att_zip", "size": 1024},
                    }
                ],
            }
        ],
    }
    result = extract_attachments(payload)
    assert len(result) == 1
    assert result[0]["filename"] == "archive.zip"


def test_extract_attachments_no_attachments():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("plain text")}}
    assert extract_attachments(payload) == []


def test_extract_attachments_skips_empty_filename():
    """Inline parts (no filename or empty filename) must not appear in results."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "filename": "", "body": {"data": _b64("plain")}},
            {"mimeType": "text/html", "filename": "", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert extract_attachments(payload) == []


# --- format_attachments ---

def test_format_attachments_renders_size_in_kb():
    atts = [
        {"filename": "invoice.pdf", "mime_type": "application/pdf", "size": 86016, "attachment_id": "x"}
    ]
    result = format_attachments(atts)
    assert "invoice.pdf" in result
    assert "application/pdf" in result
    assert "KB" in result


def test_format_attachments_multiple_comma_separated():
    atts = [
        {"filename": "a.pdf", "mime_type": "application/pdf", "size": 1024, "attachment_id": "a"},
        {"filename": "b.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size": 512, "attachment_id": "b"},
    ]
    result = format_attachments(atts)
    assert "a.pdf" in result and "b.docx" in result
    assert ", " in result


def test_format_attachments_empty_returns_empty_string():
    assert format_attachments([]) == ""


# --- gmail_to_email_input includes attachments ---

def test_gmail_to_email_input_includes_attachments():
    message = {
        "id": "m1",
        "threadId": "t1",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Invoice"},
            ],
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": _b64("see attached")}},
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice.pdf",
                    "body": {"attachmentId": "att_x", "size": 5000},
                },
            ],
        },
    }
    result = gmail_to_email_input(message)
    assert "attachments" in result
    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["filename"] == "invoice.pdf"


def test_gmail_to_email_input_no_attachments_gives_empty_list():
    message = {
        "id": "m2",
        "threadId": "t2",
        "payload": {
            "headers": [
                {"name": "From", "value": "bob@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Hey"},
            ],
            "body": {"data": _b64("hello")},
        },
    }
    result = gmail_to_email_input(message)
    assert result["attachments"] == []


# --- format_email_markdown with attachments ---

def test_format_email_markdown_includes_attachments_line():
    atts = [{"filename": "doc.pdf", "mime_type": "application/pdf", "size": 2048, "attachment_id": "a"}]
    out = format_email_markdown("Test Subject", "from@x.com", "to@x.com", "body text", attachments=atts)
    assert "**Attachments**:" in out
    assert "doc.pdf" in out


def test_format_email_markdown_omits_attachments_line_when_none():
    out = format_email_markdown("Test Subject", "from@x.com", "to@x.com", "body text")
    assert "**Attachments**:" not in out


def test_format_email_markdown_omits_attachments_line_when_empty_list():
    out = format_email_markdown("Test Subject", "from@x.com", "to@x.com", "body text", attachments=[])
    assert "**Attachments**:" not in out
