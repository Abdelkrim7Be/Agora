from __future__ import annotations

import asyncio

import pytest

from platform_core.security import SecurityClient

# Port 9 (discard) refuses immediately, so "the service is down" is a real
# connection failure here rather than a mocked one.
UNREACHABLE = "http://127.0.0.1:9"


def _client(record_usage=None):
    return SecurityClient(
        base_url=lambda: UNREACHABLE,
        timeout=lambda: 0.5,
        current_user_id=lambda: "alice",
        current_agent_instance_id=lambda: "inst",
        record_usage=record_usage,
    )


def test_an_outage_denies_a_proposed_action():
    verdict = _client().authorize_action("write_email", {}, "run-1")

    assert verdict["decision"] == "deny"


def test_an_outage_flags_an_outbound_send():
    verdict = _client().audit_output("write_email", "a@example.com", "hi", "body", "run-1")

    assert verdict["flagged"] is True


def test_an_outage_blocks_a_memory_write():
    verdict = _client().sanitize_memory_write("response_preferences", "learned text")

    assert verdict["injection_detected"] is True
    assert verdict["classification"] == "malicious"


def test_an_outage_marks_inbound_mail_suspicious_and_keeps_the_body():
    verdict = asyncio.run(_client().sanitize_email("a@example.com", "hi", "original body"))

    assert verdict["classification"] == "suspicious"
    assert verdict["classifier_unavailable"] is True
    assert verdict["cleaned_text"] == "original body"


def test_an_outage_leaves_content_untrusted_rather_than_classified():
    verdict = asyncio.run(_client().classify_content("some text"))

    assert verdict["trust"] == "UNTRUSTED"
    assert verdict["classifier_unavailable"] is True


def test_the_policy_view_degrades_open_because_it_gates_nothing():
    assert asyncio.run(_client().fetch_policy()) == {
        "policy_yaml": "",
        "error": "security_service_unreachable",
    }


def test_authorize_takes_the_tenant_from_scope_not_from_tool_arguments():
    payload = _client().authorize_payload(
        "write_email", {"user_id": "mallory", "agent_instance_id": "victim"}, "run-1"
    )

    assert payload["context"]["user_id"] == "alice"
    assert payload["context"]["agent_instance_id"] == "inst"


def test_an_action_id_is_only_sent_when_there_is_one():
    assert "action_id" not in _client().authorize_payload("write_email", {}, "run-1")["context"]
    assert (
        _client().authorize_payload("write_email", {}, "run-1", "act-1")["context"]["action_id"]
        == "act-1"
    )


def test_recipients_are_stated_explicitly_because_policy_cannot_read_them_from_args():
    payload = _client().authorize_payload(
        "write_email", {"to": "hidden@example.com"}, "run-1", recipients=["a@example.com"]
    )

    assert payload["recipients"] == ["a@example.com"]


@pytest.mark.parametrize("usage", [None, {}], ids=["none", "empty"])
def test_nothing_is_booked_when_the_service_reported_no_usage(usage):
    def _explode(*_args):
        raise AssertionError("should not have booked a cost")

    _client(record_usage=_explode).record_quarantine_usage(usage)


def test_a_failing_ledger_cannot_break_a_security_decision():
    def _explode(*_args):
        raise RuntimeError("ledger is down")

    _client(record_usage=_explode).record_quarantine_usage({"input_tokens": 10})


def test_reported_usage_reaches_the_ledger_with_its_node():
    booked = []

    _client(record_usage=lambda usage, node: booked.append((usage, node))).record_quarantine_usage(
        {"input_tokens": 10}
    )

    assert booked == [({"input_tokens": 10}, "quarantine")]
