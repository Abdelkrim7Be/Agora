from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel

from src.config import SERVICE_ROOT, settings


@tool
def write_email(to: str, subject: str, content: str) -> str:
    """Write and send an email."""
    if settings.dry_run:
        return f"Email sent to {to} with subject '{subject}' [dry run]"
    from langchain_google_community import GmailToolkit
    from langchain_google_community.gmail.utils import build_resource_service
    creds = str(SERVICE_ROOT / settings.gmail_credentials_path)
    token = str(SERVICE_ROOT / settings.gmail_token_path)
    resource = build_resource_service(credentials_path=creds, token_path=token)
    toolkit = GmailToolkit(api_resource=resource)
    send_tool = next(t for t in toolkit.get_tools() if t.name == "send_gmail_message")
    return send_tool.invoke({"message": content, "to": [to], "subject": subject})


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
