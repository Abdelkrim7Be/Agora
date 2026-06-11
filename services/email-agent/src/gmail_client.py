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


def gmail_to_email_input(message: dict) -> EmailInput:
    """Map a raw Gmail message into the agent's EmailInput shape."""
    headers = message["payload"]["headers"]
    return {
        "author": _header(headers, "From", "Unknown Sender"),
        "to": _header(headers, "To", "Unknown Recipient"),
        "subject": _header(headers, "Subject", "No Subject"),
        "email_thread": _extract_message_part(message["payload"]),
        "email_id": message["id"],
        "gmail_thread_id": message["threadId"],
    }
