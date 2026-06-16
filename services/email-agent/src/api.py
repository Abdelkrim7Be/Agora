from __future__ import annotations

import uuid

import yaml
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from langgraph.types import Command
from pydantic import BaseModel

from src.config import settings
from src.automation import DEFAULT_RULES_PATH, RulesConfig, load_rules
from src.config import AgentConfig, DEFAULT_CONFIG_PATH, load_config
from src.graph import overall_workflow, reload_config
from src.memory import namespace
from src.run_registry import list_runs, upsert_run
from src.security_client import fetch_policy
from src.storage import open_graph_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with open_graph_storage() as storage:
        # The graph's nodes are sync, so LangGraph runs them in a threadpool where
        # sync store.get/put works. A future ASYNC node must use aget/aput instead.
        app.state.graph = overall_workflow.compile(
            checkpointer=storage.checkpointer, store=storage.store
        )
        app.state.store = storage.store
        app.state.storage_backend = storage.backend
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








class RulesInput(BaseModel):
    rules_yaml: str


class CapabilitiesInput(BaseModel):
    capabilities: dict[str, bool]


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
    return {"status": "ok", "storage_backend": settings.storage_backend}


@app.get("/rules")
async def get_rules() -> dict:
    rules_yaml = DEFAULT_RULES_PATH.read_text() if DEFAULT_RULES_PATH.is_file() else "enabled: false\n"
    return {
        "rules_yaml": rules_yaml,
        "parsed": load_rules().model_dump(),
    }


@app.put("/rules")
async def update_rules(body: RulesInput) -> dict:
    data = yaml.safe_load(body.rules_yaml) or {}
    if data.get("rules") is None:
        data["rules"] = []
    parsed = RulesConfig(**data)  # validate before persisting
    # Persist the user's raw YAML verbatim so comments/formatting survive a round-trip.
    DEFAULT_RULES_PATH.write_text(body.rules_yaml)
    return {
        "rules_yaml": body.rules_yaml,
        "parsed": parsed.model_dump(),
    }


@app.get("/capabilities")
async def get_capabilities() -> dict:
    return {"capabilities": load_config().capabilities}


@app.put("/capabilities")
async def update_capabilities(body: CapabilitiesInput) -> dict:
    current = load_config().model_dump()
    current["capabilities"] = body.capabilities
    cfg = AgentConfig(**current)
    DEFAULT_CONFIG_PATH.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
    reload_config()  # make the toggle live in this process (graph reads module globals)
    return {"capabilities": cfg.capabilities}


@app.get("/policy")
async def get_policy() -> dict:
    # The policy lives in the (separate) security service — fetch it over HTTP rather
    # than reaching for a file that isn't in this container.
    return await fetch_policy()


@app.get("/config")
async def get_agent_config() -> dict:
    return load_config().model_dump()


@app.put("/config")
async def update_agent_config(body: dict) -> dict:
    cfg = AgentConfig(**body)
    DEFAULT_CONFIG_PATH.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
    reload_config()  # persona/triage edits take effect without a restart (this process)
    return cfg.model_dump()


@app.get("/memory")
async def get_preferences(request: Request) -> dict:
    cfg = load_config()
    store = request.app.state.store
    triage = await store.aget(namespace("triage_preferences"), "user_preferences")
    response = await store.aget(namespace("response_preferences"), "user_preferences")
    return {
        "triage_preferences": triage.value if triage else cfg.agent.triage_instructions,
        "response_preferences": response.value if response else cfg.agent.response_preferences,
    }


@app.put("/memory")
async def update_preferences(request: Request, body: MemoryInput) -> dict:
    # AsyncSqliteStore: must use the async API on the event loop (sync calls raise).
    store = request.app.state.store
    await store.aput(namespace("triage_preferences"), "user_preferences", body.triage_preferences)
    await store.aput(namespace("response_preferences"), "user_preferences", body.response_preferences)
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
