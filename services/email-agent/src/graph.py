from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt

from src.capabilities import approval_required, hitl_approved, load_capabilities, tools_by_name
from src.config import load_config
from src.gmail_client import format_attachments
from src.memory import UserPreferences, get_memory, namespace, update_memory
from src.prompts import (
    MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT,
    agent_system_prompt,
    triage_system_prompt,
    triage_user_prompt,
)
from src.state import RouterSchema, State, StateInput
from src.utils import format_email_markdown, parse_email

load_dotenv()

config = load_config()

tools, tools_prompt = load_capabilities(config.capabilities)
tools_by_name_map = tools_by_name(tools)
approval_set = approval_required(config.capabilities)

llm = init_chat_model("groq:llama-3.3-70b-versatile", temperature=0.0)
llm_router = llm.with_structured_output(RouterSchema)
llm_with_tools = llm.bind_tools(tools, tool_choice="any")
llm_memory = llm.with_structured_output(UserPreferences)


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


def tool_node(state: State, store: BaseStore):
    """Execute tool calls, pausing for approval on gated tools and learning from decisions."""
    result = []
    sent = False
    for tool_call in state["messages"][-1].tool_calls:
        name = tool_call["name"]
        args = tool_call["args"]

        if name in approval_set:
            decision = interrupt(
                {
                    "action": name,
                    "args": args,
                    "tool_call_id": tool_call["id"],
                    "description": f"Approve the '{name}' action?",
                }
            )
            if decision.get("type") == "reject":
                # Answer the tool call FIRST so the message sequence stays valid
                # (an assistant tool_call must be followed by a tool message — Groq
                # rejects a dangling call). Only then learn from the rejection.
                rejection = {
                    "role": "tool",
                    "content": f"Action '{name}' was rejected by the user. Do not retry it; call Done.",
                    "tool_call_id": tool_call["id"],
                }
                result.append(rejection)
                # Teach: this kind of email should not be classified as respond.
                update_memory(
                    store,
                    namespace("triage_preferences"),
                    list(state["messages"])
                    + result
                    + [
                        {
                            "role": "user",
                            "content": (
                                f"The user rejected the draft '{name}'. "
                                "Emails like this should not be classified as respond. "
                                f"{MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}"
                            ),
                        }
                    ],
                    llm_memory,
                )
                continue

            edited_args = decision.get("args")
            if edited_args and edited_args != args:
                # Teach: the draft style didn't match preferences — capture the diff.
                update_memory(
                    store,
                    namespace("response_preferences"),
                    [
                        {
                            "role": "user",
                            "content": (
                                f"The user edited the email draft. "
                                f"Original: {args}. "
                                f"Edited: {edited_args}. "
                                f"{MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}"
                            ),
                        }
                    ],
                    llm_memory,
                )
            args = edited_args or args

        tool = tools_by_name_map[name]
        if name in approval_set:
            tok = hitl_approved.set(True)
            try:
                observation = tool.invoke(args)
            finally:
                hitl_approved.reset(tok)
        else:
            observation = tool.invoke(args)
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
    """Sending the email is the terminal action; otherwise keep working the loop."""
    if state.get("email_sent"):
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


def triage_router(state: State, store: BaseStore) -> Command[Literal["llm_call", "__end__"]]:
    """Classify the email as ignore / notify / respond and route accordingly."""
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
    .add_node("triage_router", triage_router)
    .add_node("llm_call", llm_call)
    .add_node("environment", tool_node)
    .add_edge(START, "triage_router")
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
