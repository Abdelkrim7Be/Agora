# Email Agent Capabilities

The email agent loads tools from capability modules configured in `config.yaml`. The default config keeps new inbox actions off until explicitly enabled.

## Config

```yaml
capabilities:
  email: true       # send, forward, reply-all, Done
  calendar: false   # schedule_meeting + check_calendar_availability
  inbox: false      # labels, archive, read/unread, trash
  drafts: false     # create Gmail drafts without sending

auto_organize:
  enabled: false
  ignored_label: Auto/Ignored
```

Enable inbox organization and draft creation like this:

```yaml
capabilities:
  email: true
  calendar: false
  inbox: true
  drafts: true

auto_organize:
  enabled: true
  ignored_label: Auto/Ignored
```

`auto_organize.enabled` only has an effect when `capabilities.inbox` is also enabled.

## Tools

| Capability | Tool | Effect | Approval |
|------------|------|--------|----------|
| `email` | `write_email(to, subject, content)` | Sends a new email | HITL |
| `email` | `forward_email(to, note)` | Forwards the current email | HITL |
| `email` | `notify_internal(to, subject, note)` | Sends an internal workflow notice — synthesized note only, never the original message body | HITL |
| `email` | `reply_all(content)` | Replies to all participants on the current thread | HITL |
| `email` | `Done(done)` | Ends the run | None |
| `inbox` | `apply_label(label)` | Applies or creates a Gmail label on the current email | None |
| `inbox` | `remove_label(label)` | Removes an existing Gmail label from the current email | None |
| `inbox` | `archive_email()` | Removes the current email from the inbox | None |
| `inbox` | `mark_read()` | Removes the `UNREAD` label | None |
| `inbox` | `mark_unread()` | Adds the `UNREAD` label | None |
| `inbox` | `trash_email()` | Moves the current email to trash | HITL |
| `drafts` | `create_draft(to, subject, content)` | Creates a Gmail draft without sending | None |

Inbox and thread tools do not accept Gmail message ids from model-provided arguments. `tool_node` injects trusted `email_id` and `gmail_thread_id` from the poller-derived `EmailInput` using context variables.

A category's `policy: notify` (routing to `owner`/`approver`/`route_to`) calls `notify_internal`,
not `forward_email` — the workflow note (category, owner, approver, original sender/subject) is
sent on its own, without re-fetching and re-sending the original message body verbatim. Since it
never needs the original Gmail message, `notify_internal` works for manually-submitted runs too
(unlike a true `forward_email`, which needs a trusted `email_id` to fetch what it forwards).

## Security Policy

When `AGENT_SECURITY_ENABLED=true`, every tool call is authorized through the security service before execution.

Default policy decisions:

| Tool | Decision |
|------|----------|
| `apply_label`, `remove_label`, `mark_read`, `mark_unread`, `archive_email` | `allow` |
| `create_draft` | `allow` with recipient/content caps |
| `trash_email` | `hitl` |
| `write_email`, `forward_email`, `notify_internal`, `reply_all` | `hitl` |
| unknown tools | `deny` |

The security service remains default-deny: adding a new tool also requires adding an explicit `policy.yaml` rule.

### Per-workflow approval policy

A category (`categories.yaml`) can layer two extra rules on top of the tool-level table above.
Both are strictly additive — they can only make a workflow's tool calls stricter, never less
strict than the tool's own default:

- `require_approval: true` — escalates that category's tool calls from `allow` to `hitl`, even
  when the tool's own policy default is `allow` (e.g. forcing human review before an `organize`
  category auto-labels and archives).
- `external_send_allowed: false` — blocks that category's send-style tool calls (`write_email`,
  `forward_email`, `notify_internal`, `reply_all`) from reaching any recipient outside
  `AGENT_INTERNAL_DOMAINS`. With no internal domains configured, this fails closed (blocks
  every recipient) rather than silently doing nothing.

Both are enforced in `tool_node`, independent of `AGENT_SECURITY_ENABLED` — they are a local
workflow-policy rule, not a call to the external security service. Configurable per category via
`PUT /categories/{name}` (`require_approval`, `external_send_allowed`) or the Workflows page.

### Output audit before send

`write_email`, `forward_email`, and `reply_all` get one more check beyond `/authorize`:
right after HITL approval/edit resolves — the last moment before the real Gmail
send — the security service's `/audit-output` scans the actual outbound content for
leaked prompt-injection artifacts (leftover instruction-override phrasing, role
hijack, an obvious secret/credential, or literal `/sanitize` fence markers). This
catches content that survived inbound sanitization and got echoed into the draft,
whether by the LLM or a careless human edit during review. A flagged draft is
blocked outright, even if `/authorize` already allowed the action. Fail-closed: a
security-service outage blocks the send rather than letting it through unaudited.

## Dry Run

`AGENT_DRY_RUN=true` is the default. Agent-proposed Gmail mutations return dry-run results instead of changing the mailbox or sending mail.

One exception is poller bookkeeping: `mark_as_read` can still remove `UNREAD` under dry-run so completed messages are not reprocessed forever.

## Auto Organization

When enabled, ignored emails are automatically labeled and archived:

1. `apply_label(auto_organize.ignored_label)`
2. `archive_email()`

These actions still run through `tool_node`, so dry-run, trusted message context, and security authorization are reused. They are reversible `allow` actions by default and do not pause for human approval.

## Automation (Phase 6)

Configured in `rules.yaml`, fully opt-in (`enabled: false` everywhere by default — the
file then changes nothing). The poller builds a deterministic plan and the graph's
`automation_router` executes it **through the normal `tool_node` path**, so security
authorization, HITL, dry-run, and trusted message context all still apply.

- **Rules** (`when` → `then`): match on `sender_contains` / `sender_domain` /
  `subject_contains` / `labels` and apply labels, archive, mark read, snooze, or draft a
  human-gated response. Rules run **before** triage (cheap, no LLM), so there is no
  `classification` predicate — classification-driven organization is `auto_organize`'s job.
- **Daily digest**: records `notify` / `pending_approval` items and emits once per day at the
  configured hour. Output goes to the **process logs** (`print`) for now — no email/UI channel yet.
- **Snooze**: a `Snoozed/<YYYY-MM-DD>` label; the poller resurfaces due messages to
  `INBOX`/`UNREAD`. Resurfacing respects `AGENT_DRY_RUN` (inert under dry-run).
- **Follow-ups**: threads labeled "Awaiting Reply" older than N days get a HITL-gated nudge.
- **Learning**: human corrections (ignore/edit/feedback) append **disabled** rule suggestions
  to a JSONL log when `learning.enabled` — never auto-applied.

## Caveats

- Forwarding does not include attachments from the original message.
- `reply_all` recipients are derived from the Gmail thread and are not submitted to recipient policy checks.
- `reply_all` sets `References` to the original `Message-ID` only.
- `notify_internal`'s note still interpolates the original sender address and subject line
  as plain text (not the full body) — content caps and HITL review still apply, since those
  fields are untrusted-content-derived.
- Auto-organization can perform reversible allowed actions without human approval when enabled.
- In the Phase 4 compose stack, security rate limits use Redis; local dev can still use the in-memory backend.
