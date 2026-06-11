from __future__ import annotations

import asyncio
import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from src.config import settings
from src.gmail_client import (
    fetch_unread,
    get_message,
    gmail_resource,
    gmail_to_email_input,
    mark_as_read,
)
from src.graph import overall_workflow


async def poll_once(graph, resource=None, max_results: int | None = None) -> list[tuple]:
    """Process one batch of unread emails through the graph.

    A run that completes (ignore/notify/sent) is marked read. A run that pauses for
    approval is left UNREAD — its pending action surfaces via the API / Agent Inbox,
    and the email is reprocessed-free until resolved. Returns (msg_id, status, run_id).
    """
    resource = resource or gmail_resource()
    max_results = max_results or settings.max_emails_per_run

    outcomes: list[tuple] = []
    for ref in fetch_unread(max_results, resource=resource):
        msg_id = ref["id"]
        email_input = gmail_to_email_input(get_message(msg_id, resource=resource))
        run_id = str(uuid.uuid4())
        cfg = {"configurable": {"thread_id": run_id}}

        result = await graph.ainvoke({"email_input": email_input}, cfg)

        if result.get("__interrupt__"):
            outcomes.append((msg_id, "pending_approval", run_id))
        else:
            mark_as_read(msg_id, resource=resource)
            outcomes.append((msg_id, "completed", run_id))
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
