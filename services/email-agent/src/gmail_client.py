from __future__ import annotations

import base64

from src.config import SERVICE_ROOT, settings
from src.state import EmailInput

# Full scope covers read (list/get) and modify (mark-as-read) plus send.
GMAIL_SCOPES = ["https://mail.google.com/"]


def gmail_resource():
    """Build an authenticated Gmail API resource (googleapiclient discovery object)."""
    from langchain_google_community.gmail.utils import (
        build_gmail_service,
        get_google_credentials,
    )

    creds = str(SERVICE_ROOT / settings.gmail_credentials_path)
    token = str(SERVICE_ROOT / settings.gmail_token_path)
    credentials = get_google_credentials(
        token_file=token,
        client_secrets_file=creds,
        scopes=GMAIL_SCOPES,
    )
    return build_gmail_service(credentials=credentials)


def fetch_unread(max_results: int, resource=None) -> list[dict]:
    """Return unread inbox message refs ([{id, threadId}, ...]), newest first."""
    resource = resource or gmail_resource()
    results = (
        resource.users()
        .messages()
        .list(userId="me", q="is:unread in:inbox", maxResults=max_results)
        .execute()
    )
    return results.get("messages", [])


def get_message(msg_id: str, resource=None) -> dict:
    """Fetch a full Gmail message by id."""
    resource = resource or gmail_resource()
    return resource.users().messages().get(userId="me", id=msg_id).execute()


def mark_as_read(msg_id: str, resource=None) -> None:
    """Remove the UNREAD label from a message."""
    resource = resource or gmail_resource()
    resource.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def fetch_thread(thread_id: str, resource=None) -> list[dict]:
    """Return all messages in a Gmail thread (oldest first, as the API orders them)."""
    resource = resource or gmail_resource()
    thread = resource.users().threads().get(userId="me", id=thread_id).execute()
    return thread.get("messages", [])


def _extract_message_part(payload: dict) -> str:
    """Pull the plain-text body out of a Gmail message payload, preferring text/plain."""
    if payload.get("parts"):
        for mime in ("text/plain", "text/html"):
            for part in payload["parts"]:
                if part.get("mimeType") == mime and part.get("body", {}).get("data"):
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                        "utf-8", errors="replace"
                    )
        # Nested multipart — recurse.
        for part in payload["parts"]:
            content = _extract_message_part(part)
            if content:
                return content
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    return ""


def _header(headers: list[dict], name: str, default: str) -> str:
    return next((h["value"] for h in headers if h["name"] == name), default)


def format_thread(
    messages: list[dict],
    max_messages: int | None = None,
    max_chars_per_message: int = 2000,
) -> str:
    """Render a thread's messages chronologically as labeled blocks.

    Keeps the most-recent `max_messages` (token budget; defaults to the configured
    cap) and truncates each body so one long message can't blow the context window.
    """
    limit = max_messages if max_messages is not None else settings.thread_max_messages
    if limit:
        messages = messages[-limit:]

    blocks = []
    for m in messages:
        headers = m["payload"]["headers"]
        author = _header(headers, "From", "Unknown Sender")
        date = _header(headers, "Date", "")
        body = _extract_message_part(m["payload"])
        if len(body) > max_chars_per_message:
            body = body[:max_chars_per_message] + "\n…[truncated]"
        blocks.append(f"From: {author}\nDate: {date}\n\n{body}")
    return "\n\n---\n\n".join(blocks)


def gmail_to_email_input(message: dict, thread_messages: list[dict] | None = None) -> EmailInput:
    """Map a raw Gmail message into the agent's EmailInput shape.

    Headers (author/to/subject) come from the triggering `message`; when
    `thread_messages` is given, `email_thread` carries the full conversation history
    so the agent understands prior exchanges before drafting.
    """
    headers = message["payload"]["headers"]
    if thread_messages:
        email_thread = format_thread(thread_messages)
    else:
        email_thread = _extract_message_part(message["payload"])
    return {
        "author": _header(headers, "From", "Unknown Sender"),
        "to": _header(headers, "To", "Unknown Recipient"),
        "subject": _header(headers, "Subject", "No Subject"),
        "email_thread": email_thread,
        "email_id": message["id"],
        "gmail_thread_id": message["threadId"],
    }
