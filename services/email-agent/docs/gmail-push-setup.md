# Gmail push notifications (Pub/Sub) setup

Push mode replaces constant polling: Google publishes a Pub/Sub message when a
watched mailbox changes, Pub/Sub POSTs it to `/webhooks/gmail`, and the agent
fetches only the delta via `history.list`. Polling stays on as a slow safety
net (`AGENT_WEBHOOK_FALLBACK_POLL_MIN`, default 10 min).

Everything below happens once per Google Cloud project.

## 1. Google Cloud console

1. Open <https://console.cloud.google.com> and select the project that owns the
   OAuth client in `credentials.json` (Gmail watch requires the topic and the
   OAuth client to live in the same project).
2. **Enable APIs**: Gmail API and Cloud Pub/Sub API
   (APIs & Services → Enable APIs and services).
3. **Create a topic**: Pub/Sub → Topics → Create topic, e.g. `gmail-push`.
   Note the full name: `projects/<project-id>/topics/gmail-push`.
4. **Let Gmail publish to it**: on the topic → Permissions → Grant access →
   principal `gmail-api-push@system.gserviceaccount.com`, role
   **Pub/Sub Publisher**.
5. **Create a push subscription**: Pub/Sub → Subscriptions → Create:
   - Topic: the one above
   - Delivery type: **Push**
   - Endpoint URL:
     `https://<public-host>/api/agent/webhooks/gmail?token=<GMAIL_WEBHOOK_SECRET>`
   - Acknowledgement deadline: 30s is fine

## 2. Public URL

Pub/Sub must reach the gateway over HTTPS.

- **Deployed**: use the deployment's domain. The gateway already allows
  `/api/agent/webhooks/gmail` unauthenticated; the shared-secret `token` query
  param is the auth.
- **Local dev**: tunnel to the gateway port, e.g.
  `cloudflared tunnel --url http://localhost:8090` (or ngrok). Use the printed
  HTTPS hostname in the subscription endpoint. Free tunnels change hostname on
  restart — update the subscription endpoint when it changes.

## 3. Service configuration

Set for the email-agent API and poller (compose: `infra/.env`):

```
GMAIL_WEBHOOK_ENABLED=true
GMAIL_WEBHOOK_TOPIC=projects/<project-id>/topics/gmail-push
GMAIL_WEBHOOK_SECRET=<long random string>       # same value as in the endpoint URL
GMAIL_POLLING_FALLBACK_ENABLED=true             # keep the safety net
AGENT_WEBHOOK_FALLBACK_POLL_MIN=10
```

Restart the stack. On startup the API and poller register a Gmail `watch` for
every connected instance (one per mailbox token) and renew them every
`GMAIL_WATCH_RENEW_HOURS` (default 24h; Google expires watches after 7 days).

## 4. Verify

1. `docker logs infra-poller-1` shows `gmail watch registered for N instance(s)`.
2. Send a mail to a connected mailbox. Within seconds the API log shows
   `POST /webhooks/gmail` and a run appears in the UI — no poll cycle needed.
3. `GET /api/agent/gmail/status` reports `sync_mode: webhook` with a
   `watch_expires_at` timestamp.

## Notes

- The watch covers `INBOX` changes only (`labelIds: ["INBOX"]`).
- A push for a mailbox with no stored baseline seeds the baseline and processes
  from the *next* push onward.
- History older than ~1 week is purged by Gmail; the webhook and the polling
  fallback both detect the stale window and reseed automatically.
- Per-call cost: `history.list` = 2 quota units vs `messages.list` = 5 —
  and pushes only fire when something actually changed.
