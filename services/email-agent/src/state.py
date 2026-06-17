from __future__ import annotations

from pydantic import BaseModel, Field
from typing_extensions import Literal, NotRequired, TypedDict
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


class EmailInput(TypedDict):
    author: str
    to: str
    subject: str
    email_thread: str
    # Gmail identifiers — present when an email comes from the poller, absent on the
    # manual /run path. NotRequired keeps that path valid without these fields.
    email_id: NotRequired[str]
    gmail_thread_id: NotRequired[str]
    attachments: NotRequired[list]
    labels: NotRequired[list[str]]
    automation: NotRequired[dict]
    # Verdict from the security service /sanitize, attached by the poller when
    # AGENT_SECURITY_ENABLED=true. Absent on the manual /run path. triage_router reads it.
    security: NotRequired[dict]


class StateInput(TypedDict):
    email_input: EmailInput


class State(MessagesState):
    # MessagesState provides the `messages` key; we add email-specific fields.
    email_input: EmailInput
    classification_decision: Literal["ignore", "respond", "notify"]
    # Set once an email has actually been sent — the run's terminal action.
    email_sent: bool
    # Set when an approved send action fails after the human decision.
    email_send_failed: NotRequired[str]
    # Set when deterministic post-triage organization has run; terminal after tools.
    auto_organized: NotRequired[bool]
    automation_acted: NotRequired[bool]
    # Set after human feedback asks for a revised draft; the run must not finish
    # until a new gated write_email draft has been produced and approved.
    redraft_requested: NotRequired[bool]
