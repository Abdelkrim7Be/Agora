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
from src.config import load_config, settings, validate_gmail_webhook_config, validate_live_send_config, validate_model_redaction
from src.runtime_settings import load_runtime_settings
from src.memory import ORIGIN_LEARNED, namespace, wrap_preferences
from src.style_learning import analyze_style, build_style_text
from src.security_client import classify_content, sanitize_email
from src.gmail_client import extract_pdf_text
from src.mail import get_provider
from src.categories import classify_category, load_categories
from src.junk_config import load_junk
from src.junk_gate import is_junk
from src.graph import overall_workflow, reload_config
from src.migrate import upgrade_to_head
from src.postgres import validate_runtime_role
from src.run_lock import try_claim_message
from src.notifications import notify_overdue_approval, notify_pending_approval
from src.dlq import record_dead_letter, setup_dlq
from src.metrics import inc_counter
from src.health import aggregate_health
from src.alerts import evaluate_alerts
from src.retention import run_retention
from src.trace import setup_trace_store
from src.gmail_sync import get_last_history_id, set_last_history_id, setup_gmail_sync
from src.sync_status import get_status, record_failure, record_success, setup_sync_status
from src.campaigns import due_campaign_ids, load_campaign_runs, save_campaign_runs, send_campaign_run
from src.run_registry import (
    ACTIVE_RUN_STATUSES,
    find_run_by_email,
    list_runs,
    setup_run_registry,
    upsert_run,
)
from src.storage import open_graph_storage
from src.token_store import has_stored_token, validate_token_security
from src.job_queue import enqueue_job
from src.tenant import (
    agent_instance_context,
    current_agent_instance_id,
    normalize_agent_instance_id,
    user_context,
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


async def process_message_with_retry(
    graph,
    msg_id: str,
    provider,
    rules_config: RulesConfig,
    message: dict | None = None,
) -> tuple:
    max_retries = max(0, settings.poll_max_retries)
    attempt = 0
    while True:
        try:
            return await process_message(graph, msg_id, provider, rules_config, message=message)
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
                    message = provider.get_message(msg_id)
                    thread = provider.fetch_thread(message["threadId"])
                    payload = provider.to_email_input(message, thread_messages=thread)
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


def sweep_due_campaigns(now: datetime | None = None) -> list[tuple[str, str, str]]:
    """Send scheduled campaigns that were already approved by the owner."""
    instance_id = current_agent_instance_id()
    records = load_campaign_runs(agent_instance_id=instance_id)
    outcomes: list[tuple[str, str, str]] = []
    changed = False
    for campaign_id in due_campaign_ids(now=now, agent_instance_id=instance_id):
        record = records.get(campaign_id)
        if not record or record.get("agent_instance_id") != instance_id:
            continue
        try:
            send_campaign_run(campaign_id, record)
            status = record.get("status") or "sent"
        except Exception as exc:
            record["status"] = "failed"
            record["result"] = {"sent": [], "denied": [], "failed": [{"email": None, "error": str(exc)}]}
            status = "failed"
        record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records[campaign_id] = record
        changed = True
        outcomes.append((campaign_id, status, "campaign"))
    if changed:
        save_campaign_runs(records, agent_instance_id=instance_id)
    return outcomes


def ensure_watch(provider=None) -> dict | None:
    """Register/renew the Gmail push watch and seed the sync baseline.

    Seeding the baseline at watch time is what makes the first push processable:
    the next notification queries history from this id forward (see api.gmail_webhook).
    No-op unless Gmail webhooks are enabled.
    """
    if not settings.gmail_webhook_enabled:
        return None
    provider = provider or get_provider()
    result = provider.watch_mailbox()
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


def resurface_due_snoozed(provider, rules_config: RulesConfig) -> list[tuple[str, str]]:
    """Move due snoozed messages back to INBOX/UNREAD."""
    surfaced: list[tuple[str, str]] = []
    for label in due_snooze_labels(provider.list_labels(), rules_config):
        label_id = label["id"]
        label_name = label.get("name", label_id)
        refs = provider.list_messages_by_label(
            label_id,
            rules_config.snooze.max_resurface_per_run,
        )
        for ref in refs:
            msg_id = ref["id"]
            provider.modify_labels(
                msg_id,
                add_label_ids=["INBOX", "UNREAD"],
                remove_label_ids=[label_id],
            )
            surfaced.append((msg_id, label_name))
    return surfaced


async def poll_follow_ups(graph, provider, rules_config: RulesConfig) -> list[tuple]:
    """Find old awaiting-reply threads and propose a nudge through HITL."""
    if not rules_config.follow_ups.enabled:
        return []

    outcomes: list[tuple] = []
    refs = provider.search_messages(
        follow_up_query(rules_config),
        rules_config.follow_ups.max_results,
    )
    for ref in refs:
        msg_id = ref["id"]
        message = provider.get_message(msg_id)
        thread = provider.fetch_thread(message["threadId"])
        email_input = provider.to_email_input(message, thread_messages=thread)
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


async def retry_security_holds(
    graph,
    provider,
    rules_config: RulesConfig,
    exclude_message_ids: set[str] | None = None,
) -> list[tuple]:
    """Re-attempt every security_hold run directly by message id.

    Gmail's incremental history.list diff only surfaces messages that changed
    since the last saved baseline — a message that was already unread when it
    got parked at security_hold in an earlier cycle never reappears in that
    diff on its own, so without this it would sit held forever even though
    _process_message_locked is now willing to retry it. Bypasses discovery
    entirely and goes straight to the known message ids from the registry.
    """
    held = list_runs(
        status="security_hold",
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
        limit=500,
    )
    exclude_message_ids = exclude_message_ids or set()
    outcomes: list[tuple] = []
    now = datetime.now(timezone.utc)
    for msg_id, record in _newest_hold_per_message(held).items():
        if msg_id in exclude_message_ids:
            continue
        if not _security_hold_retry_due(record, now):
            continue
        outcomes.append(await process_message_with_retry(graph, msg_id, provider, rules_config))
    return outcomes


def _newest_hold_per_message(held: list[dict]) -> dict[str, dict]:
    """One retry candidate per held message, not per held run row.

    A message that keeps tripping the classifier accumulates a run row per
    attempt, so iterating the rows re-processes the same few messages hundreds
    of times a cycle and starves the rest of the inbox. Retry the message.
    """
    newest: dict[str, dict] = {}
    for record in held:
        msg_id = record.get("email_id")
        if not msg_id:
            continue
        current = newest.get(msg_id)
        if current is None or _hold_sort_key(record) > _hold_sort_key(current):
            newest[msg_id] = record
    return newest


def _hold_sort_key(record: dict) -> str:
    return str(record.get("updated_at") or record.get("created_at") or "")


# A message the classifier will always hold (an injection attempt, say) would
# otherwise be re-attempted on every single cycle, burning an LLM call each time
# and — because holds are retried before new mail — starving the rest of the
# inbox. Back off between attempts instead of hammering it.
SECURITY_HOLD_RETRY_BACKOFF_MIN = 15


def _security_hold_retry_due(record: dict, now: datetime | None = None) -> bool:
    """True when a held run has waited long enough to be worth re-attempting."""
    now = now or datetime.now(timezone.utc)
    last = record.get("updated_at") or record.get("created_at")
    if not last:
        return True
    try:
        seen = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    waited = (now - seen.astimezone(timezone.utc)).total_seconds()
    return waited >= SECURITY_HOLD_RETRY_BACKOFF_MIN * 60


async def process_message(
    graph,
    msg_id: str,
    provider,
    rules_config: RulesConfig,
    message: dict | None = None,
) -> tuple:
    # A webhook push and the polling fallback can both reach this message at
    # nearly the same instant; without exclusion both would pass the
    # find_run_by_email check below and create duplicate runs (a duplicate send,
    # for an auto-approved workflow). A non-owner skips outright — the owner's
    # run becomes visible on the next status check.
    with try_claim_message(current_agent_instance_id(), msg_id) as claimed:
        if not claimed:
            return (msg_id, "skipped", "")
        return await _process_message_locked(graph, msg_id, provider, rules_config, message)


async def _process_message_locked(
    graph,
    msg_id: str,
    provider,
    rules_config: RulesConfig,
    message: dict | None = None,
) -> tuple:
    # An email left UNREAD because it already has a run must not be reprocessed:
    # a pending run would spawn a duplicate every cycle; a resolved one (e.g. an
    # approved reply the API sent but couldn't mark read) just needs housekeeping.
    # Checked BEFORE fetching the message: with a short poll interval, re-fetching
    # every known unread email each cycle burns the Gmail per-user quota (429s on
    # sends share the same budget).
    #
    # security_hold is the one exception: the registry doesn't distinguish a
    # genuine detected threat from a transient classifier outage (both set
    # classifier_unavailable/injection_detected but only the verdict, not which,
    # survives into the stored run), so treating it as permanently settled meant
    # a single contended Ollama moment parked a message forever — retried here
    # every cycle instead; the message stays unread either way, so a real threat
    # is never any less visible than it already was.
    existing = find_run_by_email(
        msg_id,
        # Runs belong to the mailbox instance, not to the actor who triggered sync.
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    # A security_hold is retried below rather than skipped. Its existing run id
    # is carried into that retry so the attempt updates the run instead of
    # filing a new one: retrying every cycle otherwise left a fresh row per
    # cycle for the same message, and one held message became dozens of runs.
    retry_run_id = ""
    if existing:
        if existing["status"] == "pending_approval":
            return (msg_id, existing["status"], existing["run_id"])
        if existing["status"] != "security_hold":
            provider.mark_as_read(msg_id)
            return (msg_id, "skipped", existing["run_id"])
        retry_run_id = existing["run_id"]

    # A prefetched message (poll_once batch) skips the per-message round-trip.
    if message is None:
        message = provider.get_message(msg_id)
    labels = message.get("labelIds")
    if labels is not None and ("INBOX" not in labels or "UNREAD" not in labels):
        return (msg_id, "skipped", "")

    # Deterministic junk gate: bulk/no-reply mail never reaches the LLM or the
    # validation box. Runs before fetch_thread so gated mail costs no extra
    # Gmail call.
    #
    # Junk is judged on the message, not on who sent it. A category claim used to
    # skip this check entirely, which meant one directory contact carrying a
    # category turned every alert digest and newsletter from that address into a
    # drafted reply — the sender matched, so nothing ever looked at the bulk
    # headers. Detection now runs first and a category may only rescue automated
    # mail when it says so explicitly (accepts_automated), which is what a real
    # automated workflow like machine-issued invoices needs. Everything else
    # loses to the junk verdict, and the junk allowlist remains the way to
    # exempt a sender wholesale.
    gate_input = {
        **provider.to_email_input(message),
        "agent_instance_id": current_agent_instance_id(),
    }
    categories_config = load_categories(agent_instance_id=current_agent_instance_id())
    category_match = classify_category(
        gate_input, categories_config, agent_instance_id=current_agent_instance_id()
    )
    junk, junk_reason = is_junk(
        gate_input, load_junk(agent_instance_id=current_agent_instance_id())
    )
    claimed_category = category_match.get("category")
    if junk and claimed_category:
        claiming = next(
            (c for c in categories_config.categories if c.name == claimed_category),
            None,
        )
        if claiming is not None and claiming.accepts_automated:
            print(
                f"poller: {msg_id} looks automated ({junk_reason}) but category "
                f"'{claimed_category}' accepts automated mail; keeping it"
            )
            junk = False
        else:
            print(
                f"poller: {msg_id} claimed by category '{claimed_category}' but "
                f"junk-gated anyway ({junk_reason})"
            )
    # An automation rule the owner wrote explicitly outranks the generic junk
    # heuristic. Both agree the mail is bulk; only the rule says where to file it.
    # Without this the shipped starter rules were dead on arrival: "archive
    # promotions" keys on CATEGORY_PROMOTIONS, which is exactly what the junk gate
    # drops first, so the label was never applied and the mail never left the inbox.
    junk_rule_plan = build_rule_plan(gate_input, rules_config) if junk else None
    # A rule that matched but asks for nothing is not a reason to pay for the full
    # pipeline on bulk mail — it would fall straight through to the triage LLM.
    if junk_rule_plan and not (junk_rule_plan["tool_calls"] or junk_rule_plan["terminal_status"]):
        junk_rule_plan = None
    if junk and junk_rule_plan:
        print(
            f"poller: {msg_id} is automated ({junk_reason}) but matches "
            f"{', '.join(junk_rule_plan['matched_rules'])}; applying the rule instead"
        )
        junk = False

    if junk:
        run_id = str(uuid.uuid4())
        upsert_run(
            run_id,
            "completed",
            email_input={
                **gate_input,
                "category": "junk_auto",
                "category_display_name": "Ignoré automatiquement",
                "junk_reason": junk_reason,
            },
            classification="ignore",
            pending_action=None,
            agent_instance_id=current_agent_instance_id(),
        )
        provider.mark_as_read(msg_id)
        print(f"poller: junk-gated {msg_id} ({junk_reason})")
        return (msg_id, "completed", run_id)

    thread = provider.fetch_thread(message["threadId"])
    email_input = {
        **provider.to_email_input(message, thread_messages=thread),
        "agent_instance_id": current_agent_instance_id(),
    }

    # Relationship context: how the owner previously wrote to this sender
    # (outside this thread) so replies match the established register. Runs
    # before sanitization so the block passes the same security boundary.
    correspondence = provider.fetch_sender_correspondence(
        email_input.get("author", ""),
        exclude_thread_id=message.get("threadId", ""),
    )
    if correspondence:
        email_input = {
            **email_input,
            "email_thread": email_input["email_thread"]
            + "\n\n"
            + provider.format_sender_correspondence(correspondence),
        }

    if settings.extract_attachments:
        pdf_blocks = []
        for att in email_input.get("attachments", []):
            if att["mime_type"] == "application/pdf" and att.get("attachment_id"):
                try:
                    raw = provider.download_attachment(msg_id, att["attachment_id"])
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
        # When the drafting model is hosted, it reads the redacted copy. The
        # real values are restored in tool_node immediately before an action
        # runs, so nothing leaves with a placeholder in it.
        redaction_map = verdict.get("redaction_map") or {}
        use_redacted = bool(settings.redact_for_model and redaction_map)
        email_input = {
            **email_input,
            "email_thread": verdict["redacted_text"] if use_redacted else verdict["cleaned_text"],
            "security": {
                "redaction_map": redaction_map if use_redacted else {},
                "injection_detected": verdict["injection_detected"],
                "classification": verdict["classification"],
                "classifier_unavailable": verdict["classifier_unavailable"],
                "source_trust": verdict.get("source_trust", "UNTRUSTED"),
                "fields": verdict.get("fields", {}),
            },
        }

    rule_plan = build_rule_plan(email_input, rules_config)
    if rule_plan:
        email_input = {**email_input, "automation": rule_plan}

    run_id = retry_run_id or str(uuid.uuid4())
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

    # Second look before a draft becomes approvable.
    #
    # /sanitize skips the quarantined classifier when no heuristic keyword fires,
    # so an injection written without the obvious phrases reaches the model
    # unclassified. Running the classifier on every message would put a slow
    # local model in front of the whole mailbox — which is what parked every
    # message at security_hold before. Running it only on the messages that
    # produced a reply narrows the cost to the output that can actually reach a
    # human and be approved, and it happens before the run is recorded, so there
    # is never a window where a poisoned draft is sitting there approvable.
    if (
        outcome_status == "pending_approval"
        and settings.security_enabled
        and settings.security_deep_check_drafts
        and not security_flagged
    ):
        verdict = await classify_content(email_input.get("email_thread", ""))
        if verdict.get("trust") == "HOSTILE":
            print(f"🛡️ {msg_id}: draft withdrawn, deep classification returned HOSTILE")
            security_flagged = True
            outcome_status = "security_hold"
            result = {**result, "__interrupt__": None}

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
        provider.mark_as_read(msg_id)
    return (msg_id, outcome_status, run_id)


async def poll_history(
    graph,
    start_history_id: str,
    provider=None,
    rules_config: RulesConfig | None = None,
) -> list[tuple]:
    """Process Gmail messages referenced by push-notification history events."""
    provider = provider or get_provider()
    rules_config = rules_config or load_rules()
    outcomes: list[tuple] = []
    for ref in provider.fetch_changes_since(start_history_id):
        outcome = await process_message_with_retry(graph, ref["id"], provider, rules_config)
        if outcome[1] != "skipped":
            outcomes.append(outcome)
    maybe_emit_daily_digest(rules_config)
    return outcomes


def _unique_refs(refs: list[dict]) -> list[dict]:
    """Drop repeated message ids, keeping first-seen order."""
    seen: set[str] = set()
    unique: list[dict] = []
    for ref in refs:
        msg_id = ref.get("id")
        if not msg_id or msg_id in seen:
            continue
        seen.add(msg_id)
        unique.append(ref)
    return unique


# Run statuses that make a message a no-op for the main loop: it is either
# finished or parked on a person. Only `security_hold` is missing, and that on
# purpose — held runs are retried, by `retry_security_holds`, not here.
_SETTLED_RUN_STATUSES = ("pending_approval", "completed", "notify", "failed")


def _messages_awaiting_approval() -> set[str]:
    """Gmail ids this instance has already settled — nothing left for the loop.

    history.list replays *changes*, not current state, so a message delivered
    and then processed keeps reappearing in every window until the baseline
    moves past it. With a truncated window the baseline never moves, so the same
    already-finished messages were re-listed forever while new mail waited
    outside the window. They are dropped before the cut, so the budget goes to
    messages that can still do something.
    """
    try:
        pending = []
        for status in _SETTLED_RUN_STATUSES:
            pending.extend(list_runs(
                status=status,
                user_id=None,
                agent_instance_id=current_agent_instance_id(),
                limit=500,
            ))
    except Exception as exc:
        # Never let a registry hiccup stop detection; worst case is the old
        # behavior of re-fetching mail that will be deduped downstream anyway.
        print(f"poller: could not list pending approvals, not filtering: {exc}")
        return set()
    return {record["email_id"] for record in pending if record.get("email_id")}


async def _discover_unread_refs(
    provider,
    max_results: int,
    prefetch: bool = True,
) -> tuple[list[dict], dict[str, dict], str, bool]:
    """Shared detection: incremental history diff (fallback to full unread scan)
    plus optional batch prefetch. Returns (refs, prefetched, next_baseline, truncated).

    Incremental sync: with a stored baseline, ask Gmail only for what changed
    (history.list) instead of relisting the unread inbox every cycle. A stale
    baseline (Gmail purges history after ~1 week) falls back to the full scan,
    which reseeds below. The new baseline is captured BEFORE the scan so mail
    arriving mid-cycle lands in the next window as overlap, never as a gap —
    downstream processing dedups overlap via the run registry.
    """
    refs: list[dict] | None = None
    truncated = False

    # Mail already parked on a human keeps its UNREAD flag on purpose, so it
    # reappears in every window forever. It has to be dropped BEFORE the window
    # is truncated, not after.
    #
    # It used to be filtered further down, once the batch had already been cut
    # to `max_results`. history.list returns changes oldest-first, so a mailbox
    # holding twenty pending approvals spent its entire window budget on them,
    # discarded them, processed nothing — and because a truncated window
    # deliberately does not advance the baseline, asked for the exact same
    # window again next cycle. Nothing ever drained it: a pending approval only
    # clears when a person acts, and until then every message that arrived
    # afterwards was invisible. The mailbox stopped taking new mail for good,
    # while the poller looked busy.
    awaiting_human = _messages_awaiting_approval()

    def _ready(items: list[dict]) -> list[dict]:
        # Deduplicated, minus anything already waiting on a person.
        unique = _unique_refs(items)
        if not awaiting_human:
            return unique
        return [ref for ref in unique if ref["id"] not in awaiting_human]

    baseline = get_last_history_id()
    if baseline:
        try:
            # history.list reports one record per change, so the same message
            # shows up several times in a window where it was e.g. delivered and
            # then labelled. Deduplicating before the cut also stops those
            # duplicates from eating the budget.
            history_refs = _ready(provider.fetch_changes_since(baseline))
            truncated = len(history_refs) > max_results
            refs = history_refs[:max_results]
        except Exception as exc:
            if not provider.is_stale_cursor_error(exc):
                raise
            print(f"poller: history window stale; falling back to full unread scan: {exc}")
    try:
        next_baseline = provider.current_sync_cursor()
    except Exception:
        next_baseline = ""
    if refs is None:
        refs = _ready(provider.fetch_unread(max_results))
    elif not refs:
        # An empty history window means "nothing changed since the baseline",
        # which is not the same as "nothing is waiting". Anything that became
        # unread while the baseline was being advanced — or was restored to the
        # inbox out of spam, or had its run cleared by hand — is invisible to the
        # diff forever after. This mailbox sat on unread mail for hours that way.
        # A full unread list is one call, and the run registry dedups whatever it
        # returns, so reconcile whenever the incremental path comes back empty.
        refs = _ready(provider.fetch_unread(max_results))

    # Batch the full-message fetch when a cycle has more than 3 messages: one HTTP
    # round-trip per 50 instead of one per message. Failure falls back to the
    # per-message serial fetch inside process_message. Skipped by the producer
    # path (enqueue_once) — the message body isn't used there, only the id.
    prefetched: dict[str, dict] = {}
    if prefetch and len(refs) > 3:
        try:
            prefetched = await asyncio.to_thread(
                provider.fetch_messages_batch, [ref["id"] for ref in refs]
            )
        except Exception as exc:
            print(f"poller: batch message fetch failed, using serial: {exc}")
    return refs, prefetched, next_baseline, truncated


async def poll_once(
    graph,
    provider=None,
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
    provider = provider or get_provider()
    max_results = max_results or load_runtime_settings().sync_limit
    rules_config = rules_config or load_rules()

    outcomes: list[tuple] = []
    if rules_config.snooze.enabled:
        for msg_id, label_name in resurface_due_snoozed(provider, rules_config):
            outcomes.append((msg_id, "snoozed_resurfaced", label_name))

    refs, prefetched, next_baseline, truncated = await _discover_unread_refs(
        provider, max_results
    )

    for ref in refs:
        outcomes.append(await process_message_with_retry(
            graph, ref["id"], provider, rules_config, message=prefetched.get(ref["id"])
        ))
    # A truncated history batch keeps the old baseline so the overflow is picked
    # up next cycle (already-processed overlap is deduped, never re-run).
    if next_baseline and not truncated:
        set_last_history_id(next_baseline)

    outcomes.extend(await retry_security_holds(
        graph, provider, rules_config, {msg_id for msg_id, _status, _run_id in outcomes}
    ))
    outcomes.extend(await poll_follow_ups(graph, provider, rules_config))
    outcomes.extend(await asyncio.to_thread(sweep_due_campaigns))
    for _msg_id, status, _run_id in outcomes:
        inc_counter("agora_poller_processed_total", status=status)
    await asyncio.to_thread(sweep_pending_approval_slas)
    maybe_emit_daily_digest(rules_config)
    return outcomes


async def enqueue_once(
    graph,
    provider=None,
    max_results: int | None = None,
    rules_config: RulesConfig | None = None,
) -> list[tuple]:
    """Producer path (AGENT_JOB_QUEUE_ENABLED=true): detect unread mail the same
    way poll_once does, but enqueue a job per message instead of invoking the
    graph inline. One or more `src.worker` processes claim and process jobs
    later via Postgres SKIP LOCKED. Returns (msg_id, "enqueued"|"queue_duplicate",
    job_id) tuples, the same outcome shape poll_once returns.

    Snoozed resurfacing and follow-ups stay synchronous here, outside the queue:
    both just flip a message back to UNREAD / propose a nudge run directly, and
    the next detection pass enqueues any resulting unread mail normally.
    """
    provider = provider or get_provider()
    max_results = max_results or load_runtime_settings().sync_limit
    rules_config = rules_config or load_rules()
    instance_id = current_agent_instance_id()

    if rules_config.snooze.enabled:
        resurface_due_snoozed(provider, rules_config)

    refs, _prefetched, next_baseline, truncated = await _discover_unread_refs(
        provider, max_results, prefetch=False
    )
    outcomes: list[tuple] = []
    for ref in refs:
        job = enqueue_job(instance_id, ref["id"])
        if job is None:
            outcomes.append((ref["id"], "queue_duplicate", ""))
        else:
            outcomes.append((ref["id"], "enqueued", str(job["id"])))
    if next_baseline and not truncated:
        set_last_history_id(next_baseline)

    outcomes.extend(await poll_follow_ups(graph, provider, rules_config))
    outcomes.extend(await asyncio.to_thread(sweep_due_campaigns))
    for _msg_id, status, _run_id in outcomes:
        inc_counter("agora_poller_processed_total", status=status)
    enqueued_count = sum(1 for _msg_id, status, _run_id in outcomes if status == "enqueued")
    if enqueued_count:
        print(f"poller: {instance_id} enqueued {enqueued_count} job(s)")
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
        from src.postgres import tenant_connection

        with tenant_connection() as conn:
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


def _instance_owner(instance_id: str) -> str | None:
    """Look up the platform user an instance belongs to (its creator).

    Every run the poller creates needs the tenant context bound to whoever
    actually owns the mailbox, not a single global default — otherwise every
    instance's runs get stamped with AGENT_DEFAULT_USER_ID regardless of who
    created it, and the real owner's Validation/Dashboard views never see
    them (RLS/tenant-scoped queries filter by the requesting user's id).
    """
    if not settings.database_url:
        return None
    try:
        from src.postgres import tenant_connection

        with tenant_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT created_by FROM agent_instance WHERE id = %s", (instance_id,)
                )
                row = cur.fetchone()
    except Exception as exc:
        print(f"poller: owner lookup failed for {instance_id}: {exc}")
        return None
    return row[0] if row and row[0] else None


# Above this many active mailboxes, random jitter no longer spreads the load
# evenly; switch to a strict round-robin rotation + even spacing instead.
_ROUND_ROBIN_THRESHOLD = 5
_ROUND_ROBIN_INTERVAL_SECONDS = 6.0
_rotation_offset = 0


def _rotate_instances(instances: list[str]) -> list[str]:
    """Rotate the polling start point each cycle so no mailbox is always first.

    Below the threshold the order is unchanged (jitter still spreads a small
    fleet fine); above it, a deterministic rotation shares first-place fairly.
    """
    global _rotation_offset
    if len(instances) <= _ROUND_ROBIN_THRESHOLD:
        return list(instances)
    offset = _rotation_offset % len(instances)
    _rotation_offset = (_rotation_offset + 1) % len(instances)
    return instances[offset:] + instances[:offset]


def _instance_stagger_seconds(count: int) -> float:
    """Even spacing for a large fleet; random jitter for a small one."""
    if count <= _ROUND_ROBIN_THRESHOLD:
        return random.uniform(0.5, 3.0)
    return _ROUND_ROBIN_INTERVAL_SECONDS


# One auto-seed attempt per instance per process: builds the owner's writing
# style profile from their sent mail the first time a connected mailbox is
# polled, so drafts carry the owner's voice without any manual setup step.
_style_seed_attempted: set[str] = set()


async def maybe_seed_style_profile(instance_id: str, store, provider) -> None:
    if store is None or instance_id in _style_seed_attempted:
        return
    _style_seed_attempted.add(instance_id)
    try:
        cfg = load_config()
        if not cfg.style_learning.enabled:
            return
        existing = await store.aget(namespace("writing_style"), "user_preferences")
        if existing:
            return
        samples = await asyncio.to_thread(provider.fetch_sent, load_runtime_settings().style_sent_sample)
        if not samples:
            print(f"poller: {instance_id} has no sent mail yet; style auto-seed deferred")
            _style_seed_attempted.discard(instance_id)
            return
        from src import graph as graph_module

        profile = await asyncio.to_thread(analyze_style, samples, graph_module.llm)
        text = build_style_text(profile)
        await store.aput(
            namespace("writing_style"), "user_preferences", wrap_preferences(text, ORIGIN_LEARNED)
        )
        print(f"poller: {instance_id} seeded writing style from {len(samples)} sent email(s)")
    except Exception as exc:
        # Best-effort: drafts fall back to the configured default style.
        print(f"poller: {instance_id} style auto-seed skipped: {exc}")


async def poll_active_instances_once(
    graph,
    instance_ids: list[str] | None = None,
    store=None,
) -> dict[str, list[tuple]]:
    """Poll every active, connected instance without cross-instance failure spread."""
    if time.time() < _gmail_rate_limited_until:
        remaining = int(_gmail_rate_limited_until - time.time())
        print(f"poller: Gmail rate-limit pause active ({remaining}s left); skipping cycle")
        return {}
    instances = instance_ids or active_email_agent_instance_ids()
    ordered = _rotate_instances(instances)
    results: dict[str, list[tuple]] = {}
    for index, raw_instance_id in enumerate(ordered):
        if index:
            await asyncio.sleep(_instance_stagger_seconds(len(ordered)))
        instance_id = normalize_agent_instance_id(raw_instance_id)
        with user_context(_instance_owner(instance_id)):
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
                    provider = get_provider()
                    await maybe_seed_style_profile(instance_id, store, provider)
                    if settings.job_queue_enabled:
                        outcomes = await enqueue_once(graph, provider=provider)
                    else:
                        outcomes = await poll_once(graph, provider=provider)
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
        with user_context(_instance_owner(instance_id)):
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
    validate_model_redaction()
    validate_gmail_webhook_config()
    validate_live_send_config()
    validate_token_security()
    # With push webhooks on, polling is only a safety net — run it slowly.
    interval_minutes = (
        settings.webhook_fallback_poll_minutes
        if settings.gmail_webhook_enabled
        else settings.poll_interval_minutes
    )
    interval = interval_minutes * 60
    upgrade_to_head()
    validate_runtime_role()
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
                await poll_active_instances_once(graph, store=storage.store)
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
