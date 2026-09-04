from __future__ import annotations

import pytest

from src import notifications
from src.config import settings
from tests.conftest import patch_provider


@pytest.fixture(autouse=True)
def _enable_notify(monkeypatch):
    monkeypatch.setattr(settings, "notify_enabled", True)
    monkeypatch.setattr(settings, "notify_app_base_url", "")


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    patch_provider(
        monkeypatch,
        notifications,
        send_message=lambda to, subject, body: calls.append(
            {"to": to, "subject": subject, "body": body}
        )
        or {"id": "sent"},
    )
    return calls


def _result(**overrides) -> dict:
    base = {
        "workflow_owner": "HR",
        "workflow_approver": "hr",
        "workflow_route_to": ["hr"],
        "__interrupt__": [
            type("Interrupt", (), {"value": [{"action_request": {"action": "write_email", "args": {}}}]})()
        ],
    }
    base.update(overrides)
    return base


def test_notify_resolves_role_key_to_directory_email(sent):
    email_input = {"subject": "Question stage", "author": "candidate@example.com"}
    notifications.notify_pending_approval("run-1", email_input, _result())

    assert len(sent) == 1
    assert sent[0]["to"] == "amina.diallo@example.com"


def test_notify_prefers_literal_email_over_role_key(sent):
    email_input = {"subject": "S", "author": "a@b.com"}
    result = _result(workflow_approver="literal@example.com")
    notifications.notify_pending_approval("run-2", email_input, result)

    assert sent[0]["to"] == "literal@example.com"


def test_notify_body_excludes_email_content_and_includes_action_type(sent):
    email_input = {
        "subject": "Confidentiel: salaire",
        "author": "salarie@example.com",
        "email_thread": "SENSITIVE BODY CONTENT should never leak into a notification",
    }
    notifications.notify_pending_approval("run-3", email_input, _result())

    body = sent[0]["body"]
    assert "SENSITIVE BODY CONTENT" not in body
    assert "run-3" in body
    assert "Brouillon de réponse" in sent[0]["subject"] or "Brouillon de réponse" in body
    assert "vers HR" in body


def test_notify_link_uses_app_base_url_when_configured(sent, monkeypatch):
    monkeypatch.setattr(settings, "notify_app_base_url", "https://panel.example.com")
    notifications.notify_pending_approval("run-4", {"subject": "S", "author": "a@b.com"}, _result())

    assert "https://panel.example.com/#run/run-4" in sent[0]["body"]


def test_notify_disabled_sends_nothing(sent, monkeypatch):
    monkeypatch.setattr(settings, "notify_enabled", False)
    notifications.notify_pending_approval("run-5", {"subject": "S", "author": "a@b.com"}, _result())

    assert sent == []


def test_notify_no_resolvable_recipient_sends_nothing(sent):
    result = _result(workflow_approver=None, workflow_route_to=["unknown-role-key"])
    notifications.notify_pending_approval("run-6", {"subject": "S", "author": "a@b.com"}, result)

    assert sent == []


def test_notify_send_failure_does_not_raise(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("smtp exploded")

    patch_provider(monkeypatch, notifications, send_message=_boom)
    # Must not raise — a notification failure can never break approval creation.
    notifications.notify_pending_approval("run-7", {"subject": "S", "author": "a@b.com"}, _result())


def test_forward_action_label_names_the_workflow_owner(sent):
    result = _result(
        workflow_owner="Finance",
        workflow_approver="finance",
        workflow_route_to=["finance"],
        __interrupt__=[
            type("Interrupt", (), {"value": [{"action_request": {"action": "forward_email", "args": {}}}]})()
        ],
    )
    notifications.notify_pending_approval("run-8", {"subject": "Facture", "author": "a@b.com"}, result)

    assert "Transfert vers Finance" in sent[0]["subject"]


def test_overdue_notification_uses_owner_fallback(sent):
    recipient = notifications.notify_overdue_approval(
        "run-9",
        {
            "subject": "Demande urgente",
            "author": "alice@example.com",
            "workflow_approver": None,
            "workflow_owner": "literal-owner@example.com",
        },
        overdue_by_seconds=5400,
        due_at="2026-06-16T10:00:00+00:00",
    )

    assert recipient == "literal-owner@example.com"
    assert sent[0]["to"] == "literal-owner@example.com"
    assert "Escalade SLA" in sent[0]["subject"]
    assert "Retard cumulé" in sent[0]["body"]



def test_notify_admin_alert_routes_through_directory_email(sent):
    delivered = notifications.notify_admin_alert("hr", "Sujet", "Corps")
    assert delivered is True
    assert sent == [{"to": "amina.diallo@example.com", "subject": "Sujet", "body": "Corps"}]
