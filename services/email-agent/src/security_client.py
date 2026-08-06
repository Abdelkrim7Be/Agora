from __future__ import annotations

from platform_core.security import SecurityClient

from src.config import settings


def _record_quarantine_usage(usage: dict | None, node: str = "quarantine") -> None:
    """Write the security service's reported token spend into the cost ledger."""
    if not usage:
        return
    from src.cost_tracker import compute_cost, record_cost

    model = str(usage.get("model") or "unknown")
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    if not input_tokens and not output_tokens:
        return
    record_cost({
        "node": node,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_eur": compute_cost(model, input_tokens, output_tokens),
    })


def _user_id() -> str:
    from src.tenant import current_user_id

    return current_user_id()


def _agent_instance_id() -> str:
    from src.tenant import current_agent_instance_id

    return current_agent_instance_id()


_client = SecurityClient(
    base_url=lambda: settings.security_url,
    timeout=lambda: settings.security_timeout,
    current_user_id=_user_id,
    current_agent_instance_id=_agent_instance_id,
    record_usage=lambda usage, node: _record_quarantine_usage(usage, node),
)

classify_content = _client.classify_content
sanitize_email = _client.sanitize_email
fetch_policy = _client.fetch_policy
authorize_action = _client.authorize_action
audit_output = _client.audit_output
sanitize_memory_write = _client.sanitize_memory_write
