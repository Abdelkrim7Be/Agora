from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Command
from pydantic import BaseModel

from src.config import settings
from src.graph import overall_workflow


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Separate files avoid SQLite locking between saver and store; shared with the
    # poller (src/config Settings) so either process can resume the other's runs.
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoints_db) as checkpointer:
        async with AsyncSqliteStore.from_conn_string(settings.store_db) as mem_store:
            # AsyncSqliteStore.aget/aput do NOT auto-run setup — call explicitly.
            await checkpointer.setup()
            await mem_store.setup()
            # The graph's nodes are sync, so LangGraph runs them in a threadpool where
            # sync store.get/put works. A future ASYNC node must use aget/aput instead —
            # a sync store call on the event loop raises InvalidStateError.
            app.state.graph = overall_workflow.compile(
                checkpointer=checkpointer, store=mem_store
            )
            yield


app = FastAPI(title="email-agent", version="0.1.0", lifespan=lifespan)


class EmailInput(BaseModel):
    author: str
    to: str
    subject: str
    email_thread: str


class ApprovalInput(BaseModel):
    # Optional override of the action args (e.g. an edited email draft).
    args: dict | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str  # "pending_approval" | "completed"
    classification: str | None = None
    pending_action: dict | None = None


def _thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _format(result: dict, run_id: str) -> RunResponse:
    """Turn a graph result into a response — paused on approval, or completed."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        return RunResponse(
            run_id=run_id,
            status="pending_approval",
            pending_action=interrupts[0].value,
        )
    return RunResponse(
        run_id=run_id,
        status="completed",
        classification=result.get("classification_decision", "unknown"),
    )


async def _require_run(graph, run_id: str) -> dict:
    config = _thread_config(run_id)
    state = await graph.aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return config


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def run(request: Request, email: EmailInput) -> RunResponse:
    graph = request.app.state.graph
    run_id = str(uuid.uuid4())
    result = await graph.ainvoke(
        {"email_input": email.model_dump()}, _thread_config(run_id)
    )
    return _format(result, run_id)


@app.post("/run/{run_id}/approve", response_model=RunResponse)
async def approve(request: Request, run_id: str, approval: ApprovalInput) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    result = await graph.ainvoke(
        Command(resume={"type": "approve", "args": approval.args}), config
    )
    return _format(result, run_id)


@app.post("/run/{run_id}/reject", response_model=RunResponse)
async def reject(request: Request, run_id: str) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    result = await graph.ainvoke(
        Command(resume={"type": "reject"}), config
    )
    return _format(result, run_id)
