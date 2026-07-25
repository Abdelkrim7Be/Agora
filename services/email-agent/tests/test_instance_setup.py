from __future__ import annotations

import pytest

import src.instance_setup as instance_setup
from src.config import settings
from src.instance_setup import (
    FATAL_STEPS,
    SETUP_STEPS,
    SetupContext,
    SkipStep,
    claim_next_step,
    complete_step,
    fail_step,
    get_setup,
    requeue_stale_steps,
    run_step,
    skip_step,
    start_setup,
)


@pytest.fixture(autouse=True)
def _json_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "instance_setup_path", str(tmp_path / "instance_setup.json"))


def test_start_setup_creates_all_steps():
    result = start_setup("user-a", "instance-a")

    assert result["status"] == "created"
    assert [s["step_key"] for s in result["steps"]] == list(SETUP_STEPS)
    assert all(s["status"] == "pending" for s in result["steps"])
    assert result["progress"] == {"done": 0, "total": len(SETUP_STEPS), "percent": 0}


def test_start_setup_is_idempotent():
    first = start_setup("user-a", "instance-a")
    second = start_setup("user-a", "instance-a")

    assert first["started_at"] == second["started_at"]
    assert len(second["steps"]) == len(SETUP_STEPS)


def test_start_setup_force_resets_failed_steps_only():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-1")
    fail_step(step["setup_id"], step["step_key"], "boom")

    retried = start_setup("user-a", "instance-a", force=True)

    first_step = retried["steps"][0]
    assert first_step["status"] == "pending"
    assert first_step["error"] is None
    assert retried["status"] == "created"


def test_claim_next_step_respects_order():
    start_setup("user-a", "instance-a")

    first_claim = claim_next_step("worker-1")
    assert first_claim["step_key"] == SETUP_STEPS[0]

    # Step 2 must not be claimable while step 1 is still 'running'.
    second_claim = claim_next_step("worker-2")
    assert second_claim is None

    complete_step(first_claim["setup_id"], first_claim["step_key"], detail={})
    second_claim = claim_next_step("worker-2")
    assert second_claim["step_key"] == SETUP_STEPS[1]


def test_claim_next_step_skip_locked_never_double_claims():
    start_setup("user-a", "instance-a")
    start_setup("user-b", "instance-b")

    first = claim_next_step("worker-1")
    second = claim_next_step("worker-2")

    assert first is not None and second is not None
    assert (first["user_id"], first["agent_instance_id"]) != (second["user_id"], second["agent_instance_id"])


def test_fatal_step_failure_fails_setup():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-1")
    assert step["step_key"] in FATAL_STEPS

    fail_step(step["setup_id"], step["step_key"], "invalid_grant: token revoked")

    result = get_setup("user-a", "instance-a")
    assert result["status"] == "failed"
    assert result["error"]
    remaining = [s for s in result["steps"] if s["step_key"] != step["step_key"]]
    assert all(s["status"] == "pending" for s in remaining)
    # claim_next_step must not offer more work for a terminated setup.
    assert claim_next_step("worker-2") is None


def test_nonfatal_step_failure_continues():
    start_setup("user-a", "instance-a")
    for key in ("verify_provider", "fetch_recent", "import_contacts"):
        step = claim_next_step("worker-1")
        assert step["step_key"] == key
        complete_step(step["setup_id"], step["step_key"], detail={})

    step = claim_next_step("worker-1")
    assert step["step_key"] == "learn_style"
    fail_step(step["setup_id"], step["step_key"], "some transient error")

    result = get_setup("user-a", "instance-a")
    assert result["status"] != "failed"
    learn_style_entry = next(s for s in result["steps"] if s["step_key"] == "learn_style")
    assert learn_style_entry["status"] == "failed"

    next_step = claim_next_step("worker-1")
    assert next_step["step_key"] == "suggest_persona"


def test_skip_condition_marks_skipped():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-1")
    skip_step(step["setup_id"], step["step_key"], "style learning disabled")

    result = get_setup("user-a", "instance-a")
    assert result["steps"][0]["status"] == "skipped"
    assert result["steps"][0]["detail"]["reason"] == "style learning disabled"


def test_requeue_stale_steps():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-dead")

    user_id, instance_id = step["user_id"], step["agent_instance_id"]
    record = instance_setup._json_get_record(user_id, instance_id)
    for s in record["steps"]:
        if s["step_key"] == step["step_key"]:
            s["claimed_at"] = "2000-01-01T00:00:00+00:00"
    instance_setup._json_save_record(user_id, instance_id, record)

    requeued = requeue_stale_steps(stale_seconds=1, max_attempts=5)

    assert requeued == 1
    result = get_setup(user_id, instance_id)
    assert result["steps"][0]["status"] == "pending"


def test_requeue_stale_steps_abandons_past_max_attempts():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-dead")
    user_id, instance_id = step["user_id"], step["agent_instance_id"]
    record = instance_setup._json_get_record(user_id, instance_id)
    for s in record["steps"]:
        if s["step_key"] == step["step_key"]:
            s["claimed_at"] = "2000-01-01T00:00:00+00:00"
            s["attempts"] = 5
    instance_setup._json_save_record(user_id, instance_id, record)

    requeue_stale_steps(stale_seconds=1, max_attempts=5)

    result = get_setup(user_id, instance_id)
    assert result["steps"][0]["status"] == "abandoned"


async def test_finalize_marks_ready_and_notifies(monkeypatch):
    notified = []
    monkeypatch.setitem(
        instance_setup.STEP_HANDLERS,
        "finalize",
        instance_setup._step_finalize,
    )

    class _FakeNotificationStore:
        @staticmethod
        def create_notification(**kwargs):
            notified.append(kwargs)
            return {}

    monkeypatch.setattr("src.notification_store.create_notification", _FakeNotificationStore.create_notification)

    start_setup("user-a", "instance-a")
    for key in SETUP_STEPS[:-1]:
        step = claim_next_step("worker-1")
        assert step["step_key"] == key
        complete_step(step["setup_id"], step["step_key"], detail={})

    step = claim_next_step("worker-1")
    assert step["step_key"] == "finalize"
    context = SetupContext(user_id="user-a", agent_instance_id="instance-a")
    await run_step(step, context)

    result = get_setup("user-a", "instance-a")
    assert result["status"] == "ready"
    assert result["finished_at"] is not None
    assert len(notified) == 1
    assert notified[0]["notification_type"] == "setup_completed"


def test_progress_percent():
    start_setup("user-a", "instance-a")
    for _ in range(4):
        step = claim_next_step("worker-1")
        complete_step(step["setup_id"], step["step_key"], detail={})

    result = get_setup("user-a", "instance-a")
    assert result["progress"]["done"] == 4
    assert result["progress"]["total"] == len(SETUP_STEPS)
    assert result["progress"]["percent"] == round(4 * 100 / len(SETUP_STEPS))


def test_error_message_is_public_safe():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-1")
    fail_step(step["setup_id"], step["step_key"], "invalid_grant: token has been expired or revoked")

    result = get_setup("user-a", "instance-a")
    assert "invalid_grant" not in result["error"]


def test_tenant_isolation():
    start_setup("user-a", "instance-a")
    start_setup("user-a", "instance-b")

    result = get_setup("user-a", "instance-b")
    assert result["status"] == "created"
    # instance-a's steps must never leak into instance-b's record.
    other = get_setup("user-a", "instance-a")
    assert other is not result


def test_get_setup_not_started_for_unknown_instance():
    result = get_setup("nobody", "no-instance")
    assert result["status"] == "not_started"
    assert result["progress"] == {"done": 0, "total": len(SETUP_STEPS), "percent": 0}


async def test_run_step_skip_exception_marks_skipped():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-1")

    async def _skip(context):
        raise SkipStep("not applicable")

    original = instance_setup.STEP_HANDLERS[step["step_key"]]
    instance_setup.STEP_HANDLERS[step["step_key"]] = _skip
    try:
        context = SetupContext(user_id="user-a", agent_instance_id="instance-a")
        await run_step(step, context)
    finally:
        instance_setup.STEP_HANDLERS[step["step_key"]] = original

    result = get_setup("user-a", "instance-a")
    assert result["steps"][0]["status"] == "skipped"


async def test_run_step_generic_exception_marks_failed_not_ready():
    start_setup("user-a", "instance-a")
    step = claim_next_step("worker-1")

    async def _boom(context):
        raise RuntimeError("gmail unavailable")

    original = instance_setup.STEP_HANDLERS[step["step_key"]]
    instance_setup.STEP_HANDLERS[step["step_key"]] = _boom
    try:
        context = SetupContext(user_id="user-a", agent_instance_id="instance-a")
        await run_step(step, context)
    finally:
        instance_setup.STEP_HANDLERS[step["step_key"]] = original

    result = get_setup("user-a", "instance-a")
    assert result["status"] == "failed"
