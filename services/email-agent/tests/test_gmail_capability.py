import pytest

from src.capabilities import current_email_id, hitl_approved
from src.capabilities.email_tools import forward_email, reply_all, write_email


def test_write_email_dry_run():
    """With AGENT_DRY_RUN=true (default), write_email returns a dry-run string without touching Gmail."""
    result = write_email.invoke({"to": "test@example.com", "subject": "hi", "content": "body"})
    assert "Email sent to test@example.com" in result
    assert "dry run" in result


def test_forward_and_reply_all_schema_do_not_expose_email_id():
    assert set(forward_email.args_schema.model_json_schema()["properties"]) == {"to", "note"}
    assert set(reply_all.args_schema.model_json_schema()["properties"]) == {"content"}


def test_forward_email_requires_context_email_id():
    with pytest.raises(RuntimeError, match="trusted email_id"):
        forward_email.invoke({"to": "a@example.com", "note": "FYI"})


def test_reply_all_requires_context_email_id():
    with pytest.raises(RuntimeError, match="trusted email_id"):
        reply_all.invoke({"content": "Thanks"})


def test_forward_email_dry_run_uses_context_email_id(monkeypatch):
    token = current_email_id.set("msg-1")
    try:
        result = forward_email.invoke({"to": "a@example.com", "note": "FYI"})
    finally:
        current_email_id.reset(token)

    assert result == "Forwarded current email to a@example.com [dry run]"


def test_reply_all_dry_run_uses_context_email_id():
    token = current_email_id.set("msg-1")
    try:
        result = reply_all.invoke({"content": "Thanks"})
    finally:
        current_email_id.reset(token)

    assert result == "Reply-all sent on the current thread [dry run]"


def test_forward_email_live_path_requires_human_approval(monkeypatch):
    from src.capabilities import email_tools

    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    email_token = current_email_id.set("msg-1")
    try:
        with pytest.raises(RuntimeError, match="requires human approval"):
            forward_email.invoke({"to": "a@example.com", "note": "FYI"})
    finally:
        current_email_id.reset(email_token)


def test_reply_all_live_path_requires_human_approval(monkeypatch):
    from src.capabilities import email_tools

    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    email_token = current_email_id.set("msg-1")
    try:
        with pytest.raises(RuntimeError, match="requires human approval"):
            reply_all.invoke({"content": "Thanks"})
    finally:
        current_email_id.reset(email_token)


def test_forward_email_live_path_invokes_gmail_helper_after_approval(monkeypatch):
    from src.capabilities import email_tools

    calls: list[dict] = []
    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    monkeypatch.setattr(
        "src.gmail_client.forward_message",
        lambda message_id, to, note: calls.append({"message_id": message_id, "to": to, "note": note})
        or {"id": "sent-forward"},
    )

    email_token = current_email_id.set("msg-1")
    approval_token = hitl_approved.set(True)
    try:
        result = forward_email.invoke({"to": "a@example.com", "note": "FYI"})
    finally:
        hitl_approved.reset(approval_token)
        current_email_id.reset(email_token)

    assert result == "Forwarded current email to a@example.com (message id: sent-forward)"
    assert calls == [{"message_id": "msg-1", "to": "a@example.com", "note": "FYI"}]


def test_reply_all_live_path_invokes_gmail_helper_after_approval(monkeypatch):
    from src.capabilities import email_tools

    calls: list[dict] = []
    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    monkeypatch.setattr(
        "src.gmail_client.reply_all_message",
        lambda message_id, body: calls.append({"message_id": message_id, "body": body})
        or {"id": "sent-reply"},
    )

    email_token = current_email_id.set("msg-1")
    approval_token = hitl_approved.set(True)
    try:
        result = reply_all.invoke({"content": "Thanks"})
    finally:
        hitl_approved.reset(approval_token)
        current_email_id.reset(email_token)

    assert result == "Reply-all sent on the current thread (message id: sent-reply)"
    assert calls == [{"message_id": "msg-1", "body": "Thanks"}]
