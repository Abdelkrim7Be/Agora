from __future__ import annotations

import pytest

from src.prompts import agent_system_prompt, triage_system_prompt

# Prefix caching only pays off while the leading text is byte-identical across
# calls, so every prompt must run stable blocks before volatile ones. These
# tests pin that order: a reshuffle silently costs cache hits, not correctness,
# so nothing else would catch it.
ORDERINGS = [
    pytest.param(
        triage_system_prompt,
        ["{background}", "{category_section}", "{triage_instructions}"],
        id="triage",
    ),
    pytest.param(
        agent_system_prompt,
        [
            "{tools_prompt}",
            "{background}",
            "{reply_language}",
            "{response_preferences}",
            "{writing_style}",
            "{workflow_instructions_section}",
        ],
        id="agent",
    ),
]


@pytest.mark.parametrize("prompt,expected", ORDERINGS)
def test_placeholders_run_stable_before_volatile(prompt, expected):
    positions = [prompt.index(placeholder) for placeholder in expected]

    assert positions == sorted(positions), f"expected order: {expected}"


@pytest.mark.parametrize("prompt,expected", ORDERINGS)
def test_every_placeholder_is_accounted_for(prompt, expected):
    import re

    assert set(re.findall(r"\{[a-z_]+\}", prompt)) == set(expected)
