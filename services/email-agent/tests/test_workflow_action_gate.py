"""CaMeL step #2: the workflow decides the tool, not the model.

Steps #1 (the model cannot name a recipient) and the trust derivation cover
*where* an action goes and *how much* it is trusted. Neither covers *which*
action runs: the drafting model picked that after reading untrusted mail, so an
injection could steer a run from "draft a reply" into forwarding or trashing,
and every check downstream would then be asked about the tool the attacker
chose.

The action space now comes from operator configuration. The model only fills in
content for an action that was already decided.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from src.categories import POLICY_DEFAULT_ACTIONS, Category


def _category(**kwargs) -> Category:
    base = {"name": "devis", "display_name": "Devis", "policy": "auto_draft"}
    return Category(**{**base, **kwargs})


def _state(tool_name: str, args: dict | None = None) -> dict:
    return {
        "email_input": {
            "author": "client@example.com",
            "to": "me@company.example",
            "subject": "Demande de devis",
            "email_thread": "Bonjour, un devis SVP.",
            "email_id": "msg-1",
        },
        "category": "devis",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": tool_name,
                    "args": args or {},
                    "id": f"call-{tool_name}",
                    "type": "tool_call",
                }],
            )
        ],
    }


# --- the declaration itself --------------------------------------------------

def test_a_policy_opens_only_its_own_actions():
    assert _category(policy="auto_draft").actions() == POLICY_DEFAULT_ACTIONS["auto_draft"]
    assert _category(policy="notify").actions() == ["notify_internal"]
    assert _category(policy="ignore").actions() == ["apply_label", "archive_email"]


@pytest.mark.parametrize("policy", ["auto_draft", "notify", "organize", "ignore"])
def test_no_policy_opens_a_destructive_or_outbound_tool_by_default(policy):
    # forward_email and trash_email have to be named by a workflow. A default
    # that included them would put them back in reach of an injection.
    actions = _category(policy=policy).actions()

    assert "forward_email" not in actions
    assert "trash_email" not in actions


def test_an_explicit_list_replaces_the_policy_default():
    category = _category(policy="auto_draft", allowed_actions=["forward_email"])

    assert category.actions() == ["forward_email"]


def test_an_empty_list_means_the_workflow_executes_nothing():
    # Distinct from None, which means "whatever this policy opens".
    assert _category(allowed_actions=[]).actions() == []


# --- enforcement in tool_node ------------------------------------------------

def test_a_tool_the_workflow_opens_is_allowed():
    import src.graph as g

    assert g._off_workflow_action(_category(policy="auto_draft"), "write_email") is None


def test_a_tool_the_workflow_does_not_open_is_refused():
    import src.graph as g

    reason = g._off_workflow_action(_category(policy="auto_draft"), "trash_email")

    assert reason is not None
    assert "devis" in reason and "trash_email" in reason


def test_a_run_with_no_workflow_is_not_constrained_here():
    # There is no declared intent to enforce; the tool-level default in
    # security/policy.yaml is what governs those runs.
    import src.graph as g

    assert g._off_workflow_action(None, "trash_email") is None


def test_an_injection_cannot_steer_a_drafting_workflow_into_forwarding(monkeypatch):
    """The end-to-end shape of the attack this step removes."""
    import src.graph as g

    monkeypatch.setattr(g, "_category_for_run", lambda state: _category(policy="auto_draft"))
    monkeypatch.setitem(
        g.tools_by_name_map,
        "forward_email",
        type("ShouldNotRun", (), {
            "invoke": staticmethod(lambda args: (_ for _ in ()).throw(AssertionError("tool executed")))
        })(),
    )

    result = g.tool_node(
        _state("forward_email", {"note": "FYI"}),
        InMemoryStore(),
        config={"configurable": {"thread_id": "run-gate"}},
    )

    message = result["messages"][0]
    assert message["tool_call_id"] == "call-forward_email"
    assert "does not perform" in message["content"]


def test_a_refusal_is_recoverable_not_a_crash(monkeypatch):
    # It comes back as a tool message so the run continues and the model can be
    # told why, the same way an authorization denial does.
    import src.graph as g

    monkeypatch.setattr(g, "_category_for_run", lambda state: _category(policy="notify"))

    result = g.tool_node(
        _state("trash_email"),
        InMemoryStore(),
        config={"configurable": {"thread_id": "run-gate-2"}},
    )

    assert result["messages"][0]["role"] == "tool"


def test_the_gate_runs_before_authorization(monkeypatch):
    """An off-workflow call is never submitted as a candidate action.

    Otherwise the policy engine, the rate limiter and the approval queue would
    all be asked about a tool the attacker chose — and a human would be shown it
    for approval.
    """
    import src.graph as g

    monkeypatch.setattr(g.settings, "security_enabled", True)
    g._authorization_cache.clear()
    monkeypatch.setattr(g, "_category_for_run", lambda state: _category(policy="auto_draft"))

    def _must_not_be_asked(*args, **kwargs):
        raise AssertionError("authorization was asked about an off-workflow tool")

    monkeypatch.setattr(g, "authorize_action", _must_not_be_asked)

    result = g.tool_node(
        _state("trash_email"),
        InMemoryStore(),
        config={"configurable": {"thread_id": "run-gate-3"}},
    )

    assert "does not perform" in result["messages"][0]["content"]


def test_the_action_list_survives_a_yaml_round_trip():
    # It is edited through PUT /categories/{name} and stored as YAML; a field
    # that does not round-trip would silently revert to the policy default.
    import yaml
    from src.categories import CategoriesConfig, dump_categories

    config = CategoriesConfig(categories=[_category(allowed_actions=["notify_internal"])])

    reloaded = CategoriesConfig(**yaml.safe_load(dump_categories(config)))

    assert reloaded.categories[0].allowed_actions == ["notify_internal"]
    assert reloaded.categories[0].actions() == ["notify_internal"]


def test_an_unset_action_list_round_trips_as_unset():
    import yaml
    from src.categories import CategoriesConfig, dump_categories

    config = CategoriesConfig(categories=[_category(policy="notify")])

    reloaded = CategoriesConfig(**yaml.safe_load(dump_categories(config)))

    assert reloaded.categories[0].allowed_actions is None
    assert reloaded.categories[0].actions() == ["notify_internal"]


def test_the_gate_cannot_grant_a_tool_security_policy_denies(monkeypatch):
    """It only ever narrows. Naming a tool in allowed_actions is not a grant."""
    import src.graph as g

    monkeypatch.setattr(g.settings, "security_enabled", True)
    g._authorization_cache.clear()
    monkeypatch.setattr(
        g, "_category_for_run", lambda state: _category(allowed_actions=["trash_email"])
    )
    monkeypatch.setattr(
        g, "authorize_action",
        lambda *a, **k: {"decision": "deny", "reason": "tool denied by policy"},
    )
    monkeypatch.setitem(
        g.tools_by_name_map,
        "trash_email",
        type("ShouldNotRun", (), {
            "invoke": staticmethod(lambda args: (_ for _ in ()).throw(AssertionError("tool executed")))
        })(),
    )

    result = g.tool_node(
        _state("trash_email"),
        InMemoryStore(),
        config={"configurable": {"thread_id": "run-gate-4"}},
    )

    assert "Security policy denied" in result["messages"][0]["content"]
