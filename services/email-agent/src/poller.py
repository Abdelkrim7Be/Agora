from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
import uuid


TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 30.0


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
    download_attachment,
    extract_pdf_text,
    fetch_history_message_refs,
    fetch_thread,
    fetch_unread,
    get_message,
    gmail_resource,
    gmail_to_email_input,
    list_labels,
    list_messages_by_label,
    mark_as_read,
    modify_labels,
    search_messages,
    watch_mailbox,
)
from src.categories import load_categories
from src.graph import overall_workflow, reload_config
from src.migrate import upgrade_to_head
from src.notifications import notify_overdue_approval, notify_pending_approval
from src.dlq import record_dead_letter, setup_dlq
from src.metrics import inc_counter
from src.health import aggregate_health
from src.alerts import evaluate_alerts
from src.retention import run_retention
from src.trace import setup_trace_store
from src.gmail_sync import set_last_history_id, setup_gmail_sync
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
    message = get_message(msg_id, resource=resource)
    labels = message.get("labelIds")
    if labels is not None and ("INBOX" not in labels or "UNREAD" not in labels):
        return (msg_id, "skipped", "")

    # An email left UNREAD because it already has a run must not be reprocessed:
    # a pending/held run would spawn a duplicate every cycle; a resolved one (e.g.
    # an approved reply the API sent but couldn't mark read) just needs housekeeping.
    existing = find_run_by_email(
        message.get("id"),
        # Runs belong to the mailbox instance, not to the actor who triggered sync.
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    if existing:
        if existing["status"] in ACTIVE_RUN_STATUSES:
            return (msg_id, existing["status"], existing["run_id"])
        mark_as_read(msg_id, resource=resource)
        return (msg_id, "skipped", existing["run_id"])

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

    for ref in fetch_unread(max_results, resource=resource):
        outcomes.append(await _process_message_with_retry(graph, ref["id"], resource, rules_config))

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
    instances = instance_ids or active_email_agent_instance_ids()
    results: dict[str, list[tuple]] = {}
    for raw_instance_id in instances:
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
    interval = settings.poll_interval_minutes * 60
    renew = settings.gmail_watch_renew_hours * 3600
    upgrade_to_head()
    setup_run_registry()
    setup_gmail_sync()
    setup_sync_status()
    setup_trace_store()
    setup_dlq()
    last_watch = 0.0
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
            f"poller: {mode} every {settings.poll_interval_minutes} min "
            f"({storage.backend})"
        )
        while True:
            if settings.gmail_webhook_enabled and time.monotonic() - last_watch >= renew:
                try:
                    ensure_watch()
                    last_watch = time.monotonic()
                    print("poller: gmail watch registered")
                except Exception as exc:
                    print(f"poller: gmail watch failed: {exc}")
                    record_failure(str(exc))
            if settings.polling_fallback_enabled:
                await poll_active_instances_once(graph)
            else:
                escalations = await sweep_active_instances_once()
                for instance_id, items in escalations.items():
                    if items:
                        print(f"poller: {instance_id} escalated {len(items)} overdue approval(s): {items}")
            await asyncio.sleep(interval)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
