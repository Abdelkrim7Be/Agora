from __future__ import annotations

from src.authorize import authorize
from src.models import AuthorizeRequest
from src.policy import load_policy


def _authorize(action: str, args: dict, arg_trust: dict | None = None):
    req = AuthorizeRequest(
        action=action,
        args=args,
        context={"run_id": f"run-flow-{action}-{arg_trust}", "action_id": f"action-{arg_trust}"},
        arg_trust=arg_trust or {},
    )
    return authorize(req, policy=load_policy())


def test_hostile_recipient_denied():
    resp = _authorize(
        "forward_email",
        {"to": "exfil@evil.example", "note": "FYI"},
        {"to": "HOSTILE"},
    )

    assert resp.decision == "deny"
    assert "HOSTILE" in resp.reason


def test_untrusted_recipient_denied():
    resp = _authorize(
        "forward_email",
        {"to": "external@example.com", "note": "FYI"},
        {"to": "UNTRUSTED"},
    )

    assert resp.decision == "deny"
    assert "UNTRUSTED" in resp.reason


def test_trusted_recipient_allowed():
    resp = _authorize(
        "forward_email",
        {"to": "ops@company.example", "note": "FYI"},
        {"to": "TRUSTED"},
    )

    assert resp.decision == "hitl"


def test_no_arg_trust_backcompat():
    resp = _authorize(
        "forward_email",
        {"to": "external@example.com", "note": "FYI"},
    )

    assert resp.decision == "hitl"


def test_draft_allows_untrusted():
    resp = _authorize(
        "create_draft",
        {"to": "external@example.com", "subject": "Draft", "content": "Draft body"},
        {"to": "UNTRUSTED"},
    )

    assert resp.decision == "allow"
