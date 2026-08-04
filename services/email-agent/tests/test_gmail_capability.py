import pytest

from src.capabilities import current_email_id, hitl_approved
from src.capabilities.email_tools import forward_email, notify_internal, reply_all, write_email
from tests.conftest import patch_provider, reply_to, route_targets


def test_write_email_dry_run():
    """With AGENT_DRY_RUN=true (default), write_email returns a dry-run string without touching Gmail."""
    with reply_to("test@example.com"):
        result = write_email.invoke({"subject": "hi", "content": "body"})
    assert "Email sent to test@example.com" in result
    assert "Simulé" in result


def test_write_email_live_path_invokes_rich_gmail_helper_after_approval(monkeypatch):
    from src.capabilities import email_tools

    calls: list[dict] = []
    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    patch_provider(
        monkeypatch,
        email_tools,
        send_message=lambda to, subject, body: calls.append({"to": to, "subject": subject, "body": body})
        or {"id": "sent-write"},
    )

    approval_token = hitl_approved.set(True)
    try:
        with reply_to("client@example.com"):
            result = write_email.invoke({
                "subject": "Re: hello",
                "content": "Bonjour,\n\n- Point A\n- Point B",
            })
    finally:
        hitl_approved.reset(approval_token)

    assert result == "Email sent to client@example.com with subject 'Re: hello' (message id: sent-write)"
    assert calls == [
        {
            "to": "client@example.com",
            "subject": "Re: hello",
            "body": "Bonjour,\n\n- Point A\n- Point B",
        }
    ]


def test_send_tool_schemas_expose_no_recipient():
    """No send tool takes a recipient argument.

    This is the structural half of the injection defence: whatever an email
    tells the model to do, there is no argument through which it can name a
    destination. Recipients come from graph context only.
    """
    assert set(forward_email.args_schema.model_json_schema()["properties"]) == {"note"}
    assert set(reply_all.args_schema.model_json_schema()["properties"]) == {"content"}
    assert set(write_email.args_schema.model_json_schema()["properties"]) == {"subject", "content"}


def test_forward_email_requires_context_email_id():
    with pytest.raises(RuntimeError, match="trusted email_id"):
        with route_targets("a@example.com"):

            forward_email.invoke({"note": "FYI"})


def test_reply_all_requires_context_email_id():
    with pytest.raises(RuntimeError, match="trusted email_id"):
        reply_all.invoke({"content": "Thanks"})


def test_forward_email_dry_run_uses_context_email_id(monkeypatch):
    token = current_email_id.set("msg-1")
    try:
        with route_targets("a@example.com"):

            result = forward_email.invoke({"note": "FYI"})
    finally:
        current_email_id.reset(token)

    assert result == "Forwarded current email to a@example.com (Simulé — aucun e-mail réel envoyé)"


def test_reply_all_dry_run_uses_context_email_id():
    token = current_email_id.set("msg-1")
    try:
        result = reply_all.invoke({"content": "Thanks"})
    finally:
        current_email_id.reset(token)

    assert result == "Reply-all sent on the current thread (Simulé — aucun e-mail réel envoyé)"


def test_forward_email_live_path_requires_human_approval(monkeypatch):
    from src.capabilities import email_tools

    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    email_token = current_email_id.set("msg-1")
    try:
        with pytest.raises(RuntimeError, match="requires human approval"):
            with route_targets("a@example.com"):

                forward_email.invoke({"note": "FYI"})
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
    patch_provider(
        monkeypatch,
        email_tools,
        forward_message=lambda message_id, to, note: calls.append({"message_id": message_id, "to": to, "note": note})
        or {"id": "sent-forward"},
    )

    email_token = current_email_id.set("msg-1")
    approval_token = hitl_approved.set(True)
    try:
        with route_targets("a@example.com"):

            result = forward_email.invoke({"note": "FYI"})
    finally:
        hitl_approved.reset(approval_token)
        current_email_id.reset(email_token)

    assert result == "Forwarded current email to a@example.com (message ids: sent-forward)"
    assert calls == [{"message_id": "msg-1", "to": "a@example.com", "note": "FYI"}]


def test_reply_all_live_path_invokes_gmail_helper_after_approval(monkeypatch):
    from src.capabilities import email_tools

    calls: list[dict] = []
    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    patch_provider(
        monkeypatch,
        email_tools,
        reply_all_message=lambda message_id, body: calls.append({"message_id": message_id, "body": body})
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


def test_notify_internal_schema_has_no_email_id_or_recipient():
    assert set(notify_internal.args_schema.model_json_schema()["properties"]) == {"subject", "note"}


def test_notify_internal_does_not_require_context_email_id():
    """Unlike forward_email, notify_internal never re-fetches the original message,
    so it must not need a trusted email_id at all — this is what lets it work for
    manually-submitted runs, not just Gmail-sourced ones."""
    with route_targets("ops@example.com"):

        result = notify_internal.invoke({"subject": "Route", "note": "FYI"})
    assert result == "Notified ops@example.com (Simulé — aucun e-mail réel envoyé)"


def test_notify_internal_live_path_requires_human_approval(monkeypatch):
    from src.capabilities import email_tools

    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    with pytest.raises(RuntimeError, match="requires human approval"):
        with route_targets("ops@example.com"):

            notify_internal.invoke({"subject": "Route", "note": "FYI"})


def test_notify_internal_live_path_invokes_gmail_helper_after_approval(monkeypatch):
    from src.capabilities import email_tools

    calls: list[dict] = []
    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    patch_provider(
        monkeypatch,
        email_tools,
        notify_internal_message=lambda to, subject, note: calls.append({"to": to, "subject": subject, "note": note})
        or {"id": "sent-notify"},
    )

    approval_token = hitl_approved.set(True)
    try:
        with route_targets("ops@example.com"):

            result = notify_internal.invoke({"subject": "Route", "note": "FYI"})
    finally:
        hitl_approved.reset(approval_token)

    assert result == "Notified ops@example.com (message id: sent-notify)"
    assert calls == [{"to": ["ops@example.com"], "subject": "Route", "note": "FYI"}]
