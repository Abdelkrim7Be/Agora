from __future__ import annotations

from pydantic import BaseModel, Field
from typing_extensions import Literal, TypedDict
from langgraph.graph import MessagesState


class RouterSchema(BaseModel):
    """Analyze the unread email and route it according to its content."""

    reasoning: str = Field(
        description="Step-by-step reasoning behind the classification."
    )
    classification: Literal["ignore", "respond", "notify"] = Field(
        description="The classification of an email: 'ignore' for irrelevant emails, "
        "'notify' for important information that doesn't need a response, "
        "'respond' for emails that need a reply",
    )


class StateInput(TypedDict):
    # The input handed to the graph: a single email.
    email_input: dict


class State(MessagesState):
    # MessagesState provides the `messages` key; we add email-specific fields.
    email_input: dict
    classification_decision: Literal["ignore", "respond", "notify"]
