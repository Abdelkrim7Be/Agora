# Incident response

Security incident classes specific to this platform, where to look first,
and the response steps for each. Not a generic corporate incident-response
template — grounded in what Agora actually logs and what an operator can
actually pull, per `docs/SECURITY_MODEL.md`.

This plan is intentionally lightweight, written for a small operating team.
If you're running Agora at a scale with a dedicated security on-call
rotation, adapt it — the incident classes and detection sources below stay
correct, the response-ownership section will need more than one name.

## Incident classes

| Class | What it looks like | Primary signal |
|---|---|---|
| **Prompt injection succeeded** | The agent sent/forwarded content it shouldn't have, or the adversarial eval suite's attack-success rate regresses | `/authorize` deny logs, `security` service audit, CI eval regression |
| **Credential/token leak** | A Gmail/Outlook OAuth token or the token-encryption master key is exposed (git history, a log line, a container layer) | `gitleaks` CI hit, manual discovery, Trivy scan |
| **Tenant data crossover** | One tenant's run/mailbox data becomes visible to another | Tenant-isolation test failure, RLS policy violation, manual report |
| **Unauthorized send** | An email left the system without a human-in-the-loop approval that should have gated it | Gateway audit log, email-agent run trace |
| **Auth bypass** | JWT forged/replayed, MFA bypassed, login brute-forced | Gateway auth logs, login rate-limit counters |

## Detection sources — what to check first

1. **Gateway audit log** (`GET /audit`, owner role) — append-only,
   SHA-256 hash-chained. First stop for "who did what, when." Check
   `GET /audit/verify` before trusting entries around the suspected
   window — a broken hash chain is itself evidence of tampering.
2. **Security service decisions** — `/authorize` deny reasons and
   `/sanitize` verdicts are the record of what the policy engine blocked or
   flagged.
3. **Run registry / traces** — per-run token and tool-call history on the
   email-agent side.
4. **CI scan history** — `gitleaks` (secrets) and Trivy (dependency/image)
   job results in your CI provider; both are merge-blocking gates, so a
   failure there means something was caught before it shipped, not after.

## Response steps, by class

### Prompt injection succeeded
1. Pull the run id from the audit/trace logs; capture the email thread and
   the tool calls that executed.
2. Determine whether `/authorize` was bypassed (a bug) or correctly allowed
   a flow that shouldn't have been permitted by policy (a `policy.yaml`
   gap).
3. If a send/forward actually went out, notify the affected recipient
   through whatever process your deployment's data-protection obligations
   require. Log the incident even if nothing sensitive was exposed.
4. Add the case to the adversarial eval suite
   (`services/email-agent/eval/cases/`) so it's a regression test going
   forward, not just a postmortem note.

### Credential/token leak
1. Rotate immediately: revoke the Gmail/Outlook OAuth grant and re-run
   onboarding; rotate the token-encryption master key (see
   `services/email-agent/docs/token-encryption.md`); rotate
   `GATEWAY_JWT_SECRET` (this invalidates every current session — expected
   and correct).
2. If leaked via git history, treat the secret as permanently compromised —
   rotation is mandatory; scrubbing history is not sufficient on its own
   (assume it's already been cloned or cached elsewhere).
3. Check the gateway audit log for any activity using the leaked credential
   during the exposure window.

### Tenant data crossover
1. Stop the affected instance(s) first — this is a hard-stop-first incident
   class, not observe-then-fix.
2. Confirm scope: which runs, which tenants, what time window. The
   tenant-isolation test suite's assertions describe the ownership
   invariant that was supposed to hold.
3. Fix the ownership-check gap. PostgreSQL row-level security does not
   reach LangGraph's own checkpoint/store tables (see
   `docs/SECURITY_MODEL.md` — tenant isolation section) — a regression
   there is a bug in the application-level ownership check, not something
   RLS would have caught on its own.
4. Notify affected tenants per your own data-protection obligations.

### Unauthorized send
1. Pull the tool-call trace for the run: was `/authorize` never called (a
   wiring bug), or did it return `allow` incorrectly (a policy gap)?
2. If the recipient was external, this is also a data-exfiltration event —
   cross-reference `docs/compliance/GDPR_DATA_INVENTORY.md` for the
   notification path if personal data left the deployment boundary.

### Auth bypass
1. Rotate `GATEWAY_JWT_SECRET` — forces re-login for every session, which is
   the correct hard stop here.
2. Check the audit log for the account/role in question across the
   suspected window.
3. If TOTP MFA was involved, check for recovery-code misuse.

## Roles

Name the people explicitly for your own deployment — this plan doesn't
assume a particular team size:

- **Incident owner**: whoever holds the `owner` platform role — currently
  the only role with `/audit` read access.
- **Escalation**: not formalized by default. If you're running this for
  anything beyond personal/internal use, name a real contact or retained
  security resource before you need one.

## Post-incident

- Record every incident with a dated entry in the log below — don't let
  write-ups live only in chat history.
- Any gap an incident reveals (a missing check, a policy hole) should become
  a tracked issue, not just a one-off fix.

## Incident log

_(none — append dated entries here as they occur)_

## Known gaps in this plan

- No named escalation contact or retained security vendor by default.
- No formal SLA for detection-to-notification timing. If your jurisdiction's
  data-protection law sets one (for example, GDPR Article 33's 72-hour
  window to notify a supervisory authority of a breach), know that number
  before you need it.
- This plan has not been exercised in a tabletop drill. Run one against at
  least one incident class above before treating it as tested rather than
  theoretical.
