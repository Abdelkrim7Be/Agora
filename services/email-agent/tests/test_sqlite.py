from __future__ import annotations

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Command

from src.graph import overall_workflow
from tests.conftest import RESPOND_EMAIL, ai_tool_call


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
