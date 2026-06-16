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


def get_memory(store, ns: tuple, default_content: str) -> str:
    """Return stored preferences for ns, seeding from default_content on first call."""
    item = store.get(ns, "user_preferences")
    if item:
        return item.value
    store.put(ns, "user_preferences", default_content)
    return default_content


def update_memory(store, ns: tuple, messages: list, llm) -> None:
    """Synthesize feedback from messages and write updated preferences back to store."""
    item = store.get(ns, "user_preferences")
    current = item.value if item else ""
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
    store.put(ns, "user_preferences", result.user_preferences)
