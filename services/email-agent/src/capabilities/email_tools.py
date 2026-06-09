from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel


@tool
def write_email(to: str, subject: str, content: str) -> str:
    """Write and send an email."""
    # Mock implementation for Slice 1 — real Gmail send arrives in Slice 5.
    return f"Email sent to {to} with subject '{subject}' and content: {content}"


@tool
class Done(BaseModel):
    """E-mail has been sent."""

    done: bool


TOOLS = [write_email, Done]
TOOLS_PROMPT = """
1. write_email(to, subject, content) - Send emails to specified recipients
2. Done - E-mail has been sent
"""

# Tools that require human approval before executing (HITL gate).
REQUIRES_APPROVAL = {"write_email"}
