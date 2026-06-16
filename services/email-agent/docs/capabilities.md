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

## Security Policy

When `AGENT_SECURITY_ENABLED=true`, every tool call is authorized through the security service before execution.

Default policy decisions:

| Tool | Decision |
|------|----------|
| `apply_label`, `remove_label`, `mark_read`, `mark_unread`, `archive_email` | `allow` |
| `create_draft` | `allow` with recipient/content caps |
| `trash_email` | `hitl` |
| `write_email`, `forward_email`, `reply_all` | `hitl` |
| unknown tools | `deny` |

The security service remains default-deny: adding a new tool also requires adding an explicit `policy.yaml` rule.

## Dry Run

`AGENT_DRY_RUN=true` is the default. Agent-proposed Gmail mutations return dry-run results instead of changing the mailbox or sending mail.

One exception is poller bookkeeping: `mark_as_read` can still remove `UNREAD` under dry-run so completed messages are not reprocessed forever.

## Auto Organization

When enabled, ignored emails are automatically labeled and archived:

1. `apply_label(auto_organize.ignored_label)`
2. `archive_email()`

These actions still run through `tool_node`, so dry-run, trusted message context, and security authorization are reused. They are reversible `allow` actions by default and do not pause for human approval.

## Caveats

- Forwarding does not include attachments from the original message.
- `reply_all` recipients are derived from the Gmail thread and are not submitted to recipient policy checks.
- `reply_all` sets `References` to the original `Message-ID` only.
- Auto-organization can perform reversible allowed actions without human approval when enabled.
- Security rate limits are still in-memory and per-process; move them out of process in Phase 4.
