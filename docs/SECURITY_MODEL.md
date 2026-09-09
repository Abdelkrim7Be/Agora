# Security model

Agora's security model is defense-in-depth: no single layer is trusted to
catch everything, and each layer is independently enforceable: a bug in one
doesn't remove the others. This document explains what's actually
implemented, what it defends against, and where the real limitations are.
Precision is the goal here, not reassurance; every claim below is checked
against the code, not the intent behind it.

## Threat model

**What Agora defends against:**
- An unauthenticated or under-privileged user reaching platform or agent
  data (JWT + platform RBAC + instance RBAC).
- One tenant's data being visible to another tenant sharing the same
  deployment (per-instance scoping, PostgreSQL row-level security).
- An agent's LLM being manipulated by content in an email it processes into
  taking an action the operator didn't authorize (inbound sanitization,
  capability policy, human-in-the-loop approval, an independent outbound
  check on top of all of it).
- A send/forward reaching an unintended recipient (recipient allow/deny
  lists enforced at the policy layer, plus a separate, independent outbound
  allowlist that isn't derived from the same code path).
- Tampering with the audit trail going undetected (SHA-256 hash-chained
  audit log).

**What Agora does not claim to solve:**
- Prompt injection is *mitigated*, not eliminated. See "Prompt injection
  defenses" below; this is the section most likely to be misread as a
  stronger guarantee than it is.
- Agora does not audit the LLM provider itself. If you point it at a
  third-party model endpoint, that provider sees the content you send it,
  subject to whatever data-handling terms that provider offers.
- Availability/DoS resistance is not a design goal of the current
  implementation. There is no rate limiting on most endpoints beyond login
  attempts.
- Outlook support has not been verified against a live mailbox (see
  `services/email-agent/docs/mail-providers.md`); treat it as less battle-
  tested than the Gmail path from a correctness standpoint, though it goes
  through the same authorization chain.

## Authentication

The gateway is the only component that authenticates a user; no other
service does. Login (`POST /auth/login`) issues a short-lived JWT access
token (default 15 minutes) and a rotating `HttpOnly`, `SameSite=Strict`
refresh cookie (default 7 days); the refresh token never touches
JavaScript. Passwords are hashed with BCrypt. Optional TOTP-based MFA is
supported per account, with recovery codes issued at enrollment; when
enabled, login requires a second `/auth/mfa/verify` step against a
short-lived challenge token before a session is issued. Logging out revokes
the access token itself (not just the refresh cookie), so a captured but
already-logged-out token is rejected, not just orphaned. Login attempts are
rate-limited per account and globally.

## Authorization

Two independent role axes, both enforced server-side in the gateway:

- **Platform role**: `admin`, `owner`, `viewer`, or `approver`, carried in
  the JWT. Gates broad categories of gateway endpoint (user management,
  agent-type registration, audit access).
- **Instance role**: `owner`, `approver`, or `viewer`, resolved per agent
  instance: the instance's creator is always its owner; beyond that, an
  explicit grant (`AgentInstanceGrant`) or an instance's configured allowed
  roles determine who can do what. This is what lets a platform-level
  viewer hold real approver access on one specific mailbox without touching
  any other instance.

A request has to clear both: the platform-role gate before it reaches the
proxy, and the instance-role resolution inside the proxy before it's
forwarded to the agent. Neither check trusts anything the browser sends
except the JWT itself; instance role is resolved server-side from the
database on every request, not cached in a client-readable token claim.

## Tenant isolation

Every agent instance is a separate scope, not just a filter applied to a
shared table. `email-agent` tags every piece of state (run records,
learned memory, OAuth tokens, cost entries, categories) with both a user id
and an agent instance id, and the gateway stamps the instance id on every
proxied request from the resolved, server-side value, never from anything
the client supplies directly. Underneath the application-level scoping,
PostgreSQL enforces row-level security on the app-owned business tables,
bound to the tenant/instance pair for the runtime database role, so a bug
in the application-level scoping doesn't automatically mean cross-tenant
data is readable at the database layer too, for the tables RLS covers.

**Known boundary**: LangGraph's own checkpoint/store tables (conversation
state, not the app's own business tables) are not covered by PostgreSQL RLS
. Isolation there is enforced by application-level ownership checks before
a checkpoint is read, not by the database. A bug in that specific check path
is a tenant-isolation bug, not something RLS would independently catch. This
is exercised by `email-agent`'s own tenant-isolation test suite
(`test_run_tenant_isolation.py`, `test_tenant_rls_postgres.py`), which
should stay green as evidence, not just aspiration.

## Agent capability isolation

Every tool an agent can call is registered with a policy decision in
`services/security/policy.yaml`:

| Decision | Meaning |
|---|---|
| `allow` | Executes immediately. Used for reversible, low-risk actions (labeling, archiving). |
| `hitl` | Pauses for human approval before executing. Used for anything that sends, forwards, or is otherwise hard to undo. |
| `deny` | Never executes. The default for anything not explicitly listed: an unlisted tool is refused, not silently allowed. |

The policy engine (`security` service, `/authorize`) evaluates every tool
call **before** it executes, independent of what the LLM decided to do. The
model proposes a tool call, the policy decides whether it's allowed to
happen. Recipient-level allow/deny lists can further constrain send-style
tools regardless of the tool's own default decision. A category's own
configuration can only make a workflow's policy *stricter* than the tool's
default (escalate `allow` to `hitl`, narrow allowed recipients), never
looser. See `services/email-agent/docs/capabilities.md` for the full
tool-by-tool table.

## Human-in-the-loop

When a tool call's policy decision is `hitl`, the LangGraph run interrupts:
execution genuinely pauses, it doesn't queue behind a timer, and surfaces
as a pending approval in the validation queue. A reviewer with sufficient
instance role can approve as-is, edit and approve, reject, or send free-text
feedback for the agent to revise and re-propose. **An edited approval is
re-authorized before execution**, not just re-displayed. Editing a draft
doesn't bypass the policy check that would otherwise have applied to the
original content.

## Prompt injection defenses

Inbound email content passes through the `security` service's sanitization
step before any agent reasoning sees it. This is a mitigation, layered with
the capability-policy and HITL gates above it, not a claim that prompt
injection is solved. Concretely:

- Sanitization runs on every inbound message before triage, independent of
  which workflow eventually handles it.
- A message that trips an injection signal forces the routing outcome toward
  `notify` (a human sees it) rather than a silent autonomous `respond`.
- Right before an approved send actually reaches Gmail/Outlook, a second,
  independent check (`/audit-output`) scans the *outbound* content for
  injection artifacts that survived sanitization and got echoed into a
  draft, whether by the model or a careless human edit during review. A
  flagged draft is blocked even if `/authorize` already allowed the action,
  and a `security`-service outage blocks the send rather than letting it
  through unaudited.
- CI runs an adversarial regression suite
  (`services/email-agent/eval/`, AgentDojo-style attack cases) on every PR
  and **fails the build on any successful attack**. This is a regression
  gate against known attack patterns, not a proof that novel attacks don't
  exist.

If you are evaluating Agora for a use case where prompt injection is a
serious concern, read the eval cases in `services/email-agent/eval/cases/`
yourself rather than taking "there's a defense" at face value.

## Outbound safeguards

Independent of the capability policy above, `AGENT_OUTBOUND_ALLOWLIST` (an
environment variable, not a database row a compromised process could edit
at runtime) can restrict which addresses the agent may ever send or forward
to, regardless of what the policy engine or the model decided. This is
meant as a last-resort, config-level backstop for testing or narrowly-scoped
deployments. It's independent by design, not a replacement for
`policy.yaml`'s recipient rules.

## Secrets and OAuth token protection

Gmail/Outlook OAuth tokens can be stored as envelope-encrypted blobs at
rest, with key rotation supported (old keys keep decrypting existing tokens
until they're naturally re-wrapped on next write). Production deployments
can back the encryption key with an external secret manager (Vault KV v2)
instead of an environment variable. Full detail, including the rotation and
recovery procedure, is in
`services/email-agent/docs/token-encryption.md`; this section is a
summary, that file is the operational reference.

CI runs secret scanning (`gitleaks`, full git history, every PR) and
dependency/image vulnerability scanning (`Trivy`, `HIGH`/`CRITICAL`,
fixable CVEs fail the build) as merge-blocking gates, not optional checks.

## Auditability

Every gateway-mediated action is recorded to an append-only audit log, each
row hash-chained to the previous one (`prevHash`/`hash`, SHA-256).
`GET /audit/verify` recomputes the chain and reports whether it's intact,
and if not, the id of the first row where it breaks. A direct database edit
to a historical row breaks that row's recomputed hash or the next row's
link; either shows up as `valid: false`. Rows written before hash-chaining
existed are counted separately (`unverifiableLegacyCount`) rather than
treated as a broken chain. Audit rows are intentionally exempt from data
retention; see `docs/compliance/DATA_RETENTION.md`.

## Security limitations

Stated plainly, not buried:

- No formal third-party security audit has been performed.
- Rate limiting exists on login; most other endpoints have none.
- The LangGraph checkpoint/store tenant boundary relies on an
  application-level check, not database RLS (see "Tenant isolation" above).
- Outlook's authorization path is code-identical to Gmail's but has not
  been exercised against a live mailbox.
- The prompt-injection eval suite catches known attack patterns; it is not
  a guarantee against novel ones.
- This is a defense-in-depth model built and tested by a small team, not a
  vendor-certified security product. Treat every claim in this document as
  a starting point for your own review, not a substitute for one.

If you find a security issue, see `SECURITY.md` for how to report it.
