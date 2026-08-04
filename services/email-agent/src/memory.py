from __future__ import annotations

from pydantic import BaseModel, Field

from src.config import settings
from src.prompts import MEMORY_UPDATE_INSTRUCTIONS
from src.security_client import sanitize_memory_write
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)


class UserPreferences(BaseModel):
    """Updated user preferences synthesized from HITL feedback."""

    chain_of_thought: str = Field(
        description="Reasoning about which preferences to add or update."
    )
    user_preferences: str = Field(
        description="The complete updated preferences string."
    )


def _namespace_label(value: str) -> str:
    return value.replace(".", "_")


def namespace(
    kind: str,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> tuple[str, str, str, str]:
    """Memory is scoped by user and agent instance.

    Existing stores keyed as ("email_agent", user_id, kind) are not copied in-place.
    The default instance will reseed from config on first read when no instance-keyed
    item exists, preserving single-mailbox behavior without mutating legacy rows.
    """
    return (
        "email_agent",
        _namespace_label(normalize_user_id(user_id or current_user_id())),
        _namespace_label(normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())),
        kind,
    )


# Where a stored preference came from, surfaced to the user as provenance.
ORIGIN_SETUP = "setup"      # seeded by the onboarding wizard
ORIGIN_LEARNED = "learned"  # distilled by the agent from real activity
ORIGIN_MANUAL = "manual"    # typed or edited by a human in the UI
ORIGIN_DEFAULT = "default"  # never written — falling back to config.yaml


def wrap_preferences(text: str, origin: str | None = None) -> dict:
    """Store shape for a preferences string.

    The BaseStore contract expects a dict value; the Postgres (JSONB) store fails to
    deserialize a bare string. Anything writing preferences must go through this.
    `origin` records provenance so the UI can say whether the user configured this
    or the agent learned it; rows written before it existed simply have none.
    """
    wrapped = {"preferences": text}
    if origin:
        wrapped["origin"] = origin
    return wrapped


def preferences_text(value) -> str:
    """Read the preferences string back from a stored value.

    Tolerates legacy SQLite dev data that may still hold a raw string.
    """
    if isinstance(value, dict):
        return value.get("preferences", "")
    return value or ""


def preferences_origin(value) -> str | None:
    """Provenance of a stored value, or None for rows written before it was tracked."""
    if isinstance(value, dict):
        origin = value.get("origin")
        return origin if isinstance(origin, str) and origin else None
    return None


# Backwards-compatible internal alias.
_unwrap = preferences_text


def get_memory(store, ns: tuple, default_content: str) -> str:
    """Return stored preferences for ns, seeding from default_content on first call."""
    item = store.get(ns, "user_preferences")
    if item:
        return preferences_text(item.value)
    store.put(ns, "user_preferences", wrap_preferences(default_content, ORIGIN_DEFAULT))
    return default_content


def _invoke_memory_llm(llm, messages: list, invoke_config: dict | None):
    if invoke_config is None:
        return llm.invoke(messages)
    try:
        return llm.invoke(messages, config=invoke_config)
    except TypeError as exc:
        if "config" not in str(exc):
            raise
        return llm.invoke(messages)


def update_memory(
    store,
    ns: tuple,
    messages: list,
    llm,
    invoke_config: dict | None = None,
) -> None:
    """Synthesize feedback from messages and write updated preferences back to store."""
    item = store.get(ns, "user_preferences")
    current = preferences_text(item.value) if item else ""
    current_origin = preferences_origin(item.value) if item else None
    prompt_messages = [
        {
            "role": "system",
            "content": MEMORY_UPDATE_INSTRUCTIONS.format(
                current_profile=current, namespace=ns
            ),
        }
    ] + messages
    try:
        result = _invoke_memory_llm(llm, prompt_messages, invoke_config)
    except Exception as exc:
        print(f"memory: preference update skipped: {exc}")
        return
    updated = (result.user_preferences or "").strip()
    # Guard against destructive rewrites: small models sometimes replace the
    # whole profile with a one-line summary of the latest feedback. A real
    # incremental update never collapses an established profile.
    if current_origin != ORIGIN_DEFAULT and current and len(current) > 200 and len(updated) < len(current) // 2:
        print(
            f"memory: preference update rejected for {ns}: proposed profile "
            f"({len(updated)} chars) would collapse the current one ({len(current)} chars)"
        )
        return
    if settings.security_enabled:
        namespace_label = "/".join(str(part) for part in ns)
        verdict = sanitize_memory_write(namespace_label, updated)
        if verdict.get("injection_detected") or verdict.get("classification") == "malicious":
            print(
                f"memory: preference update rejected for {ns}: sanitize flagged "
                f"synthesized text ({verdict.get('reasons')})"
            )
            return
        updated = verdict.get("cleaned_text") or updated
    store.put(ns, "user_preferences", wrap_preferences(updated, ORIGIN_LEARNED))
