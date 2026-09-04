from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from src.graph import REDRAFT_GIVE_UP_MESSAGE, RedraftGiveUpError, email_assistant
from tests.conftest import ai_tool_call


DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def _pending_args(result) -> dict:
    request = result["__interrupt__"][0].value[0]
    assert request["action_request"]["action"] == "write_email"
    return request["action_request"]["args"]


def test_ten_consecutive_redrafts_never_give_up(fake_llms, respond_email):
    """The dedicated structured path survives arbitrarily long feedback loops."""
    revisions = [
        {"to": DRAFT["to"], "subject": DRAFT["subject"], "content": f"Revision {i}."}
        for i in range(1, 11)
    ]
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
        redraft_sequence=revisions,
    )
    cfg = _cfg()

    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"]

    for i in range(1, 11):
        paused = email_assistant.invoke(
            Command(resume=[{"type": "response", "args": f"change {i}"}]), cfg
        )
        assert paused["__interrupt__"], f"redraft round {i} should pause on a new draft"
        assert _pending_args(paused)["content"] == f"Revision {i}."


def test_redraft_falls_back_to_previous_recipient_and_subject(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
        redraft_sequence=[{"content": "Body only revision."}],
    )
    cfg = _cfg()

    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"]

    paused = email_assistant.invoke(
        Command(resume=[{"type": "response", "args": "shorter"}]), cfg
    )
    args = _pending_args(paused)
    assert args["to"] == DRAFT["to"]
    assert args["subject"] == DRAFT["subject"]
    assert args["content"] == "Body only revision."


def test_redraft_gives_up_in_french_after_empty_outputs(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
        redraft_sequence=[{"content": ""}],
    )
    cfg = _cfg()

    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"]

    with pytest.raises(RedraftGiveUpError) as excinfo:
        email_assistant.invoke(
            Command(resume=[{"type": "response", "args": "shorter"}]), cfg
        )
    assert str(excinfo.value) == REDRAFT_GIVE_UP_MESSAGE


def test_feedback_is_stored_in_state_for_the_redraft_node(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
        redraft_sequence=[{"content": "Revised."}],
    )
    cfg = _cfg()

    email_assistant.invoke({"email_input": respond_email}, cfg)
    email_assistant.invoke(
        Command(resume=[{"type": "response", "args": "please shorten it"}]), cfg
    )
    state = email_assistant.get_state(cfg)
    assert state.values.get("redraft_feedback") == "please shorten it"
    assert state.values.get("redraft_requested") is True


def test_accept_after_redraft_completes_the_run(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
        redraft_sequence=[{"content": "Final revision."}],
    )
    cfg = _cfg()

    email_assistant.invoke({"email_input": respond_email}, cfg)
    paused = email_assistant.invoke(
        Command(resume=[{"type": "response", "args": "shorter"}]), cfg
    )
    assert _pending_args(paused)["content"] == "Final revision."

    done = email_assistant.invoke(Command(resume=[{"type": "accept"}]), cfg)
    assert "__interrupt__" not in done
    assert done.get("email_sent") is True


def test_redraft_strips_signature_before_prompt_and_reappends_once(fake_llms, respond_email, monkeypatch):
    """Regression for the duplicate-signoff bug: the redraft LLM must never see
    the signature block (so it can't improvise its own), and the canonical
    signature must come back exactly once in the revised draft."""
    import src.graph as g
    import src.signature as sig
    from src.signature import SignatureConfig

    signature = SignatureConfig(enabled=True, text="Karim Martin\nAcme Corp")
    monkeypatch.setattr(sig, "load_signature", lambda *args, **kwargs: signature)

    signed_block = "-- \nKarim Martin\nAcme Corp"
    already_signed_draft = {**DRAFT, "content": f"Here you go.\n\n{signed_block}"}

    captured_prompts = []

    class _CapturingRedraftLLM:
        def invoke(self, messages, config=None):
            captured_prompts.append(messages)
            return SimpleNamespace(to="", subject="", content="Shorter version.")

    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", already_signed_draft, "c1")],
    )
    monkeypatch.setattr(g, "llm_redraft", _CapturingRedraftLLM())
    cfg = _cfg()

    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"]

    paused = email_assistant.invoke(
        Command(resume=[{"type": "response", "args": "shorter"}]), cfg
    )

    user_prompt = captured_prompts[0][1]["content"]
    assert signed_block not in user_prompt
    assert "Karim" not in user_prompt

    args = _pending_args(paused)
    assert args["content"] == f"Shorter version.\n\n{signed_block}"
    assert args["content"].count("Karim") == 1
