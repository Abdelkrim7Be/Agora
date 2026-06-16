from __future__ import annotations

import asyncio
import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from src.automation import (
    RulesConfig,
    build_rule_plan,
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
    mark_as_read,
)
from src.graph import overall_workflow


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
        outcomes.append((msg_id, outcome_status, run_id))

    maybe_emit_daily_digest(rules_config)
    return outcomes


async def run_forever() -> None:
    """Poll the inbox every poll_interval_minutes against the durable sqlite graph."""
    interval = settings.poll_interval_minutes * 60
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoints_db) as checkpointer:
        async with AsyncSqliteStore.from_conn_string(settings.store_db) as mem_store:
            await checkpointer.setup()
            await mem_store.setup()
            graph = overall_workflow.compile(checkpointer=checkpointer, store=mem_store)
            print(f"poller: watching inbox every {settings.poll_interval_minutes} min")
            while True:
                outcomes = await poll_once(graph)
                if outcomes:
                    print(f"poller: processed {len(outcomes)} email(s): {outcomes}")
                await asyncio.sleep(interval)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
