from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import random
import re as _re
import time
import uuid


TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 30.0

# When Gmail reports rateLimitExceeded, pause ALL polling instead of retrying
# every cycle — hammering an exhausted quota keeps the window busy and starves
# interactive actions (approve/send) that share the same per-user budget.
# Google's "Retry after" slides forward when probed too early, so the pause
# honors the server timestamp with a margin and doubles on consecutive hits.
RATE_LIMIT_PAUSE_MIN_SECONDS = 300.0
RATE_LIMIT_PAUSE_MAX_SECONDS = 14400.0
RATE_LIMIT_RETRY_MARGIN_SECONDS = 300.0
_gmail_rate_limited_until = 0.0
_gmail_rate_limit_pause = RATE_LIMIT_PAUSE_MIN_SECONDS


def gmail_rate_limit_pause_remaining() -> float:
    """Seconds until the global Gmail rate-limit pause lifts (0 when not paused)."""
    return max(0.0, _gmail_rate_limited_until - time.time())


def _retry_after_epoch(exc_text: str) -> float | None:
    match = _re.search(r"Retry after (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z", exc_text)
    if not match:
        return None
    try:
        parsed = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.timestamp()


def _note_gmail_rate_limit(exc: Exception) -> bool:
    """Record a global polling pause when the error is a Gmail rate limit."""
    global _gmail_rate_limited_until, _gmail_rate_limit_pause
    text = str(exc)
    if "rateLimitExceeded" not in text and "Too Many Requests" not in text:
        return False
    now = time.time()
    consecutive = _gmail_rate_limited_until > 0 and now < _gmail_rate_limited_until + 600
    if consecutive:
        _gmail_rate_limit_pause = min(_gmail_rate_limit_pause * 2, RATE_LIMIT_PAUSE_MAX_SECONDS)
    else:
        _gmail_rate_limit_pause = RATE_LIMIT_PAUSE_MIN_SECONDS
    until = now + _gmail_rate_limit_pause
    retry_after = _retry_after_epoch(text)
    if retry_after:
        until = max(until, retry_after + RATE_LIMIT_RETRY_MARGIN_SECONDS)
    _gmail_rate_limited_until = until
    # Next consecutive hit doubles from the pause actually served, not the
    # small base — otherwise a sliding server ban is re-probed (and renewed)
    # many times before the cap is reached.
    _gmail_rate_limit_pause = min(
        max(_gmail_rate_limit_pause, until - now), RATE_LIMIT_PAUSE_MAX_SECONDS
    )
    print(
        f"poller: Gmail rate limit hit; pausing all polling for "
        f"{int(until - now)}s"
    )
    return True


from src.automation import (
    RulesConfig,
    build_follow_up_plan,
    build_rule_plan,
    due_snooze_labels,
    follow_up_query,
    load_escalation_state,
    load_rules,
    mark_run_escalated,
    maybe_emit_daily_digest,
    record_digest_item,
    workflow_sla_snapshot,
)
from src.config import settings
from src.security_client import sanitize_email
from src.gmail_client import (
    current_history_id,
    download_attachment,
    extract_pdf_text,
    fetch_history_message_refs,
    fetch_thread,
    fetch_unread,
    get_message,
    gmail_resource,
    gmail_to_email_input,
    is_stale_history_error,
    list_labels,
    list_messages_by_label,
    mark_as_read,
    modify_labels,
    search_messages,
    watch_mailbox,
)
from src.categories import classify_category, load_categories
from src.junk_gate import is_junk
from src.graph import overall_workflow, reload_config
from src.migrate import upgrade_to_head
from src.notifications import notify_overdue_approval, notify_pending_approval
from src.dlq import record_dead_letter, setup_dlq
from src.metrics import inc_counter
from src.health import aggregate_health
from src.alerts import evaluate_alerts
from src.retention import run_retention
from src.trace import setup_trace_store
from src.gmail_sync import get_last_history_id, set_last_history_id, setup_gmail_sync
from src.sync_status import get_status, record_failure, record_success, setup_sync_status
from src.run_registry import (
    ACTIVE_RUN_STATUSES,
    find_run_by_email,
    list_runs,
    setup_run_registry,
    upsert_run,
)
from src.storage import open_graph_storage
from src.token_store import has_stored_token
from src.tenant import (
    agent_instance_context,
    current_agent_instance_id,
    normalize_agent_instance_id,
)


def _http_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "resp", None)
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    status = _http_status_code(exc)
    if status in TRANSIENT_HTTP_STATUS_CODES:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in (
        "rate_limit",
        "rate limit",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "server disconnected",
    ))


def _backoff_seconds(attempt: int) -> float:
    base = max(settings.poll_backoff_base_seconds, 0.0)
    return min(base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


async def _process_message_with_retry(
    graph,
    msg_id: str,
    resource,
    rules_config: RulesConfig,
) -> tuple:
    max_retries = max(0, settings.poll_max_retries)
    attempt = 0
    while True:
        try:
            return await process_message(graph, msg_id, resource, rules_config)
        except Exception as exc:
            if not _is_transient_error(exc):
                print(f"poller: {msg_id} failed without retry: {exc}")
                record_failure(str(exc))
                record_dead_letter({"message_id": msg_id, "reason": "terminal_failure", "error": str(exc), "payload": {"email_id": msg_id}})
                inc_counter("agora_poller_dlq_total", reason="terminal_failure")
                return (msg_id, "failed", "")
            attempt += 1
            inc_counter("agora_poller_retry_total", reason="transient")
            if attempt > max_retries:
                print(f"poller: {msg_id} exhausted retries: {exc}")
                record_failure(str(exc))
                try:
                    message = get_message(msg_id, resource=resource)
                    thread = fetch_thread(message["threadId"], resource=resource)
                    payload = gmail_to_email_input(message, thread_messages=thread)
                except Exception:
                    payload = {"email_id": msg_id}
                record_dead_letter({"message_id": msg_id, "reason": "retry_exhausted", "error": str(exc), "payload": payload})
                inc_counter("agora_poller_dlq_total", reason="retry_exhausted")
                return (msg_id, "failed", "")
            delay = _backoff_seconds(attempt)
            print(f"poller: {msg_id} transient failure (attempt {attempt}/{max_retries}): {exc}")
            await asyncio.sleep(delay)


def _run_email_input(email_input: dict, result: dict) -> dict:
    return {
        **email_input,
        "category": result.get("category"),
        "category_display_name": result.get("category_display_name"),
        "priority": result.get("priority"),
        "template": result.get("template"),
        "workflow_owner": result.get("workflow_owner"),
        "workflow_approver": result.get("workflow_approver"),
        "workflow_route_to": result.get("workflow_route_to") or [],
    }


def sweep_pending_approval_slas(now: datetime | None = None) -> list[tuple[str, str]]:
    """Escalate overdue approvals once per run."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    categories_cfg = load_categories(agent_instance_id=current_agent_instance_id())
    escalation_state = load_escalation_state()
    escalated: list[tuple[str, str]] = []
    now_iso = now.isoformat(timespec="seconds")
    for run in list_runs(
        status="pending_approval",
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
        limit=5000,
    ):
        snapshot = workflow_sla_snapshot(run, categories_cfg, escalation_state=escalation_state, now=now)
        if not snapshot.get("overdue") or snapshot.get("escalated_at"):
            continue
        recipient = notify_overdue_approval(
            run["run_id"],
            run,
            int(snapshot.get("overdue_by_seconds") or 0),
            snapshot.get("due_at"),
        )
        if not recipient:
            continue
        mark_run_escalated(run["run_id"], recipient, now=now)
        escalation_state.setdefault("runs", {})[run["run_id"]] = {
            "escalated_at": now_iso,
            "escalation_target": recipient,
        }
        escalated.append((run["run_id"], recipient))
    return escalated


def ensure_watch(resource=None) -> dict | None:
    """Register/renew the Gmail push watch and seed the sync baseline.

    Seeding the baseline at watch time is what makes the first push processable:
    the next notification queries history from this id forward (see api.gmail_webhook).
    No-op unless Gmail webhooks are enabled.
    """
    if not settings.gmail_webhook_enabled:
        return None
    resource = resource or gmail_resource()
    result = watch_mailbox(resource=resource)
    history_id = str(result.get("historyId") or "")
    if history_id:
        set_last_history_id(history_id)
    # Gmail watch expiration is a Unix ms timestamp; convert to ISO for storage.
    watch_expires_at: str | None = None
    raw_expiry = result.get("expiration")
    if raw_expiry:
        from datetime import datetime, timezone
        try:
            watch_expires_at = datetime.fromtimestamp(
                int(raw_expiry) / 1000, tz=timezone.utc
            ).isoformat(timespec="seconds")
        except (ValueError, TypeError):
            pass
    record_success("webhook", watch_expires_at=watch_expires_at)
    return result


def watch_is_fresh(instance_id: str, margin_seconds: float | None = None) -> bool:
    """Whether the instance's recorded watch expiration is still beyond the margin.

    A missing or unparseable expiration counts as stale so the watch gets
    re-registered — renewal is idempotent on the Gmail side.
    """
    if margin_seconds is None:
        margin_seconds = settings.gmail_watch_renew_margin_hours * 3600
    expires = get_status(agent_instance_id=instance_id).get("watch_expires_at")
    if not expires:
        return False
    try:
        expires_dt = datetime.fromisoformat(expires)
    except ValueError:
        return False
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    remaining = (expires_dt - datetime.now(timezone.utc)).total_seconds()
    return remaining > margin_seconds


def ensure_watches(
    instance_ids: list[str] | None = None, force: bool = False
) -> dict[str, dict | None]:
    """Register/renew the Gmail push watch for every connected instance.

    Each agent instance has its own mailbox token, so each needs its own watch.
    One instance failing (revoked token, rate limit) must not block the others.
    Renewal is expiration-driven: instances whose recorded watch expiration is
    still beyond the renewal margin are skipped unless force=True.
    """
    if not settings.gmail_webhook_enabled:
        return {}
    results: dict[str, dict | None] = {}
    for raw_instance_id in instance_ids or active_email_agent_instance_ids():
        instance_id = normalize_agent_instance_id(raw_instance_id)
        with agent_instance_context(instance_id):
            if not has_stored_token(instance_id):
                results[instance_id] = None
                continue
            if not force and watch_is_fresh(instance_id):
                results[instance_id] = None
                continue
            try:
                results[instance_id] = ensure_watch()
            except Exception as exc:
                print(f"poller: {instance_id} gmail watch failed: {exc}")
                record_failure(str(exc))
                results[instance_id] = None
    return results


def resurface_due_snoozed(resource, rules_config: RulesConfig) -> list[tuple[str, str]]:
    """Move due snoozed messages back to INBOX/UNREAD."""
    surfaced: list[tuple[str, str]] = []
    for label in due_snooze_labels(list_labels(resource=resource), rules_config):
        label_id = label["id"]
        label_name = label.get("name", label_id)
        refs = list_messages_by_label(
            label_id,
            rules_config.snooze.max_resurface_per_run,
            resource=resource,
        )
        for ref in refs:
            msg_id = ref["id"]
            modify_labels(
                msg_id,
                add_label_ids=["INBOX", "UNREAD"],
                remove_label_ids=[label_id],
                resource=resource,
            )
            surfaced.append((msg_id, label_name))
    return surfaced


async def poll_follow_ups(graph, resource, rules_config: RulesConfig) -> list[tuple]:
    """Find old awaiting-reply threads and propose a nudge through HITL."""
    if not rules_config.follow_ups.enabled:
        return []

    outcomes: list[tuple] = []
    refs = search_messages(
        follow_up_query(rules_config),
        rules_config.follow_ups.max_results,
        resource=resource,
    )
    for ref in refs:
        msg_id = ref["id"]
        message = get_message(msg_id, resource=resource)
        thread = fetch_thread(message["threadId"], resource=resource)
        email_input = gmail_to_email_input(message, thread_messages=thread)
        plan = build_follow_up_plan(email_input, rules_config)
        if not plan:
            continue
        email_input = {**email_input, "automation": plan}
        run_id = str(uuid.uuid4())
        result = await graph.ainvoke(
            {"email_input": email_input},
            {"configurable": {"thread_id": run_id}},
        )
        status = "pending_approval" if result.get("__interrupt__") else "follow_up_proposed"
        run_email_input = _run_email_input(email_input, result)
        upsert_run(
            run_id,
            status,
            email_input=run_email_input,
            classification=result.get("classification_decision"),
            pending_action=result["__interrupt__"][0].value if result.get("__interrupt__") else None,
            agent_instance_id=current_agent_instance_id(),
        )
        outcomes.append((msg_id, status, run_id))
    return outcomes


async def process_message(
    graph,
    msg_id: str,
    resource,
    rules_config: RulesConfig,
) -> tuple:
    # An email left UNREAD because it already has a run must not be reprocessed:
    # a pending/held run would spawn a duplicate every cycle; a resolved one (e.g.
    # an approved reply the API sent but couldn't mark read) just needs housekeeping.
    # Checked BEFORE fetching the message: with a short poll interval, re-fetching
    # every known unread email each cycle burns the Gmail per-user quota (429s on
    # sends share the same budget).
    existing = find_run_by_email(
        msg_id,
        # Runs belong to the mailbox instance, not to the actor who triggered sync.
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    if existing:
        if existing["status"] in ACTIVE_RUN_STATUSES:
            return (msg_id, existing["status"], existing["run_id"])
        mark_as_read(msg_id, resource=resource)
        return (msg_id, "skipped", existing["run_id"])

    message = get_message(msg_id, resource=resource)
    labels = message.get("labelIds")
    if labels is not None and ("INBOX" not in labels or "UNREAD" not in labels):
        return (msg_id, "skipped", "")

    # Deterministic junk gate: bulk/no-reply mail never reaches the LLM or the
    # validation box — unless a configured workflow claims it (workflow wins).
    # Runs before fetch_thread so gated mail costs no extra Gmail call.
    gate_input = {
        **gmail_to_email_input(message),
        "agent_instance_id": current_agent_instance_id(),
    }
    categories_config = load_categories(agent_instance_id=current_agent_instance_id())
    category_match = classify_category(gate_input, categories_config)
    if not category_match.get("category"):
        junk, junk_reason = is_junk(gate_input)
        if junk:
            run_id = str(uuid.uuid4())
            upsert_run(
                run_id,
                "completed",
                email_input={
                    **gate_input,
                    "category": "junk_auto",
                    "category_display_name": "Ignoré automatiquement",
                },
                classification="ignore",
                pending_action=None,
                agent_instance_id=current_agent_instance_id(),
            )
            mark_as_read(msg_id, resource=resource)
            print(f"poller: junk-gated {msg_id} ({junk_reason})")
            return (msg_id, "completed", run_id)

    thread = fetch_thread(message["threadId"], resource=resource)
    email_input = {
        **gmail_to_email_input(message, thread_messages=thread),
        "agent_instance_id": current_agent_instance_id(),
    }

    if settings.extract_attachments:
        pdf_blocks = []
        for att in email_input.get("attachments", []):
            if att["mime_type"] == "application/pdf" and att.get("attachment_id"):
                try:
                    raw = download_attachment(msg_id, att["attachment_id"], resource=resource)
                    text = extract_pdf_text(raw, settings.attachment_max_chars)
                    if text:
                        pdf_blocks.append(f"--- {att['filename']} ---\n{text}")
                except Exception:
                    pass
        if pdf_blocks:
            extra = "\n\n".join(pdf_blocks)
            email_input = {
                **email_input,
                "email_thread": email_input["email_thread"] + "\n\nAttachment contents:\n" + extra,
            }

    security_flagged = False
    if settings.security_enabled:
        verdict = await sanitize_email(
            sender=email_input.get("author", ""),
            subject=email_input.get("subject", ""),
            content=email_input["email_thread"],
        )
        security_flagged = bool(
            verdict["injection_detected"] or verdict["classifier_unavailable"]
        )
        email_input = {
            **email_input,
            "email_thread": verdict["cleaned_text"],
            "security": {
                "injection_detected": verdict["injection_detected"],
                "classification": verdict["classification"],
                "classifier_unavailable": verdict["classifier_unavailable"],
            },
        }

    rule_plan = build_rule_plan(email_input, rules_config)
    if rule_plan:
        email_input = {**email_input, "automation": rule_plan}

    run_id = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": run_id}}

    result = await graph.ainvoke({"email_input": email_input}, cfg)

    if result.get("__interrupt__"):
        outcome_status = "pending_approval"
    elif result.get("email_send_failed"):
        outcome_status = "failed"
    elif security_flagged:
        # Leave UNREAD so the threat stays visible; forced-notify is not delivered anywhere.
        outcome_status = "security_hold"
    else:
        outcome_status = "notify" if result.get("classification_decision") == "notify" else "completed"

    record_digest_item(rules_config, outcome_status, email_input, run_id)
    run_email_input = _run_email_input(email_input, result)
    upsert_run(
        run_id,
        outcome_status,
        email_input=run_email_input,
        classification=result.get("classification_decision"),
        pending_action=result["__interrupt__"][0].value if result.get("__interrupt__") else None,
        agent_instance_id=current_agent_instance_id(),
    )
    if outcome_status == "pending_approval":
        # Run is already durably persisted above; a notification failure must not
        # affect the approval that was just created.
        notify_pending_approval(run_id, email_input, result)
    if outcome_status in {"completed", "notify"}:
        mark_as_read(msg_id, resource=resource)
    return (msg_id, outcome_status, run_id)


async def poll_history(
    graph,
    start_history_id: str,
    resource=None,
    rules_config: RulesConfig | None = None,
) -> list[tuple]:
    """Process Gmail messages referenced by push-notification history events."""
    resource = resource or gmail_resource()
    rules_config = rules_config or load_rules()
    outcomes: list[tuple] = []
    for ref in fetch_history_message_refs(start_history_id, resource=resource):
        outcome = await _process_message_with_retry(graph, ref["id"], resource, rules_config)
        if outcome[1] != "skipped":
            outcomes.append(outcome)
    maybe_emit_daily_digest(rules_config)
    return outcomes


async def poll_once(
    graph,
    resource=None,
    max_results: int | None = None,
    rules_config: RulesConfig | None = None,
) -> list[tuple]:
    """Process one batch of unread emails through the graph.

    A run that completes (ignore/notify/sent) is marked read. A run that pauses for
    approval is left UNREAD — its pending action surfaces via the API / Agent Inbox,
    and the email is reprocessed-free until resolved. An email the security service
    flagged (injection or unavailable classifier) is also left UNREAD ("security_hold"):
    forced-notify has no delivery surface yet, so don't archive a threat silently —
    keep it visible in the inbox until the human handles it. Returns (msg_id, status, run_id).
    """
    resource = resource or gmail_resource()
    max_results = max_results or settings.max_emails_per_run
    rules_config = rules_config or load_rules()

    outcomes: list[tuple] = []
    if rules_config.snooze.enabled:
        for msg_id, label_name in resurface_due_snoozed(resource, rules_config):
            outcomes.append((msg_id, "snoozed_resurfaced", label_name))

    # Incremental sync: with a stored baseline, ask Gmail only for what changed
    # (history.list) instead of relisting the unread inbox every cycle. A stale
    # baseline (Gmail purges history after ~1 week) falls back to the full scan,
    # which reseeds below. The new baseline is captured BEFORE the scan so mail
    # arriving mid-cycle lands in the next window as overlap, never as a gap —
    # process_message dedups overlap via the run registry.
    refs: list[dict] | None = None
    truncated = False
    baseline = get_last_history_id()
    if baseline:
        try:
            history_refs = fetch_history_message_refs(baseline, resource=resource)
            truncated = len(history_refs) > max_results
            refs = history_refs[:max_results]
        except Exception as exc:
            if not is_stale_history_error(exc):
                raise
            print(f"poller: history window stale; falling back to full unread scan: {exc}")
    try:
        next_baseline = current_history_id(resource=resource)
    except Exception:
        next_baseline = ""
    if refs is None:
        refs = fetch_unread(max_results, resource=resource)

    for ref in refs:
        outcomes.append(await _process_message_with_retry(graph, ref["id"], resource, rules_config))
    # A truncated history batch keeps the old baseline so the overflow is picked
    # up next cycle (already-processed overlap is deduped, never re-run).
    if next_baseline and not truncated:
        set_last_history_id(next_baseline)

    outcomes.extend(await poll_follow_ups(graph, resource, rules_config))
    for _msg_id, status, _run_id in outcomes:
        inc_counter("agora_poller_processed_total", status=status)
    await asyncio.to_thread(sweep_pending_approval_slas)
    maybe_emit_daily_digest(rules_config)
    return outcomes


def active_email_agent_instance_ids() -> list[str]:
    """Discover active email-agent instances from the gateway registry.

    The gateway and agent share Postgres in platform mode. Local/JSON mode has no
    registry, so it preserves the original single default-instance behavior.
    """
    default = normalize_agent_instance_id(settings.default_agent_instance_id)
    if not settings.database_url:
        return [default]
    try:
        import psycopg

        with psycopg.connect(settings.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM agent_instance "
                    "WHERE LOWER(status) = 'active' AND agent_type = 'email-agent' "
                    "ORDER BY id"
                )
                rows = cur.fetchall()
    except Exception as exc:
        # The poller can start before the gateway creates/seeds its registry table.
        # Retry discovery next cycle while preserving the legacy default mailbox.
        print(f"poller: instance discovery failed; using {default}: {exc}")
        return [default]

    instances = [normalize_agent_instance_id(row[0]) for row in rows if row and row[0]]
    return list(dict.fromkeys(instances)) or [default]


async def poll_active_instances_once(
    graph,
    instance_ids: list[str] | None = None,
) -> dict[str, list[tuple]]:
    """Poll every active, connected instance without cross-instance failure spread."""
    if time.time() < _gmail_rate_limited_until:
        remaining = int(_gmail_rate_limited_until - time.time())
        print(f"poller: Gmail rate-limit pause active ({remaining}s left); skipping cycle")
        return {}
    instances = instance_ids or active_email_agent_instance_ids()
    results: dict[str, list[tuple]] = {}
    for index, raw_instance_id in enumerate(instances):
        if index:
            # Stagger instances inside a cycle so N mailboxes don't produce one
            # synchronized burst of Gmail calls.
            await asyncio.sleep(random.uniform(0.5, 3.0))
        instance_id = normalize_agent_instance_id(raw_instance_id)
        with agent_instance_context(instance_id):
            try:
                if get_status().get("paused"):
                    print(f"poller: {instance_id} is paused")
                    results[instance_id] = []
                    continue
                if not has_stored_token(instance_id):
                    print(f"poller: {instance_id} has no Gmail token; skipping")
                    results[instance_id] = []
                    continue
                try:
                    reload_config()
                    resource = gmail_resource()
                    outcomes = await poll_once(graph, resource=resource)
                except Exception as exc:
                    print(f"poller: {instance_id} poll failed: {exc}")
                    record_failure(str(exc))
                    results[instance_id] = []
                    if _note_gmail_rate_limit(exc):
                        break
                    continue

                results[instance_id] = outcomes
                if outcomes:
                    print(f"poller: {instance_id} processed {len(outcomes)} email(s): {outcomes}")
                record_success("polling")
            finally:
                await _run_instance_maintenance(instance_id)
    return results




async def _run_instance_maintenance(instance_id: str) -> None:
    try:
        retention_result = await asyncio.to_thread(run_retention)
        deleted_runs = int((retention_result.get("deleted") or {}).get("runs") or 0)
        if deleted_runs:
            print(f"poller: {instance_id} purged {deleted_runs} old run(s)")
    except Exception as exc:
        print(f"poller: {instance_id} retention sweep failed: {exc}")
    try:
        health_snapshot = await aggregate_health()
        events = await asyncio.to_thread(evaluate_alerts, health_snapshot)
        if events:
            print(f"poller: {instance_id} emitted {len(events)} alert event(s): {events}")
    except Exception as exc:
        print(f"poller: {instance_id} alert evaluation failed: {exc}")


async def sweep_active_instances_once(
    instance_ids: list[str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    instances = instance_ids or active_email_agent_instance_ids()
    results: dict[str, list[tuple[str, str]]] = {}
    for raw_instance_id in instances:
        instance_id = normalize_agent_instance_id(raw_instance_id)
        with agent_instance_context(instance_id):
            try:
                if get_status().get("paused") or not has_stored_token(instance_id):
                    results[instance_id] = []
                    continue
                try:
                    reload_config()
                    results[instance_id] = await asyncio.to_thread(sweep_pending_approval_slas)
                except Exception as exc:
                    print(f"poller: {instance_id} SLA sweep failed: {exc}")
                    record_failure(str(exc))
                    results[instance_id] = []
            finally:
                await _run_instance_maintenance(instance_id)
    return results


async def run_forever() -> None:
    """Poll the inbox every poll_interval_minutes against the durable graph."""
    # With push webhooks on, polling is only a safety net — run it slowly.
    interval_minutes = (
        settings.webhook_fallback_poll_minutes
        if settings.gmail_webhook_enabled
        else settings.poll_interval_minutes
    )
    interval = interval_minutes * 60
    upgrade_to_head()
    setup_run_registry()
    setup_gmail_sync()
    setup_sync_status()
    setup_trace_store()
    setup_dlq()
    async with open_graph_storage() as storage:
        graph = overall_workflow.compile(
            checkpointer=storage.checkpointer, store=storage.store
        )
        if settings.polling_fallback_enabled:
            mode = "watch+poll" if settings.gmail_webhook_enabled else "poll"
        elif settings.gmail_webhook_enabled:
            mode = "watch-only"
        else:
            mode = "idle"
        print(
            f"poller: {mode} every {interval_minutes} min "
            f"({storage.backend})"
        )
        while True:
            if settings.gmail_webhook_enabled:
                # Expiration-driven: ensure_watches skips instances whose recorded
                # watch expiration is still beyond the renewal margin, so checking
                # every loop is cheap and a watch never silently lapses.
                watches = await asyncio.to_thread(ensure_watches)
                registered = sum(1 for value in watches.values() if value)
                if registered:
                    print(f"poller: gmail watch registered for {registered} instance(s)")
            if settings.polling_fallback_enabled:
                await poll_active_instances_once(graph)
            else:
                escalations = await sweep_active_instances_once()
                for instance_id, items in escalations.items():
                    if items:
                        print(f"poller: {instance_id} escalated {len(items)} overdue approval(s): {items}")
            # Jitter so a fleet of pollers (or many instances) never hits Gmail in
            # lockstep — synced bursts look robotic to Google's abuse limiter.
            await asyncio.sleep(interval * random.uniform(0.85, 1.15))


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
