from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from src.memory import UserPreferences


# --- Fake LLMs: let tests drive the real graph deterministically, no Groq calls. ---


def ai_tool_call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """An AI message that requests a single tool call."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


class _FakeRouter:
    def __init__(self, classification: str, category: str | None = None):
        self._classification = classification
        self._category = category

    def invoke(self, _messages, config=None):
        return SimpleNamespace(classification=self._classification, category=self._category)


class _FakeToolLLM:
    def __init__(self, sequence: list[AIMessage]):
        self._sequence = list(sequence)
        self._i = 0

    def invoke(self, _messages, config=None):
        msg = self._sequence[min(self._i, len(self._sequence) - 1)]
        self._i += 1
        return msg


class _FakeMemoryLLM:
    """Returns a canned UserPreferences so memory updates run offline."""

    def __init__(self, preference_text: str = "updated preference"):
        self._pref = preference_text

    def invoke(self, _messages, config=None):
        return UserPreferences(
            chain_of_thought="fake reasoning",
            user_preferences=self._pref,
        )


class _FakeRedraftLLM:
    """Replays structured RedraftOutput-shaped revisions for the redraft node.

    Each entry is a dict with to/subject/content (missing keys default to "" so
    the node falls back to the previous draft's recipient/subject).
    """

    def __init__(self, sequence: list[dict]):
        self._sequence = list(sequence)
        self._i = 0

    def invoke(self, _messages, config=None):
        draft = self._sequence[min(self._i, len(self._sequence) - 1)]
        self._i += 1
        return SimpleNamespace(
            to=draft.get("to", ""),
            subject=draft.get("subject", ""),
            content=draft.get("content", ""),
        )


@pytest.fixture(autouse=True)
def _send_mode_follows_dry_run(monkeypatch):
    """Legacy tests toggle settings.dry_run directly to reach the live-send paths.

    Map the per-instance send mode to 'live' so effective_dry_run() keeps
    following the global flag in those tests; send-mode-specific tests
    monkeypatch src.send_mode.get_send_mode themselves.
    """
    import src.send_mode as sm

    monkeypatch.setattr(sm, "get_send_mode", lambda agent_instance_id=None: "live")


@pytest.fixture
def fake_llms(monkeypatch):
    """Patch the graph's router, tool LLM, and memory LLM for offline deterministic tests."""

    def _install(
        classification: str = "respond",
        tool_sequence=None,
        memory_preference: str = "updated preference",
        redraft_sequence=None,
    ):
        import src.graph as g

        monkeypatch.setattr(g, "llm_router", _FakeRouter(classification))
        if tool_sequence is not None:
            monkeypatch.setattr(g, "llm_with_tools", _FakeToolLLM(tool_sequence))
        monkeypatch.setattr(g, "llm_memory", _FakeMemoryLLM(memory_preference))
        monkeypatch.setattr(
            g,
            "llm_redraft",
            _FakeRedraftLLM(redraft_sequence or [{"content": "Revised draft after feedback."}]),
        )

    return _install


# Reference per-email shape (author/to/subject/email_thread) — used by the graph.
RESPOND_EMAIL = {
    "author": "Alice Smith <alice@example.com>",
    "to": "Me <me@example.com>",
    "subject": "Quick question about the API",
    "email_thread": (
        "Hi, I'm trying to use the email endpoint but can't find it in the docs. "
        "Could you point me to the right place? Thanks!"
    ),
}

IGNORE_EMAIL = {
    "author": "Promotions <newsletter@promo.io>",
    "to": "Me <me@example.com>",
    "subject": "Your weekly digest — 50% off this week only!",
    "email_thread": "Here are this week's top deals. Unsubscribe anytime.",
}


@pytest.fixture
def respond_email() -> dict:
    return RESPOND_EMAIL


@pytest.fixture
def ignore_email() -> dict:
    return IGNORE_EMAIL


# Gmail-ish shape — parked for the Slice 5 real-Gmail integration.
MOCK_EMAILS = [
    {
        "id": "msg-001",
        "from": "alice@example.com",
        "subject": "Quick question about the project",
        "body": "Hey, can we sync tomorrow at 10am?",
        "labels": ["INBOX", "UNREAD"],
    },
    {
        "id": "msg-002",
        "from": "newsletter@promo.io",
        "subject": "Your weekly digest",
        "body": "Here are this week's top stories...",
        "labels": ["INBOX", "CATEGORY_PROMOTIONS"],
    },
]


@pytest.fixture
def mock_mailbox() -> list[dict]:
    return MOCK_EMAILS


@pytest.fixture
def empty_mailbox() -> list[dict]:
    return []
