"""Analysis judge: one API-side call over real provider ids."""

from __future__ import annotations

import time

import pytest

from orchestrator.fakes import FakeChatProvider
from orchestrator.judge import ANALYSIS_SYSTEM, run_deliberation_judge
from orchestrator.models import RawAnswer
from orchestrator.tests.helpers import ANALYSIS_JSON_IDS

DEADLINE = time.monotonic() + 60
ANSWERS = [RawAnswer(id="a", answer="answer a"), RawAnswer(id="b", answer="answer b")]


def test_analysis_system_prompt_forbids_authoring_and_anonymization():
    lowered = ANALYSIS_SYSTEM.lower()
    assert "do not curate, merge" in lowered
    assert "final answer" in lowered
    assert "anonym" not in lowered
    assert "a/b/c" not in lowered


@pytest.mark.asyncio
async def test_judge_runs_at_temperature_zero_with_analysis_system():
    provider = FakeChatProvider("ja", text=ANALYSIS_JSON_IDS)
    await run_deliberation_judge("q", ANSWERS, provider, deadline=DEADLINE)
    assert provider.requests[0].temperature == 0
    assert provider.requests[0].system == ANALYSIS_SYSTEM


@pytest.mark.asyncio
async def test_analysis_succeeds_with_real_provider_ids():
    provider = FakeChatProvider("ja", text=ANALYSIS_JSON_IDS)
    outcome = await run_deliberation_judge(
        "q",
        ANSWERS,
        provider,
        deadline=DEADLINE,
    )
    assert outcome.analysis_error is None
    assert outcome.analysis.consensus == ["agree on X"]
    assert outcome.analysis.unique_insights[0].id == "a"
    assert "[a]" in provider.requests[0].user
    assert "[b]" in provider.requests[0].user


@pytest.mark.asyncio
async def test_malformed_analysis_json_degrades():
    outcome = await run_deliberation_judge(
        "q",
        ANSWERS,
        FakeChatProvider("ja", text="totally not json"),
        deadline=DEADLINE,
    )
    assert outcome.analysis is None
    assert "malformed JSON" in outcome.analysis_error


@pytest.mark.asyncio
async def test_valid_json_wrong_shape_degrades_to_schema_error():
    bad_shape = '{"contradictions": [{"topic": "t", "positions": "oops"}]}'
    outcome = await run_deliberation_judge(
        "q",
        ANSWERS,
        FakeChatProvider("ja", text=bad_shape),
        deadline=DEADLINE,
    )
    assert outcome.analysis is None
    assert "schema error" in outcome.analysis_error
    assert outcome.usages


@pytest.mark.asyncio
async def test_extra_keys_in_analysis_degrade():
    extra = '{"consensus": ["x"], "final_answer": "smuggled"}'
    outcome = await run_deliberation_judge(
        "q",
        ANSWERS,
        FakeChatProvider("ja", text=extra),
        deadline=DEADLINE,
    )
    assert outcome.analysis is None
    assert "schema error" in outcome.analysis_error


@pytest.mark.asyncio
async def test_empty_analysis_json_degrades_to_schema_error():
    outcome = await run_deliberation_judge(
        "q",
        ANSWERS,
        FakeChatProvider("ja", text="{}"),
        deadline=DEADLINE,
    )
    assert outcome.analysis is None
    assert "schema error" in outcome.analysis_error


@pytest.mark.asyncio
async def test_missing_analysis_provider_degrades():
    outcome = await run_deliberation_judge(
        "q",
        ANSWERS,
        None,
        deadline=DEADLINE,
    )
    assert outcome.analysis is None
    assert outcome.analysis_error == "no analysis provider configured"
