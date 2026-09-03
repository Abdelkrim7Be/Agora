"""Request-scoped helpers shared by `api.py` and the domain routers.

These live outside `api.py` so router modules can use them without importing
the module that includes them — that would be a cycle.
"""

from __future__ import annotations

import hmac
import unicodedata
from datetime import datetime, timezone

from fastapi import HTTPException, Request

import yaml

from src.categories import CategoriesConfig, DEFAULT_CATEGORIES_PATH
from src.config import settings
from src.instance_config import read_instance_text
from src.contacts import Contact, Segment, resolve_segment
from src.tenant import current_agent_instance_id


def _request_user_id(request: Request) -> str | None:
    return request.headers.get("x-agora-user")


def _request_user_dept(request: Request) -> str | None:
    return request.headers.get("x-agora-user-dept")


def _request_agent_instance_id(request: Request) -> str | None:
    return request.headers.get("x-agora-agent-instance")


def _gateway_secret_is_valid(request: Request) -> bool:
    expected = settings.gateway_shared_secret.strip()
    if not expected:
        return True
    actual = request.headers.get("x-agora-gateway-secret", "")
    return hmac.compare_digest(actual, expected)


def _bypasses_gateway_secret(path: str) -> bool:
    return path in {"/health", "/metrics"}


def _require_instance_role(request: Request, min_role: str) -> None:
    """Defense-in-depth: verify the gateway-stamped instance role is sufficient.

    The gateway resolves and stamps X-Agora-Instance-Role before forwarding.
    The gateway resolves and stamps this after authenticating itself with the
    shared gateway secret checked by tenant_context_middleware.
    Roles: owner > approver > viewer.
    """
    ROLE_TIER = {"owner": 3, "approver": 2, "viewer": 1}
    MIN_TIER = {"owner": 3, "approver": 2, "viewer": 1}
    header = request.headers.get("x-agora-instance-role")
    if header is None:
        return  # no gateway header → direct call, let it through (gateway is gatekeeper)
    caller_tier = ROLE_TIER.get(header.lower(), 0)
    required_tier = MIN_TIER.get(min_role, 99)
    if caller_tier < required_tier:
        raise HTTPException(status_code=403, detail="Insufficient instance role")


def _normalize_dept(value: str | None) -> str:
    """Case/accent-fold a department string for comparison.

    Department names are free text (seeded per account, assigned per run by the
    triage/routing step) and the same department has shown up spelled two ways
    ("Securite" vs "Sécurité") — an exact-string compare silently hid a user's
    own department's runs from them. Does not reorder words: a genuinely
    different department name is a data-entry problem to fix at the source,
    not something a security-relevant comparison should paper over.
    """
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _require_dept_access(request: Request, record: dict | None) -> None:
    if record is None:
        return
    user_dept = _request_user_dept(request)
    workflow_dept = record.get("workflow_dept")
    if user_dept and workflow_dept and _normalize_dept(workflow_dept) != _normalize_dept(user_dept):
        raise HTTPException(status_code=403, detail="Not authorized for this department's approval.")


def _serialize_role(role) -> dict:
    return role.model_dump() | {"primary_email": role.primary_email}


def _serialize_contact(contact: Contact) -> dict:
    return contact.model_dump()


def _serialize_segment(segment: Segment) -> dict:
    resolved = resolve_segment(segment, agent_instance_id=current_agent_instance_id())
    return segment.model_dump() | {
        "resolved_count": len(resolved),
        "resolved_members": [member.model_dump() for member in resolved],
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _current_categories() -> tuple[str, CategoriesConfig]:
    categories_yaml = read_instance_text("categories", DEFAULT_CATEGORIES_PATH)
    data = yaml.safe_load(categories_yaml) or {}
    data.setdefault("categories", [])
    data.setdefault("templates", [])
    data.setdefault("contacts", [])
    return categories_yaml, CategoriesConfig(**data)


def _run_timestamp_at_or_after(record: dict, since_dt: datetime) -> bool:
    value = record.get("created_at") or record.get("updated_at")
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) >= since_dt
