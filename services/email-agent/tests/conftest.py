from __future__ import annotations

import pytest


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
