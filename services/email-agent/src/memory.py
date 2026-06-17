from __future__ import annotations

from pydantic import BaseModel, Field

from src.prompts import MEMORY_UPDATE_INSTRUCTIONS
from src.tenant import current_user_id, normalize_user_id


class UserPreferences(BaseModel):
    """Updated user preferences synthesized from HITL feedback."""

    chain_of_thought: str = Field(
        description="Reasoning about which preferences to add or update."
    )
    user_preferences: str = Field(
        description="The complete updated preferences string."
    )


def namespace(kind: str, user_id: str | None = None) -> tuple[str, str, str]:
    return ("email_agent", normalize_user_id(user_id or current_user_id()), kind)


def wrap_preferences(text: str) -> dict:
    """Store shape for a preferences string.

    The BaseStore contract expects a dict value; the Postgres (JSONB) store fails to
    deserialize a bare string. Anything writing preferences must go through this.
    """
    return {"preferences": text}


def preferences_text(value) -> str:
    """Read the preferences string back from a stored value.

    Tolerates legacy SQLite dev data that may still hold a raw string.
    """
    if isinstance(value, dict):
        return value.get("preferences", "")
    return value or ""


# Backwards-compatible internal alias.
_unwrap = preferences_text


def get_memory(store, ns: tuple, default_content: str) -> str:
    """Return stored preferences for ns, seeding from default_content on first call."""
    item = store.get(ns, "user_preferences")
    if item:
        return preferences_text(item.value)
    store.put(ns, "user_preferences", wrap_preferences(default_content))
    return default_content


def update_memory(store, ns: tuple, messages: list, llm) -> None:
    """Synthesize feedback from messages and write updated preferences back to store."""
    item = store.get(ns, "user_preferences")
    current = preferences_text(item.value) if item else ""
    try:
        result = llm.invoke(
            [
                {
                    "role": "system",
                    "content": MEMORY_UPDATE_INSTRUCTIONS.format(
                        current_profile=current, namespace=ns
                    ),
                }
            ]
            + messages
        )
    except Exception as exc:
        print(f"memory: preference update skipped: {exc}")
        return
    store.put(ns, "user_preferences", wrap_preferences(result.user_preferences))
