from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from platform_core.runs import RunRegistry, parse_timestamp

COLUMNS = (
    "run_id",
    "user_id",
    "agent_instance_id",
    "status",
    "pending_action",
    "subject",
    "assignee",
    "created_at",
    "decision",
    "decision_at",
    "updated_at",
)


def _registry(tmp_path: Path, *, backend="json", database_url="", instance="inst"):
    return RunRegistry(
        table="agent_runs",
        columns=COLUMNS,
        json_columns=("pending_action",),
        overwrite_columns=("assignee",),
        max_runs=3,
        resolve_path=lambda path: Path(path) if path else tmp_path / "runs.json",
        backend=lambda: backend,
        database_url=lambda: database_url,
        connect=lambda: pytest.fail("json backend must not open a connection"),
        normalize_user_id=lambda v: (v or "").strip().lower(),
        normalize_agent_instance_id=lambda v: (v or "").strip().lower(),
        current_agent_instance_id=lambda: instance,
    )


def _record(**overrides):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "run_id": "run-1",
        "user_id": "alice",
        "agent_instance_id": "inst",
        "status": "pending_approval",
        "pending_action": None,
        "subject": "Hello",
        "assignee": None,
        "created_at": now,
        "decision": None,
        "decision_at": None,
        "updated_at": now,
        **overrides,
    }


# -- generated SQL -----------------------------------------------------------
# Every statement is built from one column list, which is what stops a column
# from being written by the upsert and then dropped by claim's RETURNING.


def test_every_declared_column_is_returned_by_the_upsert(tmp_path):
    sql = _registry(tmp_path)._upsert_sql()

    for column in COLUMNS:
        assert f"%({column})s" in sql
    assert sql.endswith("RETURNING " + ", ".join(COLUMNS))


def test_reads_and_updates_share_one_column_list(tmp_path):
    # This is the property that keeps a column from being written by the upsert
    # and then silently dropped by claim's RETURNING.
    assert _registry(tmp_path)._column_list == ", ".join(COLUMNS)


def test_the_run_id_is_not_reassigned_on_conflict(tmp_path):
    assert "run_id = EXCLUDED.run_id" not in _registry(tmp_path)._upsert_sql()


def test_the_first_write_owns_created_at(tmp_path):
    assert (
        "created_at = COALESCE(agent_runs.created_at, EXCLUDED.created_at)"
        in _registry(tmp_path)._upsert_sql()
    )


def test_a_cleared_field_overwrites_while_an_absent_one_is_kept(tmp_path):
    sql = _registry(tmp_path)._upsert_sql()

    assert "assignee = EXCLUDED.assignee" in sql
    assert "subject = COALESCE(EXCLUDED.subject, agent_runs.subject)" in sql


# -- json backend behaviour --------------------------------------------------


def test_a_second_upsert_updates_rather_than_duplicates(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record())
    registry.upsert(_record(status="sent"))

    runs = registry.list()
    assert len(runs) == 1
    assert runs[0]["status"] == "sent"


def test_an_absent_field_does_not_erase_what_was_stored(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(subject="Original"))
    registry.upsert(_record(subject=None))

    assert registry.get("run-1")["subject"] == "Original"


def test_a_cleared_assignee_is_actually_cleared(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(assignee="bob"))
    registry.upsert(_record(assignee=None))

    assert registry.get("run-1")["assignee"] is None


def test_the_local_index_is_capped_so_a_dev_poller_cannot_grow_it_forever(tmp_path):
    registry = _registry(tmp_path)
    for index in range(5):
        registry.upsert(_record(run_id=f"run-{index}", updated_at=f"2026-08-0{index + 1}T00:00:00+00:00"))

    assert len(registry.list()) == 3


def test_listing_is_scoped_to_one_tenant(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(run_id="mine"))
    registry.upsert(_record(run_id="theirs", user_id="mallory"))

    assert [r["run_id"] for r in registry.list(user_id="alice")] == ["mine"]


def test_a_run_from_another_instance_is_not_readable(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(agent_instance_id="other"))

    assert registry.get("run-1", agent_instance_id="inst") is None


def test_only_the_first_claimant_of_a_run_wins(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(status="pending_approval"))

    first = registry.claim("run-1", "pending_approval", "claimed")
    second = registry.claim("run-1", "pending_approval", "claimed")

    assert first["status"] == "claimed"
    assert second is None


def test_claiming_a_run_belonging_to_another_instance_fails(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(agent_instance_id="other"))

    assert registry.claim("run-1", "pending_approval", "claimed") is None


def test_assigning_sets_the_assignee_and_bumps_the_timestamp(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(updated_at="2020-01-01T00:00:00+00:00"))

    assigned = registry.assign("run-1", "bob")

    assert assigned["assignee"] == "bob"
    assert assigned["updated_at"] > "2020-01-01T00:00:00+00:00"


def test_assigning_an_unknown_run_returns_nothing(tmp_path):
    assert _registry(tmp_path).assign("nope", "bob") is None


def test_runs_before_a_cutoff_are_returned_oldest_first(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(run_id="old", created_at="2020-01-01T00:00:00+00:00"))
    registry.upsert(_record(run_id="older", created_at="2019-01-01T00:00:00+00:00"))
    registry.upsert(_record(run_id="recent", created_at="2099-01-01T00:00:00+00:00"))

    cutoff = datetime(2021, 1, 1, tzinfo=timezone.utc)
    assert [r["run_id"] for r in registry.list_before(cutoff)] == ["older", "old"]


def test_deletion_removes_only_the_named_runs(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record(run_id="doomed"))
    registry.upsert(_record(run_id="spared"))

    assert registry.delete(["doomed"]) == 1
    assert [r["run_id"] for r in registry.list()] == ["spared"]


def test_deleting_nothing_touches_nothing(tmp_path):
    registry = _registry(tmp_path)
    registry.upsert(_record())

    assert registry.delete([]) == 0
    assert len(registry.list()) == 1


# -- backend selection -------------------------------------------------------


def test_an_explicit_path_selects_the_file_backend(tmp_path):
    registry = _registry(tmp_path, backend="postgres", database_url="postgresql://example")

    assert registry.selected_backend(tmp_path / "other.json") == "json"


def test_postgres_without_a_database_url_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _registry(tmp_path, backend="postgres").selected_backend()


def test_an_unknown_backend_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="AGENT_RUN_REGISTRY_BACKEND"):
        _registry(tmp_path, backend="parchment").selected_backend()


def test_a_naive_timestamp_is_read_as_utc():
    assert parse_timestamp("2026-08-06T12:00:00") == parse_timestamp("2026-08-06T12:00:00Z")


def test_a_missing_timestamp_is_not_an_error():
    assert parse_timestamp(None) is None
