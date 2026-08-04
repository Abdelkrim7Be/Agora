"""OutlookProvider against recorded Microsoft Graph payloads.

No network. A fake session answers routes by pattern, so these assert the
translation decisions — Gmail-shaped labels onto categories and folders, delta
links as cursors, conversationId as the thread key — rather than Graph itself.
"""

from __future__ import annotations

import base64
import inspect

import pytest

from src.config import settings
from src.mail.base import MailProvider
from src.mail.outlook import OutlookProvider, OutlookProviderError, _html_to_text


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.content = b"x" if payload is not None else b""
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    """Answers Graph calls from a routing table and records every request."""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append((method, url, kwargs.get("json") or {}))
        for (route_method, fragment), response in self.routes.items():
            if route_method == method and fragment in url:
                return response() if callable(response) else response
        return _Response(200, {"value": []})


@pytest.fixture(autouse=True)
def no_token_refresh(monkeypatch):
    monkeypatch.setattr("src.mail.outlook.refresh_access_token", lambda user, instance: "access-token")
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "default_send_mode", "live")
    monkeypatch.setattr(settings, "outbound_allowlist", set())
    monkeypatch.setattr(settings, "thread_max_messages", 10)


def _provider(routes=None):
    session = _Session(routes)
    return OutlookProvider(user_id="owner@example.com", agent_instance_id="sales", session=session), session


MESSAGE = {
    "id": "AAMk-1",
    "conversationId": "conv-9",
    "subject": "Demande de devis",
    "from": {"emailAddress": {"name": "Claire Meunier", "address": "claire@client.fr"}},
    "toRecipients": [{"emailAddress": {"name": "Ventes", "address": "sales@acme.fr"}}],
    "receivedDateTime": "2026-08-01T09:12:00Z",
    "isRead": False,
    "categories": ["Devis"],
    "bodyPreview": "Bonjour, pourriez-vous",
    "hasAttachments": True,
    "body": {"contentType": "html", "content": "<p>Bonjour,</p><p>pourriez-vous chiffrer ?</p>"},
    "internetMessageHeaders": [
        {"name": "List-Unsubscribe", "value": "<mailto:stop@client.fr>"},
        {"name": "Auto-Submitted", "value": "auto-generated"},
    ],
}


# --- normalisation -------------------------------------------------------
def test_to_email_input_matches_the_gmail_shape():
    provider, _ = _provider()
    message = {**MESSAGE, "_attachments": [
        {"id": "att-1", "name": "devis.pdf", "contentType": "application/pdf", "size": 2048}
    ]}

    result = provider.to_email_input(message)

    assert result["author"] == "Claire Meunier <claire@client.fr>"
    assert result["to"] == "Ventes <sales@acme.fr>"
    assert result["subject"] == "Demande de devis"
    assert result["email_id"] == "AAMk-1"
    assert result["gmail_thread_id"] == "conv-9"
    assert "pourriez-vous chiffrer" in result["email_thread"]
    assert "<p>" not in result["email_thread"]
    assert result["attachments"] == [
        {"filename": "devis.pdf", "mime_type": "application/pdf", "size": 2048, "attachment_id": "att-1"}
    ]


def test_unread_state_surfaces_as_the_unread_label():
    """The junk gate and poller read `labels`, so isRead has to become UNREAD."""
    provider, _ = _provider()
    assert "UNREAD" in provider.to_email_input(MESSAGE)["labels"]
    assert "UNREAD" not in provider.to_email_input({**MESSAGE, "isRead": True})["labels"]


def test_bulk_mail_headers_reach_the_junk_gate():
    provider, _ = _provider()
    result = provider.to_email_input(MESSAGE)
    assert result["list_unsubscribe"] is True
    assert result["auto_submitted"] is True
    assert result["list_id"] is False
    assert result["precedence_bulk"] is False


def test_precedence_bulk_is_detected():
    provider, _ = _provider()
    message = {**MESSAGE, "internetMessageHeaders": [{"name": "Precedence", "value": "Bulk"}]}
    assert provider.to_email_input(message)["precedence_bulk"] is True


def test_missing_fields_fall_back_like_gmail_does():
    provider, _ = _provider()
    result = provider.to_email_input({"id": "m", "conversationId": "c"})
    assert result["author"] == "Unknown Sender"
    assert result["to"] == "Unknown Recipient"
    assert result["subject"] == "No Subject"


def test_html_to_text_drops_markup_and_scripts():
    assert _html_to_text("<style>a{}</style><p>Bonjour</p><br/>Merci") == "Bonjour\n\nMerci"
    assert _html_to_text("<p>caf&eacute;</p>") == "café"


# --- discovery -----------------------------------------------------------
def test_fetch_unread_returns_gmail_shaped_refs():
    provider, session = _provider(
        {("GET", "isRead eq false"): _Response(200, {"value": [{"id": "m1", "conversationId": "c1"}]})}
    )
    assert provider.fetch_unread(10) == [{"id": "m1", "threadId": "c1"}]
    assert "mailFolders/inbox/messages" in session.calls[0][1]


def test_paging_follows_next_link():
    page_two = _Response(200, {"value": [{"id": "m2", "conversationId": "c2"}]})
    page_one = _Response(
        200,
        {
            "value": [{"id": "m1", "conversationId": "c1"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
        },
    )
    provider, _ = _provider({("GET", "next-page"): page_two, ("GET", "isRead eq false"): page_one})

    assert provider.fetch_unread(10) == [
        {"id": "m1", "threadId": "c1"},
        {"id": "m2", "threadId": "c2"},
    ]


def test_list_inbox_summaries_match_the_management_view_shape():
    provider, _ = _provider({("GET", "mailFolders/inbox/messages"): _Response(200, {"value": [MESSAGE]})})
    [summary] = provider.list_inbox(25)
    assert summary == {
        "id": "AAMk-1",
        "thread_id": "conv-9",
        "from": "Claire Meunier <claire@client.fr>",
        "subject": "Demande de devis",
        "snippet": "Bonjour, pourriez-vous",
        "date": "2026-08-01T09:12:00Z",
        "unread": True,
    }


def test_fetch_sent_skips_trivial_and_automated_mail():
    messages = {
        "value": [
            {**MESSAGE, "id": "s1", "toRecipients": [{"emailAddress": {"address": "client@x.fr"}}],
             "body": {"contentType": "text", "content": "Bonjour, voici le devis complet ci-joint."},
             "sentDateTime": "2026-08-01T10:00:00Z"},
            {**MESSAGE, "id": "s2", "body": {"contentType": "text", "content": "ok"}},
            {**MESSAGE, "id": "s3", "toRecipients": [{"emailAddress": {"address": "no-reply@x.fr"}}],
             "body": {"contentType": "text", "content": "Un message assez long pour passer le filtre."}},
        ]
    }
    provider, _ = _provider({("GET", "sentitems"): _Response(200, messages)})

    samples = provider.fetch_sent(10)

    assert [s["id"] for s in samples] == ["s1"]
    assert samples[0]["to"] == "client@x.fr"


# --- incremental sync ----------------------------------------------------
def test_current_cursor_asks_for_the_latest_delta_token():
    provider, session = _provider(
        {("GET", "$deltatoken=latest"): _Response(200, {"@odata.deltaLink": "https://graph/delta?token=abc"})}
    )
    assert provider.current_sync_cursor() == "https://graph/delta?token=abc"
    assert "$deltatoken=latest" in session.calls[0][1]


def test_delta_skips_removed_messages_and_dedupes():
    provider, _ = _provider(
        {
            ("GET", "delta?token=abc"): _Response(
                200,
                {
                    "value": [
                        {"id": "m1", "conversationId": "c1"},
                        {"id": "m1", "conversationId": "c1"},
                        {"id": "m2", "@removed": {"reason": "deleted"}},
                        {"conversationId": "c3"},
                    ]
                },
            )
        }
    )
    assert provider.fetch_changes_since("https://graph/delta?token=abc") == [
        {"id": "m1", "threadId": "c1"}
    ]


def test_empty_cursor_fetches_nothing():
    provider, session = _provider()
    assert provider.fetch_changes_since("") == []
    assert session.calls == []


def test_expired_delta_token_is_recognised_as_stale():
    provider, _ = _provider()
    assert provider.is_stale_cursor_error(OutlookProviderError("gone", status=410)) is True
    assert provider.is_stale_cursor_error(OutlookProviderError("resyncRequired", status=400)) is True
    assert provider.is_stale_cursor_error(OutlookProviderError("boom", status=500)) is False


def test_cursor_comparison_can_only_answer_different():
    provider, _ = _provider()
    assert provider.cursor_is_newer("link-b", "link-a") is True
    assert provider.cursor_is_newer("link-a", "link-a") is False
    assert provider.cursor_is_newer("", "link-a") is False


def test_watch_is_explicitly_unavailable():
    provider, _ = _provider()
    with pytest.raises(NotImplementedError, match="poll"):
        provider.watch_mailbox()


# --- labels, folders -----------------------------------------------------
def test_unread_label_removal_patches_is_read():
    provider, session = _provider({("PATCH", "/me/messages/m1"): _Response(200, {"id": "m1"})})
    provider.mark_as_read("m1")
    patches = [c for c in session.calls if c[0] == "PATCH"]
    assert patches[0][2] == {"isRead": True}


def test_marking_unread_sets_is_read_false():
    provider, session = _provider({("PATCH", "/me/messages/m1"): _Response(200, {"id": "m1"})})
    provider.mark_as_unread("m1")
    assert [c for c in session.calls if c[0] == "PATCH"][0][2] == {"isRead": False}


def test_archive_moves_out_of_the_inbox():
    provider, session = _provider({("POST", "/move"): _Response(200, {"id": "m1"})})
    provider.archive_message("m1")
    moves = [c for c in session.calls if "/move" in c[1]]
    assert moves[0][2] == {"destinationId": "archive"}


def test_trash_moves_to_deleted_items():
    provider, session = _provider({("POST", "/move"): _Response(200, {"id": "m1"})})
    provider.trash_message("m1")
    assert [c for c in session.calls if "/move" in c[1]][0][2] == {"destinationId": "deleteditems"}


def test_custom_labels_become_categories_merged_with_existing():
    provider, session = _provider(
        {
            ("GET", "?$select=categories"): _Response(200, {"categories": ["Devis"]}),
            ("PATCH", "/me/messages/m1"): _Response(200, {"id": "m1"}),
        }
    )
    provider.modify_labels("m1", add_label_ids=["Facture"], remove_label_ids=["Devis"])
    patch = [c for c in session.calls if c[0] == "PATCH"][0][2]
    assert patch["categories"] == ["Facture"]


def test_label_edit_and_folder_move_combine_in_one_call():
    """Auto-organize applies a label then archives — both must land."""
    provider, session = _provider(
        {
            ("GET", "?$select=categories"): _Response(200, {"categories": []}),
            ("PATCH", "/me/messages/m1"): _Response(200, {"id": "m1"}),
            ("POST", "/move"): _Response(200, {"id": "m1"}),
        }
    )
    provider.modify_labels("m1", add_label_ids=["Ignoré"], remove_label_ids=["INBOX"])

    assert [c for c in session.calls if c[0] == "PATCH"][0][2]["categories"] == ["Ignoré"]
    assert [c for c in session.calls if "/move" in c[1]][0][2] == {"destinationId": "archive"}


def test_list_labels_includes_the_gmail_pseudo_labels():
    provider, _ = _provider(
        {("GET", "masterCategories"): _Response(200, {"value": [{"displayName": "Devis"}]})}
    )
    labels = provider.list_labels()
    assert {label["id"] for label in labels} == {"Devis", "UNREAD", "INBOX"}


def test_ensure_label_creates_a_missing_category_and_returns_its_name():
    provider, session = _provider(
        {
            ("GET", "masterCategories"): _Response(200, {"value": []}),
            ("POST", "masterCategories"): _Response(201, {"displayName": "Facture"}),
        }
    )
    assert provider.ensure_label("Facture") == "Facture"
    assert [c for c in session.calls if c[0] == "POST"][0][2]["displayName"] == "Facture"


def test_ensure_label_tolerates_a_concurrent_creation():
    provider, _ = _provider(
        {
            ("GET", "masterCategories"): _Response(200, {"value": []}),
            ("POST", "masterCategories"): _Response(409, {"error": {"message": "exists"}}),
        }
    )
    assert provider.ensure_label("Facture") == "Facture"


def test_dry_run_suppresses_every_mutation(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)
    provider, session = _provider()

    assert provider.modify_labels("m1", add_label_ids=["X"])["dry_run"] is True
    assert provider.trash_message("m1")["dry_run"] is True
    assert provider.send_message("a@b.c", "s", "b")["dry_run"] is True
    assert provider.create_draft("a@b.c", "s", "b")["dry_run"] is True
    assert provider.forward_message("m1", "a@b.c", "n")["dry_run"] is True
    assert provider.reply_all_message("m1", "b")["dry_run"] is True
    assert session.calls == []


# --- send ----------------------------------------------------------------
def test_send_posts_a_graph_message():
    provider, session = _provider({("POST", "/me/sendMail"): _Response(202)})
    provider.send_message("Claire <claire@client.fr>", "Devis", "Bonjour")
    payload = session.calls[0][2]
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "claire@client.fr"}}]
    assert payload["message"]["body"]["contentType"] == "Text"
    assert payload["saveToSentItems"] is True


def test_html_send_marks_the_body_html():
    provider, session = _provider({("POST", "/me/sendMail"): _Response(202)})
    provider.send_html_message("a@b.c", "s", "<p>hi</p>", "hi")
    assert session.calls[0][2]["message"]["body"] == {"contentType": "HTML", "content": "<p>hi</p>"}


def test_reply_all_goes_through_a_draft_so_graph_sets_the_threading_headers():
    provider, session = _provider(
        {
            ("POST", "createReplyAll"): _Response(201, {"id": "draft-1"}),
            ("GET", "draft-1?$select=toRecipients"): _Response(
                200, {"toRecipients": [{"emailAddress": {"address": "claire@client.fr"}}], "ccRecipients": []}
            ),
            ("PATCH", "draft-1"): _Response(200, {"id": "draft-1"}),
            ("POST", "draft-1/send"): _Response(202),
        }
    )
    result = provider.reply_all_message("m1", "Merci")

    assert result["status"] == "sent"
    assert [c for c in session.calls if c[0] == "PATCH"][0][2]["body"]["content"] == "Merci"
    assert any("draft-1/send" in c[1] for c in session.calls)


def test_forward_sets_the_recipient_on_the_draft():
    provider, session = _provider(
        {
            ("POST", "createForward"): _Response(201, {"id": "draft-2"}),
            ("PATCH", "draft-2"): _Response(200, {"id": "draft-2"}),
            ("POST", "draft-2/send"): _Response(202),
        }
    )
    provider.forward_message("m1", "collegue@acme.fr", "Pour info")
    patch = [c for c in session.calls if c[0] == "PATCH"][0][2]
    assert patch["toRecipients"] == [{"emailAddress": {"address": "collegue@acme.fr"}}]


# --- outbound allowlist --------------------------------------------------
def test_allowlist_blocks_sends_on_the_outlook_path(monkeypatch):
    """The guard has to bind below every provider, not just Gmail."""
    from src.outbound_guard import OutboundRecipientBlocked

    monkeypatch.setattr(settings, "outbound_allowlist", {"allowed@acme.fr"})
    provider, session = _provider({("POST", "/me/sendMail"): _Response(202)})

    with pytest.raises(OutboundRecipientBlocked):
        provider.send_message("stranger@evil.com", "s", "b")
    with pytest.raises(OutboundRecipientBlocked):
        provider.send_html_message("stranger@evil.com", "s", "<p>h</p>", "h")
    with pytest.raises(OutboundRecipientBlocked):
        provider.forward_message("m1", "stranger@evil.com", "n")
    with pytest.raises(OutboundRecipientBlocked):
        provider.create_draft("stranger@evil.com", "s", "b")
    with pytest.raises(OutboundRecipientBlocked):
        provider.notify_internal_message("stranger@evil.com", "s", "n")
    assert session.calls == []


def test_allowlist_blocks_reply_all_on_the_recipients_graph_derived(monkeypatch):
    from src.outbound_guard import OutboundRecipientBlocked

    monkeypatch.setattr(settings, "outbound_allowlist", {"allowed@acme.fr"})
    provider, session = _provider(
        {
            ("POST", "createReplyAll"): _Response(201, {"id": "draft-1"}),
            ("GET", "draft-1?$select=toRecipients"): _Response(
                200, {"toRecipients": [{"emailAddress": {"address": "stranger@evil.com"}}]}
            ),
        }
    )

    with pytest.raises(OutboundRecipientBlocked):
        provider.reply_all_message("m1", "Merci")
    # The draft was created but never sent.
    assert not any("/send" in call[1] for call in session.calls)


def test_allowed_recipient_passes():
    provider, session = _provider({("POST", "/me/sendMail"): _Response(202)})
    provider.send_message("ok@acme.fr", "s", "b")
    assert any("sendMail" in call[1] for call in session.calls)


# --- threads, attachments, errors ---------------------------------------
def test_thread_is_fetched_by_conversation_id_and_ordered_chronologically():
    older = {**MESSAGE, "id": "m0", "receivedDateTime": "2026-07-30T08:00:00Z",
             "body": {"contentType": "text", "content": "Premier message"}}
    newer = {**MESSAGE, "id": "m1", "receivedDateTime": "2026-08-01T09:12:00Z",
             "body": {"contentType": "text", "content": "Deuxieme message"}}
    provider, session = _provider({("GET", "conversationId eq"): _Response(200, {"value": [newer, older]})})

    messages = provider.fetch_thread("conv-9")
    rendered = provider.format_thread(messages)

    assert "conversationId eq 'conv-9'" in session.calls[0][1]
    assert rendered.index("Premier message") < rendered.index("Deuxieme message")


def test_thread_respects_the_message_cap_and_truncates_long_bodies():
    provider, _ = _provider()
    messages = [
        {**MESSAGE, "id": f"m{i}", "receivedDateTime": f"2026-08-0{i+1}T09:00:00Z",
         "body": {"contentType": "text", "content": "x" * 5000}}
        for i in range(3)
    ]
    rendered = provider.format_thread(messages, max_messages=2, max_chars_per_message=100)
    assert rendered.count("From:") == 2
    assert "[truncated]" in rendered


def test_get_message_expands_attachment_metadata():
    provider, _ = _provider(
        {
            ("GET", "/attachments"): _Response(
                200, {"value": [{"id": "att-1", "name": "devis.pdf", "contentType": "application/pdf", "size": 10}]}
            ),
            ("GET", "/me/messages/AAMk-1"): _Response(200, MESSAGE),
        }
    )
    message = provider.get_message("AAMk-1")
    assert message["_attachments"][0]["name"] == "devis.pdf"


def test_attachment_download_decodes_base64():
    provider, _ = _provider(
        {("GET", "/attachments/att-1"): _Response(200, {"contentBytes": base64.b64encode(b"%PDF-1.4").decode()})}
    )
    assert provider.download_attachment("m1", "att-1") == b"%PDF-1.4"


def test_graph_errors_carry_their_status_code():
    provider, _ = _provider(
        {("GET", "/me/messages/m1"): _Response(404, {"error": {"message": "not found"}})}
    )
    with pytest.raises(OutlookProviderError) as exc:
        provider.get_message("m1")
    assert exc.value.status_code == 404
    assert "not found" in str(exc.value)


def test_batch_fetch_skips_messages_that_fail():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return _Response(200, {"id": "m1"}) if calls["n"] == 1 else _Response(500, {"error": {"message": "boom"}})

    provider, _ = _provider({("GET", "/me/messages/"): flaky})
    result = provider.fetch_messages_batch(["m1", "m2"])
    assert list(result) == ["m1"]


def test_probe_and_self_address_never_raise():
    provider, _ = _provider({("GET", "/me?"): _Response(500, {"error": {"message": "down"}})})
    assert provider.probe()["ok"] is False
    assert provider.self_address() == ""

    ok, _ = _provider({("GET", "/me?"): _Response(200, {"mail": "sales@acme.fr"})})
    assert ok.probe() == {"ok": True, "mailbox": "sales@acme.fr", "error": ""}
    assert ok.self_address() == "sales@acme.fr"


def test_outlook_provider_implements_the_whole_protocol():
    expected = {
        name
        for name in dir(MailProvider)
        if not name.startswith("_") and callable(getattr(MailProvider, name, None))
    }
    for name in sorted(expected):
        impl = getattr(OutlookProvider, name, None)
        assert impl is not None, f"OutlookProvider is missing {name}"
        assert list(inspect.signature(impl).parameters) == list(
            inspect.signature(getattr(MailProvider, name)).parameters
        ), f"OutlookProvider.{name} signature diverges from the protocol"


def test_get_provider_returns_outlook_when_the_instance_says_so(monkeypatch):
    from src.mail import get_provider

    monkeypatch.setattr("src.mail.get_mail_provider", lambda instance_id: "outlook")
    assert isinstance(get_provider(agent_instance_id="sales"), OutlookProvider)
