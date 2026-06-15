from __future__ import annotations

from fastapi import FastAPI

from src.models import SanitizeRequest, SanitizeResponse
from src.sanitize import sanitize

app = FastAPI(title="agora-security")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/sanitize", response_model=SanitizeResponse)
def sanitize_endpoint(req: SanitizeRequest) -> SanitizeResponse:
    return sanitize(req)
