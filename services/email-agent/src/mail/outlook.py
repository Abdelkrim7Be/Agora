"""Outlook mailboxes over Microsoft Graph.

The contract in `base.py` was shaped by Gmail, so most of the work here is
translation. The mappings that are not obvious:

* **Labels.** Graph has no label ids. Custom labels map to Outlook *categories*
  (free-text strings on the message), and the two Gmail pseudo-labels the rest
  of the codebase relies on are special-cased: ``UNREAD`` becomes the ``isRead``
  flag and ``INBOX`` becomes folder membership. Keeping them inside
  `modify_labels` is what lets `src/capabilities/inbox_tools.py`, the
  auto-organize path and `mark_as_read` work unchanged — they all express
  themselves as label edits.
* **Cursor.** Gmail's numeric `historyId` becomes a `deltaLink` URL. Delta links
  carry no ordering, so `cursor_is_newer` can only answer "different", which is
  why it is a provider method instead of the numeric compare it used to be.
* **Attachments.** `to_email_input` must stay pure — no I/O — the way the Gmail
  one is, so `get_message` expands attachment metadata into the message payload
  and normalisation just reads it back out.

Not implemented: `watch_mailbox`. Graph change notifications need a publicly
reachable validation endpoint and a renewal loop of their own; Outlook instances
run on the polling path until that is built.
"""

from __future__ import annotations

import base64
import html as _html
import re
from typing import Any

import httpx

from src.config import settings
from src.outbound_guard import email_addresses, enforce_outbound_allowlist
from src.outlook_oauth import GRAPH_BASE, refresh_access_token
from src.send_mode import effective_dry_run
from src.state import EmailInput
from src.utils import THREAD_BLOCK_SEPARATOR

# Gmail label ids that the rest of the codebase treats as mailbox state rather
# than user-visible labels.
UNREAD = "UNREAD"
INBOX = "INBOX"

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\n{3,}")


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = _HTML_TAG_RE.sub("", text)
    return _WHITESPACE_RE.sub("\n\n", _html.unescape(text)).strip()


def _body_text(message: dict) -> str:
    body = message.get("body") or {}
    content = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        return _html_to_text(content)
    return content.strip() or (message.get("bodyPreview") or "").strip()


def _address(recipient: dict | None) -> str:
    """Render a Graph recipient as an RFC 822 style "Name <addr>" string.

    The rest of the pipeline (junk gate, contacts, category rules) parses
    author/to strings, so Outlook has to produce the same shape Gmail headers do.
    """
    if not recipient:
        return ""
    entry = recipient.get("emailAddress") or {}
    name = (entry.get("name") or "").strip()
    address = (entry.get("address") or "").strip()
    if name and address and name.lower() != address.lower():
        return f"{name} <{address}>"
    return address or name


def _recipients(values: list[dict] | None) -> str:
    return ", ".join(part for part in (_address(v) for v in values or []) if part)


def _internet_headers(message: dict) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in message.get("internetMessageHeaders") or []
    }


def _dry_run_result(action: str, **fields) -> dict:
    return {"dry_run": True, "action": action, **fields}


class OutlookProviderError(RuntimeError):
    """A Graph call failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status_code = status


class OutlookProvider:
    name = "outlook"

    # Enough to satisfy the junk gate, the category router and normalisation
    # without pulling whole bodies when only metadata is needed.
    SUMMARY_FIELDS = (
        "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,"
        "sentDateTime,isRead,categories,bodyPreview,hasAttachments"
    )
    FULL_FIELDS = SUMMARY_FIELDS + ",body,internetMessageHeaders,internetMessageId"

    def __init__(self, user_id: str | None = None, agent_instance_id: str | None = None, session=None):
        self._user_id = user_id
        self._agent_instance_id = agent_instance_id
        self._session = session

    # --- transport -------------------------------------------------------
    def _token(self) -> str:
        return refresh_access_token(self._user_id, self._agent_instance_id)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Call Graph. `path` may be a bare path or an absolute Graph URL (delta/next links)."""
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._token()}", **kwargs.pop("headers", {})}
        client = self._session or httpx
        try:
            response = client.request(method, url, headers=headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise OutlookProviderError(f"Graph request failed: {exc}") from exc

        if response.status_code == 204 or not (response.content or b""):
            return {}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            detail = ((payload.get("error") or {}).get("message")) or response.text[:200]
            raise OutlookProviderError(
                f"Graph {method} {url} failed ({response.status_code}): {detail}",
                status=response.status_code,
            )
        return payload

    def _paged(self, path: str, limit: int) -> list[dict]:
        """Follow @odata.nextLink until `limit` items are collected."""
        items: list[dict] = []
        next_url: str | None = path
        while next_url and len(items) < limit:
            payload = self._request("GET", next_url)
            items.extend(payload.get("value", []))
            next_url = payload.get("@odata.nextLink")
        return items[:limit]

    @staticmethod
    def _ref(message: dict) -> dict:
        return {"id": message.get("id", ""), "threadId": message.get("conversationId", "")}

    # --- discovery -------------------------------------------------------
    def fetch_unread(self, max_results: int) -> list[dict]:
        messages = self._paged(
            f"/me/mailFolders/inbox/messages?$filter=isRead eq false"
            f"&$select=id,conversationId&$orderby=receivedDateTime desc&$top={min(max_results, 50)}",
            max_results,
        )
        return [self._ref(m) for m in messages]

    def list_inbox(self, max_results: int) -> list[dict]:
        messages = self._paged(
            f"/me/mailFolders/inbox/messages?$select={self.SUMMARY_FIELDS}"
            f"&$orderby=receivedDateTime desc&$top={min(max_results, 50)}",
            max_results,
        )
        return [
            {
                "id": m.get("id"),
                "thread_id": m.get("conversationId"),
                "from": _address(m.get("from")),
                "subject": m.get("subject") or "",
                "snippet": m.get("bodyPreview") or "",
                "date": m.get("receivedDateTime") or "",
                "unread": not m.get("isRead", True),
            }
            for m in messages
        ]

    def fetch_recent(self, max_messages: int = 50) -> list[dict]:
        messages = self._paged(
            f"/me/mailFolders/inbox/messages?$select={self.FULL_FIELDS}"
            f"&$orderby=receivedDateTime desc&$top={min(max_messages, 50)}",
            max_messages,
        )
        return [
            {
                "id": m.get("id"),
                "thread_id": m.get("conversationId"),
                "from": _address(m.get("from")),
                "subject": m.get("subject") or "",
                "date": m.get("receivedDateTime") or "",
                "body": _body_text(m),
            }
            for m in messages
        ]

    def fetch_sent(self, max_messages: int = 50) -> list[dict]:
        """Sent samples for style learning, filtered the same way Gmail's are."""
        messages = self._paged(
            f"/me/mailFolders/sentitems/messages?$select={self.FULL_FIELDS}"
            f"&$orderby=sentDateTime desc&$top={min(max_messages, 50)}",
            max_messages,
        )
        samples: list[dict] = []
        for m in messages:
            body = _body_text(m)
            to = _recipients(m.get("toRecipients"))
            if len(body) < 20 or _is_automated_address(to):
                continue
            samples.append(
                {
                    "id": m.get("id"),
                    "thread_id": m.get("conversationId"),
                    "to": to,
                    "subject": m.get("subject") or "",
                    "date": m.get("sentDateTime") or "",
                    "body": body,
                }
            )
        return samples

    def search_messages(self, query: str, max_results: int) -> list[dict]:
        escaped = query.replace("'", "''")
        messages = self._paged(
            f"/me/messages?$search=\"{escaped}\"&$select=id,conversationId&$top={min(max_results, 50)}",
            max_results,
        )
        return [self._ref(m) for m in messages]

    def list_messages_by_label(self, label_id: str, max_results: int) -> list[dict]:
        escaped = label_id.replace("'", "''")
        messages = self._paged(
            f"/me/messages?$filter=categories/any(c:c eq '{escaped}')"
            f"&$select=id,conversationId&$top={min(max_results, 50)}",
            max_results,
        )
        return [self._ref(m) for m in messages]

    def fetch_messages_batch(
        self, message_ids: list[str], fmt: str = "full", chunk: int = 50
    ) -> dict[str, dict]:
        """Graph has a $batch endpoint, but it caps at 20 sub-requests and needs
        its own error unpacking. Serial gets is slower and far easier to reason
        about; revisit if a mailbox makes it hurt."""
        select = self.SUMMARY_FIELDS if fmt == "metadata" else self.FULL_FIELDS
        results: dict[str, dict] = {}
        for message_id in message_ids:
            try:
                results[message_id] = self._request(
                    "GET", f"/me/messages/{message_id}?$select={select}"
                )
            except OutlookProviderError:
                continue
        return results

    # --- incremental sync ------------------------------------------------
    def current_sync_cursor(self) -> str:
        """A delta link representing "the mailbox as of now", with no items."""
        payload = self._request(
            "GET", "/me/mailFolders/inbox/messages/delta?$deltatoken=latest"
        )
        return str(payload.get("@odata.deltaLink") or "")

    def fetch_changes_since(self, cursor: str) -> list[dict]:
        if not cursor:
            return []
        refs: list[dict] = []
        next_url: str | None = cursor
        seen: set[str] = set()
        while next_url:
            payload = self._request("GET", next_url)
            for item in payload.get("value", []):
                # Deleted messages come back as @removed and have nothing to process.
                if item.get("@removed") or not item.get("id"):
                    continue
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                refs.append(self._ref(item))
            next_url = payload.get("@odata.nextLink")
        return refs

    def is_stale_cursor_error(self, exc: Exception) -> bool:
        """Graph answers 410 Gone with resyncRequired when a delta token expired."""
        return getattr(exc, "status_code", None) == 410 or "resyncrequired" in str(exc).lower()

    def cursor_is_newer(self, candidate: str, existing: str | None) -> bool:
        """Delta links carry no ordering, so "different" is the strongest claim available.

        Safe direction: a stale-looking cursor is re-stored rather than dropped,
        and the poller's reconcile-on-empty full scan is what actually guarantees
        nothing is missed.
        """
        if not candidate:
            return False
        return candidate != (existing or "")

    def watch_mailbox(self, topic_name: str | None = None) -> dict:
        raise NotImplementedError(
            "Graph change notifications are not wired up — Outlook instances poll. "
            "Keep GMAIL_POLLING_FALLBACK_ENABLED=true."
        )

    # --- read ------------------------------------------------------------
    def get_message_headers(self, msg_id: str) -> dict:
        # Graph does not have Gmail's exact metadataHeaders mode in this provider.
        # The sensitivity no-body guarantee is Gmail-specific; Outlook keeps the
        # existing full fetch behavior until a native metadata path is added.
        return self.get_message(msg_id)

    def get_message(self, msg_id: str) -> dict:
        message = self._request("GET", f"/me/messages/{msg_id}?$select={self.FULL_FIELDS}")
        if message.get("hasAttachments"):
            # Expanded here so to_email_input stays pure, like the Gmail one.
            try:
                attachments = self._request(
                    "GET",
                    f"/me/messages/{msg_id}/attachments?$select=id,name,contentType,size",
                )
                message["_attachments"] = attachments.get("value", [])
            except OutlookProviderError:
                message["_attachments"] = []
        return message

    def fetch_thread(self, thread_id: str) -> list[dict]:
        if not thread_id:
            return []
        escaped = thread_id.replace("'", "''")
        return self._paged(
            f"/me/messages?$filter=conversationId eq '{escaped}'"
            f"&$select={self.FULL_FIELDS}&$top=50",
            settings.thread_max_messages if settings.thread_max_messages > 0 else 50,
        )

    def format_thread(
        self,
        messages: list[dict],
        max_messages: int | None = None,
        max_chars_per_message: int = 2000,
    ) -> str:
        """Same chronological labeled blocks the Gmail formatter produces."""
        messages = sorted(messages, key=lambda m: str(m.get("receivedDateTime") or ""))
        limit = max_messages if max_messages is not None else settings.thread_max_messages
        if limit and limit > 0:
            messages = messages[-limit:]

        blocks = []
        for m in messages:
            author = _address(m.get("from")) or "Unknown Sender"
            date = m.get("receivedDateTime") or ""
            body = _body_text(m)
            if len(body) > max_chars_per_message:
                body = body[:max_chars_per_message] + "\n…[truncated]"
            blocks.append(f"From: {author}\nDate: {date}\n\n{body}")
        return THREAD_BLOCK_SEPARATOR.join(blocks)

    def fetch_sender_correspondence(
        self,
        sender_email: str,
        exclude_thread_id: str = "",
        max_messages: int = 2,
        max_chars: int = 600,
    ) -> list[dict]:
        address = (sender_email or "").strip()
        if "<" in address and ">" in address:
            address = address.split("<", 1)[1].split(">", 1)[0].strip()
        if not address or "@" not in address:
            return []
        try:
            escaped = address.replace("'", "''")
            messages = self._paged(
                f"/me/mailFolders/sentitems/messages"
                f"?$filter=toRecipients/any(r:r/emailAddress/address eq '{escaped}')"
                f"&$select={self.FULL_FIELDS}&$orderby=sentDateTime desc&$top={max_messages + 2}",
                max_messages + 2,
            )
            blocks: list[dict] = []
            for m in messages:
                if exclude_thread_id and m.get("conversationId") == exclude_thread_id:
                    continue
                body = _body_text(m)
                if len(body) < 20:
                    continue
                blocks.append(
                    {
                        "subject": m.get("subject") or "",
                        "date": m.get("sentDateTime") or "",
                        "body": body[:max_chars],
                    }
                )
                if len(blocks) >= max_messages:
                    break
            return blocks
        except Exception:
            # Bonus context, never a blocker — identical policy to the Gmail path.
            return []

    def format_sender_correspondence(self, blocks: list[dict]) -> str:
        if not blocks:
            return ""
        parts = []
        for block in blocks:
            header = " — ".join(p for p in (block.get("date", ""), block.get("subject", "")) if p)
            parts.append(f"--- {header} ---\n{block.get('body', '')}")
        return (
            "Previous emails the mailbox owner sent to this correspondent "
            "(style/register reference only):\n" + "\n\n".join(parts)
        )

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        payload = self._request("GET", f"/me/messages/{message_id}/attachments/{attachment_id}")
        raw = payload.get("contentBytes") or ""
        return base64.b64decode(raw) if raw else b""

    # --- mutate ----------------------------------------------------------
    def _move(self, message_id: str, folder: str) -> dict:
        return self._request(
            "POST", f"/me/messages/{message_id}/move", json={"destinationId": folder}
        )

    def modify_labels(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        respect_dry_run: bool = True,
    ) -> dict:
        """Apply a Gmail-shaped label edit to an Outlook message.

        UNREAD and INBOX are mailbox state, not categories: they become the
        isRead flag and a folder move. Everything else is a category name.
        """
        add = list(add_label_ids or [])
        remove = list(remove_label_ids or [])
        if respect_dry_run and effective_dry_run():
            return _dry_run_result(
                "modify_labels",
                message_id=message_id,
                add_label_ids=add,
                remove_label_ids=remove,
            )

        result: dict[str, Any] = {"id": message_id}
        patch: dict[str, Any] = {}

        if UNREAD in remove:
            patch["isRead"] = True
        if UNREAD in add:
            patch["isRead"] = False

        categories_add = [label for label in add if label not in (UNREAD, INBOX)]
        categories_remove = [label for label in remove if label not in (UNREAD, INBOX)]
        if categories_add or categories_remove:
            current = self._request("GET", f"/me/messages/{message_id}?$select=categories")
            categories = list(current.get("categories") or [])
            for label in categories_add:
                if label not in categories:
                    categories.append(label)
            categories = [c for c in categories if c not in categories_remove]
            patch["categories"] = categories

        if patch:
            result = self._request("PATCH", f"/me/messages/{message_id}", json=patch)

        # Folder moves have to be their own request; do them after the patch so a
        # failed move leaves the flag/category edit applied rather than the reverse.
        if INBOX in remove:
            result = self._move(message_id, "archive")
        if INBOX in add:
            result = self._move(message_id, "inbox")
        return result

    def mark_as_read(self, msg_id: str) -> None:
        self.modify_labels(msg_id, remove_label_ids=[UNREAD], respect_dry_run=False)

    def mark_as_unread(self, msg_id: str) -> dict:
        return self.modify_labels(msg_id, add_label_ids=[UNREAD])

    def archive_message(self, msg_id: str) -> dict:
        return self.modify_labels(msg_id, remove_label_ids=[INBOX])

    def trash_message(self, msg_id: str) -> dict:
        if effective_dry_run():
            return _dry_run_result("trash_message", message_id=msg_id)
        return self._move(msg_id, "deleteditems")

    def list_labels(self) -> list[dict]:
        """Categories, plus the two pseudo-labels the rest of the code expects.

        `inbox_tools._label_id` resolves a user-typed label against this list, so
        omitting UNREAD/INBOX here would make `remove_label("INBOX")` fail on
        Outlook while working on Gmail.
        """
        payload = self._request("GET", "/me/outlook/masterCategories")
        labels = [
            {"id": c.get("displayName"), "name": c.get("displayName")}
            for c in payload.get("value", [])
            if c.get("displayName")
        ]
        return labels + [{"id": UNREAD, "name": UNREAD}, {"id": INBOX, "name": INBOX}]

    def ensure_label(self, name: str) -> str:
        """Category names *are* their own ids in Graph, so this returns `name`."""
        if effective_dry_run():
            return f"dry-run-label:{name}"
        if name in (UNREAD, INBOX):
            return name
        existing = {label["id"] for label in self.list_labels()}
        if name not in existing:
            try:
                self._request(
                    "POST",
                    "/me/outlook/masterCategories",
                    json={"displayName": name, "color": "preset0"},
                )
            except OutlookProviderError as exc:
                # A category that already exists races to 409; applying it still works.
                if getattr(exc, "status_code", None) != 409:
                    raise
        return name

    # --- send ------------------------------------------------------------
    def _graph_recipients(self, to: str | list[str]) -> list[dict]:
        values = to if isinstance(to, list) else [to]
        return [{"emailAddress": {"address": addr}} for addr in email_addresses(*values)]

    def _send_mail(self, to: str | list[str], subject: str, body: str, html: str | None = None) -> dict:
        enforce_outbound_allowlist(to)
        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if html is not None else "Text",
                    "content": html if html is not None else body,
                },
                "toRecipients": self._graph_recipients(to),
            },
            "saveToSentItems": True,
        }
        self._request("POST", "/me/sendMail", json=payload)
        return {"status": "sent", "to": to, "subject": subject}

    def send_message(
        self, to: str, subject: str, body: str, attachments: list[dict] | None = None
    ) -> dict:
        if effective_dry_run():
            return _dry_run_result("send_message", to=to, subject=subject)
        if attachments:
            # Attachment passthrough is Gmail-only for now — see docs/mail-providers.md.
            raise NotImplementedError("Outlook send attachments are not yet supported.")
        return self._send_mail(to, subject, body)

    def send_html_message(
        self, to: str, subject: str, html: str, text: str, respect_dry_run: bool = True
    ) -> dict:
        if respect_dry_run and effective_dry_run():
            return _dry_run_result("send_html_message", to=to, subject=subject)
        return self._send_mail(to, subject, text, html=html)

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> dict:
        if effective_dry_run():
            return _dry_run_result("create_draft", to=to, subject=subject)
        if attachments:
            # Attachment passthrough is Gmail-only for now — see docs/mail-providers.md.
            raise NotImplementedError("Outlook draft attachments are not yet supported.")
        # Drafts never leave the mailbox, but the allowlist still applies: a draft
        # is one click from a send, and this mirrors the Gmail policy.
        enforce_outbound_allowlist(to)
        return self._request(
            "POST",
            "/me/messages",
            json={
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": self._graph_recipients(to),
            },
        )

    def _reply_draft(self, message_id: str, action: str, comment: str, to: str | list[str] | None = None) -> dict:
        """createReply/createReplyAll/createForward then patch and send the draft.

        Going through a draft is what makes Graph populate References and
        In-Reply-To itself, so the reply threads correctly in the recipient's
        client without us assembling headers.
        """
        draft = self._request("POST", f"/me/messages/{message_id}/{action}")
        draft_id = draft.get("id")
        if not draft_id:
            raise OutlookProviderError(f"Graph {action} returned no draft id")

        patch: dict[str, Any] = {"body": {"contentType": "Text", "content": comment}}
        if to is not None:
            patch["toRecipients"] = self._graph_recipients(to)
        self._request("PATCH", f"/me/messages/{draft_id}", json=patch)
        self._request("POST", f"/me/messages/{draft_id}/send")
        return {"status": "sent", "id": draft_id, "action": action}

    def forward_message(
        self, message_id: str, to: str, note: str, attachments: list[dict] | None = None
    ) -> dict:
        if effective_dry_run():
            return _dry_run_result("forward_message", message_id=message_id, to=to)
        if attachments:
            # Attachment passthrough is Gmail-only for now — see docs/mail-providers.md.
            raise NotImplementedError("Outlook forward attachments are not yet supported.")
        enforce_outbound_allowlist(to)
        return self._reply_draft(message_id, "createForward", note, to=to)

    def notify_internal_message(
        self, to: str | list[str], subject: str, note: str, attachments: list[dict] | None = None
    ) -> dict:
        if effective_dry_run():
            return _dry_run_result("notify_internal_message", to=to, subject=subject)
        if attachments:
            raise NotImplementedError("Outlook notify attachments are not yet supported.")
        return self._send_mail(to, subject, note)

    def reply_all_message(
        self, message_id: str, body: str, attachments: list[dict] | None = None
    ) -> dict:
        if effective_dry_run():
            return _dry_run_result("reply_all_message", message_id=message_id)
        if attachments:
            raise NotImplementedError("Outlook reply-all attachments are not yet supported.")
        # reply-all takes no explicit recipient list, so the allowlist is checked
        # against the recipients Graph derived — the same gap the Gmail path
        # closes inside its send helper.
        draft = self._request("POST", f"/me/messages/{message_id}/createReplyAll")
        draft_id = draft.get("id")
        if not draft_id:
            raise OutlookProviderError("Graph createReplyAll returned no draft id")
        recipients = self._request(
            "GET", f"/me/messages/{draft_id}?$select=toRecipients,ccRecipients"
        )
        addresses = [
            _address(r)
            for r in (recipients.get("toRecipients") or []) + (recipients.get("ccRecipients") or [])
        ]
        enforce_outbound_allowlist([a for a in addresses if a])
        self._request(
            "PATCH",
            f"/me/messages/{draft_id}",
            json={"body": {"contentType": "Text", "content": body}},
        )
        self._request("POST", f"/me/messages/{draft_id}/send")
        return {"status": "sent", "id": draft_id, "action": "createReplyAll"}

    # --- identity --------------------------------------------------------
    def self_address(self) -> str:
        try:
            profile = self._request("GET", "/me?$select=mail,userPrincipalName")
        except Exception:
            return ""
        return (profile.get("mail") or profile.get("userPrincipalName") or "").strip()

    def probe(self) -> dict:
        try:
            profile = self._request("GET", "/me?$select=mail,userPrincipalName")
        except Exception as exc:
            return {"ok": False, "mailbox": "", "error": f"{type(exc).__name__}: {exc}"}
        mailbox = (profile.get("mail") or profile.get("userPrincipalName") or "").strip()
        return {"ok": True, "mailbox": mailbox, "error": ""}

    # --- normalisation ---------------------------------------------------
    def to_email_input(
        self, message: dict, thread_messages: list[dict] | None = None
    ) -> EmailInput:
        headers = _internet_headers(message)
        if thread_messages:
            email_thread = self.format_thread(thread_messages)
        else:
            email_thread = _body_text(message)

        attachments = [
            {
                "filename": a.get("name", ""),
                "mime_type": a.get("contentType", ""),
                "size": a.get("size", 0),
                "attachment_id": a.get("id", ""),
            }
            for a in message.get("_attachments") or []
            if a.get("name")
        ]

        labels = list(message.get("categories") or [])
        if not message.get("isRead", True):
            labels.append(UNREAD)

        return {
            "author": _address(message.get("from")) or "Unknown Sender",
            "to": _recipients(message.get("toRecipients")) or "Unknown Recipient",
            "subject": message.get("subject") or "No Subject",
            "email_thread": email_thread,
            "email_id": message.get("id", ""),
            "gmail_thread_id": message.get("conversationId", ""),
            "attachments": attachments,
            "labels": labels,
            "list_unsubscribe": bool(headers.get("list-unsubscribe")),
            "precedence_bulk": headers.get("precedence", "").strip().lower()
            in {"bulk", "list", "junk"},
            "list_id": bool(headers.get("list-id")),
            "auto_submitted": headers.get("auto-submitted", "").strip().lower() not in {"", "no"},
        }


def _is_automated_address(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("no-reply", "noreply", "donotreply", "do-not-reply"))


__all__ = ["OutlookProvider", "OutlookProviderError"]
