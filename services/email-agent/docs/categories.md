# Category automation

Categories are a typed overlay on the existing automation engine. Each category
matches inbound email deterministically and routes it to a handling policy without
touching the triage LLM. The triage node only runs when no deterministic rule
matches, and can still tag a category via its `RouterSchema.category` output so the
run record carries the right metadata.

## Configuration

Categories live in `email-agent/categories.yaml` (default `enabled: false`):

```yaml
enabled: true

categories:
  - name: attestation_travail
    display_name: Attestation de travail
    priority: normal
    when:
      subject_contains: [attestation, attestation de travail]
    policy: auto_draft
    template: attestation_travail

  - name: support
    display_name: Support request
    priority: urgent
    when:
      sender_domain: [helpdesk.example.com]
    policy: notify

templates:
  - name: attestation_travail
    subject: "Re: {{original_subject}}"
    body: |
      Bonjour {{name}},

      Veuillez trouver ci-joint votre attestation de travail.

      Cordialement

contacts:
  - email: rh@example.com
    name: Marie Dupont
    category: attestation_travail
```

Relative paths are resolved from `SERVICE_ROOT`. A missing or empty file is treated
as `enabled: false` — zero behavior change for the default single-mailbox setup.

## Classification order

1. **Contact match** — sender email or domain found in `contacts` with a `category`
   field. Highest priority.
2. **Predicate match** — first `Category` whose `when` conditions match the message
   (sender, domain, subject, label). An empty `when: {}` never matches by design;
   at least one condition is required.
3. **LLM fallback** — the triage router can attach a `category` tag (from
   `RouterSchema.category`) when no deterministic match was found. Deterministic
   always wins.

## Policies

| Policy | Behavior |
|---|---|
| `auto_draft` | Render the template and queue a `write_email` HITL interrupt. The human reviews, edits, and approves before any send. |
| `notify` | Mark the run as `notify` and end. No reply generated. |
| `organize` | Apply category labels + archive via the inbox capability tools (requires `capabilities.inbox: true`). |
| `ignore` | End silently. If `auto_organize.enabled` is true, also labels + archives. |

## Template rendering

Placeholders `{{name}}`, `{{prenom}}` are filled from the matched contact's `name`
field. Any unresolved `{{var}}` is forwarded to the LLM as an instruction rather
than sent raw, so the model finalizes the draft with natural language. The resulting
draft still goes through the HITL approval gate and the security `/authorize` endpoint
before any send.

## Dry-run

`AGENT_DRY_RUN=true` (default) suppresses all real mailbox mutations, including sends
and inbox organization actions triggered by categories. Category routing, template
rendering, and HITL pausing all still operate normally in dry-run mode.

## Drafts view

The control-panel Drafts view (`/drafts` tab inside the agent instance workspace)
lists all runs in `pending_approval` state that carry a `write_email` action, grouped
by category and sorted urgent → normal → low. Each card shows the original email,
the editable draft, and Send / Edit / Reject / Respond actions backed by the existing
`/run/{id}/approve|reject|respond` endpoints.

## Security

Category-driven `auto_draft` sends go through exactly the same code path as
manually-triggered sends:

1. The `write_email` tool call is queued as a HITL interrupt.
2. The human approves (optionally editing the body).
3. `tool_node` calls the security service `/authorize` with the action.
4. Only an `allow` or `hitl` (second-level approval) result proceeds to send.

There is no code path that bypasses this sequence for templated sends.

## Per-instance storage (v1)

In v1 all instances share the committed `categories.yaml`. The loader and all
endpoints accept `agent_instance_id` so that a future DB-backed per-instance store
(CEO/HR/support mailboxes with different templates) requires only a backend swap,
not an API or schema change.
