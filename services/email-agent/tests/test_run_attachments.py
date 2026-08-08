from __future__ import annotations

import pytest

import src.media as media
from src.run_attachments import (
    AttachmentLimitError,
    discard_run_attachments,
    load_attachments,
    save_attachment,
    staged_attachments,
)


@pytest.fixture
def media_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(media.settings, "media_dir", str(tmp_path))
    return tmp_path


def test_save_and_load_attachment_round_trips(media_tmp):
    entry = save_attachment("run-1", "invoice.pdf", "application/pdf", b"file-bytes")

    assert entry["filename"] == "invoice.pdf"
    assert entry["mime_type"] == "application/pdf"
    assert entry["size"] == len(b"file-bytes")
    assert staged_attachments("run-1") == [entry]

    attachments, notes = load_attachments("run-1", [entry["attachment_id"]])
    assert notes == []
    assert attachments == [
        {"filename": "invoice.pdf", "mime_type": "application/pdf", "data": b"file-bytes"}
    ]


def test_load_attachments_notes_missing_id(media_tmp):
    attachments, notes = load_attachments("run-1", ["does-not-exist"])
    assert attachments == []
    assert "not found" in notes[0]


def test_save_attachment_enforces_count_cap(media_tmp, monkeypatch):
    monkeypatch.setattr("src.run_attachments.settings.max_attachment_count", 1)
    save_attachment("run-1", "a.pdf", "application/pdf", b"x")

    with pytest.raises(AttachmentLimitError):
        save_attachment("run-1", "b.pdf", "application/pdf", b"y")


def test_save_attachment_enforces_total_byte_cap(media_tmp, monkeypatch):
    monkeypatch.setattr("src.run_attachments.settings.max_attachment_bytes", 10)
    save_attachment("run-1", "a.bin", "application/octet-stream", b"1234567890")

    with pytest.raises(AttachmentLimitError):
        save_attachment("run-1", "b.bin", "application/octet-stream", b"1")


def test_discard_run_attachments_removes_everything(media_tmp):
    entry = save_attachment("run-1", "a.pdf", "application/pdf", b"data")
    discard_run_attachments("run-1")

    assert staged_attachments("run-1") == []
    attachments, notes = load_attachments("run-1", [entry["attachment_id"]])
    assert attachments == []
    assert notes


def test_discard_run_attachments_is_a_noop_for_unknown_run(media_tmp):
    discard_run_attachments("never-existed")  # must not raise
