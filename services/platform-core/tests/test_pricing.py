from __future__ import annotations

import pytest

from platform_core.pricing import (
    CACHED_PREFIX_MULTIPLIER,
    compute_cost,
    is_priced,
    resolve_price,
)

PRICES = {"acme-large": {"in": 1.0, "out": 2.0}, "mistral-small-latest": {"in": 1.0, "out": 2.0}}


def test_a_provider_prefix_falls_back_to_the_bare_model_name():
    assert resolve_price(PRICES, "openai:acme-large") == {"in": 1.0, "out": 2.0}


def test_an_unknown_model_has_no_price():
    assert resolve_price(PRICES, "who-knows") is None
    assert is_priced(PRICES, "who-knows") is False


def test_an_unpriced_model_books_at_zero_rather_than_raising():
    assert compute_cost(PRICES, "who-knows", 1_000_000, 1_000_000) == 0.0


def test_tokens_are_billed_per_million():
    assert compute_cost(PRICES, "acme-large", 1_000_000, 500_000) == 2.0


def test_a_cached_prefix_is_discounted_where_the_provider_offers_one():
    full = compute_cost(PRICES, "mistral-small-latest", 1_000_000, 0)
    cached = compute_cost(
        PRICES, "mistral-small-latest", 1_000_000, 0, cached_input_tokens=1_000_000
    )

    assert cached == pytest.approx(full * CACHED_PREFIX_MULTIPLIER)


def test_a_provider_without_prefix_caching_pays_full_price():
    full = compute_cost(PRICES, "acme-large", 1_000_000, 0)

    assert compute_cost(PRICES, "acme-large", 1_000_000, 0, cached_input_tokens=1_000_000) == full


def test_more_cached_tokens_than_input_cannot_produce_a_negative_bill():
    cost = compute_cost(PRICES, "acme-large", 100, 0, cached_input_tokens=10_000)

    assert cost >= 0.0
