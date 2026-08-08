"""The mail provider contract.

Every method here exists because a call site already needed it — the set was
derived from what `src/poller.py`, `src/api.py`, `src/capabilities/*`,
`src/campaigns.py`, `src/signature.py`, `src/instance_setup.py`,
`src/notifications.py` and `src/worker.py` import from `src.gmail_client`
today, not designed up front.

Three deliberate differences from the `gmail_client` functions they replace:

* **No `resource` argument.** The provider owns its authenticated connection
  and builds it lazily. Call sites that used to thread a Gmail resource down
  the stack thread the provider instead — same object lifetime, one fewer
  concept.
* **Cursor, not history id.** Gmail tracks incremental sync with a numeric
  `historyId`; Graph uses an opaque `deltaLink` URL. `current_sync_cursor` /
  `fetch_changes_since` / `cursor_is_newer` / `is_stale_cursor_error` cover
  both, which is why ordering comparison is a provider method rather than the
  numeric compare `gmail_sync.history_id_is_newer` used to do inline.
* **`to_email_input` is the normalisation seam.** Whatever a provider's native
  message shape is, this returns the same `EmailInput` the graph, junk gate and
  category router already expect, so nothing downstream knows the difference.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.state import EmailInput


@runtime_checkable
class MailProvider(Protocol):
    """Everything the agent needs from a mailbox."""

    name: str

    # --- discovery -------------------------------------------------------
    def fetch_unread(self, max_results: int) -> list[dict]:
        """Unread inbox message refs ([{id, threadId}, ...]), newest first."""
        ...

    def list_inbox(self, max_results: int) -> list[dict]: ...

    def fetch_recent(self, max_messages: int = 50) -> list[dict]: ...

    def fetch_sent(self, max_messages: int = 50) -> list[dict]:
        """Sent-mail samples usable for writing-style learning."""
        ...

    def search_messages(self, query: str, max_results: int) -> list[dict]: ...

    def list_messages_by_label(self, label_id: str, max_results: int) -> list[dict]: ...

    def fetch_messages_batch(
        self, message_ids: list[str], fmt: str = "full", chunk: int = 50
    ) -> dict[str, dict]: ...

    # --- incremental sync ------------------------------------------------
    def current_sync_cursor(self) -> str:
        """Opaque marker for 'the mailbox as of now', stored as the next baseline."""
        ...

    def fetch_changes_since(self, cursor: str) -> list[dict]:
        """Message refs that changed since `cursor`. Empty is not proof of quiet —
        callers reconcile with a full unread scan, which is what fixed the
        14-hour stall."""
        ...

    def is_stale_cursor_error(self, exc: Exception) -> bool:
        """True when the stored cursor is too old and a full resync is required."""
        ...

    def cursor_is_newer(self, candidate: str, existing: str | None) -> bool: ...

    def watch_mailbox(self, topic_name: str | None = None) -> dict:
        """Register push notifications. May raise NotImplementedError — callers
        must fall back to polling."""
        ...

    # --- read ------------------------------------------------------------
    def get_message(self, msg_id: str) -> dict: ...

    def fetch_thread(self, thread_id: str) -> list[dict]: ...

    def format_thread(
        self,
        messages: list[dict],
        max_messages: int | None = None,
        max_chars_per_message: int = 2000,
    ) -> str: ...

    def fetch_sender_correspondence(
        self,
        sender_email: str,
        exclude_thread_id: str = "",
        max_messages: int = 2,
        max_chars: int = 600,
    ) -> list[dict]: ...

    def format_sender_correspondence(self, blocks: list[dict]) -> str: ...

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes: ...

    # --- mutate ----------------------------------------------------------
    def modify_labels(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        respect_dry_run: bool = True,
    ) -> dict: ...

    def mark_as_read(self, msg_id: str) -> None: ...

    def mark_as_unread(self, msg_id: str) -> dict: ...

    def archive_message(self, msg_id: str) -> dict: ...

    def trash_message(self, msg_id: str) -> dict: ...

    def list_labels(self) -> list[dict]: ...

    def ensure_label(self, name: str) -> str:
        """Return the id of `name`, creating it if the mailbox lacks it."""
        ...

    # --- send ------------------------------------------------------------
    def send_message(
        self, to: str, subject: str, body: str, attachments: list[dict] | None = None
    ) -> dict:
        """`attachments` items are `{filename, mime_type, data}` with `data` as bytes."""
        ...

    def send_html_message(
        self, to: str, subject: str, html: str, text: str, respect_dry_run: bool = True
    ) -> dict: ...

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> dict:
        """`attachments` items are `{filename, mime_type, data}` with `data` as bytes."""
        ...

    def forward_message(self, message_id: str, to: str, note: str) -> dict: ...

    def notify_internal_message(self, to: str | list[str], subject: str, note: str) -> dict: ...

    def reply_all_message(self, message_id: str, body: str) -> dict: ...

    # --- identity --------------------------------------------------------
    def self_address(self) -> str:
        """The authenticated account's address. Returns "" on failure rather than
        raising — reply-all self-exclusion must never break a send."""
        ...

    def probe(self) -> dict:
        """Cheap liveness check against the provider. Returns
        {"ok": bool, "mailbox": str, "error": str}. Never raises."""
        ...

    # --- normalisation ---------------------------------------------------
    def to_email_input(
        self, message: dict, thread_messages: list[dict] | None = None
    ) -> EmailInput: ...
