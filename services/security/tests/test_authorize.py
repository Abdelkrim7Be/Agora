from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.authorize import authorize
from src.models import AuthorizeRequest
from src.policy import LimitsPolicy, PolicyConfig, RecipientPolicy, ToolPolicy

client = TestClient(app)

WRITE_EMAIL_REQ = {
    "action": "write_email",
    "args": {"to": "bob@example.com", "subject": "Hello", "content": "Hi Bob!"},
    "context": {"run_id": "run-001"},
}


# --- Done -> allow ---

def test_done_is_allowed():
    r = client.post("/authorize", json={"action": "Done", "args": {}, "context": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "allow"


# --- Default-deny for unknown actions ---

def test_unknown_action_denied():
    r = client.post("/authorize", json={"action": "delete_account", "args": {}, "context": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "deny"
    assert "no policy rule" in body["reason"]


# --- write_email to ordinary address -> hitl (base decision) ---

def test_write_email_returns_hitl():
    r = client.post("/authorize", json=WRITE_EMAIL_REQ)
    assert r.status_code == 200
    assert r.json()["decision"] == "hitl"


# --- Recipient deny_domains ---

def _policy_with_recipients(allow_domains=None, deny_domains=None) -> PolicyConfig:
    return PolicyConfig(
        default="deny",
        tools={
            "write_email": ToolPolicy(
                decision="hitl",
                recipients=RecipientPolicy(
                    allow_domains=allow_domains or [],
                    deny_domains=deny_domains or [],
                ),
            )
        },
    )


def test_deny_domain_blocks():
    policy = _policy_with_recipients(deny_domains=["evil.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "attacker@evil.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "deny list" in resp.reason


def test_allow_domains_blocks_outside_domain():
    policy = _policy_with_recipients(allow_domains=["company.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "user@other.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "allow list" in resp.reason


def test_allow_domains_permits_inside_domain():
    policy = _policy_with_recipients(allow_domains=["company.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "user@company.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "hitl"


def test_empty_to_field_denied():
    policy = _policy_with_recipients()
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "empty" in resp.reason


def test_unparseable_to_field_denied():
    policy = _policy_with_recipients()
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "not-an-email", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "parse" in resp.reason


def test_deny_domain_is_case_insensitive():
    # Uppercase deny entry must still block a lowercase recipient domain.
    policy = _policy_with_recipients(deny_domains=["Evil.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "attacker@evil.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "deny list" in resp.reason


def test_allow_domain_is_case_insensitive():
    # Uppercase allow entry must still permit a matching lowercase recipient domain.
    policy = _policy_with_recipients(allow_domains=["Company.COM"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "user@company.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "hitl"


def test_comma_separated_recipients_any_denied_blocks():
    # If any recipient in a comma-separated list is on the deny list, deny the whole send.
    policy = _policy_with_recipients(deny_domains=["evil.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "ok@good.com, bad@evil.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "deny list" in resp.reason


def test_name_addr_form_parses_correctly():
    policy = _policy_with_recipients(allow_domains=["company.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "Bob Smith <bob@company.com>", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "hitl"


def test_deny_domain_matches_subdomain():
    # Denying a domain also denies its subdomains.
    policy = _policy_with_recipients(deny_domains=["evil.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "attacker@mail.evil.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "deny list" in resp.reason


def test_allow_domain_permits_subdomain():
    # Allowing a domain also permits its subdomains.
    policy = _policy_with_recipients(allow_domains=["company.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "user@eu.company.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "hitl"


def test_lookalike_domain_not_matched_as_subdomain():
    # 'notevil.com' must not be treated as a subdomain of 'evil.com'.
    policy = _policy_with_recipients(deny_domains=["evil.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "user@notevil.com", "subject": "x", "content": "x"},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "hitl"


# --- Content size cap ---

def _policy_with_limits(**kwargs) -> PolicyConfig:
    return PolicyConfig(
        default="deny",
        tools={
            "write_email": ToolPolicy(
                decision="hitl",
                limits=LimitsPolicy(**kwargs),
            )
        },
    )


def test_content_over_max_chars_denied():
    policy = _policy_with_limits(max_content_chars=10)
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x" * 11},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "max_content_chars" in resp.reason


def test_content_at_exact_limit_allowed():
    policy = _policy_with_limits(max_content_chars=10)
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x" * 10},
        context={"run_id": "r1"},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "hitl"


# --- Per-run rate cap ---

def test_per_run_cap():
    policy = _policy_with_limits(max_per_run=1)
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x"},
        context={"run_id": "run-cap-test"},
    )
    first = authorize(req, policy=policy)
    assert first.decision == "hitl"

    second = authorize(req, policy=policy)
    assert second.decision == "deny"
    assert "per-run" in second.reason


def test_per_run_cap_is_idempotent_for_same_action_id():
    policy = _policy_with_limits(max_per_run=1)
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x"},
        context={"run_id": "run-idempotent", "action_id": "call-1"},
    )

    first = authorize(req, policy=policy)
    second = authorize(req, policy=policy)

    assert first.decision == "hitl"
    assert second.decision == "hitl"


def test_per_run_cap_still_blocks_different_action_ids():
    policy = _policy_with_limits(max_per_run=1)
    first = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x"},
        context={"run_id": "run-different-actions", "action_id": "call-1"},
    )
    second = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x"},
        context={"run_id": "run-different-actions", "action_id": "call-2"},
    )

    assert authorize(first, policy=policy).decision == "hitl"
    denied = authorize(second, policy=policy)
    assert denied.decision == "deny"
    assert "per-run" in denied.reason


# --- Per-day rate cap with injectable clock ---

def test_per_day_cap_and_window_reset(monkeypatch):
    import src.ratelimit as rl

    t = [0.0]
    monkeypatch.setattr(rl, "_now", lambda: t[0])

    policy = _policy_with_limits(max_per_day=2)
    req = AuthorizeRequest(
        action="write_email",
        args={"to": "bob@example.com", "subject": "x", "content": "x"},
        context={"run_id": "day-test"},
    )

    assert authorize(req, policy=policy).decision == "hitl"
    assert authorize(req, policy=policy).decision == "hitl"
    assert authorize(req, policy=policy).decision == "deny"
    assert "per-day" in authorize(req, policy=policy).reason

    # Advance clock beyond 24h — window resets and new sends are allowed again.
    t[0] = 86401.0
    assert authorize(req, policy=policy).decision == "hitl"


# --- Regression: existing endpoints still work ---

def test_health_unaffected():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_sanitize_unaffected():
    r = client.post("/sanitize", json={"content": "hi there"})
    assert r.status_code == 200
    assert r.json()["classification"] == "benign"
