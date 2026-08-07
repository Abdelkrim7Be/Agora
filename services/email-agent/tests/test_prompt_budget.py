"""The body that reaches a model is bounded.

`format_thread` bounds a Gmail-fetched thread, but three paths skip it: a single
message with no thread, the manual `/run` API, and the poller appending extracted
attachment text. The body is paid twice per email (triage, then drafting), so an
unbounded one is both the cost risk and the context-window risk.
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.utils import (
    THREAD_BLOCK_SEPARATOR,
    clamp_email_body,
    format_email_markdown,
    parse_email,
    strip_quoted_reply,
)


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


QUOTED_TAILS = [
    "On Tue, 5 Aug 2026 at 10:12, Client <client@example.com> wrote:\n> Bonjour,\n> merci\n> cordialement\n",
    "Le mardi 5 août 2026, Client <client@example.com> a écrit :\n> Bonjour\n> merci\n> bien à vous\n",
    "-----Message d'origine-----\nDe : Client\nObjet : Devis\n\nBonjour, ...\n",
    "-----Original Message-----\nFrom: Client\nSubject: Quote\n\nHello, ...\n",
    "________________________________\nDe : Client <client@example.com>\nEnvoyé : mardi 5 août 2026\n\nBonjour\n",
    "> Bonjour,\n> pouvez-vous confirmer ?\n> merci\n",
]


@pytest.mark.parametrize("tail", QUOTED_TAILS)
def test_the_quoted_history_below_a_reply_is_dropped(tail):
    reply = "Bonjour, c'est confirmé pour jeudi 14h. Bien cordialement, Marie."

    stripped = strip_quoted_reply(reply + "\n\n" + tail)

    assert stripped == reply
    assert "wrote:" not in stripped and "a écrit" not in stripped


def test_a_body_with_no_quote_is_untouched():
    body = "Bonjour,\n\nJe souhaite un devis pour 40 postes.\n\nMerci."

    assert strip_quoted_reply(body) == body


def test_a_reply_too_short_to_be_real_keeps_its_quote():
    # If the divider matched inside the first line, cutting there would throw the
    # message away. A redundant quote costs tokens; a lost message costs the run.
    body = "Ok\n\nOn Tue, 5 Aug 2026, Client wrote:\n> the actual detailed request\n> spanning lines\n> here\n"

    assert strip_quoted_reply(body) == body


def test_one_lone_angle_bracket_is_not_a_quote_block():
    body = "Le seuil est > 10 000 € donc la remise s'applique. Merci de confirmer le devis."

    assert strip_quoted_reply(body) == body


def test_each_message_of_a_thread_keeps_its_own_content():
    # The cleanup runs per thread block. Applied to the whole assembled thread it
    # would cut at the first quote and silently drop every later message.
    first = "From: client@example.com\nDate: Mon\n\nBonjour, je souhaite un devis pour 40 postes."
    second = (
        "From: owner@example.com\nDate: Tue\n\nBonjour, voici le devis en pièce jointe.\n\n"
        "On Mon, 4 Aug 2026, Client <client@example.com> wrote:\n> Bonjour, je souhaite un devis\n"
        "> pour 40 postes\n> merci\n"
    )
    thread = THREAD_BLOCK_SEPARATOR.join([first, second])

    clamped = clamp_email_body(thread)

    assert "je souhaite un devis pour 40 postes" in clamped
    assert "voici le devis en pièce jointe" in clamped
    assert "wrote:" not in clamped
    assert len(clamped) < len(thread)


def test_the_stored_input_is_not_mutated(monkeypatch):
    # Only the prompt is shortened. The run record and the UI must still show the
    # message the client actually sent.
    monkeypatch.setattr(settings, "email_body_max_chars", 100)
    email_input = _input("w" * 5000)

    parse_email(email_input)

    assert len(email_input["email_thread"]) == 5000
