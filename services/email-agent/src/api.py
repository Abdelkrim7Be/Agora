from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from src.graph import email_assistant

app = FastAPI(title="email-agent", version="0.1.0")


class EmailInput(BaseModel):
    author: str
    to: str
    subject: str
    email_thread: str


class RunResponse(BaseModel):
    classification: str
    summary: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def run(email: EmailInput) -> RunResponse:
    result = await email_assistant.ainvoke({"email_input": email.model_dump()})
    classification = result.get("classification_decision", "unknown")
    return RunResponse(
        classification=classification,
        summary=f"email triaged as {classification}",
    )
