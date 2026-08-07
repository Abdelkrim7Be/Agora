"""The body that reaches a model is bounded.

`format_thread` bounds a Gmail-fetched thread, but three paths skip it: a single
message with no thread, the manual `/run` API, and the poller appending extracted
attachment text. The body is paid twice per email (triage, then drafting), so an
unbounded one is both the cost risk and the context-window risk.
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.utils import clamp_email_body, format_email_markdown, parse_email


def _input(body: str) -> dict:
    return {
        "author": "client@example.com",
        "to": "owner@example.com",
        "subject": "Demande de devis",
        "email_thread": body,
    }


def test_a_short_body_is_untouched():
    body = "Bonjour, pouvez-vous m'envoyer un devis ?"

    assert clamp_email_body(body) == body
    assert parse_email(_input(body))[3] == body


def test_a_long_body_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "email_body_max_chars", 100)
    body = "x" * 5000

    clamped = clamp_email_body(body)

    assert len(clamped) < 200
    assert clamped.startswith("x" * 100)
    assert "tronqué" in clamped


def test_the_head_is_kept_not_the_tail(monkeypatch):
    # The ask in a business email is at the top; a long body is mostly quoted
    # history underneath it.
    monkeypatch.setattr(settings, "email_body_max_chars", 60)
    body = "URGENT: merci de confirmer la commande 4471.\n" + ("citation " * 500)

    clamped = clamp_email_body(body)

    assert "URGENT" in clamped
    assert "4471" in clamped


def test_a_zero_cap_disables_it(monkeypatch):
    monkeypatch.setattr(settings, "email_body_max_chars", 0)
    body = "y" * 50_000

    assert clamp_email_body(body) == body


@pytest.mark.parametrize("cap", [500, 12_000])
def test_parse_email_is_the_single_choke_point(monkeypatch, cap):
    # Every prompt-facing call site in graph.py goes through parse_email; nothing
    # else in the codebase reads it. If that stops being true, this cap leaks.
    monkeypatch.setattr(settings, "email_body_max_chars", cap)
    body = "z" * 100_000

    author, to, subject, thread = parse_email(_input(body))

    assert len(thread) <= cap + 50
    # The markdown the drafting node embeds inherits the same bound.
    assert len(format_email_markdown(subject, author, to, thread)) <= cap + 300


def test_the_stored_input_is_not_mutated(monkeypatch):
    # Only the prompt is shortened. The run record and the UI must still show the
    # message the client actually sent.
    monkeypatch.setattr(settings, "email_body_max_chars", 100)
    email_input = _input("w" * 5000)

    parse_email(email_input)

    assert len(email_input["email_thread"]) == 5000
