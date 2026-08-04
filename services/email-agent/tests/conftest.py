from __future__ import annotations

from types import SimpleNamespace

from contextlib import contextmanager

import pytest
from langchain_core.messages import AIMessage

from src.memory import UserPreferences


@pytest.fixture(scope="session", autouse=True)
def _ensure_agent_role():
    """Create the agora_email_agent role before any Postgres migration runs.

    The 0009/0010 grants are conditional on this role already existing when the
    migration executes (in prod the role is provisioned before deploy). Several
    Postgres test files call upgrade_to_head() on the shared CI database; whichever
    runs first fixes the migration head. If that first migration happens without
    the role present, the conditional GRANTs are silently skipped and every later
    test connecting as agora_email_agent hits "permission denied". Creating the
    role here — once, before collection-order can decide — keeps the grants applied
    regardless of which pg test migrates first.
    """
    import os

    admin_url = os.getenv("RLS_TEST_ADMIN_URL", "")
    if not admin_url:
        yield
        return

    import psycopg

    with psycopg.connect(admin_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent'
                    ) THEN
                        CREATE ROLE agora_email_agent LOGIN PASSWORD 'agent-test-password'
                            NOSUPERUSER NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
    yield


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


class FakeProvider:
    """Stand-in for a MailProvider, built from the handful of methods a test cares about.

    Any method the test did not supply raises, so a call site reaching for a
    mailbox operation the test did not expect fails loudly instead of silently
    returning a Mock.
    """

    name = "gmail"

    def __init__(self, **methods):
        for attribute, value in methods.items():
            setattr(self, attribute, value)

    def __getattr__(self, item):
        raise AssertionError(f"FakeProvider was asked for an unstubbed method: {item}")


def patch_provider(monkeypatch, module, **methods) -> FakeProvider:
    """Point one module's `get_provider` at a FakeProvider and return it."""
    provider = FakeProvider(**methods)
    monkeypatch.setattr(module, "get_provider", lambda *args, **kwargs: provider)
    return provider


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


@contextmanager
def reply_to(address: str | None):
    """Set the trusted reply recipient tool_node would supply for a message.

    Send tools take no `to` argument: the recipient comes from graph context so
    that untrusted mail cannot redirect it. Tests calling a tool directly have
    to stand in for tool_node and set that context.
    """
    from src.capabilities import current_reply_to

    token = current_reply_to.set(address)
    try:
        yield
    finally:
        current_reply_to.reset(token)


@contextmanager
def route_targets(*addresses: str):
    """Set the workflow-configured recipients tool_node would supply."""
    from src.capabilities import current_route_targets

    token = current_route_targets.set(tuple(addresses))
    try:
        yield
    finally:
        current_route_targets.reset(token)


@pytest.fixture(autouse=True)
def _no_outbound_llm_calls(request, monkeypatch):
    """Fail loudly instead of quietly calling a real model.

    `fake_llms` is opt-in, so a test that forgot it used whatever LLM endpoint the
    developer happened to have running. The suite then passed on a laptop with
    Ollama up and failed in CI with a bare "Connection error", and a green local
    run meant nothing. Any test that genuinely needs a model must ask for
    `fake_llms` (or patch the binding itself); everything else gets a stub that
    explains what is missing.
    """
    if "fake_llms" in request.fixturenames or "allow_real_llm" in request.keywords:
        return

    import src.graph as g

    class _Unstubbed:
        def __init__(self, attr): self._attr = attr
        def _fail(self, *_a, **_k):
            raise AssertionError(
                f"{request.node.name} invoked the real {self._attr}. Add the "
                "'fake_llms' fixture, or mark the test with @pytest.mark.allow_real_llm."
            )
        invoke = __call__ = _fail
        def bind_tools(self, *a, **k): return self
        def with_structured_output(self, *a, **k): return self

    for attr in ("llm", "llm_router", "llm_with_tools", "llm_memory", "llm_redraft"):
        if hasattr(g, attr):
            monkeypatch.setattr(g, attr, _Unstubbed(attr), raising=False)
