from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Command
from pydantic import BaseModel

from src.config import settings
from src.config import load_config
from src.graph import overall_workflow
from src.memory import get_memory, namespace
from src.run_registry import list_runs, upsert_run


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
            app.state.store = mem_store
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


class RespondInput(BaseModel):
    feedback: str




class MemoryInput(BaseModel):
    triage_preferences: str
    response_preferences: str


class RunResponse(BaseModel):
    run_id: str
    status: str  # "pending_approval" | "completed"
    classification: str | None = None
    pending_action: list | None = None  # list of Agent Inbox request objects when paused


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


def _record_response(run: RunResponse, email_input: dict | None = None) -> None:
    upsert_run(
        run.run_id,
        run.status,
        email_input=email_input,
        classification=run.classification,
        pending_action=run.pending_action,
    )


async def _require_run(graph, run_id: str) -> dict:
    config = _thread_config(run_id)
    state = await graph.aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return config


def _message_summary(message) -> dict:
    if isinstance(message, dict):
        role = message.get("role", message.get("type", "message"))
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])
    else:
        role = getattr(message, "type", message.__class__.__name__)
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", []) or []
    return {
        "role": role,
        "content": content,
        "tool_calls": tool_calls,
    }


def _run_detail(values: dict, run_id: str) -> dict:
    email_input = values.get("email_input", {})
    return {
        "run_id": run_id,
        "status": "pending_approval" if values.get("__interrupt__") else "completed",
        "classification": values.get("classification_decision"),
        "email": {
            "author": email_input.get("author"),
            "to": email_input.get("to"),
            "subject": email_input.get("subject"),
            "email_id": email_input.get("email_id"),
            "gmail_thread_id": email_input.get("gmail_thread_id"),
        },
        "security": email_input.get("security"),
        "automation": email_input.get("automation"),
        "timeline": [_message_summary(m) for m in values.get("messages", [])],
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/memory")
async def get_preferences(request: Request) -> dict:
    cfg = load_config()
    store = request.app.state.store
    return {
        "triage_preferences": get_memory(
            store,
            namespace("triage_preferences"),
            cfg.agent.triage_instructions,
        ),
        "response_preferences": get_memory(
            store,
            namespace("response_preferences"),
            cfg.agent.response_preferences,
        ),
    }


@app.put("/memory")
async def update_preferences(request: Request, body: MemoryInput) -> dict:
    store = request.app.state.store
    store.put(namespace("triage_preferences"), "user_preferences", body.triage_preferences)
    store.put(namespace("response_preferences"), "user_preferences", body.response_preferences)
    return {
        "triage_preferences": body.triage_preferences,
        "response_preferences": body.response_preferences,
    }


@app.get("/runs")
async def runs(status: str | None = Query(default=None)) -> dict:
    return {"runs": list_runs(status=status)}


@app.get("/run/{run_id}", response_model=RunResponse)
async def get_run(request: Request, run_id: str) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    state = await graph.aget_state(config)
    return _format(state.values, run_id)


@app.get("/run/{run_id}/detail")
async def get_run_detail(request: Request, run_id: str) -> dict:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    state = await graph.aget_state(config)
    return _run_detail(state.values, run_id)


@app.post("/run", response_model=RunResponse)
async def run(request: Request, email: EmailInput) -> RunResponse:
    graph = request.app.state.graph
    run_id = str(uuid.uuid4())
    email_input = email.model_dump()
    result = await graph.ainvoke(
        {"email_input": email_input}, _thread_config(run_id)
    )
    response = _format(result, run_id)
    _record_response(response, email_input)
    return response


@app.post("/run/{run_id}/approve", response_model=RunResponse)
async def approve(request: Request, run_id: str, approval: ApprovalInput) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    result = await graph.ainvoke(
        Command(resume={"type": "approve", "args": approval.args}), config
    )
    response = _format(result, run_id)
    _record_response(response)
    return response


@app.post("/run/{run_id}/reject", response_model=RunResponse)
async def reject(request: Request, run_id: str) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    result = await graph.ainvoke(
        Command(resume={"type": "reject"}), config
    )
    response = _format(result, run_id)
    _record_response(response)
    return response


@app.post("/run/{run_id}/respond", response_model=RunResponse)
async def respond(request: Request, run_id: str, body: RespondInput) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    result = await graph.ainvoke(
        Command(resume=[{"type": "response", "args": body.feedback}]), config
    )
    response = _format(result, run_id)
    _record_response(response)
    return response
