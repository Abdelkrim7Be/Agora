from __future__ import annotations

import asyncio
import os
import random
import socket
import time

from src.automation import load_rules
from src.config import settings, validate_model_redaction
from src.dlq import setup_dlq
from src.mail import get_provider
from src.gmail_sync import setup_gmail_sync
from src.graph import overall_workflow
from src.instance_setup import (
    SetupContext,
    claim_next_step,
    requeue_stale_steps,
    run_step,
    setup_instance_setup,
)
from src.job_queue import (
    claim_job,
    mark_job_done,
    require_job_queue_available,
    requeue_stale_jobs,
)
from src.migrate import upgrade_to_head
from src.poller import process_message_with_retry
from src.postgres import validate_runtime_role
from src.run_registry import setup_run_registry
from src.storage import open_graph_storage
from src.sync_status import setup_sync_status
from src.tenant import agent_instance_context, user_context
from src.token_store import validate_token_security
from src.trace import setup_trace_store

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
_STALE_SWEEP_INTERVAL_SECONDS = 60.0


async def run_setup_step_once(store) -> bool:
    """Claim and run one onboarding-setup step. Returns True iff a step was claimed."""
    step = claim_next_step(WORKER_ID)
    if step is None:
        return False
    with user_context(step["user_id"]):
        with agent_instance_context(step["agent_instance_id"]):
            context = SetupContext(
                user_id=step["user_id"], agent_instance_id=step["agent_instance_id"], store=store
            )
            await run_step(step, context)
    return True


async def run_worker_once(graph) -> bool:
    """Claim and process one queued job. Returns True iff a job was claimed."""
    job = claim_job(WORKER_ID)
    if job is None:
        return False
    with agent_instance_context(job["instance_id"]):
        try:
            rules_config = load_rules()
            await process_message_with_retry(
                graph, job["message_id"], get_provider(), rules_config
            )
        except Exception as exc:
            # Left 'processing' on purpose: requeue_stale_jobs recovers it after
            # the stale timeout instead of losing it to a worker-local exception
            # (e.g. a momentarily revoked Gmail token).
            print(f"worker: job {job['id']} ({job['instance_id']}/{job['message_id']}) failed: {exc}")
            return True
    mark_job_done(job["id"])
    return True


async def run_forever() -> None:
    """Claim loop for the S-scale-2 job queue (AGENT_JOB_QUEUE_ENABLED=true).

    Runs alongside one or more other `src.worker` processes; `claim_job`'s
    SKIP LOCKED query is what makes concurrent workers safe.
    """
    require_job_queue_available()
    validate_model_redaction()
    validate_token_security()
    upgrade_to_head()
    validate_runtime_role()
    setup_run_registry()
    setup_gmail_sync()
    setup_sync_status()
    setup_trace_store()
    setup_dlq()
    setup_instance_setup()
    last_sweep = 0.0
    async with open_graph_storage() as storage:
        graph = overall_workflow.compile(
            checkpointer=storage.checkpointer, store=storage.store
        )
        print(f"worker: {WORKER_ID} ready ({storage.backend})")
        while True:
            now = time.time()
            if now - last_sweep >= _STALE_SWEEP_INTERVAL_SECONDS:
                requeued = await asyncio.to_thread(requeue_stale_jobs)
                if requeued:
                    print(f"worker: requeued {requeued} stale job(s)")
                requeued_steps = await asyncio.to_thread(requeue_stale_steps)
                if requeued_steps:
                    print(f"worker: requeued {requeued_steps} stale setup step(s)")
                last_sweep = now
            # Onboarding beats backlog processing: a new instance should reach
            # 'ready' before the poll-job queue works through its own backlog.
            if await run_setup_step_once(storage.store):
                continue
            claimed = await run_worker_once(graph)
            if not claimed:
                await asyncio.sleep(
                    settings.job_queue_poll_seconds * random.uniform(0.85, 1.15)
                )


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
