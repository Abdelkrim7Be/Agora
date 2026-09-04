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

def _policy_with_recipients(
    allow_domains=None,
    deny_domains=None,
    allow_addresses=None,
    deny_addresses=None,
) -> PolicyConfig:
    return PolicyConfig(
        default="deny",
        tools={
            "write_email": ToolPolicy(
                decision="hitl",
                recipients=RecipientPolicy(
                    allow_domains=allow_domains or [],
                    deny_domains=deny_domains or [],
                    allow_addresses=allow_addresses or [],
                    deny_addresses=deny_addresses or [],
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


def test_allow_addresses_permits_only_listed_mailboxes():
    # The whole point of the address list: same domain, different mailbox, denied.
    policy = _policy_with_recipients(allow_addresses=["ok@gmail.com"])
    allowed = authorize(
        AuthorizeRequest(
            action="write_email",
            args={"to": "OK@gmail.com", "subject": "x", "content": "x"},
            context={"run_id": "r1"},
        ),
        policy=policy,
    )
    assert allowed.decision == "hitl"

    blocked = authorize(
        AuthorizeRequest(
            action="write_email",
            args={"to": "someone.else@gmail.com", "subject": "x", "content": "x"},
            context={"run_id": "r1"},
        ),
        policy=policy,
    )
    assert blocked.decision == "deny"
    assert "not on the allow list" in blocked.reason


def test_allow_addresses_blocks_when_one_of_several_recipients_is_unlisted():
    policy = _policy_with_recipients(allow_addresses=["a@gmail.com", "b@gmail.com"])
    resp = authorize(
        AuthorizeRequest(
            action="write_email",
            args={"to": ["a@gmail.com", "stranger@gmail.com"], "subject": "x", "content": "x"},
            context={"run_id": "r1"},
        ),
        policy=policy,
    )
    assert resp.decision == "deny"
    assert "stranger@gmail.com" in resp.reason


def test_deny_addresses_blocks_a_single_mailbox_on_an_allowed_domain():
    policy = _policy_with_recipients(
        allow_domains=["company.com"], deny_addresses=["ceo@company.com"]
    )
    resp = authorize(
        AuthorizeRequest(
            action="write_email",
            args={"to": "ceo@company.com", "subject": "x", "content": "x"},
            context={"run_id": "r1"},
        ),
        policy=policy,
    )
    assert resp.decision == "deny"
    assert "deny list" in resp.reason


def test_list_recipients_are_checked_like_comma_separated_recipients():
    policy = _policy_with_recipients(deny_domains=["evil.com"])
    req = AuthorizeRequest(
        action="write_email",
        args={"to": ["good@company.com", "bad@evil.com"], "subject": "x", "content": "Hello"},
        context={},
    )
    resp = authorize(req, policy=policy)
    assert resp.decision == "deny"
    assert "evil.com" in resp.reason


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


def test_policy_endpoint_returns_yaml():
    r = client.get("/policy")
    assert r.status_code == 200
    body = r.json()
    assert "default: deny" in body["policy_yaml"]
    assert "write_email" in body["policy_yaml"]


# --- inbox/draft/send capability policy ---

def _authorize_action(action: str, args: dict | None = None, run_id: str | None = None) -> dict:
    r = client.post(
        "/authorize",
        json={
            "action": action,
            "args": args or {},
            "context": {"run_id": run_id or f"run-{action}"},
        },
    )
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize(
    "action,args",
    [
        ("apply_label", {"label": "Clients"}),
        ("remove_label", {"label": "Clients"}),
        ("mark_read", {}),
        ("mark_unread", {}),
        ("archive_email", {}),
    ],
)
def test_reversible_inbox_actions_are_allowed(action, args):
    body = _authorize_action(action, args)
    assert body["decision"] == "allow"


@pytest.mark.parametrize("action", ["trash_email", "forward_email", "reply_all"])
def test_risky_inbox_actions_return_hitl(action):
    args = {"to": "bob@example.com", "note": "FYI"} if action == "forward_email" else {}
    if action == "reply_all":
        args = {"content": "Thanks"}
    body = _authorize_action(action, args, run_id=f"run-hitl-{action}")
    assert body["decision"] == "hitl"


def test_create_draft_is_allowed_by_default_policy():
    body = _authorize_action(
        "create_draft",
        {"to": "bob@example.com", "subject": "Draft", "content": "Draft body"},
        run_id="run-create-draft-policy",
    )
    assert body["decision"] == "allow"


def test_forward_email_requires_parseable_recipient():
    body = _authorize_action("forward_email", {"to": "not-an-email", "note": "FYI"})
    assert body["decision"] == "deny"
    assert "parse" in body["reason"]


def test_create_draft_requires_parseable_recipient():
    body = _authorize_action(
        "create_draft",
        {"to": "not-an-email", "subject": "Draft", "content": "Draft body"},
    )
    assert body["decision"] == "deny"
    assert "parse" in body["reason"]


def test_reply_all_does_not_require_to_arg_because_recipients_are_thread_derived():
    body = _authorize_action(
        "reply_all",
        {"content": "Thanks"},
        run_id="run-reply-all-derived-recipients",
    )
    assert body["decision"] == "hitl"


def test_note_field_over_max_chars_denied():
    policy = PolicyConfig(
        default="deny",
        tools={
            "forward_email": ToolPolicy(
                decision="hitl",
                limits=LimitsPolicy(max_content_chars=3),
            )
        },
    )
    req = AuthorizeRequest(
        action="forward_email",
        args={"to": "bob@example.com", "note": "abcd"},
        context={"run_id": "run-note-cap"},
    )

    resp = authorize(req, policy=policy)

    assert resp.decision == "deny"
    assert "max_content_chars" in resp.reason


def test_body_field_over_max_chars_denied():
    policy = PolicyConfig(
        default="deny",
        tools={
            "reply_all": ToolPolicy(
                decision="hitl",
                limits=LimitsPolicy(max_content_chars=3),
            )
        },
    )
    req = AuthorizeRequest(
        action="reply_all",
        args={"body": "abcd"},
        context={"run_id": "run-body-cap"},
    )

    resp = authorize(req, policy=policy)

    assert resp.decision == "deny"
    assert "max_content_chars" in resp.reason


def test_create_draft_does_not_consume_send_budget():
    # Drafts don't send externally, so creating one must not eat into write_email's
    # per-run send cap (default policy: write_email max_per_run=1).
    run_id = "run-draft-then-send"
    draft = _authorize_action(
        "create_draft",
        {"to": "bob@example.com", "subject": "Draft", "content": "Body"},
        run_id=run_id,
    )
    assert draft["decision"] == "allow"

    send = _authorize_action(
        "write_email",
        {"to": "bob@example.com", "subject": "Hi", "content": "Hi Bob!"},
        run_id=run_id,
    )
    assert send["decision"] == "hitl"


def test_forward_email_per_run_cap_is_enforced():
    policy = PolicyConfig(
        default="deny",
        tools={
            "forward_email": ToolPolicy(
                decision="hitl",
                recipients=RecipientPolicy(),
                limits=LimitsPolicy(max_per_run=1),
            )
        },
    )
    first = AuthorizeRequest(
        action="forward_email",
        args={"to": "bob@example.com", "note": "FYI"},
        context={"run_id": "run-forward-cap", "action_id": "call-1"},
    )
    second = AuthorizeRequest(
        action="forward_email",
        args={"to": "bob@example.com", "note": "FYI"},
        context={"run_id": "run-forward-cap", "action_id": "call-2"},
    )

    assert authorize(first, policy=policy).decision == "hitl"
    denied = authorize(second, policy=policy)
    assert denied.decision == "deny"
    assert "per-run" in denied.reason



def test_metrics_endpoint_available():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "agora_security_health" in r.text


def test_declared_recipients_are_checked_when_no_to_argument():
    """Send tools no longer pass a `to` argument.

    The caller resolves the destination from trusted context and declares it, so
    recipient policy must read that instead of silently seeing an empty field.
    """
    from src.models import AuthorizeRequest
    from src.authorize import authorize
    from src.policy import load_policy

    policy = load_policy("policy.yaml")
    policy.tools["write_email"].recipients.deny_addresses = ["attacker@evil.example"]

    denied = authorize(
        AuthorizeRequest(
            action="write_email",
            args={"subject": "hi", "content": "body"},
            recipients=["attacker@evil.example"],
        ),
        policy,
    )
    assert denied.decision == "deny"
    assert "attacker@evil.example" in denied.reason

    allowed = authorize(
        AuthorizeRequest(
            action="write_email",
            args={"subject": "hi", "content": "body"},
            recipients=["client@example.com"],
        ),
        policy,
    )
    assert allowed.decision != "deny"
