"""Gmail behind the provider contract.

Every method is a one-line delegate to the `src.gmail_client` function that
already implements it. No logic moved and none is duplicated here — the dry-run
guard, outbound allowlist, signature inline images, budget accounting and batch
fetching all stay exactly where they were and keep behaving exactly as they did.

The only thing this class adds is ownership of the authenticated resource:
`gmail_client` functions each take `resource=None` and build one on demand, and
call sites used to thread a single resource down the stack to avoid rebuilding
it. The provider holds that resource instead.
"""

from __future__ import annotations

from src import gmail_client
from src.gmail_sync import history_id_is_newer
from src.state import EmailInput


class GmailProvider:
    name = "gmail"

    def __init__(self, user_id: str | None = None, resource=None):
        self._user_id = user_id
        self._resource = resource

    @property
    def resource(self):
        """Build the Gmail resource on first use, then reuse it.

        Lazy on purpose: constructing a provider must not require a stored
        token, so that `get_provider()` is safe to call before onboarding.
        """
        if self._resource is None:
            self._resource = gmail_client.gmail_resource(self._user_id)
        return self._resource

    @property
    def _deferred(self):
        """The resource *if already built*, otherwise None.

        Used by every dry-run-guarded call. Those `gmail_client` functions check
        dry-run before touching the network and only then do
        `resource or gmail_resource()`, so handing them an unbuilt None keeps
        simulation from loading a token at all — passing `self.resource` here
        would force an OAuth read on a path that never sends anything.
        """
        return self._resource

    # --- discovery -------------------------------------------------------
    def fetch_unread(self, max_results: int) -> list[dict]:
        return gmail_client.fetch_unread(max_results, resource=self.resource)

    def list_inbox(self, max_results: int) -> list[dict]:
        return gmail_client.list_inbox(max_results, resource=self.resource)

    def fetch_recent(self, max_messages: int = 50) -> list[dict]:
        return gmail_client.fetch_recent(max_messages, resource=self.resource)

    def fetch_sent(self, max_messages: int = 50) -> list[dict]:
        return gmail_client.fetch_sent(max_messages, resource=self.resource)

    def search_messages(self, query: str, max_results: int) -> list[dict]:
        return gmail_client.search_messages(query, max_results, resource=self.resource)

    def list_messages_by_label(self, label_id: str, max_results: int) -> list[dict]:
        return gmail_client.list_messages_by_label(label_id, max_results, resource=self.resource)

    def fetch_messages_batch(
        self, message_ids: list[str], fmt: str = "full", chunk: int = 50
    ) -> dict[str, dict]:
        return gmail_client.fetch_messages_batch(
            message_ids, resource=self.resource, fmt=fmt, chunk=chunk
        )

    # --- incremental sync ------------------------------------------------
    def current_sync_cursor(self) -> str:
        return gmail_client.current_history_id(resource=self.resource)

    def fetch_changes_since(self, cursor: str) -> list[dict]:
        return gmail_client.fetch_history_message_refs(cursor, resource=self.resource)

    def is_stale_cursor_error(self, exc: Exception) -> bool:
        return gmail_client.is_stale_history_error(exc)

    def cursor_is_newer(self, candidate: str, existing: str | None) -> bool:
        return history_id_is_newer(candidate, existing)

    def watch_mailbox(self, topic_name: str | None = None) -> dict:
        return gmail_client.watch_mailbox(topic_name, resource=self.resource)

    # --- read ------------------------------------------------------------
    def get_message_headers(self, msg_id: str) -> dict:
        return gmail_client.get_message_headers(msg_id, resource=self.resource)

    def get_message(self, msg_id: str) -> dict:
        return gmail_client.get_message(msg_id, resource=self.resource)

    def fetch_thread(self, thread_id: str) -> list[dict]:
        return gmail_client.fetch_thread(thread_id, resource=self.resource)

    def format_thread(
        self,
        messages: list[dict],
        max_messages: int | None = None,
        max_chars_per_message: int = 2000,
    ) -> str:
        return gmail_client.format_thread(
            messages, max_messages=max_messages, max_chars_per_message=max_chars_per_message
        )

    def fetch_sender_correspondence(
        self,
        sender_email: str,
        exclude_thread_id: str = "",
        max_messages: int = 2,
        max_chars: int = 600,
    ) -> list[dict]:
        return gmail_client.fetch_sender_correspondence(
            sender_email,
            exclude_thread_id=exclude_thread_id,
            max_messages=max_messages,
            max_chars=max_chars,
            resource=self.resource,
        )

    def format_sender_correspondence(self, blocks: list[dict]) -> str:
        return gmail_client.format_sender_correspondence(blocks)

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        return gmail_client.download_attachment(message_id, attachment_id, resource=self.resource)

    # --- mutate ----------------------------------------------------------
    def modify_labels(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        respect_dry_run: bool = True,
    ) -> dict:
        return gmail_client.modify_labels(
            message_id,
            add_label_ids=add_label_ids,
            remove_label_ids=remove_label_ids,
            resource=self._deferred,
            respect_dry_run=respect_dry_run,
        )

    def mark_as_read(self, msg_id: str) -> None:
        return gmail_client.mark_as_read(msg_id, resource=self.resource)

    def mark_as_unread(self, msg_id: str) -> dict:
        return gmail_client.mark_as_unread(msg_id, resource=self.resource)

    def archive_message(self, msg_id: str) -> dict:
        return gmail_client.archive_message(msg_id, resource=self.resource)

    def trash_message(self, msg_id: str) -> dict:
        return gmail_client.trash_message(msg_id, resource=self._deferred)

    def list_labels(self) -> list[dict]:
        return gmail_client.list_labels(resource=self.resource)

    def ensure_label(self, name: str) -> str:
        return gmail_client.ensure_label(name, resource=self._deferred)

    # --- send ------------------------------------------------------------
    def send_message(
        self, to: str, subject: str, body: str, attachments: list[dict] | None = None
    ) -> dict:
        return gmail_client.send_message(
            to, subject, body, attachments=attachments, resource=self._deferred
        )

    def send_html_message(
        self, to: str, subject: str, html: str, text: str, respect_dry_run: bool = True
    ) -> dict:
        return gmail_client.send_html_message(
            to, subject, html, text, resource=self._deferred, respect_dry_run=respect_dry_run
        )

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> dict:
        return gmail_client.create_draft(
            to, subject, body, thread_id=thread_id, attachments=attachments, resource=self._deferred
        )

    def forward_message(self, message_id: str, to: str, note: str) -> dict:
        return gmail_client.forward_message(message_id, to, note, resource=self._deferred)

    def notify_internal_message(self, to: str | list[str], subject: str, note: str) -> dict:
        return gmail_client.notify_internal_message(to, subject, note, resource=self._deferred)

    def reply_all_message(self, message_id: str, body: str) -> dict:
        return gmail_client.reply_all_message(message_id, body, resource=self._deferred)

    # --- identity --------------------------------------------------------
    def self_address(self) -> str:
        return gmail_client._self_address(self.resource)

    def probe(self) -> dict:
        """Round-trip users.getProfile. Never raises — the caller renders the error."""
        try:
            profile = self.resource.users().getProfile(userId="me").execute()
        except Exception as exc:
            return {"ok": False, "mailbox": "", "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "mailbox": profile.get("emailAddress", ""), "error": ""}

    # --- normalisation ---------------------------------------------------
    def to_email_input(
        self, message: dict, thread_messages: list[dict] | None = None
    ) -> EmailInput:
        return gmail_client.gmail_to_email_input(message, thread_messages=thread_messages)
