from __future__ import annotations

import json
import os
from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt

from src.automation import load_rules as load_automation_rules, suggest_rule_from_correction
from src.capabilities import (
    approval_required,
    current_email_id,
    current_gmail_thread_id,
    hitl_approved,
    load_capabilities,
    tools_by_name,
)
from src.config import load_config, settings
from src.gmail_client import format_attachments
from src.memory import UserPreferences, get_memory, namespace, update_memory
from src.security_client import authorize_action
from src.prompts import (
    MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT,
    agent_system_prompt,
    triage_system_prompt,
    triage_user_prompt,
)
from src.state import RouterSchema, State, StateInput
from src.utils import format_draft_markdown, format_email_markdown, parse_email

load_dotenv()

config = load_config()

tools, tools_prompt = load_capabilities(config.capabilities)
tools_by_name_map = tools_by_name(tools)
approval_set = approval_required(config.capabilities)

llm = init_chat_model("groq:llama-3.3-70b-versatile", temperature=0.0)
llm_router = llm.with_structured_output(RouterSchema)
llm_with_tools = llm.bind_tools(tools, tool_choice="any")
llm_memory = llm.with_structured_output(UserPreferences)


def reload_config() -> None:
    """Re-read config.yaml and rebuild capability-derived globals so config/capability
    edits (e.g. via the control-panel API) take effect without recompiling the graph.

    The node functions read these as module globals at call time, so reassigning them
    is enough for the current process. Note: a separate poller process keeps its own
    copy and must be restarted (or reload itself) to pick up the change.
    """
    global config, tools, tools_prompt, tools_by_name_map, approval_set, llm_with_tools
    config = load_config()
    tools, tools_prompt = load_capabilities(config.capabilities)
    tools_by_name_map = tools_by_name(tools)
    approval_set = approval_required(config.capabilities)
    llm_with_tools = llm.bind_tools(tools, tool_choice="any")


def automation_router(
    state: State, store: BaseStore
) -> Command[Literal["environment", "triage_router", "__end__"]]:
    """Execute deterministic poller-provided automation before LLM triage."""
    automation = state["email_input"].get("automation") or {}
    terminal_status = automation.get("terminal_status")
    tool_calls = automation.get("tool_calls") or []

    if tool_calls:
        update = {
            "automation_acted": True,
            "messages": [AIMessage(content="", tool_calls=tool_calls)],
        }
        # Only tag a classification when the rule explicitly asks to notify/respond.
        # Pure-organization rules (label/archive/snooze) stay untagged so they don't
        # pollute the daily digest or get surfaced as "notify".
        if terminal_status:
            update["classification_decision"] = terminal_status
        return Command(goto="environment", update=update)
    if terminal_status == "notify":
        return Command(goto=END, update={"classification_decision": "notify"})
    return Command(goto="triage_router")


def llm_call(state: State, store: BaseStore):
    """LLM decides which tool to call to handle the email."""
    response_prefs = get_memory(
        store,
        namespace("response_preferences"),
        config.agent.response_preferences,
    )
    return {
        "messages": [
            llm_with_tools.invoke(
                [
                    {
                        "role": "system",
                        "content": agent_system_prompt.format(
                            tools_prompt=tools_prompt,
                            background=config.agent.background,
                            response_preferences=response_prefs,
                        ),
                    }
                ]
                + state["messages"]
            )
        ]
    }


def _parse_decision(raw) -> tuple[str, object]:
    """Normalize the interrupt response from Agent Inbox (list) or REST API (dict).

    Agent Inbox resumes with a list: [{"type": "accept"|"edit"|"ignore"|"response", "args": ...}]
    REST API resumes with a dict:    {"type": "approve"|"reject", "args": ...}

    Vocabulary mapping:
    - approve + None args → accept
    - approve + args dict → edit  (backward-compat: REST /approve carries edited draft directly)
    - reject             → ignore

    Edit args un-nesting: Agent Inbox wraps as {"action": "...", "args": {...}};
    REST /approve sends the draft dict flat. Both are resolved to the same flat dict.

    Returns (normalized_type, data) where data is:
    - accept:   None
    - edit:     edited args dict
    - ignore:   None
    - response: feedback string

    Fails closed: an unknown or missing type raises rather than defaulting to a
    send — an unparseable approval must never trigger the gated action.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    d = raw[0] if isinstance(raw, list) else raw
    type_ = d.get("type")
    raw_args = d.get("args")

    if type_ == "approve":
        type_ = "edit" if raw_args else "accept"
    elif type_ == "reject":
        type_ = "ignore"

    if type_ not in ("accept", "edit", "ignore", "response"):
        raise ValueError(f"Unrecognized interrupt decision type: {type_!r}")

    if type_ == "edit":
        # Agent Inbox: {"action": "write_email", "args": {...}} — un-nest the inner args.
        # REST /approve: args is already the flat draft dict.
        if isinstance(raw_args, dict) and "args" in raw_args and "action" in raw_args:
            data = raw_args["args"]
        else:
            data = raw_args
    elif type_ == "response":
        data = raw_args
    else:
        data = None

    return type_, data


def _run_id_from_config(config) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id", ""))


def _blocked_tool_message(name: str, reason: str, tool_call_id: str) -> dict:
    return {
        "role": "tool",
        "content": (
            f"Security policy denied the '{name}' action: {reason}. "
            "Do not execute this action; call Done."
        ),
        "tool_call_id": tool_call_id,
    }


def _can_auto_organize() -> bool:
    return (
        config.auto_organize.enabled
        and config.capabilities.get("inbox", False)
        and "apply_label" in tools_by_name_map
        and "archive_email" in tools_by_name_map
    )


def _auto_organize_message() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "apply_label",
                "args": {"label": config.auto_organize.ignored_label},
                "id": "auto_apply_ignored_label",
                "type": "tool_call",
            },
            {
                "name": "archive_email",
                "args": {},
                "id": "auto_archive_ignored",
                "type": "tool_call",
            },
        ],
    )


# Caches a run's authorization decisions so a HITL resume re-running tool_node does
# not re-call (and double-count) the rate limiter. Bounded with FIFO eviction so a
# long-lived poller process can't grow it without limit (insertion-ordered dict).
_AUTHORIZATION_CACHE_MAX = 512
_authorization_cache: dict[tuple[str, str, str], dict] = {}


def _authorization_cache_key(run_id: str, name: str, tool_call: dict) -> tuple[str, str, str]:
    return (run_id, name, tool_call.get("id", ""))


def _authorize_tool_action(
    name: str,
    args: dict,
    run_id: str,
    tool_call: dict,
    refresh: bool = False,
) -> dict:
    key = _authorization_cache_key(run_id, name, tool_call)
    if refresh or key not in _authorization_cache:
        authz = authorize_action(name, args, run_id, tool_call.get("id", ""))
        decision = authz.get("decision", "deny")
        reason = authz.get("reason", "no reason provided")
        if decision not in ("allow", "deny", "hitl"):
            reason = f"invalid authorization decision: {decision!r}"
            decision = "deny"
        _authorization_cache[key] = {"decision": decision, "reason": reason}
        while len(_authorization_cache) > _AUTHORIZATION_CACHE_MAX:
            del _authorization_cache[next(iter(_authorization_cache))]
    return _authorization_cache[key]


def tool_node(state: State, store: BaseStore, config=None):
    """Execute tool calls, pausing for approval on gated tools and learning from decisions."""
    result = []
    sent = False
    run_id = _run_id_from_config(config)

    # Load automation rules at most once per call, lazily — only when a human
    # correction actually happens (rule learning is a no-op when disabled).
    _rules_cache: list = []

    def _suggest_rule(correction_type: str, details: dict) -> None:
        if not _rules_cache:
            _rules_cache.append(load_automation_rules())
        suggest_rule_from_correction(
            _rules_cache[0], state["email_input"], correction_type, details
        )

    for tool_call in state["messages"][-1].tool_calls:
        name = tool_call["name"]
        args = tool_call["args"]
        authorization_decision = "hitl" if name in approval_set else "allow"

        if settings.security_enabled:
            authz = _authorize_tool_action(name, args, run_id, tool_call)
            authorization_decision = authz["decision"]
            if authorization_decision == "deny":
                result.append(_blocked_tool_message(name, authz["reason"], tool_call["id"]))
                continue

        if authorization_decision == "hitl":
            description = (
                format_draft_markdown(args) if name == "write_email" else f"Approve '{name}'?"
            )
            request = {
                "action_request": {"action": name, "args": args},
                "config": {
                    "allow_accept": True,
                    "allow_edit": True,
                    "allow_respond": True,
                    "allow_ignore": True,
                },
                "description": description,
            }
            raw = interrupt([request])
            decision_type, decision_data = _parse_decision(raw)

            if decision_type == "ignore":
                # Answer the tool call FIRST (dangling-tool-call discipline: Groq
                # rejects an unanswered tool_call in the message sequence).
                result.append({
                    "role": "tool",
                    "content": f"User ignored the '{name}' draft. Ignore this email and call Done.",
                    "tool_call_id": tool_call["id"],
                })
                update_memory(
                    store,
                    namespace("triage_preferences"),
                    list(state["messages"])
                    + result
                    + [{
                        "role": "user",
                        "content": (
                            f"The user ignored the draft '{name}'. "
                            "Emails like this should not be classified as respond. "
                            f"{MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}"
                        ),
                    }],
                    llm_memory,
                )
                _suggest_rule("ignored_draft", {"tool": name})
                continue

            if decision_type == "response":
                feedback = decision_data
                result.append({
                    "role": "tool",
                    "content": f"User gave feedback to incorporate: {feedback}",
                    "tool_call_id": tool_call["id"],
                })
                update_memory(
                    store,
                    namespace("response_preferences"),
                    list(state["messages"])
                    + result
                    + [{
                        "role": "user",
                        "content": (
                            f"User gave feedback on the draft: {feedback}. "
                            f"{MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}"
                        ),
                    }],
                    llm_memory,
                )
                _suggest_rule("draft_feedback", {"tool": name, "feedback": feedback})
                continue

            if decision_type == "edit":
                edited_args = decision_data or args
                if edited_args != args:
                    # Rewrite the AI message's tool_call args so message history
                    # reflects what actually ran (immutable copy — reference pattern).
                    ai_message = state["messages"][-1]
                    updated_tool_calls = [
                        tc for tc in ai_message.tool_calls if tc["id"] != tool_call["id"]
                    ] + [{"type": "tool_call", "name": name, "args": edited_args, "id": tool_call["id"]}]
                    result.append(ai_message.model_copy(update={"tool_calls": updated_tool_calls}))
                    update_memory(
                        store,
                        namespace("response_preferences"),
                        [{
                            "role": "user",
                            "content": (
                                f"The user edited the email draft. "
                                f"Original: {args}. "
                                f"Edited: {edited_args}. "
                                f"{MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}"
                            ),
                        }],
                        llm_memory,
                    )
                    _suggest_rule(
                        "edited_draft",
                        {"tool": name, "original": args, "edited": edited_args},
                    )
                args = edited_args

            # accept and edit fall through to tool execution below

        if settings.security_enabled and authorization_decision == "hitl" and args != tool_call["args"]:
            authz = _authorize_tool_action(name, args, run_id, tool_call, refresh=True)
            if authz["decision"] == "deny":
                result.append(_blocked_tool_message(name, authz["reason"], tool_call["id"]))
                continue

        tool = tools_by_name_map.get(name)
        if tool is None:
            result.append({
                "role": "tool",
                "content": f"The '{name}' action is not available. Call Done.",
                "tool_call_id": tool_call["id"],
            })
            continue

        email_id_token = current_email_id.set(state["email_input"].get("email_id"))
        thread_id_token = current_gmail_thread_id.set(
            state["email_input"].get("gmail_thread_id")
        )
        try:
            if name in approval_set:
                tok = hitl_approved.set(True)
                try:
                    observation = tool.invoke(args)
                finally:
                    hitl_approved.reset(tok)
            else:
                observation = tool.invoke(args)
        except Exception as exc:
            # A tool failure (e.g. an inbox tool invoked without a trusted email_id
            # on the manual /run path) must not crash the run — surface it to the
            # agent as a tool message so it can recover and call Done.
            result.append({
                "role": "tool",
                "content": f"The '{name}' action could not be completed: {exc}. Call Done.",
                "tool_call_id": tool_call["id"],
            })
            continue
        finally:
            current_gmail_thread_id.reset(thread_id_token)
            current_email_id.reset(email_id_token)
        result.append(
            {"role": "tool", "content": observation, "tool_call_id": tool_call["id"]}
        )
        if name == "write_email":
            sent = True

    update = {"messages": result}
    if sent:
        update["email_sent"] = True
    return update


def after_tools(state: State) -> Literal["llm_call", "__end__"]:
    """Sending and auto-organization are terminal; other tools continue the loop."""
    if state.get("email_sent") or state.get("auto_organized") or state.get("automation_acted"):
        return END
    return "llm_call"


def should_continue(state: State) -> Literal["environment", "__end__"]:
    """Route to tools, or end once the Done tool is called."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            if tool_call["name"] == "Done":
                return END
            return "environment"
    return END


def triage_router(
    state: State, store: BaseStore
) -> Command[Literal["llm_call", "environment", "__end__"]]:
    """Classify the email as ignore / notify / respond and route accordingly."""
    sec = state["email_input"].get("security")
    if sec and (sec.get("injection_detected") or sec.get("classifier_unavailable")):
        print("🛡️ Classification: NOTIFY - forced by security (injection or classifier unavailable)")
        return Command(goto=END, update={"classification_decision": "notify"})

    author, to, subject, email_thread = parse_email(state["email_input"])
    atts = state["email_input"].get("attachments") or []
    att_str = format_attachments(atts)

    triage_instructions = get_memory(
        store,
        namespace("triage_preferences"),
        config.agent.triage_instructions,
    )

    system_prompt = triage_system_prompt.format(
        background=config.agent.background,
        triage_instructions=triage_instructions,
    )
    user_prompt = triage_user_prompt.format(
        author=author, to=to, subject=subject, email_thread=email_thread,
        attachments=att_str or "none",
    )
    email_markdown = format_email_markdown(subject, author, to, email_thread, attachments=atts)

    result = llm_router.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )

    classification = result.classification
    if classification == "respond":
        print("📧 Classification: RESPOND - This email requires a response")
        goto = "llm_call"
        update = {
            "classification_decision": classification,
            "messages": [
                {
                    "role": "user",
                    "content": f"Respond to the email: {email_markdown}",
                }
            ],
        }
    elif classification == "ignore":
        print("🚫 Classification: IGNORE - This email can be safely ignored")
        if _can_auto_organize():
            goto = "environment"
            update = {
                "classification_decision": classification,
                "auto_organized": True,
                "messages": [_auto_organize_message()],
            }
        else:
            goto = END
            update = {"classification_decision": classification}
    elif classification == "notify":
        print("🔔 Classification: NOTIFY - This email contains important information")
        goto = END
        update = {"classification_decision": classification}
    else:
        raise ValueError(f"Invalid classification: {classification}")

    return Command(goto=goto, update=update)


overall_workflow = (
    StateGraph(State, input_schema=StateInput)
    .add_node("automation_router", automation_router)
    .add_node("triage_router", triage_router)
    .add_node("llm_call", llm_call)
    .add_node("environment", tool_node)
    .add_edge(START, "automation_router")
    .add_conditional_edges(
        "llm_call",
        should_continue,
        {"environment": "environment", END: END},
    )
    .add_conditional_edges(
        "environment",
        after_tools,
        {"llm_call": "llm_call", END: END},
    )
)

_under_langgraph_platform = bool(os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT"))
checkpointer = None if _under_langgraph_platform else MemorySaver()
store = None if _under_langgraph_platform else InMemoryStore()
email_assistant = overall_workflow.compile(checkpointer=checkpointer, store=store)

graph = email_assistant
