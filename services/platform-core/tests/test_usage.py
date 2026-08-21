from __future__ import annotations

from types import SimpleNamespace

import pytest

from platform_core.usage import UsageCallback, model_from_response, usage_from_response


def _response(*, usage_metadata=None, response_metadata=None, llm_output=None):
    message = SimpleNamespace(
        usage_metadata=usage_metadata, response_metadata=response_metadata or {}
    )
    return SimpleNamespace(
        generations=[[SimpleNamespace(message=message)]], llm_output=llm_output
    )


def test_usage_metadata_is_preferred_when_the_provider_supplies_it():
    response = _response(
        usage_metadata={"input_tokens": 10, "output_tokens": 3},
        llm_output={"token_usage": {"prompt_tokens": 999, "completion_tokens": 999}},
    )

    assert usage_from_response(response)[:2] == (10, 3)


def test_openai_style_token_usage_is_read_when_there_is_no_usage_metadata():
    response = _response(llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 2}})

    assert usage_from_response(response)[:2] == (7, 2)


@pytest.mark.parametrize(
    "details",
    [{"cached_tokens": 5}, {"cache_read": 5}, {"cache_read_tokens": 5}],
)
def test_a_cached_prefix_is_found_under_any_of_the_provider_names(details):
    response = _response(
        usage_metadata={"input_tokens": 10, "output_tokens": 1, "input_token_details": details}
    )

    assert usage_from_response(response)[2] == 5


def test_a_response_carrying_no_usage_at_all_reports_zeros():
    assert usage_from_response(_response()) == (0, 0, 0)


def test_an_unnamed_model_is_recorded_as_unknown_rather_than_dropped():
    assert model_from_response(_response()) == "unknown"


def test_the_model_name_is_taken_from_response_metadata():
    assert model_from_response(_response(response_metadata={"model_name": "acme"})) == "acme"


def _callback(booked, *, enabled=True, **kwargs):
    return UsageCallback(
        record=booked.append,
        compute_cost=lambda model, tin, tout, cached_input_tokens=0: 1.5,
        enabled=lambda: enabled,
        user_id="alice",
        agent_instance_id="inst",
        **kwargs,
    )


def test_a_model_call_is_booked_against_the_node_that_ran():
    booked = []

    _callback(booked, run_id="run-1", node="triage").on_llm_end(
        _response(usage_metadata={"input_tokens": 10, "output_tokens": 3})
    )

    assert booked[0]["node"] == "triage"
    assert booked[0]["total_tokens"] == 13
    assert booked[0]["cost_eur"] == 1.5


def test_per_call_metadata_overrides_the_node_the_callback_was_built_with():
    booked = []

    _callback(booked, run_id="run-1", node="triage").on_llm_end(
        _response(usage_metadata={"input_tokens": 1, "output_tokens": 1}),
        metadata={"node": "draft", "run_id": "run-2"},
    )

    assert (booked[0]["node"], booked[0]["run_id"]) == ("draft", "run-2")


def test_nothing_is_booked_when_cost_tracking_is_off():
    booked = []

    _callback(booked, enabled=False).on_llm_end(
        _response(usage_metadata={"input_tokens": 10, "output_tokens": 3})
    )

    assert booked == []


def test_a_response_with_no_tokens_is_not_booked():
    booked = []

    _callback(booked).on_llm_end(_response())

    assert booked == []
