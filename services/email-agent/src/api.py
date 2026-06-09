from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from src.graph import email_assistant

app = FastAPI(title="email-agent", version="0.1.0")


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


def _require_run(run_id: str) -> dict:
    config = _thread_config(run_id)
    state = email_assistant.get_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return config


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def run(email: EmailInput) -> RunResponse:
    run_id = str(uuid.uuid4())
    result = await email_assistant.ainvoke(
        {"email_input": email.model_dump()}, _thread_config(run_id)
    )
    return _format(result, run_id)


@app.post("/run/{run_id}/approve", response_model=RunResponse)
async def approve(run_id: str, approval: ApprovalInput) -> RunResponse:
    config = _require_run(run_id)
    result = await email_assistant.ainvoke(
        Command(resume={"type": "approve", "args": approval.args}), config
    )
    return _format(result, run_id)


@app.post("/run/{run_id}/reject", response_model=RunResponse)
async def reject(run_id: str) -> RunResponse:
    config = _require_run(run_id)
    result = await email_assistant.ainvoke(
        Command(resume={"type": "reject"}), config
    )
    return _format(result, run_id)
