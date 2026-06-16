from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from src.authorize import authorize
from src.config import settings
from src.models import AuthorizeRequest, AuthorizeResponse, SanitizeRequest, SanitizeResponse
from src.policy import SERVICE_ROOT
from src.sanitize import sanitize

app = FastAPI(title="agora-security")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/policy")
def get_policy() -> dict:
    """Expose the active capability policy (read-only) for the control panel."""
    path = Path(settings.policy_path)
    if not path.is_absolute():
        path = SERVICE_ROOT / path
    return {"policy_yaml": path.read_text() if path.is_file() else ""}


@app.post("/sanitize", response_model=SanitizeResponse)
def sanitize_endpoint(req: SanitizeRequest) -> SanitizeResponse:
    return sanitize(req)


@app.post("/authorize", response_model=AuthorizeResponse)
def authorize_endpoint(req: AuthorizeRequest) -> AuthorizeResponse:
    return authorize(req)
