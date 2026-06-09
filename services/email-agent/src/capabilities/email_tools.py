from __future__ import annotations

from typing import Dict, List

from langchain_core.tools import BaseTool, tool
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


def get_tools() -> List[BaseTool]:
    return TOOLS


def get_tools_by_name(tools: List[BaseTool] | None = None) -> Dict[str, BaseTool]:
    if tools is None:
        tools = get_tools()
    return {t.name: t for t in tools}
