# Mail providers

The agent talks to a mailbox through one object satisfying `MailProvider`
(`src/mail/base.py`). Two implementations ship: Gmail and Outlook. Everything
above that layer — the poller, the graph, the junk gate, category routing, the
capability tools, campaigns — is written against the protocol and does not know
which mailbox it is working with.

## Choosing a provider

The provider is stored **per agent instance** by the email-agent, in the same
per-instance config store `send_mode` uses. It is set automatically when an
OAuth flow completes, so there is normally nothing to configure by hand.

Anything unset reads as `gmail`. Every instance that existed before Outlook
support therefore keeps working with no migration.

The value is exposed on `GET /sync/status` as `provider`. That is the only place
other components read it from — the gateway deliberately does not keep its own
copy, because the agent is the component that holds the token and so the only
one that can be authoritative.

## Gmail

Unchanged. `src/mail/gmail.py` is a thin delegate over `src/gmail_client.py`;
the dry-run guard, the outbound allowlist, signature inline images, the API call
budget and batch fetching all still live where they always did.

Connect: `GET /agent-instances/{id}/connect/gmail/start` → consent →
`GET /connect/gmail/callback`. Disconnect: `POST /disconnect/gmail` (revokes at
Google, then deletes the local token).

## Outlook / Microsoft Graph

### Setup

Register an application in Azure and grant it the **delegated** permissions
`Mail.ReadWrite`, `Mail.Send` and `User.Read`. Add the agent's callback as a
redirect URI. Then set:

```
OUTLOOK_CLIENT_ID=
OUTLOOK_CLIENT_SECRET=
OUTLOOK_TENANT=common        # or a directory id to pin single-tenant
OUTLOOK_OAUTH_REDIRECT_URI=http://localhost:8080/api/agent/connect/outlook/callback
```

Scopes requested: `offline_access User.Read Mail.ReadWrite Mail.Send`.
`offline_access` is what returns a refresh token — without it the connection
dies at the first access-token expiry (about an hour).

Gmail-only deployments leave all four blank; nothing reaches this code path
unless an instance's provider is `outlook`.

Connect: `GET /agent-instances/{id}/connect/outlook/start` → consent →
`GET /connect/outlook/callback`. Disconnect: `POST /disconnect/outlook`.

The OAuth state is signed with the same `GMAIL_OAUTH_STATE_SECRET` — that secret
protects the state envelope, not the provider account. PKCE is always used: the
redirect passes through the gateway, so the code is briefly visible on that
path, and the verifier makes an intercepted code useless on its own.

### How Gmail concepts map onto Graph

| Agora concept | Graph |
|---|---|
| unread discovery | `/me/mailFolders/inbox/messages?$filter=isRead eq false` |
| incremental cursor | `/me/mailFolders/inbox/messages/delta` deltaLink |
| stale cursor | HTTP 410 `resyncRequired` |
| thread | `conversationId` |
| label | message `categories` (+ `/me/outlook/masterCategories`) |
| `UNREAD` label | the `isRead` flag |
| `INBOX` label | membership of the inbox folder |
| archive / trash | move to `archive` / `deletedItems` |
| reply / reply-all / forward | `createReply` / `createReplyAll` / `createForward`, then send the draft |

Two of these are worth understanding:

- **The two Gmail pseudo-labels are handled inside `modify_labels`.** That is
  what lets the inbox capability tools, auto-organization and the poller's
  mark-as-read keep working unchanged on Outlook — they all express themselves
  as label edits, and the provider translates.
- **Replies go through a draft.** Creating a reply draft is what makes Graph
  populate `References` and `In-Reply-To` itself, so the message threads
  correctly in the recipient's client without us assembling headers.

## Testing a connection

`POST /connect/test` round-trips the configured mailbox and returns
`{ok, provider, mailbox, error}`. A broken mailbox is a **200 with `ok: false`**,
not an error status — the UI has to be able to show why. The result is recorded
through the normal sync-status path, so the reason appears wherever connection
state is displayed. Exposed in the workspace as "Tester la connexion".

## Caveats

- **Outlook has not been verified against a live mailbox.** It was built without
  an Azure app registration; everything Graph-side is covered by offline
  fixtures only. Treat the first live connect as the real test.
- **Graph change notifications are not implemented.** `watch_mailbox` raises
  `NotImplementedError` on Outlook and those instances run on the polling path.
  Keep `GMAIL_POLLING_FALLBACK_ENABLED=true`.
- **Microsoft has no per-token revoke endpoint.** `POST /disconnect/outlook`
  deletes our stored copy, which disconnects the instance; the directory-side
  grant survives until the user revokes it from their Microsoft account.
- `fetch_messages_batch` issues serial requests on Outlook. Graph's `$batch`
  caps at 20 sub-requests and needs its own error unpacking; revisit if a large
  mailbox makes it hurt.
- Delta links carry no ordering, so `cursor_is_newer` can only answer
  "different". The poller's reconcile-with-a-full-unread-scan is what actually
  guarantees nothing is missed.
- Outlook attachment text extraction follows the same
  `AGENT_EXTRACT_ATTACHMENTS` gate as Gmail.
- **Outlook cannot send attachments.** `write_email`/`create_draft`'s `include_attachments` and
  reviewer-uploaded files (see `docs/capabilities.md#attachments`) are Gmail-only —
  `OutlookProvider.send_message`/`create_draft` raise `NotImplementedError` if attachments are
  passed.
