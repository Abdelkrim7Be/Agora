from __future__ import annotations

import re
import unicodedata

# --- Instruction-override phrases ---
_OVERRIDE = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
     "instruction_override: ignore previous instructions"),
    (re.compile(r"disregard\s+(the\s+)?(above|previous)", re.I),
     "instruction_override: disregard above"),
    (re.compile(r"forget\s+(everything|your\s+instructions)", re.I),
     "instruction_override: forget instructions"),
    (re.compile(r"do\s+not\s+follow\s+(your\s+)?(previous|prior|above)\s+instructions", re.I),
     "instruction_override: do not follow instructions"),
]

# --- Role / system hijack ---
_ROLE_HIJACK = [
    (re.compile(r"you\s+are\s+now\b", re.I),
     "role_hijack: you are now"),
    # Require an article/determiner so "act as quickly" doesn't fire.
    (re.compile(r"\bact\s+as\s+(?:a|an|the)\b", re.I),
     "role_hijack: act as"),
    # Require possessive, imperative verb, or colon — "our system prompt was updated" is benign.
    (re.compile(r"(?:your|reveal|show|ignore|bypass|override)\s+system\s+prompt|\bsystem\s+prompt\s*:", re.I),
     "role_hijack: system prompt reference"),
    (re.compile(r"new\s+instructions\s*:", re.I),
     "role_hijack: new instructions"),
    (re.compile(r"<\s*system\s*>", re.I),
     "role_hijack: <system> tag"),
    (re.compile(r"\[INST\]", re.I),
     "role_hijack: [INST] marker"),
    # Lines that start with "system:" or "assistant:" (after optional whitespace)
    (re.compile(r"^\s*(system|assistant)\s*:", re.I | re.MULTILINE),
     "role_hijack: system/assistant role prefix"),
]

# --- Exfiltration / action coercion ---
_EXFIL = [
    (re.compile(r"forward\s+(this|all)\s+(email|mail)", re.I),
     "exfil: forward email"),
    (re.compile(r"send\s+.{0,40}to\s+[\w.+-]+@[\w.-]+", re.I),
     "exfil: send to address"),
    (re.compile(r"reply\s+with\s+your\s+(system\s+prompt|instructions|api\s+key)", re.I),
     "exfil: reply with secrets"),
    (re.compile(r"delete\s+all\b", re.I),
     "exfil: delete all"),
    (re.compile(r"exfiltrate", re.I),
     "exfil: exfiltrate keyword"),
]

# --- Prompt-fence / break-out patterns ---
_FENCEBREAK = [
    # 6+ consecutive backticks (code fence abuse)
    (re.compile(r"`{6,}"),
     "fence_breakout: long backtick run"),
    # PEM/base64 header blocks (may carry encoded payloads)
    (re.compile(r"-----BEGIN\s+\w"),
     "fence_breakout: BEGIN block"),
]

_ALL_PATTERNS = _OVERRIDE + _ROLE_HIJACK + _EXFIL + _FENCEBREAK

# Unicode categories that are abnormal in plain email text. We deliberately exclude
# "Cf" (format) wholesale to avoid false positives on legitimate multilingual / RTL
# mail (directional marks, ZWNBSP); only the zero-width chars commonly abused to hide
# payloads are called out explicitly below.
_CONTROL_CATS = {"Cc", "Cs", "Co", "Cn"}
_SAFE_CONTROLS = {"\n", "\r", "\t"}
# Zero-width / invisible chars frequently used to smuggle hidden instructions.
_ZERO_WIDTH = {
    chr(0x200B),  # zero-width space
    chr(0x200C),  # zero-width non-joiner
    chr(0x200D),  # zero-width joiner
    chr(0x2060),  # word joiner
    chr(0xFEFF),  # zero-width no-break space / BOM
}


def _has_unusual_unicode(text: str) -> bool:
    for ch in text:
        if ch in _SAFE_CONTROLS:
            continue
        if ch in _ZERO_WIDTH:
            return True
        if unicodedata.category(ch) in _CONTROL_CATS:
            return True
    return False


def scan(text: str) -> list[str]:
    """Return a list of reason strings for any injection patterns found.

    Empty list means nothing suspicious. Does not modify the text.
    """
    reasons: list[str] = []
    for pattern, reason in _ALL_PATTERNS:
        if pattern.search(text) and reason not in reasons:
            reasons.append(reason)
    if _has_unusual_unicode(text):
        reasons.append("fence_breakout: unusual unicode control characters")
    return reasons
