from __future__ import annotations

import pytest


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
