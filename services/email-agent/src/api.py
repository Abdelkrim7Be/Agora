from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from src.graph import graph

app = FastAPI(title="email-agent", version="0.1.0")


class RunRequest(BaseModel):
    max_emails: int = 20
    dry_run: bool = True


class RunResponse(BaseModel):
    actions_taken: int
    summary: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest) -> RunResponse:
    initial_state = {"emails": [], "actions": [], "done": False}
    result = await graph.ainvoke(initial_state)
    return RunResponse(
        actions_taken=len(result.get("actions", [])),
        summary="run complete",
    )
