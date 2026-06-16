from __future__ import annotations

import asyncio
import uuid


from src.automation import (
    RulesConfig,
    build_follow_up_plan,
    build_rule_plan,
    due_snooze_labels,
    follow_up_query,
    load_rules,
    maybe_emit_daily_digest,
    record_digest_item,
)
from src.config import settings
from src.security_client import sanitize_email
from src.gmail_client import (
    download_attachment,
    extract_pdf_text,
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
)
from src.graph import overall_workflow
from src.run_registry import setup_run_registry, upsert_run
from src.storage import open_graph_storage


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
        upsert_run(
            run_id,
            status,
            email_input=email_input,
            classification=result.get("classification_decision"),
            pending_action=result["__interrupt__"][0].value if result.get("__interrupt__") else None,
        )
        outcomes.append((msg_id, status, run_id))
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
        msg_id = ref["id"]
        message = get_message(msg_id, resource=resource)
        thread = fetch_thread(message["threadId"], resource=resource)
        email_input = gmail_to_email_input(message, thread_messages=thread)

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
        elif security_flagged:
            # Leave UNREAD so the threat stays visible; forced-notify is not delivered anywhere.
            outcome_status = "security_hold"
        else:
            mark_as_read(msg_id, resource=resource)
            outcome_status = "notify" if result.get("classification_decision") == "notify" else "completed"

        record_digest_item(rules_config, outcome_status, email_input, run_id)
        upsert_run(
            run_id,
            outcome_status,
            email_input=email_input,
            classification=result.get("classification_decision"),
            pending_action=result["__interrupt__"][0].value if result.get("__interrupt__") else None,
        )
        outcomes.append((msg_id, outcome_status, run_id))

    outcomes.extend(await poll_follow_ups(graph, resource, rules_config))
    maybe_emit_daily_digest(rules_config)
    return outcomes


async def run_forever() -> None:
    """Poll the inbox every poll_interval_minutes against the durable graph."""
    interval = settings.poll_interval_minutes * 60
    setup_run_registry()
    async with open_graph_storage() as storage:
        graph = overall_workflow.compile(
            checkpointer=storage.checkpointer, store=storage.store
        )
        print(
            "poller: watching inbox every "
            f"{settings.poll_interval_minutes} min ({storage.backend})"
        )
        while True:
            outcomes = await poll_once(graph)
            if outcomes:
                print(f"poller: processed {len(outcomes)} email(s): {outcomes}")
            await asyncio.sleep(interval)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
