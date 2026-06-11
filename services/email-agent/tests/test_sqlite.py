from __future__ import annotations

import asyncio

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Command

from src.graph import overall_workflow
from src.memory import get_memory, namespace, update_memory
from tests.conftest import RESPOND_EMAIL, _FakeMemoryLLM, ai_tool_call


async def test_paused_run_survives_restart(tmp_path, fake_llms):
    """Pause at approval in one graph instance; resume from a fresh instance on the
    same db files. Proves the async-sqlite path (the production path) is durable."""
    checkpoints_db = str(tmp_path / "checkpoints.db")
    store_db = str(tmp_path / "store.db")

    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}, call_id="c1"),
        ],
    )

    run_id = "test-restart-001"
    cfg = {"configurable": {"thread_id": run_id}}

    # First "process" — run until the approval interrupt.
    async with AsyncSqliteSaver.from_conn_string(checkpoints_db) as cp1:
        async with AsyncSqliteStore.from_conn_string(store_db) as st1:
            await cp1.setup()
            await st1.setup()
            g1 = overall_workflow.compile(checkpointer=cp1, store=st1)
            result = await g1.ainvoke({"email_input": RESPOND_EMAIL}, cfg)
            assert result.get("__interrupt__"), "expected interrupt at write_email"

    # Second "process" — brand-new graph instances from the same db files.
    async with AsyncSqliteSaver.from_conn_string(checkpoints_db) as cp2:
        async with AsyncSqliteStore.from_conn_string(store_db) as st2:
            await cp2.setup()
            await st2.setup()
            g2 = overall_workflow.compile(checkpointer=cp2, store=st2)
            result = await g2.ainvoke(
                Command(resume={"type": "approve", "args": None}), cfg
            )
            assert result.get("__interrupt__") is None
            assert result.get("email_sent") is True


async def test_learned_memory_survives_restart(tmp_path):
    """A preference written through update_memory in one process is readable via
    get_memory after reconnecting to the same store file — the core S8 promise.
    Sync memory calls run in a worker thread, mirroring how LangGraph executes
    sync nodes (calling them on the event loop raises InvalidStateError)."""
    store_db = str(tmp_path / "store.db")
    ns = namespace("response_preferences")
    llm = _FakeMemoryLLM("learned: keep replies short")

    # First "process" — learn a preference.
    async with AsyncSqliteStore.from_conn_string(store_db) as st1:
        await st1.setup()
        await asyncio.to_thread(
            update_memory, st1, ns, [{"role": "user", "content": "be concise"}], llm
        )

    # Second "process" — fresh connection, same file: the learning persisted.
    async with AsyncSqliteStore.from_conn_string(store_db) as st2:
        await st2.setup()
        value = await asyncio.to_thread(get_memory, st2, ns, "unused default")
        assert value == "learned: keep replies short"
