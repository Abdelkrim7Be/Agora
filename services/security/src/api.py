from __future__ import annotations

import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from src.authorize import authorize
from src.classify import classify_source
from src.config import settings
from src.metrics import render_metrics
from src.models import (
    AuditOutputRequest,
    AuditOutputResponse,
    AuthorizeRequest,
    AuthorizeResponse,
    ClassifySourceRequest,
    ClassifySourceResponse,
    RedactRequest,
    RedactResponse,
    RestoreRequest,
    RestoreResponse,
    SanitizeRequest,
    SanitizeResponse,
)
from src.output_audit import audit_output
from src.redact import redact as redact_text, restore as restore_text
from src.policy import SERVICE_ROOT
from src.sanitize import sanitize

app = FastAPI(title="agora-security")

logger = logging.getLogger("agora.security")

_OPEN_PATHS = {"/health", "/metrics"}


@app.middleware("http")
async def require_agent_shared_secret(request: Request, call_next):
    """Reject calls that don't carry the secret shared with email-agent."""
    expected = settings.shared_secret.strip()
    if expected and request.url.path not in _OPEN_PATHS:
        actual = request.headers.get("x-agora-security-secret", "")
        if not hmac.compare_digest(actual, expected):
            return JSONResponse({"detail": "invalid or missing shared secret"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def warn_about_non_default_policy() -> None:
    """Say so, loudly, when the service is not running the production policy.

    policy.live-test.yaml pins every send-style tool to a two-address allow list.
    That is correct for the live-mail harness and completely wrong for real use —
    and the difference is invisible from the UI, so it needs to be visible in the
    logs of whatever host it lands on.
    """
    policy = settings.policy_path
    if Path(policy).name != "policy.yaml":
        logger.warning(
            "security policy is %r, not policy.yaml — send tools may be restricted "
            "to a test allow list. Set SECURITY_POLICY_PATH=policy.yaml for production.",
            policy,
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


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


@app.post("/classify", response_model=ClassifySourceResponse)
def classify_source_endpoint(req: ClassifySourceRequest) -> ClassifySourceResponse:
    return classify_source(req)


@app.post("/redact", response_model=RedactResponse)
def redact_endpoint(req: RedactRequest) -> RedactResponse:
    """Strip identifiers from arbitrary text before it reaches a hosted model."""
    result = redact_text(req.text)
    return RedactResponse(
        redacted_text=result.text, mapping=result.mapping, counts=result.kinds()
    )


@app.post("/restore", response_model=RestoreResponse)
def restore_endpoint(req: RestoreRequest) -> RestoreResponse:
    """Put redacted values back into content a model produced."""
    return RestoreResponse(text=restore_text(req.text, req.mapping))


@app.post("/authorize", response_model=AuthorizeResponse)
def authorize_endpoint(req: AuthorizeRequest) -> AuthorizeResponse:
    return authorize(req)


@app.post("/audit-output", response_model=AuditOutputResponse)
def audit_output_endpoint(req: AuditOutputRequest) -> AuditOutputResponse:
    return audit_output(req)
