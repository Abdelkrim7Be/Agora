from __future__ import annotations

from typing import TypedDict

from langgraph.graph import StateGraph, END


class AgentState(TypedDict):
    emails: list[dict]
    actions: list[dict]
    done: bool


def fetch_node(state: AgentState) -> AgentState:
    # TODO: pull emails via Gmail toolkit
    return state


def triage_node(state: AgentState) -> AgentState:
    # TODO: classify each email (reply / archive / escalate / ignore)
    return state


def act_node(state: AgentState) -> AgentState:
    # TODO: execute decided actions (send reply, archive, label)
    return state


def should_act(state: AgentState) -> str:
    return "act" if state["actions"] else END


def build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("fetch", fetch_node)
    g.add_node("triage", triage_node)
    g.add_node("act", act_node)

    g.set_entry_point("fetch")
    g.add_edge("fetch", "triage")
    g.add_conditional_edges("triage", should_act, {"act": "act", END: END})
    g.add_edge("act", END)

    return g


graph = build_graph().compile()
