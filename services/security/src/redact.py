"""Reversible redaction of personal and financial identifiers.

Why this exists: the agent's drafting model may be a hosted provider, and the
content it drafts from is a person's real mailbox — invoices, payslips, bank
correspondence. Redaction replaces the identifiers that carry the most risk with
stable placeholders before that content leaves the process, and puts them back
once a draft returns.

This is data minimisation, not anonymisation. A mail body still carries names,
addresses and free text that no regular expression will catch. Treat it as
reducing the blast radius of a provider incident, never as a claim that the
provider sees no personal data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordered: the first pattern to match a span wins, so the most specific
# identifiers are listed before the generic number formats that could also
# match part of them.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # IBAN — country code, check digits, then up to 30 alphanumerics, commonly
    # written in groups of four.
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}[ ]?[A-Z0-9]{1,4}\b")),
    # BIC / SWIFT.
    ("BIC", re.compile(r"\b[A-Z]{4}[ ]?[A-Z]{2}[ ]?[A-Z0-9]{2}(?:[ ]?[A-Z0-9]{3})?\b")),
    # Payment cards: 13–19 digits in groups, Luhn-checked below.
    ("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    # French social security number (NIR). Written with spaces far more often
    # than without, which is why the separators are optional at every boundary —
    # the unspaced-only pattern this replaced was being eaten by TEL.
    ("NIR", re.compile(
        r"\b[12][ .]?\d{2}[ .]?(?:0[1-9]|1[0-2]|[2-9]\d)[ .]?\d{2}[ .]?\d{3}[ .]?\d{3}(?:[ .]?\d{2})?\b"
    )),
    # SIRET (14) and SIREN (9).
    ("SIRET", re.compile(r"\b\d{3}[ ]?\d{3}[ ]?\d{3}[ ]?\d{5}\b")),
    # French VAT number.
    ("TVA", re.compile(r"\bFR[ ]?[A-Z0-9]{2}[ ]?\d{9}\b")),
    # Phone numbers, French and international.
    ("TEL", re.compile(r"(?<![\w.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?)?\d(?:[ .-]?\d){7,13}(?![\w.])")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
]


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _accept(kind: str, value: str) -> bool:
    """Reject matches that are the right shape but not actually an identifier."""
    digits = re.sub(r"\D", "", value)
    if kind == "CARD":
        # Without a Luhn check this pattern eats order numbers and references.
        return 13 <= len(digits) <= 19 and _luhn_ok(digits)
    if kind == "TEL":
        return 8 <= len(digits) <= 15
    if kind == "BIC":
        # Requires at least one digit-free 4-letter bank code and no spaces-only
        # match; plain uppercase words otherwise qualify.
        return bool(re.fullmatch(r"[A-Z]{4}[ ]?[A-Z]{2}[ ]?[A-Z0-9]{2}([ ]?[A-Z0-9]{3})?", value))
    return True


@dataclass
class Redaction:
    """Redacted text plus the mapping needed to restore it."""

    text: str
    # placeholder -> original substring
    mapping: dict[str, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.mapping)

    def kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for placeholder in self.mapping:
            kind = placeholder.strip("[]").rsplit("_", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
        return counts


def redact(text: str, *, skip: set[str] | None = None) -> Redaction:
    """Replace identifiers with stable placeholders.

    The same value always gets the same placeholder within one call, so a model
    can still reason about "the sender" or "the same account" appearing twice.
    """
    if not text:
        return Redaction(text=text or "")

    skip = skip or set()
    mapping: dict[str, str] = {}
    seen: dict[str, str] = {}
    counters: dict[str, int] = {}
    spans: list[tuple[int, int, str]] = []
    claimed: list[tuple[int, int]] = []

    for kind, pattern in _PATTERNS:
        if kind in skip:
            continue
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue
            value = match.group(0)
            if not _accept(kind, value):
                continue
            key = f"{kind}:{re.sub(r'[ .-]', '', value).upper()}"
            placeholder = seen.get(key)
            if placeholder is None:
                counters[kind] = counters.get(kind, 0) + 1
                placeholder = f"[{kind}_{counters[kind]}]"
                seen[key] = placeholder
                mapping[placeholder] = value
            claimed.append((start, end))
            spans.append((start, end, placeholder))

    if not spans:
        return Redaction(text=text)

    out = []
    cursor = 0
    for start, end, placeholder in sorted(spans):
        out.append(text[cursor:start])
        out.append(placeholder)
        cursor = end
    out.append(text[cursor:])
    return Redaction(text="".join(out), mapping=mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put the original values back into text a model produced."""
    if not text or not mapping:
        return text or ""
    # Longest placeholder first so [EMAIL_10] is not eaten by [EMAIL_1].
    for placeholder in sorted(mapping, key=len, reverse=True):
        text = text.replace(placeholder, mapping[placeholder])
    return text
