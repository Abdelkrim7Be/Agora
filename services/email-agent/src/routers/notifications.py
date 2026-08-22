from __future__ import annotations


from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from src.notification_store import (
    delete_notification,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)
from src.tenant import current_agent_instance_id
from src.api_shared import (
    _require_instance_role,
)

router = APIRouter()

@router.get("/notifications")
async def list_notifications_endpoint(
    request: Request,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    _require_instance_role(request, "viewer")
    return {
        "agent_instance_id": current_agent_instance_id(),
        "notifications": list_notifications(unread_only=unread_only, limit=limit),
    }


@router.get("/notifications/unread-count")
async def notifications_unread_count(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return {"agent_instance_id": current_agent_instance_id(), "unread_count": unread_count()}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int, request: Request) -> dict:
    _require_instance_role(request, "viewer")
    row = mark_read(notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return row


@router.post("/notifications/read-all")
async def mark_all_notifications_read(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return {"agent_instance_id": current_agent_instance_id(), "marked_read": mark_all_read()}


@router.delete("/notifications/{notification_id}")
async def delete_notification_endpoint(notification_id: int, request: Request) -> dict:
    _require_instance_role(request, "viewer")
    delete_notification(notification_id)
    return {"agent_instance_id": current_agent_instance_id(), "deleted": True}
