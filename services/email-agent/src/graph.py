from __future__ import annotations

from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command, interrupt

from src.capabilities import approval_required, load_capabilities, tools_by_name
from src.config import load_config
from src.prompts import (
    agent_system_prompt,
    triage_system_prompt,
    triage_user_prompt,
)
from src.state import RouterSchema, State, StateInput
from src.utils import format_email_markdown, parse_email

load_dotenv()

# Behavior (persona, triage rules, tone) and capability flags come from config.yaml.
config = load_config()

tools, tools_prompt = load_capabilities(config.capabilities)
tools_by_name_map = tools_by_name(tools)
approval_set = approval_required(config.capabilities)

# Groq is the primary LLM for all agents (see CLAUDE.md).
llm = init_chat_model("groq:llama-3.3-70b-versatile", temperature=0.0)
llm_router = llm.with_structured_output(RouterSchema)
llm_with_tools = llm.bind_tools(tools, tool_choice="any")


def llm_call(state: State):
    """LLM decides which tool to call to handle the email."""
    return {
        "messages": [
            llm_with_tools.invoke(
                [
                    {
                        "role": "system",
                        "content": agent_system_prompt.format(
                            tools_prompt=tools_prompt,
                            background=config.agent.background,
                            response_preferences=config.agent.response_preferences,
                        ),
                    }
                ]
                + state["messages"]
            )
        ]
    }


def tool_node(state: State):
    """Execute the tool calls requested by the LLM, pausing for approval on gated tools."""
    result = []
    sent = False
    for tool_call in state["messages"][-1].tool_calls:
        name = tool_call["name"]
        args = tool_call["args"]

        if name in approval_set:
            # Pause the graph and surface the pending action to the caller.
            decision = interrupt(
                {
                    "action": name,
                    "args": args,
                    "tool_call_id": tool_call["id"],
                    "description": f"Approve the '{name}' action?",
                }
            )
            if decision.get("type") == "reject":
                result.append(
                    {
                        "role": "tool",
                        "content": f"Action '{name}' was rejected by the user. Do not retry it; call Done.",
                        "tool_call_id": tool_call["id"],
                    }
                )
                continue
            # Approved — allow the caller to override args (e.g. an edited draft).
            args = decision.get("args") or args

        tool = tools_by_name_map[name]
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


def triage_router(state: State) -> Command[Literal["llm_call", "__end__"]]:
    """Classify the email as ignore / notify / respond and route accordingly."""
    author, to, subject, email_thread = parse_email(state["email_input"])
    system_prompt = triage_system_prompt.format(
        background=config.agent.background,
        triage_instructions=config.agent.triage_instructions,
    )
    user_prompt = triage_user_prompt.format(
        author=author, to=to, subject=subject, email_thread=email_thread
    )
    email_markdown = format_email_markdown(subject, author, to, email_thread)

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


# Single flat workflow: triage, then the llm <-> tools loop. Keeping the response
# agent's nodes in this graph (rather than a nested subgraph) lets interrupts in
# tool_node pause and resume reliably against the checkpointer below.
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

# MemorySaver persists run state so interrupts can pause/resume within a process.
# (Swap for SqliteSaver/Postgres when runs must survive a restart — see Slice 5+.)
checkpointer = MemorySaver()
email_assistant = overall_workflow.compile(checkpointer=checkpointer)

# Backwards-compatible alias for callers importing `graph`.
graph = email_assistant
