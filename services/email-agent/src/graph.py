from __future__ import annotations

import inspect
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr
from functools import reduce, wraps
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt
from pydantic import BaseModel as PydanticBaseModel

import re as _re

from src.automation import load_rules as load_automation_rules, suggest_rule_from_correction
from src.capabilities import (
    approval_required,
    current_email_attachments,
    current_email_id,
    current_gmail_thread_id,
    current_reply_to,
    current_route_targets,
    current_uploaded_attachments,
    hitl_approved,
    load_capabilities,
    tools_by_name,
)
from src.run_attachments import load_attachments as load_uploaded_attachments
from src.config import load_config, settings
from src.cost_tracker import llm_invoke_config, totals_for_run_node
from src.categories import auto_draft_tool_call, classify_category, load_categories, unresolved_vars
from src.contacts import get_contact
from src.gmail_client import format_attachments
from src.llm import get_llm
from src.memory import UserPreferences, get_memory, namespace, update_memory
from src.roles import list_roles, resolve_role
from src.security_client import audit_output, authorize_action
from src.shared_cache import cache_get_json, cache_set_json
from src.signature import apply_signature_to_args, strip_signature
from src.tenant import current_agent_instance_id, current_user_id
from src.prompts import (
    MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT,
    agent_system_prompt,
    format_workflow_instructions,
    triage_system_prompt,
    triage_user_prompt,
)
from src.state import RouterSchema, State, StateInput
from src.utils import ensure_email_paragraphs, format_action_description, format_email_markdown, parse_email
from src.trace import record_trace

load_dotenv()

agent_config = load_config()
config = agent_config

tools, tools_prompt = load_capabilities(agent_config.capabilities)
tools_by_name_map = tools_by_name(tools)
approval_set = approval_required(agent_config.capabilities)


def _build_llm_bindings():
    base_llm = get_llm("memory_style")
    router_llm = get_llm("triage").with_structured_output(RouterSchema)
    tool_llm = get_llm("draft").bind_tools(tools, tool_choice="any")
    memory_llm = base_llm.with_structured_output(UserPreferences)
    redraft_llm = get_llm("draft").with_structured_output(RedraftOutput)
    return base_llm, router_llm, tool_llm, memory_llm, redraft_llm


class RedraftOutput(PydanticBaseModel):
    """Structured revision of a pending draft: the only output the redraft node accepts."""

    to: str = ""
    subject: str = ""
    content: str = ""


llm, llm_router, llm_with_tools, llm_memory, llm_redraft = _build_llm_bindings()


def _failed_generation_from_exception(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("failed_generation"), str):
            return error["failed_generation"]
        if isinstance(body.get("failed_generation"), str):
            return body["failed_generation"]

    text = str(exc)
    start = text.find("{")
    if start == -1:
        return None
    try:
        parsed = json.loads(text[start:])
    except json.JSONDecodeError:
        try:
            import ast

            parsed = ast.literal_eval(text[start:])
        except (SyntaxError, ValueError):
            return None

    if not isinstance(parsed, dict):
        return None
    error = parsed.get("error")
    if isinstance(error, dict) and isinstance(error.get("failed_generation"), str):
        return error["failed_generation"]
    return None


def _recover_tool_call_from_failed_generation(exc: Exception) -> AIMessage | None:
    """Recover valid tool args from Groq tool-parser failures.

    Groq sometimes rejects a llama tool call before LangChain receives it, even
    when the model produced a usable payload, e.g.
    `<function=write_email {"to": "...", ...}</function>`. Recovering that keeps
    the graph on the normal HITL path instead of returning a 500.
    """
    failed = _failed_generation_from_exception(exc)
    if not failed or "<function=" not in failed:
        return None

    marker = "<function="
    marker_index = failed.find(marker)
    name_start = marker_index + len(marker)
    name_end = len(failed)
    for delimiter in (" ", ">", "{"):
        delimiter_index = failed.find(delimiter, name_start)
        if delimiter_index != -1:
            name_end = min(name_end, delimiter_index)
    name = failed[name_start:name_end].strip()
    if not name or name not in tools_by_name_map:
        return None

    args_start = failed.find("{", name_end)
    if args_start == -1:
        return None
    try:
        args, _ = json.JSONDecoder().raw_decode(failed[args_start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(args, dict):
        return None

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": f"llm_recovered_{uuid.uuid4().hex}",
                "type": "tool_call",
            }
        ],
    )


def reload_config() -> None:
    """Re-read config.yaml and rebuild capability-derived globals so config/capability
    edits (e.g. via the control-panel API) take effect without recompiling the graph.

    The node functions read these as module globals at call time, so reassigning them
    is enough for the current process. Note: a separate poller process keeps its own
    copy and must be restarted (or reload itself) to pick up the change.
    """
    global agent_config, config, tools, tools_prompt, tools_by_name_map, approval_set
    global llm, llm_router, llm_with_tools, llm_memory, llm_redraft
    agent_config = load_config()
    config = agent_config
    tools, tools_prompt = load_capabilities(config.capabilities)
    tools_by_name_map = tools_by_name(tools)
    approval_set = approval_required(config.capabilities)
    llm, llm_router, llm_with_tools, llm_memory, llm_redraft = _build_llm_bindings()


def _resolve_route_targets(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = str(value).strip()
    if not cleaned:
        return []
    if "@" in cleaned:
        return [cleaned.lower()]
    resolved = resolve_role(cleaned)
    return resolved.emails if resolved and resolved.emails else []


def _trusted_reply_to(state: State) -> str | None:
    """The sender of the message being handled, from its headers.

    `parse_email` reads the From header, so this is the mailbox that actually
    sent the mail — not an address the model read out of the body.
    """
    email_input = state.get("email_input") or {}
    author = email_input.get("author") or ""
    address = parseaddr(str(author))[1]
    return address.lower() or None


def _trusted_route_targets(state: State) -> tuple[str, ...]:
    """Recipients the workspace configured for this run's workflow.

    Resolved from the matched category's route_to / owner through the roles
    directory. Both are workspace configuration, so an injected instruction has
    no way to add an address here.
    """
    raw = state.get("workflow_route_to") or []
    if isinstance(raw, str):
        raw = [raw]
    targets: list[str] = []
    for item in raw:
        targets.extend(_resolve_route_targets(item))
    if not targets and state.get("workflow_owner"):
        targets.extend(_resolve_route_targets(state.get("workflow_owner")))
    return tuple(dict.fromkeys(targets))


def _workflow_notify_tool_call(state: State, category_update: dict) -> dict | None:
    if "notify_internal" not in tools_by_name_map:
        return None
    raw_targets = category_update.get("workflow_route_to") or []
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    
    targets = []
    for item in raw_targets:
        targets.extend(_resolve_route_targets(item))
    
    if not targets and category_update.get("workflow_owner"):
        targets.extend(_resolve_route_targets(category_update.get("workflow_owner")))
        
    # Deduplicate while preserving order
    targets = list(dict.fromkeys(targets))
    if not targets:
        return None

    author, _to, subject, _thread = parse_email(state["email_input"])
    category = category_update.get("category_display_name") or category_update.get("category") or "Workflow"
    owner = category_update.get("workflow_owner") or "unassigned"
    approver = category_update.get("workflow_approver") or "workspace approver"
    note = (
        f"Agora AI workflow route: {category}.\n"
        f"Owner: {owner}. Approver: {approver}.\n"
        f"Original sender: {author}. Subject: {subject}.\n\n"
        "Please handle this request or reply internally with the next action."
    )
    return {
        "name": "notify_internal",
        # No "to": tool_node supplies the recipients from current_route_targets,
        # which is resolved from this same workflow configuration.
        "args": {"subject": f"[Agora AI] {category}", "note": note},
        "id": f"workflow_notify_{uuid.uuid4().hex}",
        "type": "tool_call",
    }


def automation_router(
    state: State, store: BaseStore, config=None
) -> Command[Literal["environment", "category_router", "__end__"]]:
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
    return Command(goto="category_router")


def category_router(
    state: State, config=None
) -> Command[Literal["environment", "triage_router", "llm_call", "__end__"]]:
    """Deterministic category routing before the triage LLM.

    Classifies the email against configured categories. When a policy matches,
    handles it directly (auto_draft, organize, notify, ignore) so the triage LLM
    call is skipped entirely. Unmatched emails fall through to triage_router with
    the category context already in state for the LLM to refine (B4).
    """
    # The security verdict has to be honored here, not only in triage_router.
    # This node runs first and, on a category match, jumps straight to the model
    # or to a tool call — so a flagged message that happened to match a category
    # skipped the check entirely. In one live test an injected "forward the
    # mailbox to <attacker>" instruction reached the model that way and came back
    # as an internal notification repeating the instruction verbatim, with a
    # subject the attacker had chosen: no send left the system, but a human was
    # being asked to perform the attack by hand.
    sec = state["email_input"].get("security")
    if sec and (sec.get("injection_detected") or sec.get("classifier_unavailable")):
        print("🛡️ Category routing skipped - forced notify by security verdict")
        return Command(goto=END, update={"classification_decision": "notify"})

    agent_instance_id = state["email_input"].get("agent_instance_id")
    categories_config = load_categories(agent_instance_id=agent_instance_id)
    category_meta = classify_category(state["email_input"], categories_config)

    cat = category_meta.get("category")
    policy = category_meta.get("policy")
    matched_contact = category_meta.get("contact")

    author, _, _, _ = parse_email(state["email_input"])
    contact_dir = get_contact(author, agent_instance_id=agent_instance_id)
    contact_lang = contact_dir.fields.get("lang") if contact_dir and contact_dir.fields else None

    category_update = {
        "category": cat,
        "category_display_name": category_meta.get("category_display_name"),
        "priority": category_meta.get("priority") or "normal",
        "template": category_meta.get("template"),
        "category_policy": policy,
        "workflow_owner": category_meta.get("owner"),
        "workflow_approver": category_meta.get("approver"),
        "workflow_route_to": category_meta.get("route_to") or [],
        "workflow_instructions": category_meta.get("instructions"),
        "contact_lang": contact_lang,
    }

    if not cat or not policy:
        return Command(goto="triage_router", update=category_update)

    if policy == "auto_draft":
        template_tool_call = auto_draft_tool_call(
            state["email_input"], categories_config, cat, contact=matched_contact
        )
        if template_tool_call is not None:
            content = template_tool_call["args"].get("content", "")
            remaining_vars = unresolved_vars(content)
            author, to, subject, email_thread = parse_email(state["email_input"])
            atts = state["email_input"].get("attachments") or []
            email_markdown = format_email_markdown(subject, author, to, email_thread, attachments=atts)
            if remaining_vars:
                print(f"📧 Category '{cat}': template has unresolved vars {remaining_vars}, routing to LLM for finalization")
                return Command(
                    goto="llm_call",
                    update={
                        "classification_decision": "respond",
                        **category_update,
                        "messages": [{
                            "role": "user",
                            "content": (
                                f"This email matches the '{cat}' category. Draft a response using this "
                                f"template as a starting point:\n\n"
                                f"To: {template_tool_call['args']['to']}\n"
                                f"Subject: {template_tool_call['args']['subject']}\n"
                                f"Content: {content}\n\n"
                                f"Fill in the unresolved placeholders "
                                f"({', '.join('{{' + v + '}}' for v in remaining_vars)}) "
                                f"from the email context below, then call write_email. "
                                f"Keep the template's closing (\"{content.rstrip().splitlines()[-1] if content.strip() else ''}\") "
                                f"as the last line of the body — do not add a name, title, or company "
                                f"after it; the signature is appended automatically.\n\n{email_markdown}"
                            ),
                        }],
                    },
                )
            print(f"📧 Category '{cat}': auto-draft from template")
            return Command(
                goto="environment",
                update={
                    "classification_decision": "respond",
                    **category_update,
                    "messages": [
                        {"role": "user", "content": f"Draft from category template for email: {email_markdown}"},
                        AIMessage(content="", tool_calls=[template_tool_call]),
                    ],
                },
            )
        # auto_draft policy but no template resolved → fall through to triage
        return Command(goto="triage_router", update=category_update)

    if policy == "organize":
        if "apply_label" not in tools_by_name_map or "archive_email" not in tools_by_name_map:
            print(f"📁 Category '{cat}': organize policy but inbox capability not enabled, falling through to triage")
            return Command(goto="triage_router", update=category_update)
        cat_obj = next((c for c in categories_config.categories if c.name == cat), None)
        labels = (cat_obj.labels if cat_obj and cat_obj.labels else [cat])
        org_tool_calls = [
            {"name": "apply_label", "args": {"label": label}, "id": f"org_label_{i}", "type": "tool_call"}
            for i, label in enumerate(labels)
        ] + [{"name": "archive_email", "args": {}, "id": "org_archive", "type": "tool_call"}]
        print(f"📁 Category '{cat}': organize policy, applying {labels} and archiving")
        return Command(
            goto="environment",
            update={
                "classification_decision": "ignore",
                **category_update,
                "auto_organized": True,
                "messages": [AIMessage(content="", tool_calls=org_tool_calls)],
            },
        )

    if policy == "notify":
        notify_call = _workflow_notify_tool_call(state, category_update)
        if notify_call is not None:
            print(f"🔔 Category '{cat}': notify policy, routing for approval")
            return Command(
                goto="environment",
                update={
                    "classification_decision": "notify",
                    **category_update,
                    "messages": [AIMessage(content="", tool_calls=[notify_call])],
                },
            )
        print(f"🔔 Category '{cat}': notify policy, terminating")
        return Command(goto=END, update={"classification_decision": "notify", **category_update})

    if policy == "ignore":
        print(f"🚫 Category '{cat}': ignore policy")
        if _can_auto_organize():
            return Command(
                goto="environment",
                update={
                    "classification_decision": "ignore",
                    **category_update,
                    "auto_organized": True,
                    "messages": [_auto_organize_message()],
                },
            )
        return Command(goto=END, update={"classification_decision": "ignore", **category_update})

    # Unknown or unhandled policy → fall through
    return Command(goto="triage_router", update=category_update)


def _category_policy_command(
    state: State,
    categories_config,
    cat: str,
    category_update: dict,
    matched_contact=None,
) -> Command | None:
    """Execute a category policy from deterministic or LLM fallback routing."""
    policy = category_update.get("category_policy")

    if policy == "auto_draft":
        template_tool_call = auto_draft_tool_call(
            state["email_input"], categories_config, cat, contact=matched_contact
        )
        if template_tool_call is None:
            return None
        content = template_tool_call["args"].get("content", "")
        remaining_vars = unresolved_vars(content)
        author, to, subject, email_thread = parse_email(state["email_input"])
        atts = state["email_input"].get("attachments") or []
        email_markdown = format_email_markdown(subject, author, to, email_thread, attachments=atts)
        category_obj = next((c for c in categories_config.categories if c.name == cat), None)
        # 'adapt' asks the model to rework the template against this specific
        # message. Without it a fully-resolved template goes out verbatim, which
        # is how an invoice reminder quoting its number, amount and due date got
        # a reply asking for the number, amount and due date.
        adapt_to_message = category_obj is not None and category_obj.template_mode == "adapt"
        if remaining_vars or adapt_to_message:
            reason = (
                f"unresolved vars {remaining_vars}" if remaining_vars else "template_mode=adapt"
            )
            print(f"📧 Category '{cat}': {reason}, routing to LLM for finalization")
            return Command(
                goto="llm_call",
                update={
                    "classification_decision": "respond",
                    **category_update,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"This email matches the '{cat}' category. Draft a response using this "
                            f"template as a starting point:\n\n"
                            f"To: {template_tool_call['args']['to']}\n"
                            f"Subject: {template_tool_call['args']['subject']}\n"
                            f"Content: {content}\n\n"
                            + (
                                f"Fill in the unresolved placeholders "
                                f"({', '.join('{{' + v + '}}' for v in remaining_vars)}) "
                                f"from the email context below, then call write_email. "
                                if remaining_vars
                                else (
                                    "Adapt it to what this message actually says: keep the "
                                    "template's structure and tone, drop anything it asks for "
                                    "that the sender already provided, and answer the specific "
                                    "point raised. Then call write_email. "
                                )
                            ) +
                            f"Keep the template's closing (\"{content.rstrip().splitlines()[-1] if content.strip() else ''}\") "
                            f"as the last line of the body - do not add a name, title, or company "
                            f"after it; the signature is appended automatically.\n\n{email_markdown}"
                        ),
                    }],
                },
            )
        print(f"📧 Category '{cat}': auto-draft from template")
        return Command(
            goto="environment",
            update={
                "classification_decision": "respond",
                **category_update,
                "messages": [
                    {"role": "user", "content": f"Draft from category template for email: {email_markdown}"},
                    AIMessage(content="", tool_calls=[template_tool_call]),
                ],
            },
        )

    if policy == "organize":
        if "apply_label" not in tools_by_name_map or "archive_email" not in tools_by_name_map:
            print(f"📁 Category '{cat}': organize policy but inbox capability not enabled, falling through to triage")
            return None
        cat_obj = next((c for c in categories_config.categories if c.name == cat), None)
        labels = (cat_obj.labels if cat_obj and cat_obj.labels else [cat])
        org_tool_calls = [
            {"name": "apply_label", "args": {"label": label}, "id": f"org_label_{i}", "type": "tool_call"}
            for i, label in enumerate(labels)
        ] + [{"name": "archive_email", "args": {}, "id": "org_archive", "type": "tool_call"}]
        print(f"📁 Category '{cat}': organize policy, applying {labels} and archiving")
        return Command(
            goto="environment",
            update={
                "classification_decision": "ignore",
                **category_update,
                "auto_organized": True,
                "messages": [AIMessage(content="", tool_calls=org_tool_calls)],
            },
        )

    if policy == "notify":
        notify_call = _workflow_notify_tool_call(state, category_update)
        if notify_call is not None:
            print(f"🔔 Category '{cat}': notify policy, routing for approval")
            return Command(
                goto="environment",
                update={
                    "classification_decision": "notify",
                    **category_update,
                    "messages": [AIMessage(content="", tool_calls=[notify_call])],
                },
            )
        print(f"🔔 Category '{cat}': notify policy, terminating")
        return Command(goto=END, update={"classification_decision": "notify", **category_update})

    if policy == "ignore":
        print(f"🚫 Category '{cat}': ignore policy")
        if _can_auto_organize():
            return Command(
                goto="environment",
                update={
                    "classification_decision": "ignore",
                    **category_update,
                    "auto_organized": True,
                    "messages": [_auto_organize_message()],
                },
            )
        return Command(goto=END, update={"classification_decision": "ignore", **category_update})

    return None


class _CoercedDraft(PydanticBaseModel):
    """Structured extraction of an email draft from a plain-text model reply."""

    is_email_draft: bool = False
    to: str = ""
    subject: str = ""
    content: str = ""


def _normalize_recipient_args(args: dict) -> dict:
    """Strip stray wrapping quotes from recipient addresses.

    A draft edited or extracted with a quoted address ('"user@x.com"') makes
    Gmail reject the send and derails the model into error narration.
    """
    to = args.get("to") if isinstance(args, dict) else None
    if isinstance(to, str):
        cleaned = to.strip().strip('"').strip("'").strip()
        if cleaned != to:
            return {**args, "to": cleaned}
    return args




def _email_addr(value: str | None) -> str:
    return (parseaddr(value or "")[1] or "").strip().lower()


def _guard_reply_recipient(name: str, args: dict, email_input: dict) -> dict:
    if name not in {"write_email", "create_draft"} or not isinstance(args, dict):
        return args
    proposed = _email_addr(args.get("to"))
    original_sender = _email_addr(email_input.get("author"))
    mailbox_addresses = {_email_addr(email_input.get("to"))}
    mailbox_addresses.discard("")
    if original_sender and (not proposed or proposed in mailbox_addresses):
        return {**args, "to": original_sender}
    return args

def _last_write_email_args(messages) -> dict:
    for message in reversed(messages):
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") == "write_email":
                return dict(tool_call.get("args") or {})
    return {}


def _synthetic_write_email_message(args: dict) -> AIMessage:
    """An AI message carrying a write_email tool call produced outside the tool loop."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_email",
                "args": args,
                "id": f"llm_coerced_{uuid.uuid4().hex}",
                "type": "tool_call",
            }
        ],
    )


def _coerce_text_draft_to_tool_call(response, messages, run_id: str):
    """Turn a narrated redraft into a write_email tool call.

    Small local models sometimes answer a redraft request with the revised
    email as plain text even when tool_choice is required. Extracting the
    fields keeps the feedback loop on the normal HITL path instead of nudging
    the model until the run gives up.
    """
    text = response.content if isinstance(response.content, str) else ""
    if not text.strip():
        return None
    previous = _last_write_email_args(messages)
    try:
        extractor = llm.with_structured_output(_CoercedDraft)
        extracted = extractor.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "The assistant message below may contain a revised email draft, "
                        "possibly surrounded by narration. If it contains an actual email "
                        "body addressed to the correspondent, set is_email_draft=true and "
                        "extract the final email only: recipient (to), subject, and the "
                        "full body (content). If the message is NOT an email draft — an "
                        "error explanation, a question to the operator, a refusal, or "
                        "meta-commentary about the task — set is_email_draft=false and "
                        "leave the other fields empty."
                    ),
                },
                {"role": "user", "content": text},
            ],
            config=llm_invoke_config(run_id, "llm_call"),
        )
    except Exception as exc:
        print(f"draft coercion failed: {exc}")
        return None
    content = (extracted.content or "").strip()
    if not extracted.is_email_draft or not content:
        print("✏️ Redraft narration is not an email draft; not coercing")
        return None
    args = _normalize_recipient_args({
        "to": (extracted.to or "").strip() or previous.get("to", ""),
        "subject": (extracted.subject or "").strip() or previous.get("subject", ""),
        "content": content,
    })
    print("✏️ Redraft returned as text; coerced into a write_email tool call")
    return _synthetic_write_email_message(args)


# Long feedback conversations accumulate one AI+tool pair per round; on small
# local models the resulting prompt overflows the context window, which degrades
# drafts and slows every call. Recent turns plus the original email are enough.
_HISTORY_MAX_MESSAGES = 12


def _trim_history(messages: list) -> list:
    if len(messages) <= _HISTORY_MAX_MESSAGES:
        return messages
    head = messages[:1]
    tail = list(messages[-_HISTORY_MAX_MESSAGES:])
    # Never start the tail on a tool result whose AI tool_call was trimmed away:
    # OpenAI-compatible APIs reject orphaned tool messages.
    def _is_orphan_tool(message) -> bool:
        if isinstance(message, dict):
            return message.get("role") == "tool"
        return getattr(message, "type", None) == "tool"

    while tail and _is_orphan_tool(tail[0]):
        tail.pop(0)
    return head + tail


def _prompt_memory(content: str) -> str:
    limit = settings.memory_prompt_max_chars
    if limit <= 0 or len(content) <= limit:
        return content
    return content[:limit].rstrip() + "\n[truncated]"


def _invoke_llm(llm_obj, messages: list, invoke_config: dict):
    try:
        return llm_obj.invoke(messages, config=invoke_config)
    except TypeError as exc:
        if "config" not in str(exc):
            raise
        return llm_obj.invoke(messages)


def llm_call(state: State, store: BaseStore, config=None):
    """LLM decides which tool to call to handle the email."""
    response_prefs = _prompt_memory(
        get_memory(
            store,
            namespace("response_preferences"),
            agent_config.agent.response_preferences,
        )
    )
    writing_style = _prompt_memory(
        get_memory(
            store,
            namespace("writing_style"),
            agent_config.agent.writing_style_default,
        )
    )
    
    reply_language = "Veuillez rédiger la réponse en français (fr-FR)."
    contact_lang = state.get("contact_lang")
    if contact_lang:
        lang = contact_lang.lower()
        if lang == "en":
            reply_language = "Please write the response in English (en-US)."
        elif lang == "fr":
            reply_language = "Veuillez rédiger la réponse en français (fr-FR)."
        else:
            reply_language = f"Please write the response in {lang}."

    workflow_instructions_section = format_workflow_instructions(state.get("workflow_instructions"))

    messages = [
        {
            "role": "system",
            "content": agent_system_prompt.format(
                tools_prompt=tools_prompt,
                background=agent_config.agent.background,
                response_preferences=response_prefs,
                writing_style=writing_style,
                reply_language=reply_language,
                workflow_instructions_section=workflow_instructions_section,
            ),
        }
    ] + _trim_history(list(state["messages"]))
    run_id = _run_id_from_config(config)
    try:
        response = _invoke_llm(llm_with_tools, messages, llm_invoke_config(run_id, "llm_call"))
    except Exception as exc:
        response = _recover_tool_call_from_failed_generation(exc)
        if response is None:
            raise
    if state.get("redraft_requested") and not getattr(response, "tool_calls", None):
        coerced = _coerce_text_draft_to_tool_call(response, state["messages"], run_id)
        if coerced is not None:
            response = coerced
    return {"messages": [response]}


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
        # Optional "draft" carries the user's current (possibly hand-edited)
        # draft so the redraft starts from what the user sees, not from the
        # last server-side draft.
        draft = d.get("draft")
        data = {"feedback": raw_args, "draft": draft if isinstance(draft, dict) else None}
    else:
        data = None

    return type_, data


def _run_id_from_config(config) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id", ""))





def _trace_delta(before: dict, after: dict) -> dict:
    return {
        "input_tokens": max(0, int(after.get("input_tokens") or 0) - int(before.get("input_tokens") or 0)),
        "output_tokens": max(0, int(after.get("output_tokens") or 0) - int(before.get("output_tokens") or 0)),
        "total_tokens": max(0, int(after.get("total_tokens") or 0) - int(before.get("total_tokens") or 0)),
        "cost_eur": round(float(after.get("cost_eur") or 0.0) - float(before.get("cost_eur") or 0.0), 8),
    }


def _record_node_trace(
    run_id: str,
    node_name: str,
    status: str,
    started_at: str,
    started_perf: float,
    before: dict,
    error: str = "",
) -> None:
    if not run_id:
        return
    finished_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    latency_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
    after = before
    try:
        after = totals_for_run_node(run_id, node_name)
    except Exception:
        pass
    delta = _trace_delta(before, after)
    try:
        record_trace({
            "run_id": run_id,
            "node": node_name,
            "status": status,
            "latency_ms": latency_ms,
            "started_at": started_at,
            "finished_at": finished_at,
            **delta,
            "error": error,
        })
    except Exception:
        pass


def _traced_node(node_name: str, fn):
    signature = inspect.signature(fn)
    config_index = list(signature.parameters).index("config") if "config" in signature.parameters else None

    @wraps(fn)
    def wrapped(*args, **kwargs):
        config = kwargs.get("config")
        if config is None and config_index is not None and len(args) > config_index:
            config = args[config_index]
        run_id = _run_id_from_config(config)
        if not run_id:
            return fn(*args, **kwargs)
        before = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_eur": 0.0}
        try:
            before = totals_for_run_node(run_id, node_name)
        except Exception:
            pass
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        started_perf = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            _record_node_trace(run_id, node_name, "error", started_at, started_perf, before, error=str(exc))
            raise
        _record_node_trace(run_id, node_name, "ok", started_at, started_perf, before)
        return result

    return wrapped


def _blocked_tool_message(name: str, reason: str, tool_call_id: str) -> dict:
    return {
        "role": "tool",
        "content": (
            f"Security policy denied the '{name}' action: {reason}. "
            "Do not execute this action; call Done."
        ),
        "tool_call_id": tool_call_id,
    }


# Tools that actually leave the system (as opposed to reversible inbox actions like
# apply_label/archive_email, or create_draft which never sends). These are the ones
# output-audited right before execution, after HITL approval/edit has resolved.
SEND_TOOL_NAMES = {"write_email", "forward_email", "reply_all"}


def _send_action_id(name: str, state: State, tool_call: dict) -> str:
    """The id the security service counts a send against.

    Rate-limit slots are reserved when an action is *granted*, before the human
    has approved it, and released never. A draft the reviewer sent back for
    changes had therefore already spent the run's send budget, so every revision
    of it was refused for a send that never happened — the run could no longer
    complete by any route.

    A revision is the same logical send, so it reuses the first draft's id and the
    service treats the check as idempotent. Content, recipients and caps are still
    re-evaluated on every call; only the counter is not incremented twice. An
    unrelated second send in the same run still gets its own id and its own slot.
    """
    if name not in SEND_TOOL_NAMES or not state.get("redraft_requested"):
        return tool_call.get("id", "")
    return state.get("send_action_id") or tool_call.get("id", "")


def _restore_redactions(args: dict, state: State) -> dict:
    """Put redacted identifiers back into whatever the model produced.

    `redaction_map` is only populated when the drafting model is hosted and the
    security service actually redacted something; otherwise this is a no-op.
    """
    mapping = ((state.get("email_input") or {}).get("security") or {}).get("redaction_map") or {}
    if not mapping:
        return args

    def _restore(value):
        if isinstance(value, str):
            for placeholder in sorted(mapping, key=len, reverse=True):
                value = value.replace(placeholder, mapping[placeholder])
            return value
        if isinstance(value, list):
            return [_restore(item) for item in value]
        return value

    return {key: _restore(value) for key, value in args.items()}


def _send_content(args: dict) -> str:
    return args.get("content") or args.get("body") or args.get("note") or ""


def _output_audit_reason(name: str, args: dict, run_id: str) -> str | None:
    """Return a deny reason if the content about to be sent is flagged, else None.

    Runs after /authorize and any HITL approval/edit — the last gate before a real
    external send — so it catches leaked injected instructions in whatever content
    is truly about to leave the system, whether LLM-drafted or human-edited.
    """
    audit = audit_output(name, args.get("to", ""), args.get("subject", ""), _send_content(args), run_id)
    if audit.get("flagged"):
        reasons = ", ".join(audit.get("reasons") or []) or "flagged content"
        return f"output audit blocked before send: {reasons}"
    return None


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
_ARG_ADDR_RE = _re.compile(r"[\w.+-]+@[\w.-]+")
# Where each send tool gets its recipients from, now that none of them accept one.
_REPLY_TOOL_NAMES = {"write_email", "create_draft"}
_ROUTED_TOOL_NAMES = {"forward_email", "notify_internal"}
_ARG_TRUST_ORDER = {"TRUSTED": 0, "INTERNAL": 1, "UNTRUSTED": 2, "HOSTILE": 3}


def _max_arg_trust(a: str, b: str) -> str:
    return a if _ARG_TRUST_ORDER[a] >= _ARG_TRUST_ORDER[b] else b


def _operator_origin_trust(value: str) -> str | None:
    """Trust for a value the operator configured rather than the message supplied.

    Only these can lower an argument below the message's own trust: addresses on
    an internal domain, and addresses reachable through the roles directory. Both
    are set in the workspace, so an injected instruction cannot introduce one.
    """
    address = value.strip().lower()
    if "@" not in address:
        return None
    domain = address.split("@")[-1]
    if domain in set(settings.internal_domains):
        return "INTERNAL"
    try:
        directory = {
            email.strip().lower()
            for role in list_roles()
            for email in (role.emails or [])
            if email and email.strip()
        }
    except Exception:
        # A directory read must never decide the outcome by failing open.
        return None
    return "INTERNAL" if address in directory else None


def _derive_arg_trust(args: dict, security: dict | None) -> dict:
    """Label each tool argument with the trust the policy engine should enforce.

    Derivation is fail-closed. The model produced these arguments while reading
    untrusted mail, so every argument starts at the *message's* trust level and
    substring matches against the sanitized fields can only make it worse. The
    only way down is `_operator_origin_trust`: a recipient the workspace itself
    configured.

    This used to start every argument at TRUSTED and rely on finding the value
    verbatim inside a sanitized field. An injection that spelled an address out
    ("attacker at evil dot com") or had the model paraphrase it produced no
    match, so the argument was labelled TRUSTED and the flow check passed —
    exactly the case the check exists to catch. List-valued recipients were not
    inspected at all.
    """
    fields = (security or {}).get("fields") or {}
    floor = (security or {}).get("source_trust") or ("UNTRUSTED" if fields else "TRUSTED")

    def label(value: str) -> str:
        operator = _operator_origin_trust(value)
        worst = operator if operator is not None else floor
        needles = _ARG_ADDR_RE.findall(value) or [value.strip()]
        for needle in needles:
            needle_lower = needle.lower()
            for field in fields.values():
                field_value = str((field or {}).get("value") or "").lower()
                if needle_lower and needle_lower in field_value:
                    worst = _max_arg_trust(worst, (field or {}).get("trust", "UNTRUSTED"))
        return worst

    out = {}
    for arg_name, arg_value in args.items():
        if isinstance(arg_value, list):
            values = [str(v) for v in arg_value if str(v).strip()]
            out[arg_name] = reduce(_max_arg_trust, (label(v) for v in values), "TRUSTED") if values else "TRUSTED"
        elif isinstance(arg_value, str) and arg_value.strip():
            out[arg_name] = label(arg_value)
        else:
            # Non-text arguments carry no attacker-controlled address.
            out[arg_name] = "TRUSTED"
    return out


def _authorization_cache_key(run_id: str, name: str, tool_call: dict) -> tuple[str, str, str]:
    return (run_id, name, tool_call.get("id", ""))


def _category_for_run(state: State):
    """Look up the Category object driving this run, if any (for require_approval /
    external_send_allowed — per-workflow policy layered on top of the tool-level
    default in security/policy.yaml)."""
    name = state.get("category")
    if not name:
        return None
    cfg = load_categories(agent_instance_id=state["email_input"].get("agent_instance_id"))
    return next((c for c in cfg.categories if c.name == name), None)


def _off_workflow_action(category, name: str) -> str | None:
    """Reason to refuse `name`, or None when the workflow allows it.

    CaMeL step #2: control flow must not depend on untrusted mail. A run that
    matched a workflow executes only the tools that workflow opens — the model
    fills in content for an action already decided, it does not choose the
    action after reading the message.

    Runs that matched no workflow are not constrained here: there is no declared
    intent to enforce, and the tool-level default in `security/policy.yaml`
    remains what governs them.
    """
    if category is None:
        return None
    allowed = category.actions()
    if name in allowed:
        return None
    return (
        f"workflow '{category.name}' does not perform '{name}'"
        f" (allowed: {', '.join(allowed) if allowed else 'none'})"
    )


# Recipients a human typed in the approval screen, per tool call.
# Kept out of `args` so the model can never write into it: the model's args are
# what the reviewer is checking, and a value it could set would defeat the point
# of showing the destination at all.
_REVIEWER_RECIPIENT_KEY = "_recipients"

# Attachment ids a human staged via POST /run/{id}/attachments before approving.
# Same trust boundary as _REVIEWER_RECIPIENT_KEY: the model never sees or sets
# this — tool_node resolves the ids to bytes and injects them through context.
_REVIEWER_ATTACHMENTS_KEY = "_attachments"


def _reviewer_recipients(edited_args: dict, previous: list[str]) -> list[str] | None:
    """Addresses the reviewer put in the approval screen, or None.

    The preview already renders the resolved destination under this key; letting
    it come back changed is how a person redirects or adds a recipient. Returning
    None means "unchanged", so the trusted context stays in charge.

    Nothing is trusted about these values. They are re-authorized by the policy
    engine, checked against the workflow's `external_send_allowed`, and the send
    helpers enforce AGENT_OUTBOUND_ALLOWLIST underneath all of it.
    """
    if not isinstance(edited_args, dict) or _REVIEWER_RECIPIENT_KEY not in edited_args:
        return None
    raw = edited_args.get(_REVIEWER_RECIPIENT_KEY)
    # The approval screen sends one comma-separated field, the REST API may send
    # a list. Both mean the same thing.
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw or "").split(",")
    addresses = []
    for value in values:
        address = _email_addr(value)
        if address and address not in addresses:
            addresses.append(address)
    if not addresses or addresses == list(previous):
        return None
    return addresses


def _effective_recipients(name: str, args: dict, state: State, override: list[str] | None = None) -> list[str]:
    """Every address this tool call will actually reach.

    Send tools no longer take a recipient argument, so the addresses live in the
    trusted context tool_node supplies: the message's own sender for replies,
    the workflow's configured targets for routing. Reading them from `args` here
    would find nothing and silently pass every recipient check.
    """
    if override:
        # A person named these, in front of the draft. They still go through
        # every check below this call — this only changes *what* is checked.
        return list(dict.fromkeys(address.lower() for address in override if address))
    recipients: list[str] = []
    if name in _REPLY_TOOL_NAMES:
        reply = _trusted_reply_to(state)
        if reply:
            recipients.append(reply)
    if name in _ROUTED_TOOL_NAMES:
        recipients.extend(_trusted_route_targets(state))
    # Deliberately not scanning the argument text. A workflow note quotes the
    # original sender, and treating a quoted address as a recipient made an
    # internal-only notification look like an external send.
    # reply_all fans out to the thread, which the provider resolves at send time.
    return list(dict.fromkeys(r.lower() for r in recipients if r))


def _recipient_domains(args: dict) -> list[str]:
    to = args.get("to")
    text = " ".join(str(v) for v in to) if isinstance(to, list) else str(to or "")
    return [addr.split("@")[1].lower() for addr in _ARG_ADDR_RE.findall(text)]


def _external_recipients_blocked(category, args: dict, name: str = "", state: State | None = None) -> bool:
    """True when `category` restricts sends to internal domains and this call
    would reach a recipient outside AGENT_INTERNAL_DOMAINS. Never loosens
    tool-level policy — only ever adds a stricter, workflow-scoped check."""
    if category is None or category.external_send_allowed:
        return False
    if state is not None:
        addresses = _effective_recipients(name, args, state)
        domains = [a.split("@")[1] for a in addresses if "@" in a]
    else:
        domains = _recipient_domains(args)
    if not domains:
        return False
    internal = set(settings.internal_domains)
    if not internal:
        # external_send_allowed=false with no internal domains configured has
        # nothing safe to compare against — fail closed rather than no-op.
        return True
    return any(domain not in internal for domain in domains)


def _call_authorize_action(
    name: str,
    args: dict,
    run_id: str,
    action_id: str,
    arg_trust: dict | None,
    recipients: list[str] | None = None,
) -> dict:
    """Call authorize_action, passing only the keywords it accepts.

    Tests substitute their own stubs for this function, so the signature is
    probed rather than assumed.
    """
    try:
        signature = inspect.signature(authorize_action)
        params = signature.parameters.values()
        accepts_any_kwarg = any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params
        )
        supports_arg_trust = accepts_any_kwarg or "arg_trust" in signature.parameters
        supports_recipients = accepts_any_kwarg or "recipients" in signature.parameters
    except (TypeError, ValueError):
        supports_arg_trust = True
        supports_recipients = True

    extra: dict = {}
    if supports_arg_trust:
        extra["arg_trust"] = arg_trust
    if supports_recipients:
        extra["recipients"] = list(recipients or [])
    return authorize_action(name, args, run_id, action_id, **extra)


def _authorize_tool_action(
    name: str,
    args: dict,
    run_id: str,
    tool_call: dict,
    refresh: bool = False,
    arg_trust: dict | None = None,
    recipients: list[str] | None = None,
    action_id: str | None = None,
) -> dict:
    key = _authorization_cache_key(run_id, name, tool_call)
    if refresh or key not in _authorization_cache:
        authz = _call_authorize_action(
            name,
            args,
            run_id,
            action_id or tool_call.get("id", ""),
            arg_trust,
            recipients,
        )
        decision = authz.get("decision", "deny")
        reason = authz.get("reason", "no reason provided")
        if decision not in ("allow", "deny", "hitl"):
            reason = f"invalid authorization decision: {decision!r}"
            decision = "deny"
        _authorization_cache[key] = {"decision": decision, "reason": reason}
        while len(_authorization_cache) > _AUTHORIZATION_CACHE_MAX:
            del _authorization_cache[next(iter(_authorization_cache))]
    return _authorization_cache[key]


def update_memory_background(store, ns, messages, llm, invoke_config) -> None:
    """Run the preference-learning LLM call off the request path.

    The memory synthesis call adds a full LLM round-trip; running it in a
    daemon thread keeps redrafts and edits responsive while learning still
    lands in the store shortly after.
    """
    def _run():
        try:
            update_memory(store, ns, messages, llm, invoke_config)
        except Exception as exc:
            print(f"memory: background preference update failed: {exc}")

    threading.Thread(target=_run, name="memory-update", daemon=True).start()


def tool_node(state: State, store: BaseStore, config=None):
    """Execute tool calls, pausing for approval on gated tools and learning from decisions."""
    result = []
    sent = False
    redraft_requested = False
    redraft_feedback = None
    redraft_baseline = None
    redraft_cleared = False
    # The id the run's send budget is counted against; see _send_action_id.
    first_send_action_id = None
    run_id = _run_id_from_config(config)
    category_obj = _category_for_run(state)

    # Load automation rules at most once per call, lazily — only when a human
    # correction actually happens (rule learning is a no-op when disabled).
    _rules_cache: list = []

    def _suggest_rule(correction_type: str, details: dict) -> None:
        if not _rules_cache:
            _rules_cache.append(load_automation_rules())
        learned_email_input = {
            **state["email_input"],
            "category": state.get("category"),
            "category_display_name": state.get("category_display_name"),
            "priority": state.get("priority"),
            "workflow_owner": state.get("workflow_owner"),
            "workflow_approver": state.get("workflow_approver"),
            "workflow_instructions": state.get("workflow_instructions"),
        }
        suggest_rule_from_correction(
            _rules_cache[0], learned_email_input, correction_type, details
        )

    for tool_call in state["messages"][-1].tool_calls:
        name = tool_call["name"]
        raw_args = tool_call["args"]
        # Structure safety net for agent-generated bodies (never touches
        # human-edited args, which resume through a different path below).
        if name in {"write_email", "reply_all", "create_draft"} and isinstance(raw_args.get("content"), str):
            raw_args = {**raw_args, "content": ensure_email_paragraphs(raw_args["content"])}
        args = _normalize_recipient_args(apply_signature_to_args(name, raw_args))
        args = _guard_reply_recipient(name, args, state["email_input"])
        # Single restore point. When the drafting model ran on redacted content,
        # every placeholder it echoed back becomes the real value here — before
        # authorization, before the approval preview, before execution. Doing it
        # anywhere later risks a placeholder reaching a recipient.
        args = _restore_redactions(args, state)

        # The workflow decides which tool may act, not the model that just read
        # the message. Checked before authorization so an off-workflow call is
        # never even submitted as a candidate action: an injection that steered
        # a "draft a reply" workflow into forwarding or trashing would otherwise
        # get every downstream check asked about the tool it chose.
        reviewer_recipients: list[str] | None = None
        reviewer_attachment_ids: list[str] | None = None
        blocked_action = _off_workflow_action(category_obj, name)
        if blocked_action is not None:
            result.append(_blocked_tool_message(name, blocked_action, tool_call["id"]))
            continue

        authorization_decision = "hitl" if name in approval_set else "allow"
        arg_trust = _derive_arg_trust(args, state["email_input"].get("security"))
        effective_recipients = _effective_recipients(name, args, state)
        send_action_id = _send_action_id(name, state, tool_call)
        if name in SEND_TOOL_NAMES and not state.get("send_action_id"):
            first_send_action_id = first_send_action_id or send_action_id

        if settings.security_enabled:
            authz = _authorize_tool_action(
                name,
                args,
                run_id,
                tool_call,
                arg_trust=arg_trust,
                recipients=effective_recipients,
                action_id=send_action_id,
            )
            authorization_decision = authz["decision"]
            if authorization_decision == "deny":
                result.append(_blocked_tool_message(name, authz["reason"], tool_call["id"]))
                continue

        if _external_recipients_blocked(category_obj, args, name, state):
            result.append(_blocked_tool_message(
                name,
                f"category '{category_obj.name}' does not allow sending outside internal domains",
                tool_call["id"],
            ))
            continue

        if authorization_decision != "deny" and (
            state.get("category_policy") == "auto_draft"
            or (category_obj is not None and (category_obj.require_approval or category_obj.policy == "auto_draft"))
        ):
            authorization_decision = "hitl"

        if authorization_decision == "hitl":
            # The recipient is no longer a tool argument, but the person
            # approving must still see exactly where this will go. It is shown
            # under a separate key so the UI renders it read-only: editing the
            # destination is precisely what this design removes.
            recipients = effective_recipients
            preview_args = dict(args)
            if recipients:
                preview_args["_recipients"] = recipients
            description = format_action_description(name, preview_args)
            request = {
                "action_request": {"action": name, "args": args, "recipients": recipients},
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
                redraft_cleared = True
                # Answer the tool call FIRST (dangling-tool-call discipline: Groq
                # rejects an unanswered tool_call in the message sequence).
                result.append({
                    "role": "tool",
                    "content": f"User ignored the '{name}' draft. Ignore this email and call Done.",
                    "tool_call_id": tool_call["id"],
                })
                update_memory_background(
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
                    llm_invoke_config(run_id, "memory"),
                )
                _suggest_rule("ignored_draft", {"tool": name})
                continue

            if decision_type == "response":
                feedback = decision_data.get("feedback") if isinstance(decision_data, dict) else decision_data
                user_draft = decision_data.get("draft") if isinstance(decision_data, dict) else None
                redraft_requested = True
                redraft_feedback = feedback if isinstance(feedback, str) else str(feedback)
                if user_draft:
                    redraft_baseline = {k: v for k, v in user_draft.items() if isinstance(v, str)}
                result.append({
                    "role": "tool",
                    "content": (
                        f"The user requested changes to this draft: {feedback}. "
                        "Revise the draft by calling write_email again for approval. "
                        "Apply ONLY the requested change; keep everything else in the draft "
                        "(recipients, subject, wording, paragraph structure) exactly as it was. "
                        "Do not call Done until a revised draft has been approved and sent."
                    ),
                    "tool_call_id": tool_call["id"],
                })
                update_memory_background(
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
                    llm_invoke_config(run_id, "memory"),
                )
                _suggest_rule("draft_feedback", {"tool": name, "feedback": feedback})
                continue

            if decision_type == "edit":
                edited_args = decision_data or args
                chosen = _reviewer_recipients(edited_args, effective_recipients)
                if chosen is not None:
                    reviewer_recipients = chosen
                    effective_recipients = chosen
                if isinstance(edited_args, dict) and _REVIEWER_ATTACHMENTS_KEY in edited_args:
                    raw_ids = edited_args.get(_REVIEWER_ATTACHMENTS_KEY)
                    if isinstance(raw_ids, list):
                        reviewer_attachment_ids = [str(v) for v in raw_ids if v]
                if isinstance(edited_args, dict) and (
                    _REVIEWER_RECIPIENT_KEY in edited_args or _REVIEWER_ATTACHMENTS_KEY in edited_args
                ):
                    # Preview-only keys. They must never reach the tool as an
                    # argument, and must not show up in the learned-preference
                    # diff below as if the model had written them.
                    edited_args = {
                        k: v for k, v in edited_args.items()
                        if k not in (_REVIEWER_RECIPIENT_KEY, _REVIEWER_ATTACHMENTS_KEY)
                    }
                if edited_args != args:
                    # Rewrite the AI message's tool_call args so message history
                    # reflects what actually ran (immutable copy — reference pattern).
                    ai_message = state["messages"][-1]
                    updated_tool_calls = [
                        tc for tc in ai_message.tool_calls if tc["id"] != tool_call["id"]
                    ] + [{"type": "tool_call", "name": name, "args": edited_args, "id": tool_call["id"]}]
                    result.append(ai_message.model_copy(update={"tool_calls": updated_tool_calls}))
                    update_memory_background(
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
                        llm_invoke_config(run_id, "memory"),
                    )
                    _suggest_rule(
                        "edited_draft",
                        {"tool": name, "original": args, "edited": edited_args},
                    )
                args = apply_signature_to_args(name, edited_args)

            # accept and edit fall through to tool execution below

        if settings.security_enabled and authorization_decision == "hitl" and args != tool_call["args"]:
            authz = _authorize_tool_action(
                name,
                args,
                run_id,
                tool_call,
                refresh=True,
                arg_trust=_derive_arg_trust(args, state["email_input"].get("security")),
                recipients=_effective_recipients(name, args, state, reviewer_recipients),
                action_id=send_action_id,
            )
            if authz["decision"] == "deny":
                result.append(_blocked_tool_message(name, authz["reason"], tool_call["id"]))
                continue

        if args != tool_call["args"] and _external_recipients_blocked(category_obj, args, name, state):
            result.append(_blocked_tool_message(
                name,
                f"category '{category_obj.name}' does not allow sending outside internal domains",
                tool_call["id"],
            ))
            continue

        tool = tools_by_name_map.get(name)
        if tool is None:
            result.append({
                "role": "tool",
                "content": f"The '{name}' action is not available. Call Done.",
                "tool_call_id": tool_call["id"],
            })
            continue

        if settings.security_enabled and name in SEND_TOOL_NAMES:
            deny_reason = _output_audit_reason(name, args, run_id)
            if deny_reason:
                result.append(_blocked_tool_message(name, deny_reason, tool_call["id"]))
                continue

        email_id_token = current_email_id.set(state["email_input"].get("email_id"))
        thread_id_token = current_gmail_thread_id.set(
            state["email_input"].get("gmail_thread_id")
        )
        attachments_token = current_email_attachments.set(
            tuple(state["email_input"].get("attachments") or [])
        )
        # Recipients are graph context, never tool arguments. The model can say
        # anything it likes about where mail should go; these are the only
        # places it can actually go: the sender of the message being handled,
        # the workflow's configured targets, or an address a *person* typed in
        # the approval screen. That last one arrives through the HITL resume
        # payload, has already been re-authorized above, and is still subject to
        # AGENT_OUTBOUND_ALLOWLIST inside the send helpers.
        reply_to_token = current_reply_to.set(
            (reviewer_recipients[0] if reviewer_recipients else None) or _trusted_reply_to(state)
        )
        route_targets_token = current_route_targets.set(
            tuple(reviewer_recipients) if reviewer_recipients else _trusted_route_targets(state)
        )
        uploaded, _upload_notes = (
            load_uploaded_attachments(run_id, reviewer_attachment_ids)
            if reviewer_attachment_ids
            else ([], [])
        )
        uploaded_token = current_uploaded_attachments.set(tuple(uploaded))
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
            # A send failure after human approval must be terminal and visible to
            # the UI/API. Otherwise the LLM can call Done and make a failed Gmail
            # send look like a completed delivery.
            message = f"The '{name}' action could not be completed: {exc}."
            result.append({
                "role": "tool",
                "content": f"{message} Call Done.",
                "tool_call_id": tool_call["id"],
            })
            if name in approval_set:
                return {"messages": result, "email_send_failed": message}
            continue
        finally:
            current_uploaded_attachments.reset(uploaded_token)
            current_route_targets.reset(route_targets_token)
            current_reply_to.reset(reply_to_token)
            current_email_attachments.reset(attachments_token)
            current_gmail_thread_id.reset(thread_id_token)
            current_email_id.reset(email_id_token)
        result.append(
            {"role": "tool", "content": observation, "tool_call_id": tool_call["id"]}
        )
        if name in {"write_email", "forward_email", "reply_all"}:
            sent = True

    update = {"messages": result}
    if first_send_action_id:
        update["send_action_id"] = first_send_action_id
    if redraft_requested:
        update["redraft_requested"] = True
        if redraft_feedback:
            update["redraft_feedback"] = redraft_feedback
        update["redraft_baseline"] = redraft_baseline or {}
    elif redraft_cleared:
        update["redraft_requested"] = False
    if sent:
        update["email_sent"] = True
    return update


def force_redraft_after_feedback(state: State, config=None) -> dict:
    """Keep feedback runs pending until the model produces a revised draft."""
    last_message = state["messages"][-1]
    messages = []
    for tool_call in getattr(last_message, "tool_calls", []) or []:
        if tool_call["name"] == "Done":
            messages.append({
                "role": "tool",
                "content": (
                    "The user requested changes, so this run is still waiting for a revised draft. "
                    "Call write_email with the updated draft for approval; do not call Done yet."
                ),
                "tool_call_id": tool_call["id"],
            })
    if not messages:
        messages.append({
            "role": "user",
            "content": (
                "A human gave feedback on the previous draft. Call write_email with a revised "
                "draft for approval; do not call Done until that draft has been approved and sent."
            ),
        })
    return {"messages": messages}


def after_tools(state: State) -> Literal["llm_call", "redraft_direct", "__end__"]:
    """Sending, send failures, and auto-organization are terminal."""
    if (
        state.get("email_sent")
        or state.get("email_send_failed")
        or state.get("auto_organized")
        or state.get("automation_acted")
    ):
        return END
    if state.get("redraft_requested"):
        # Same wall as in _force_redraft_or_give_up: a refused action stays
        # refused, so revising the body forever cannot get past it.
        if _denied_since_last_feedback(state["messages"]):
            raise RedraftGiveUpError(POLICY_REFUSED_MESSAGE)
        if len(state["messages"]) > _MAX_RUN_MESSAGES:
            raise RedraftGiveUpError(LOOP_ABORTED_MESSAGE)
        return "redraft_direct"
    return "llm_call"


REDRAFT_GIVE_UP_MESSAGE = (
    "La retouche automatique a échoué — modifiez le texte directement "
    "ou renvoyez une instruction plus précise."
)

# Small models sometimes echo email headers into the body field ("À : …",
# "Sujet : …", "Corps :"). Strip any such leading header lines.
_CONTENT_HEADER_LINE = _re.compile(
    r"^\s*(?:to|à|a|destinataire|subject|sujet|objet|body|corps)\s*:.*$",
    _re.IGNORECASE,
)


def _strip_content_headers(content: str) -> str:
    lines = content.splitlines()
    index = 0
    while index < len(lines) and (
        not lines[index].strip() or _CONTENT_HEADER_LINE.match(lines[index])
    ):
        index += 1
    return "\n".join(lines[index:]).strip() if index else content.strip()


def _feedback_from_messages(messages) -> str:
    """Recover the latest feedback text from the message history (fallback path)."""
    for message in reversed(messages):
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, str) and _FEEDBACK_MARKER in content:
            feedback = content.split(_FEEDBACK_MARKER, 1)[1]
            return feedback.split(". Revise the draft by calling write_email", 1)[0].strip()
    return ""


def redraft_direct(state: State, store: BaseStore, config=None) -> dict:
    """Revise the pending draft with one structured LLM call (no tool-choice loop).

    The generic tool loop lets small local models 'forget' to re-call write_email
    after feedback, exhausting the nudge budget and giving up. Here the output
    schema IS the revised draft, so there is no tool decision to get wrong; the
    synthetic write_email tool call re-enters tool_node for a fresh approval.
    """
    previous = _last_write_email_args(state["messages"])
    baseline = state.get("redraft_baseline") or {}
    # The user's on-screen draft (with any manual edits) wins over the last
    # server-side draft so a retouche never resets hand edits.
    previous = {**previous, **{k: v for k, v in baseline.items() if isinstance(v, str) and v.strip()}}
    feedback = state.get("redraft_feedback") or _feedback_from_messages(state["messages"])
    response_prefs = _prompt_memory(
        get_memory(
            store,
            namespace("response_preferences"),
            agent_config.agent.response_preferences,
        )
    )
    writing_style = _prompt_memory(
        get_memory(
            store,
            namespace("writing_style"),
            agent_config.agent.writing_style_default,
        )
    )
    run_id = _run_id_from_config(config)

    # Strip any already-appended signature before the model ever sees the
    # body: otherwise it tends to "helpfully" add its own closing line on
    # top of the real signature block, producing a duplicate sign-off. The
    # canonical signature is re-appended after the LLM call by tool_node's
    # apply_signature_to_args, exactly once.
    body_for_prompt = strip_signature(previous.get("content", ""))
    prompt = [
        {
            "role": "system",
            "content": (
                "You revise an email draft according to the user's instruction. "
                "Apply ONLY the requested change; keep everything else (recipient, "
                "subject, wording, paragraph structure) exactly as it was. "
                "HARD RULE: write the revised email in the SAME language as the "
                "current draft — never translate it. If the draft is in French, "
                "the revision MUST be in French. "
                "HARD RULE: do not add a closing sign-off or signature line "
                "(name, title, company). The signature is appended "
                "automatically after your revision — never write one yourself. "
                "Return the complete revised email body, without any signature.\n"
                f"<Response preferences>\n{response_prefs}\n</Response preferences>\n"
                f"<Writing style>\n{writing_style}\n</Writing style>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Current draft:\nTo: {previous.get('to', '')}\n"
                f"Subject: {previous.get('subject', '')}\n"
                f"Body (signature already stripped, do not add one back):\n{body_for_prompt}\n\n"
                f"Instruction from the user: {feedback}"
            ),
        },
    ]
    for attempt in range(_REDRAFT_MAX_ATTEMPTS):
        try:
            revised = _invoke_llm(llm_redraft, prompt, llm_invoke_config(run_id, "redraft"))
        except Exception as exc:
            print(f"✏️ Redraft attempt {attempt + 1} failed: {exc}")
            continue
        content = _strip_content_headers((getattr(revised, "content", "") or "").strip())
        if not content:
            print(f"✏️ Redraft attempt {attempt + 1} returned no draft body")
            continue
        # Recipient and subject are NEVER taken from the model: a retouche is a
        # body revision, and small models corrupt addresses (dropped letters,
        # translations). Changing to/subject is a direct field edit in the UI.
        args = _normalize_recipient_args({
            "to": previous.get("to", ""),
            "subject": previous.get("subject", ""),
            "content": content,
        })
        return {"messages": [_synthetic_write_email_message(args)]}
    raise RedraftGiveUpError(REDRAFT_GIVE_UP_MESSAGE)


_REDRAFT_NUDGE_SNIPPETS = (
    "Call write_email with the updated draft for approval",
    "Call write_email with a revised draft for approval",
)
# Marks the start of a feedback round (appended by tool_node on a respond
# decision); nudge attempts reset at each new round.
_FEEDBACK_MARKER = "The user requested changes to this draft:"
_REDRAFT_MAX_ATTEMPTS = 3
# Start of the tool message tool_node appends when the security service refuses
# an action outright (see _blocked_tool_message).
_POLICY_DENIED_MARKER = "Security policy denied the"
# A ceiling no legitimate run approaches: a normal draft-approve-send run holds
# well under twenty messages, and the longest observed real feedback round held
# thirty. Anything past this is a loop, and every extra turn costs a full-window
# model call.
_MAX_RUN_MESSAGES = 60

POLICY_REFUSED_MESSAGE = (
    "La politique de sécurité a refusé l'envoi de ce brouillon — "
    "envoyez-le manuellement ou ajustez la politique."
)
LOOP_ABORTED_MESSAGE = (
    "La reprise automatique tournait en boucle et a été arrêtée — "
    "le brouillon précédent reste en attente."
)


class RedraftGiveUpError(RuntimeError):
    """Raised when the model repeatedly fails to produce a revised draft.

    Aborting the graph (instead of routing to END) lets the API keep the run
    pending with its previous draft rather than silently completing it.
    """


def _redraft_attempts(messages) -> int:
    count = 0
    for message in messages:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        if _FEEDBACK_MARKER in content:
            count = 0
        if any(s in content for s in _REDRAFT_NUDGE_SNIPPETS):
            count += 1
    return count


def _denied_since_last_feedback(messages) -> bool:
    """True when policy refused an action during the current feedback round."""
    denied = False
    for message in messages:
        content = (
            message.get("content") if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if not isinstance(content, str):
            continue
        if _FEEDBACK_MARKER in content:
            denied = False
        if _POLICY_DENIED_MARKER in content:
            denied = True
    return denied


def _force_redraft_or_give_up(state: State) -> Literal["force_redraft"]:
    messages = state["messages"]
    # A refusal is not a drafting mistake, so re-drafting cannot clear it. The
    # denial message tells the model to call Done, and this router used to answer
    # Done with "no, produce a revised draft" — a livelock that re-sent a
    # full-window prompt on every turn. One observed run reached 758 messages and
    # 379 model calls without ever being able to succeed.
    if _denied_since_last_feedback(messages):
        raise RedraftGiveUpError(POLICY_REFUSED_MESSAGE)
    # Belt and braces for any future loop this router does not know about.
    if len(messages) > _MAX_RUN_MESSAGES:
        raise RedraftGiveUpError(LOOP_ABORTED_MESSAGE)
    if _redraft_attempts(messages) >= _REDRAFT_MAX_ATTEMPTS:
        raise RedraftGiveUpError(
            f"No revised draft after {_REDRAFT_MAX_ATTEMPTS} attempts; "
            "the previous draft is kept pending."
        )
    return "force_redraft"


def should_continue(state: State) -> Literal["environment", "force_redraft", "__end__"]:
    """Route to tools, or keep feedback runs alive until a revised draft exists."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            if tool_call["name"] == "Done":
                return _force_redraft_or_give_up(state) if state.get("redraft_requested") else END
            return "environment"
    if state.get("redraft_requested"):
        return _force_redraft_or_give_up(state)
    return END


_SUBJECT_TOKEN_RE = _re.compile(r"\b\d+\b")
_SUBJECT_SPACE_RE = _re.compile(r"\s+")


def _triage_subject_shape(subject: str) -> str:
    shaped = _SUBJECT_TOKEN_RE.sub("#", subject.lower())
    return _SUBJECT_SPACE_RE.sub(" ", shaped).strip()


def _triage_sender_key(author: str) -> str:
    _name, address = parseaddr(author or "")
    return (address or author or "").strip().lower()


def _triage_cache_key(
    *,
    author: str,
    subject: str,
    triage_instructions: str,
    category_section: str,
) -> str:
    payload = {
        "user": current_user_id(),
        "instance": current_agent_instance_id(),
        "sender": _triage_sender_key(author),
        "subject_shape": _triage_subject_shape(subject),
        "rules_hash": hashlib.sha256(
            f"{triage_instructions}\n{category_section}".encode("utf-8")
        ).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"agora:triage:{digest}"


TRIAGE_CACHEABLE_DECISIONS = frozenset({"respond", "notify"})


def _triage_cacheable(
    state: State, attachments: list, category_update: dict, classification: str
) -> bool:
    return (
        settings.triage_cache_ttl_seconds > 0
        and classification in TRIAGE_CACHEABLE_DECISIONS
        and not attachments
        and not state["email_input"].get("security")
        and not category_update
    )


def _route_triage_decision(
    state: State,
    *,
    classification: str,
    category_update: dict,
    email_markdown: str,
) -> Command[Literal["llm_call", "environment", "__end__"]]:
    if classification == "respond":
        print("📧 Classification: RESPOND - This email requires a response")
        return Command(
            goto="llm_call",
            update={
                "classification_decision": classification,
                **category_update,
                "messages": [
                    {
                        "role": "user",
                        "content": f"Respond to the email: {email_markdown}",
                    }
                ],
            },
        )
    if classification == "ignore":
        print("🚫 Classification: IGNORE - This email can be safely ignored")
        if _can_auto_organize():
            return Command(
                goto="environment",
                update={
                    "classification_decision": classification,
                    **category_update,
                    "auto_organized": True,
                    "messages": [_auto_organize_message()],
                },
            )
        return Command(
            goto=END,
            update={"classification_decision": classification, **category_update},
        )
    if classification == "notify":
        print("🔔 Classification: NOTIFY - This email contains important information")
        return Command(
            goto=END,
            update={"classification_decision": classification, **category_update},
        )
    raise ValueError(f"Invalid classification: {classification}")


def triage_router(
    state: State, store: BaseStore, config=None
) -> Command[Literal["llm_call", "environment", "__end__"]]:
    """Classify the email as ignore / notify / respond and route accordingly.

    Category classification is already done by category_router. This node runs
    the triage LLM on emails that did not match a deterministic category policy.
    When categories are configured, the LLM may also tag one (B4 fallback).
    """
    sec = state["email_input"].get("security")
    if sec and (sec.get("injection_detected") or sec.get("classifier_unavailable")):
        print("🛡️ Classification: NOTIFY - forced by security (injection or classifier unavailable)")
        return Command(goto=END, update={"classification_decision": "notify"})

    author, to, subject, email_thread = parse_email(state["email_input"])
    atts = state["email_input"].get("attachments") or []
    att_str = format_attachments(atts)

    triage_instructions = _prompt_memory(
        get_memory(
            store,
            namespace("triage_preferences"),
            agent_config.agent.triage_instructions,
        )
    )

    # Build optional category section for B4 LLM fallback tagging.
    categories_config = load_categories()
    pre_classified_category = state.get("category")
    active_categories = [c for c in categories_config.categories if c.enabled]
    if categories_config.enabled and active_categories and not pre_classified_category:
        cat_lines = "\n".join(
            f"- {c.name}: {c.display_name}" for c in active_categories
        )
        category_section = (
            "\n< Email Categories >\n"
            "If this email clearly fits one of the categories below and no rule matched it, "
            "include the category name in your response. Leave it null if unsure.\n"
            f"{cat_lines}\n"
            "</ Email Categories >"
        )
    else:
        category_section = ""

    system_prompt = triage_system_prompt.format(
        background=agent_config.agent.background,
        triage_instructions=triage_instructions,
        category_section=category_section,
    )
    user_prompt = triage_user_prompt.format(
        author=author, to=to, subject=subject, email_thread=email_thread,
        attachments=att_str or "none",
    )
    email_markdown = format_email_markdown(subject, author, to, email_thread, attachments=atts)
    cache_key = _triage_cache_key(
        author=author,
        subject=subject,
        triage_instructions=triage_instructions,
        category_section=category_section,
    )
    cached = cache_get_json(cache_key)
    # "ignore" is never cached: a stale one drops real mail silently for a whole TTL.
    if isinstance(cached, dict) and cached.get("classification") in TRIAGE_CACHEABLE_DECISIONS:
        return _route_triage_decision(
            state,
            classification=str(cached["classification"]),
            category_update={},
            email_markdown=email_markdown,
        )

    run_id = _run_id_from_config(config)
    result = _invoke_llm(
        llm_router,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        llm_invoke_config(run_id, "triage"),
    )

    classification = result.classification

    # Merge LLM-suggested category (B4) only when category_router found no match.
    category_update: dict = {}
    if not pre_classified_category and result.category:
        category_by_name = {c.name: c for c in active_categories}
        if result.category in category_by_name:
            c = category_by_name[result.category]
            category_update = {
                "category": c.name,
                "category_display_name": c.display_name,
                "priority": c.priority,
                "template": c.template,
                "category_policy": c.policy,
                "workflow_owner": c.owner,
                "workflow_approver": c.approver,
                "workflow_route_to": c.route_to,
            }

    if category_update.get("category") and category_update.get("category_policy"):
        policy_command = _category_policy_command(
            state, categories_config, category_update["category"], category_update
        )
        if policy_command is not None:
            return policy_command

    if _triage_cacheable(state, atts, category_update, classification):
        cache_set_json(
            cache_key,
            {"classification": classification},
            settings.triage_cache_ttl_seconds,
        )

    return _route_triage_decision(
        state,
        classification=classification,
        category_update=category_update,
        email_markdown=email_markdown,
    )


overall_workflow = (
    StateGraph(State, input_schema=StateInput)
    .add_node("automation_router", _traced_node("automation_router", automation_router))
    .add_node("category_router", _traced_node("category_router", category_router))
    .add_node("triage_router", _traced_node("triage_router", triage_router))
    .add_node("llm_call", _traced_node("llm_call", llm_call))
    .add_node("environment", _traced_node("environment", tool_node))
    .add_node("force_redraft", _traced_node("force_redraft", force_redraft_after_feedback))
    .add_node("redraft_direct", _traced_node("redraft_direct", redraft_direct))
    .add_edge(START, "automation_router")
    .add_conditional_edges(
        "llm_call",
        should_continue,
        {"environment": "environment", "force_redraft": "force_redraft", END: END},
    )
    .add_edge("force_redraft", "llm_call")
    .add_edge("redraft_direct", "environment")
    .add_conditional_edges(
        "environment",
        after_tools,
        {"llm_call": "llm_call", "redraft_direct": "redraft_direct", END: END},
    )
)

_under_langgraph_platform = bool(os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT"))
checkpointer = None if _under_langgraph_platform else MemorySaver()
store = None if _under_langgraph_platform else InMemoryStore()
email_assistant = overall_workflow.compile(checkpointer=checkpointer, store=store)

graph = email_assistant
