from __future__ import annotations

from types import SimpleNamespace

import pytest

from platform_core.notify import SystemNotifier, format_duration


def _notifier(sent, *, enabled=True, base_url="", directory=None):
    directory = directory or {}
    return SystemNotifier(
        enabled=lambda: enabled,
        send=lambda **kwargs: sent.append(kwargs),
        app_base_url=lambda: base_url,
        resolve_role=lambda name: directory.get(name),
    )


def _role(email):
    return SimpleNamespace(primary_email=email)


def test_a_literal_address_is_used_as_is():
    assert _notifier([]).resolve_recipient("Support@Example.com") == "support@example.com"


def test_a_role_name_is_looked_up_in_the_directory():
    notifier = _notifier([], directory={"support": _role("team@example.com")})

    assert notifier.resolve_recipient("support") == "team@example.com"


def test_the_first_candidate_that_resolves_wins():
    notifier = _notifier([], directory={"support": _role("team@example.com")})

    assert notifier.resolve_recipient("", "   ", "unknown-role", "support") == "team@example.com"


def test_a_role_with_no_mailbox_is_skipped_rather_than_returned():
    notifier = _notifier([], directory={"ghost": _role(None), "support": _role("t@example.com")})

    assert notifier.resolve_recipient("ghost", "support") == "t@example.com"


def test_no_resolvable_candidate_means_no_recipient():
    assert _notifier([]).resolve_recipient(None, "", "nobody") is None


def test_a_notification_reaches_the_provider():
    sent = []

    assert _notifier(sent).send("a@example.com", "Subject", "Body") is True
    assert sent == [{"to": "a@example.com", "subject": "Subject", "body": "Body"}]


def test_nothing_is_sent_when_notifications_are_off():
    sent = []

    assert _notifier(sent, enabled=False).send("a@example.com", "s", "b") is False
    assert sent == []


def test_nothing_is_sent_without_a_recipient():
    sent = []

    assert _notifier(sent).send(None, "s", "b") is False
    assert sent == []


def test_a_failing_send_is_reported_rather_than_raised():
    notifier = SystemNotifier(
        enabled=lambda: True,
        send=lambda **_: (_ for _ in ()).throw(RuntimeError("smtp is down")),
        app_base_url=lambda: "",
        resolve_role=lambda _: None,
    )

    assert notifier.send("a@example.com", "s", "b") is False


def test_a_run_reference_is_a_deep_link_when_the_app_url_is_known():
    notifier = _notifier([], base_url="https://agora.example.com/")

    assert notifier.run_reference("run-1") == "https://agora.example.com/#run/run-1"


def test_a_run_reference_falls_back_to_the_bare_id():
    assert _notifier([]).run_reference("run-1") == "run-1"


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "1 min"), (30, "1 min"), (600, "10 min"), (3600, "1 h"), (5400, "1 h 30 min"), (-5, "1 min")],
)
def test_durations_read_the_way_a_person_would_say_them(seconds, expected):
    assert format_duration(seconds) == expected
