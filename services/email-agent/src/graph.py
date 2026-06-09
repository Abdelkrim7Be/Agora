from __future__ import annotations

from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command

from src.capabilities import load_capabilities, tools_by_name
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
    """Execute the tool calls requested by the LLM."""
    result = []
    for tool_call in state["messages"][-1].tool_calls:
        tool = tools_by_name_map[tool_call["name"]]
        observation = tool.invoke(tool_call["args"])
        result.append(
            {"role": "tool", "content": observation, "tool_call_id": tool_call["id"]}
        )
    return {"messages": result}


def should_continue(state: State) -> Literal["environment", "__end__"]:
    """Route to tools, or end once the Done tool is called."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            if tool_call["name"] == "Done":
                return END
            return "environment"
    return END


# Response agent: a minimal llm <-> tools loop.
agent_builder = StateGraph(State)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("environment", tool_node)
agent_builder.add_edge(START, "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {"environment": "environment", END: END},
)
agent_builder.add_edge("environment", "llm_call")
agent = agent_builder.compile()


def triage_router(state: State) -> Command[Literal["response_agent", "__end__"]]:
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
        goto = "response_agent"
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


# Overall workflow: triage first, then hand off to the response agent.
overall_workflow = (
    StateGraph(State, input_schema=StateInput)
    .add_node(triage_router)
    .add_node("response_agent", agent)
    .add_edge(START, "triage_router")
)

email_assistant = overall_workflow.compile()

# Backwards-compatible alias for callers importing `graph`.
graph = email_assistant
