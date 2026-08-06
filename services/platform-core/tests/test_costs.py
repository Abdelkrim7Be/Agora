from __future__ import annotations

from pathlib import Path

import pytest

from platform_core.costs import CostLedger, parse_timestamp, period_start

PRICES = {"acme-large": {"in": 1.0, "out": 2.0}}


def _ledger(tmp_path: Path, *, backend="json", database_url="", user="alice", instance="inst"):
    return CostLedger(
        resolve_path=lambda path: Path(path) if path else tmp_path / "costs.jsonl",
        backend=lambda: backend,
        database_url=lambda: database_url,
        connect=lambda: pytest.fail("json backend must not open a connection"),
        load_prices=lambda: PRICES,
        normalize_user_id=lambda v: (v or "").strip().lower(),
        normalize_agent_instance_id=lambda v: (v or "").strip().lower(),
        current_user_id=lambda: user,
        current_agent_instance_id=lambda: instance,
    )


def _entry(ledger, **overrides):
    return ledger.record({
        "run_id": "run-1",
        "node": "triage",
        "model": "acme-large",
        "input_tokens": 100,
        "output_tokens": 10,
        "cost_eur": 0.5,
        **overrides,
    })


def test_a_recorded_entry_inherits_the_ambient_tenant(tmp_path):
    ledger = _ledger(tmp_path)

    entry = _entry(ledger)

    assert entry["user_id"] == "alice"
    assert entry["agent_instance_id"] == "inst"


def test_total_tokens_are_derived_when_not_supplied(tmp_path):
    assert _entry(_ledger(tmp_path))["total_tokens"] == 110


def test_every_entry_gets_an_id_so_a_retry_cannot_double_book(tmp_path):
    ledger = _ledger(tmp_path)

    assert _entry(ledger)["event_id"] != _entry(ledger)["event_id"]


def test_listing_is_scoped_to_one_tenant(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger)
    _entry(ledger, user_id="mallory")

    assert [e["user_id"] for e in ledger.list(user_id="alice")] == ["alice"]


def test_an_explicit_path_selects_the_file_backend_whatever_is_configured(tmp_path):
    ledger = _ledger(tmp_path, backend="postgres", database_url="postgresql://example")

    assert ledger.selected_backend(tmp_path / "other.jsonl") == "json"


def test_postgres_without_a_database_url_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _ledger(tmp_path, backend="postgres").selected_backend()


def test_an_unknown_backend_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="AGENT_COST_BACKEND"):
        _ledger(tmp_path, backend="cassette").selected_backend()


def test_setup_does_not_touch_the_database(tmp_path):
    # Schema is owned by migrations; connecting here would fail the injected
    # connect above.
    _ledger(tmp_path, backend="postgres", database_url="postgresql://example").setup()


def test_a_summary_splits_the_same_spend_by_model_and_by_node(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger, node="triage", cost_eur=0.25)
    _entry(ledger, node="draft", cost_eur=0.75)

    summary = ledger.summarize("session", "alice", "inst")

    assert summary["totals"]["cost_eur"] == 1.0
    assert summary["by_node"]["draft"]["cost_eur"] == 0.75
    assert summary["by_model"]["acme-large"]["calls"] == 2


def test_a_summary_names_models_it_could_not_price(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger, model="mystery-model")

    assert ledger.summarize("session", "alice", "inst")["unpriced_models"] == ["mystery-model"]


def test_a_priced_model_is_not_reported_as_unpriced(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger)

    assert ledger.summarize("session", "alice", "inst")["unpriced_models"] == []


def test_totals_are_scoped_to_one_run_and_node(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger, node="triage", input_tokens=10)
    _entry(ledger, node="draft", input_tokens=99)

    assert ledger.totals_for_run_node("run-1", "triage")["input_tokens"] == 10


def test_totals_for_no_run_are_zero_without_reading_anything(tmp_path):
    assert _ledger(tmp_path).totals_for_run_node("", "triage")["total_tokens"] == 0


def test_erasure_removes_only_the_named_runs(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger, run_id="run-1")
    _entry(ledger, run_id="run-2")

    assert ledger.count_for_runs(["run-1"]) == 1
    assert ledger.delete_for_runs(["run-1"]) == 1
    assert [e["run_id"] for e in ledger.list()] == ["run-2"]


def test_erasing_nothing_touches_nothing(tmp_path):
    ledger = _ledger(tmp_path)
    _entry(ledger)

    assert ledger.delete_for_runs([]) == 0
    assert len(ledger.list()) == 1


def test_a_session_summary_has_no_start_bound():
    assert period_start("session") is None


@pytest.mark.parametrize("period", ["day", "month"])
def test_bounded_periods_start_at_midnight_utc(period):
    start = period_start(period)

    assert (start.hour, start.minute, start.second) == (0, 0, 0)


def test_an_unknown_period_is_refused():
    with pytest.raises(ValueError, match="period"):
        period_start("fortnight")


def test_a_naive_timestamp_is_read_as_utc():
    assert parse_timestamp("2026-08-06T12:00:00") == parse_timestamp("2026-08-06T12:00:00Z")
