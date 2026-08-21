from src.sensitivity_config import SensitivityConfig
from src.sensitivity_gate import is_sensitive


def _email(**overrides):
    base = {
        "author": "Finance <finance@example.com>",
        "subject": "Ordinary update",
    }
    base.update(overrides)
    return base


def test_disabled_gate_passes():
    sensitive, reason = is_sensitive(_email(), SensitivityConfig(enabled=False, blocked_domains=["example.com"]))
    assert sensitive is False
    assert reason == ""


def test_blocked_sender_is_sensitive():
    sensitive, reason = is_sensitive(
        _email(author="Bank <private@bank.example>"),
        SensitivityConfig(enabled=True, blocked_senders=["private@bank.example"]),
    )
    assert sensitive is True
    assert reason == "sender:blocked"


def test_blocked_domain_is_sensitive():
    sensitive, reason = is_sensitive(
        _email(author="Advisor <ana@legal.example>"),
        SensitivityConfig(enabled=True, blocked_domains=["legal.example"]),
    )
    assert sensitive is True
    assert reason == "domain:blocked"


def test_subject_keyword_is_sensitive_case_insensitive():
    sensitive, reason = is_sensitive(
        _email(subject="Confidentiel - closing"),
        SensitivityConfig(enabled=True, subject_keywords=["confidentiel"]),
    )
    assert sensitive is True
    assert reason == "subject:keyword"


def test_allowlist_wins_over_blocklist():
    sensitive, _ = is_sensitive(
        _email(author="Ana <ana@legal.example>", subject="confidentiel"),
        SensitivityConfig(
            enabled=True,
            allowed_senders=["ana@legal.example"],
            blocked_domains=["legal.example"],
            subject_keywords=["confidentiel"],
        ),
    )
    assert sensitive is False
