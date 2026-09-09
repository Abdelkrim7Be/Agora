# GDPR data processing inventory

A record of processing activities in the shape of a GDPR Article 30 record:
the kind of document a controller or data protection officer can be asked
to produce on request. Grounded in the actual codebase; update this file
whenever a new class of personal data is stored or a new processor (LLM
provider, object storage, etc.) is added.

## 1. Roles

- **Data controller**: the Agora operator running an instance for their own
  mailbox, or the business deploying Agora on behalf of end users
  (multi-instance/multi-tenant mode).
- **Data processor**: Agora itself (self-hosted software) processing
  mailbox content on the controller's behalf. In a single-operator
  deployment, controller and processor are the same party.
- **Sub-processors**: whichever external inference provider is selected for
  the active LLM profile (`services/email-agent/config/llm.*.yaml`, see
  `docs/DEPLOYMENT.md`):
  - **Ollama** (`local`/`local-docker`/`local-host`/`safe` profiles): runs
    on your own infrastructure. No sub-processor, no data leaves the
    deployment.
  - **Groq** (`dev` profile, opt-in): US-hosted, only contacted if this
    profile is explicitly selected.
  - **Mistral** (`prod` profile's primary model, via a self-hosted LiteLLM
    proxy), with **OpenAI** and **Anthropic** configured as fallback
    providers if the primary is unavailable. All three are opt-in via the
    `cloud` Compose profile; none is contacted unless `prod` is explicitly
    selected.

  The local-first default means no sub-processor is contacted at all unless
  a cloud profile is explicitly enabled by the operator.

## 2. Categories of personal data processed

| Category | Examples | Where stored | Code touchpoint |
|---|---|---|---|
| Mailbox content | email subject/body/thread, attachments | Gmail/Outlook (source of truth) + PostgreSQL/SQLite checkpoints | `src/gmail_client.py`, `src/state.py`, `src/run_registry.py` |
| Contacts / directory | name, email, department, audience tag | `contacts.yaml` or per-instance store | `src/contacts.py` |
| OAuth credentials | refresh/access tokens | Vault KV v2 (envelope-encrypted) or local encrypted file | `src/token_store.py` |
| Platform accounts | username, bcrypt password hash, role | PostgreSQL `app_user` table | gateway `AppUser` entity |
| Audit trail | actor, action, timestamp, outcome (hash-chained) | PostgreSQL `audit_event` table | gateway `AuditEvent`/`AuditService` |
| Run/cost/trace telemetry | per-run token counts, model, cost, latency | PostgreSQL or SQLite | `src/cost_tracker.py`, `src/trace.py` |
| Learned preferences | triage/response style derived from sent mail | LangGraph memory store | `src/memory.py` |
| Campaign recipients | contact list snapshot at send time | campaign run record | `src/campaigns.py` |

## 3. Purpose of processing

Triage, drafting, and routing of inbound email on behalf of the controller;
audit and cost accounting for the controller's own oversight; learned style
preferences to improve draft quality. No processing for advertising,
profiling, or resale. No data leaves the deployment boundary unless a cloud
LLM profile is explicitly enabled by the operator (§1).

## 4. Legal basis

Determined by the controller's relationship with the mailbox owner
(typically contract or legitimate interest for an internal company
mailbox). Agora as processor does not itself establish a legal basis; this
section is a placeholder the deploying business must fill in for their own
data processing agreement.

## 5. Retention

Full detail in `docs/compliance/DATA_RETENTION.md`. Summary: configurable
per-instance retention window (default 30 days, 0 disables), automated
deletion of runs/costs/traces/checkpoints past the cutoff, audit trail
explicitly exempt (append-only, outlives run retention by design).

## 6. Right to erasure

Automated via `POST /gdpr/erase` (dry-run variant at
`/gdpr/erase/dry-run`, both owner-role gated); see
`docs/compliance/DATA_RETENTION.md` §3 for exactly what it covers and its
known limitation (subject-matching only checks the sender field, not
`to`/`cc`).

## 7. International transfers

- Local-first default (Ollama, self-hosted): no transfer.
- Groq (`dev` profile, opt-in): US-hosted.
- Mistral (`prod` profile's primary): confirm current hosting region against
  Mistral's own data-processing terms before relying on this for a
  residency-sensitive deployment; this document states what Agora is
  configured to call, not what any given provider guarantees about where
  processing happens.
- OpenAI / Anthropic (`prod` profile's fallback only, contacted solely if
  the primary is unreachable): US-hosted.

## 8. Security measures applied

Cross-reference `docs/SECURITY_MODEL.md` for the full picture: TLS ingress
in production, envelope-encrypted OAuth tokens (optionally backed by
Vault), JWT + RBAC + hash-chained audit log, row-level tenant isolation on
PostgreSQL business tables, dependency/image/secret scanning as CI gates,
and a documented backup/restore drill (`docs/DEPLOYMENT.md`).

## 9. Open items

- [ ] Fill in legal basis per deployment: controller-specific, not
      something the code can decide.
- [ ] Decide and document a data processing agreement template for
      business customers, if you're operating this on behalf of others.
- [ ] Extend subject-matching in the erasure flow beyond the sender field
      if/when recipient-side erasure requests become something you need to
      handle.
- [ ] Confirm attachment/PDF-extracted text follows the same retention
      cutoff as the email thread it came from; currently piggybacks on run
      retention; verify no copy survives elsewhere (e.g. temp files).
