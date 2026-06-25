from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import getaddresses

try:
    import pypdf as _pypdf
except ImportError:
    _pypdf = None  # type: ignore[assignment]

from src.config import SERVICE_ROOT, settings
from src.token_store import prepared_token_file
from src.state import EmailInput

# Full scope covers read (list/get) and modify (mark-as-read) plus send.
GMAIL_SCOPES = ["https://mail.google.com/"]


def gmail_resource(user_id: str | None = None):
    """Build an authenticated Gmail API resource (googleapiclient discovery object)."""
    from langchain_google_community.gmail.utils import (
        build_gmail_service,
        get_google_credentials,
    )

    creds = str(SERVICE_ROOT / settings.gmail_credentials_path)
    with prepared_token_file(user_id) as token:
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


def _is_automated_address(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("no-reply", "noreply", "donotreply", "do-not-reply"))


def fetch_sent(max_messages: int = 50, resource=None) -> list[dict]:
    """Return usable sent-mail samples for writing-style learning.

    The returned samples include distilled metadata and body text only; callers decide
    whether to persist a learned profile. Trivial messages and automated recipients
    are excluded so the profile is based on real authored mail.
    """
    resource = resource or gmail_resource()
    refs = (
        resource.users()
        .messages()
        .list(userId="me", q="in:sent", maxResults=max_messages)
        .execute()
        .get("messages", [])
    )
    samples: list[dict] = []
    for ref in refs:
        message = get_message(ref["id"], resource=resource)
        body = _extract_message_part(message.get("payload", {})).strip()
        to = _header_value(message, "To")
        if len(body) < 20 or _is_automated_address(to):
            continue
        samples.append(
            {
                "id": message.get("id", ref.get("id")),
                "thread_id": message.get("threadId", ref.get("threadId")),
                "to": to,
                "subject": _header_value(message, "Subject"),
                "date": _header_value(message, "Date"),
                "body": body,
            }
        )
    return samples


def list_messages_by_label(label_id: str, max_results: int, resource=None) -> list[dict]:
    """Return message refs carrying a Gmail label id."""
    resource = resource or gmail_resource()
    results = (
        resource.users()
        .messages()
        .list(userId="me", labelIds=[label_id], maxResults=max_results)
        .execute()
    )
    return results.get("messages", [])


def search_messages(query: str, max_results: int, resource=None) -> list[dict]:
    """Return message refs for an arbitrary Gmail search query."""
    resource = resource or gmail_resource()
    results = (
        resource.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    return results.get("messages", [])


def list_inbox(max_results: int, resource=None) -> list[dict]:
    """Return summarized inbox messages (newest first) for the management view.

    Uses the lightweight metadata format (headers + snippet, no body) so the UI can
    list many messages cheaply. Read and unread are both returned; the UNREAD label
    is surfaced so the caller can render state and drive mark read/unread actions.
    """
    resource = resource or gmail_resource()
    refs = (
        resource.users()
        .messages()
        .list(userId="me", q="in:inbox", maxResults=max_results)
        .execute()
        .get("messages", [])
    )
    summaries: list[dict] = []
    for ref in refs:
        message = (
            resource.users()
            .messages()
            .get(
                userId="me",
                id=ref["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        labels = message.get("labelIds", [])
        summaries.append(
            {
                "id": message["id"],
                "thread_id": message.get("threadId"),
                "from": _header_value(message, "From"),
                "subject": _header_value(message, "Subject"),
                "snippet": message.get("snippet", ""),
                "date": _header_value(message, "Date"),
                "unread": "UNREAD" in labels,
            }
        )
    return summaries


def watch_mailbox(topic_name: str | None = None, resource=None) -> dict:
    """Register a Gmail push notification watch for inbox changes."""
    topic = topic_name or settings.gmail_webhook_topic
    if not topic:
        raise RuntimeError("GMAIL_WEBHOOK_TOPIC is required to register a Gmail watch")
    resource = resource or gmail_resource()
    return (
        resource.users()
        .watch(
            userId="me",
            body={
                "topicName": topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        )
        .execute()
    )


def fetch_history_message_refs(start_history_id: str, resource=None) -> list[dict]:
    """Return unique message refs mentioned by Gmail history since start_history_id."""
    resource = resource or gmail_resource()
    refs: list[dict] = []
    seen: set[str] = set()
    page_token = None
    while True:
        kwargs = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded", "labelAdded"],
            "labelId": "INBOX",
        }
        if page_token:
            kwargs["pageToken"] = page_token
        results = resource.users().history().list(**kwargs).execute()
        for entry in results.get("history", []):
            events = entry.get("messagesAdded", []) + entry.get("labelsAdded", [])
            for event in events:
                message = event.get("message", {})
                msg_id = message.get("id")
                if msg_id and msg_id not in seen:
                    seen.add(msg_id)
                    refs.append({"id": msg_id, "threadId": message.get("threadId")})
        page_token = results.get("nextPageToken")
        if not page_token:
            return refs


def get_message(msg_id: str, resource=None) -> dict:
    """Fetch a full Gmail message by id."""
    resource = resource or gmail_resource()
    return resource.users().messages().get(userId="me", id=msg_id).execute()


def _dry_run_result(action: str, **fields) -> dict:
    return {"dry_run": True, "action": action, **fields}


def _encode_message(message: EmailMessage) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _send_email_message(
    to: str | list[str],
    subject: str,
    body: str,
    thread_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
    resource=None,
) -> dict:
    resource = resource or gmail_resource()
    recipients = to if isinstance(to, list) else [to]
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    for name, value in (extra_headers or {}).items():
        if value:
            message[name] = value
    message.set_content(body)
    gmail_message = {"raw": _encode_message(message)}
    if thread_id:
        gmail_message["threadId"] = thread_id
    return (
        resource.users()
        .messages()
        .send(userId="me", body=gmail_message)
        .execute()
    )


def _message_headers(message: dict) -> list[dict]:
    return message.get("payload", {}).get("headers", [])


def _header_value(message: dict, name: str, default: str = "") -> str:
    return _header(_message_headers(message), name, default)


def _prefixed_subject(prefix: str, subject: str) -> str:
    return subject if subject.lower().startswith(prefix.lower()) else f"{prefix}{subject}"


def _email_addresses(*values: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for _name, address in getaddresses([v for v in values if v]):
        address = address.strip()
        key = address.lower()
        if address and key not in seen:
            seen.add(key)
            results.append(address)
    return results


def _self_address(resource) -> str:
    """Return the authenticated account's email address, for reply-all self-exclusion.

    Degrades to "" on any failure so reply-all never crashes — worst case we keep
    the account in the recipients (the prior behaviour) rather than failing the send.
    """
    try:
        profile = resource.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")
    except Exception:
        return ""


def modify_labels(
    message_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
    resource=None,
    respect_dry_run: bool = True,
) -> dict:
    """Add/remove Gmail labels on a message.

    `respect_dry_run=False` lets internal housekeeping (e.g. mark-as-read in the
    poller) run even under AGENT_DRY_RUN — dry-run gates agent-proposed actions,
    not the dedup bookkeeping the poller relies on.
    """
    add_label_ids = add_label_ids or []
    remove_label_ids = remove_label_ids or []
    if respect_dry_run and settings.dry_run:
        return _dry_run_result(
            "modify_labels",
            message_id=message_id,
            add_label_ids=add_label_ids,
            remove_label_ids=remove_label_ids,
        )
    resource = resource or gmail_resource()
    return (
        resource.users()
        .messages()
        .modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": add_label_ids, "removeLabelIds": remove_label_ids},
        )
        .execute()
    )


def mark_as_read(msg_id: str, resource=None) -> None:
    """Remove the UNREAD label from a message.

    Poller housekeeping — runs even in dry-run so the poller doesn't reprocess the
    same unread emails every cycle.
    """
    modify_labels(msg_id, remove_label_ids=["UNREAD"], resource=resource, respect_dry_run=False)


def mark_as_unread(msg_id: str, resource=None) -> dict:
    """Add the UNREAD label to a message."""
    return modify_labels(msg_id, add_label_ids=["UNREAD"], resource=resource)


def archive_message(msg_id: str, resource=None) -> dict:
    """Archive a message by removing it from the inbox."""
    return modify_labels(msg_id, remove_label_ids=["INBOX"], resource=resource)


def trash_message(msg_id: str, resource=None) -> dict:
    """Move a message to Gmail trash."""
    if settings.dry_run:
        return _dry_run_result("trash_message", message_id=msg_id)
    resource = resource or gmail_resource()
    return resource.users().messages().trash(userId="me", id=msg_id).execute()


def list_labels(resource=None) -> list[dict]:
    """Return Gmail labels for the current mailbox."""
    resource = resource or gmail_resource()
    results = resource.users().labels().list(userId="me").execute()
    return results.get("labels", [])


def ensure_label(name: str, resource=None) -> str:
    """Return a Gmail label id, creating the label when missing."""
    if settings.dry_run:
        return f"dry-run-label:{name}"
    resource = resource or gmail_resource()
    for label in list_labels(resource=resource):
        if label.get("name") == name:
            return label["id"]
    created = (
        resource.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return created["id"]


def create_draft(
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    resource=None,
) -> dict:
    """Create a Gmail draft without sending it."""
    if settings.dry_run:
        return _dry_run_result(
            "create_draft",
            to=to,
            subject=subject,
            thread_id=thread_id,
        )
    resource = resource or gmail_resource()
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    draft_message = {"raw": _encode_message(message)}
    if thread_id:
        draft_message["threadId"] = thread_id
    return (
        resource.users()
        .drafts()
        .create(userId="me", body={"message": draft_message})
        .execute()
    )


def forward_message(message_id: str, to: str, note: str, resource=None) -> dict:
    """Forward a Gmail message to a recipient, optionally with a note."""
    if settings.dry_run:
        return _dry_run_result("forward_message", message_id=message_id, to=to)
    resource = resource or gmail_resource()
    original = get_message(message_id, resource=resource)
    subject = _prefixed_subject("Fwd: ", _header_value(original, "Subject", "No Subject"))
    body = (
        f"{note.strip()}\n\n" if note.strip() else ""
    ) + (
        "---------- Forwarded message ---------\n"
        f"From: {_header_value(original, 'From', 'Unknown Sender')}\n"
        f"Date: {_header_value(original, 'Date')}\n"
        f"Subject: {_header_value(original, 'Subject', 'No Subject')}\n"
        f"To: {_header_value(original, 'To', 'Unknown Recipient')}\n\n"
        f"{_extract_message_part(original.get('payload', {}))}"
    )
    return _send_email_message(to=to, subject=subject, body=body, resource=resource)


def reply_all_message(message_id: str, body: str, resource=None) -> dict:
    """Reply to all participants on a Gmail message's thread."""
    if settings.dry_run:
        return _dry_run_result("reply_all_message", message_id=message_id)
    resource = resource or gmail_resource()
    original = get_message(message_id, resource=resource)
    recipients = _email_addresses(
        _header_value(original, "From"),
        _header_value(original, "To"),
        _header_value(original, "Cc"),
    )
    # Exclude our own address so reply-all doesn't email the agent itself (which
    # would also land back in the inbox and risk the poller reprocessing it).
    self_addr = _self_address(resource).lower()
    if self_addr:
        recipients = [r for r in recipients if r.lower() != self_addr]
    subject = _prefixed_subject("Re: ", _header_value(original, "Subject", "No Subject"))
    message_id_header = _header_value(original, "Message-ID")
    extra_headers = {}
    if message_id_header:
        extra_headers = {"In-Reply-To": message_id_header, "References": message_id_header}
    return _send_email_message(
        to=recipients,
        subject=subject,
        body=body,
        thread_id=original.get("threadId"),
        extra_headers=extra_headers,
        resource=resource,
    )


def fetch_thread(thread_id: str, resource=None) -> list[dict]:
    """Return all messages in a Gmail thread (oldest first, as the API orders them)."""
    resource = resource or gmail_resource()
    thread = resource.users().threads().get(userId="me", id=thread_id).execute()
    return thread.get("messages", [])


def download_attachment(message_id: str, attachment_id: str, resource=None) -> bytes:
    """Download a raw Gmail attachment by id and return decoded bytes."""
    resource = resource or gmail_resource()
    data = (
        resource.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    return base64.urlsafe_b64decode(data["data"])


def extract_pdf_text(data: bytes, max_chars: int) -> str:
    """Extract text from PDF bytes, capped at max_chars characters."""
    from io import BytesIO

    if _pypdf is None:
        return ""
    reader = _pypdf.PdfReader(BytesIO(data))
    text_parts = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n".join(text_parts)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n…[truncated]"
    return full_text


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


def _walk_for_attachments(part: dict, results: list) -> None:
    filename = part.get("filename", "")
    if filename:
        results.append({
            "filename": filename,
            "mime_type": part.get("mimeType", ""),
            "size": part.get("body", {}).get("size", 0),
            "attachment_id": part.get("body", {}).get("attachmentId"),
        })
    for child in part.get("parts", []):
        _walk_for_attachments(child, results)


def extract_attachments(payload: dict) -> list[dict]:
    """Collect attachment metadata from a Gmail message payload (recursive walk)."""
    results: list[dict] = []
    _walk_for_attachments(payload, results)
    return results


def format_attachments(attachments: list[dict]) -> str:
    """Render attachment list as a single-line summary, or '' when empty."""
    if not attachments:
        return ""
    parts = []
    for att in attachments:
        size = att.get("size") or 0
        size_str = f"{size // 1024} KB" if size >= 1024 else f"{size} B"
        parts.append(f"{att['filename']} ({att['mime_type']}, {size_str})")
    return ", ".join(parts)


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
    A non-positive cap (<= 0) means no limit — include the whole thread.
    """
    # Sort by internalDate so chronological order doesn't depend on the API's
    # ordering. Gmail returns oldest-first today, but this makes it a guarantee.
    messages = sorted(messages, key=lambda m: int(m.get("internalDate", 0)))

    limit = max_messages if max_messages is not None else settings.thread_max_messages
    if limit and limit > 0:
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
        "attachments": extract_attachments(message["payload"]),
        "labels": message.get("labelIds", []),
    }
