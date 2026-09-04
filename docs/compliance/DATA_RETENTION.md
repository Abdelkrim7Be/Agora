# Data retention and deletion

Companion to `docs/compliance/GDPR_DATA_INVENTORY.md`. This is the
operator-facing policy: what gets kept, for how long, how to purge it
early, and how to erase a specific person's data on request. Grounded in
shipped code — every mechanism described below has a corresponding source
file, not just a stated intention.

## 1. Automated retention (time-based)

Configured per agent instance in `retention.yaml`:

```yaml
retention_days: 30   # 0 disables automatic purging
```

Enforced by `run_retention()` in `services/email-agent/src/retention.py`,
which on each invocation:

1. Computes `cutoff = now - retention_days`.
2. Finds runs older than the cutoff, scoped to the current tenant/instance.
3. Deletes, for those runs only: LangGraph checkpoints and checkpoint
   writes (whichever storage backend is active), cost entries, trace
   entries, and the run registry record itself.
4. Leaves the **audit log untouched** — audit is append-only and outlives
   run data by design; see `docs/SECURITY_MODEL.md`.

A dry-run mode (`preview_retention()`) runs the same query without
deleting, for a count before committing to a purge.

**Operator action**: set `retention_days` to match your own data-protection
policy or contractual obligations. The shipped default of `30` is a
placeholder, not a compliance decision — confirm it against whatever
agreement governs your deployment.

## 2. What is NOT covered by automatic retention

- **Mailbox content itself** — lives in Gmail/Outlook, governed by the
  mailbox owner's own account settings, not by Agora.
- **Contacts, segments, campaign definitions** — persist until explicitly
  deleted by the operator.
- **OAuth tokens** — persist until revoked/deleted or overwritten by
  re-authorization.
- **Platform accounts** — persist until an admin removes the account.
- **Backups** — `infra/backup-postgres.sh` snapshots are retained per your
  own backup rotation, separate from in-app retention. Purging a run in-app
  does not retroactively scrub it from an already-taken backup; expire old
  backups on your own schedule.

## 3. Right-to-erasure procedure

When a data subject (an employee, a contact, a mailbox owner) requests
erasure: call `POST /gdpr/erase/dry-run` first (owner role, same RBAC gate
as retention endpoints) to see exactly what would be removed, then
`POST /gdpr/erase` with the same body to execute. Both take `{email,
agent_instance_id?, revoke_owner_token?}`.

`erase_subject()` in `services/email-agent/src/gdpr.py` automates:

1. **Contact/employee record**: deletes the matching row, if one exists.
2. **OAuth token** — only when the caller explicitly passes
   `revoke_owner_token=True`. This is never inferred automatically, because
   there is no reliable email-to-instance-owner mapping; set it only when
   the subject *is* the mailbox owner being offboarded, not a third party
   whose address merely appears in the mailbox's mail.
3. **Run history mentioning the subject**: matched by scanning every run's
   sender field for the subject's address (case-insensitive substring).
4. **Cost/trace entries** tied to those runs.
5. **Checkpoints** tied to those runs.

Two things this does **not** touch, matching the age-based retention path:

6. **Backups** — note the erasure date; the subject's data reappears in any
   backup taken before that date until that backup itself expires. Disclose
   this to the requester — it's a standard limitation of backup retention,
   not a bug.
7. **Audit log** — deliberately not erased. The audit trail is append-only
   by design (tamper-evidence requirement — see `docs/SECURITY_MODEL.md`).
   If a request legally requires scrubbing audit entries too, that's a
   policy exception outside this procedure — deleting audit rows ad hoc
   breaks the hash chain and invalidates the trail from that point forward.

## 4. Known limitations

- The subject-match in step 3 only searches the sender field. A subject who
  only ever appeared as a `to`/`cc` recipient on outbound mail (not the
  original sender) is not currently matched.
- Deciding whether the audit-log exception (§3.7) needs a documented policy
  exception for your jurisdiction is your call to make before it's ever
  actually requested, not something the code decides for you.
