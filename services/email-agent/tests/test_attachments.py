from __future__ import annotations

import base64
from unittest.mock import MagicMock

import src.gmail_client as gc
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


# --- extract_pdf_text (mocked pypdf) ---

def test_extract_pdf_text_returns_page_text(monkeypatch):
    """extract_pdf_text joins text from all pages."""
    mock_pypdf = MagicMock()
    mock_reader = MagicMock()
    mock_reader.pages = [
        MagicMock(extract_text=MagicMock(return_value="Page one content")),
        MagicMock(extract_text=MagicMock(return_value="Page two content")),
    ]
    mock_pypdf.PdfReader.return_value = mock_reader
    monkeypatch.setattr(gc, "_pypdf", mock_pypdf)

    result = gc.extract_pdf_text(b"fake pdf bytes", max_chars=1000)
    assert "Page one content" in result
    assert "Page two content" in result


def test_extract_pdf_text_truncates_at_max_chars(monkeypatch):
    mock_pypdf = MagicMock()
    mock_reader = MagicMock()
    mock_reader.pages = [MagicMock(extract_text=MagicMock(return_value="x" * 5000))]
    mock_pypdf.PdfReader.return_value = mock_reader
    monkeypatch.setattr(gc, "_pypdf", mock_pypdf)

    result = gc.extract_pdf_text(b"fake", max_chars=100)
    assert "…[truncated]" in result
    assert len(result) < 200


def _build_pdf(text: str) -> bytes:
    """Build a minimal but valid single-page PDF with extractable text."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\nBT /F1 24 Tf 72 700 Td (%s) Tj ET\nendstream"
        % (len(text) + 24, text.encode()),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Root 1 0 R /Size %d >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_pos,
    )
    return out


def test_extract_pdf_text_real_pdf():
    """Exercise real pypdf parsing (not mocked) so API drift is caught."""
    pdf_bytes = _build_pdf("Invoice total 500 EUR")
    result = gc.extract_pdf_text(pdf_bytes, max_chars=1000)
    assert "Invoice total 500 EUR" in result
