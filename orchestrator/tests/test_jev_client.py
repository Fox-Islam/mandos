"""The Jev wire format, its retries, and its refusal to raise into the batch."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from orchestrator.jev import JevClient, choice, noul, score
from orchestrator.jev.questions import (
    qname,
    qparts,
    read_choice,
    read_confidence,
    read_noul,
    read_probabilities,
    read_score,
)

URL = "https://api.typesafe.ai/v1/systemone"


def _body(answers: dict, **extra) -> dict:
    return {"model": "jev-latest", "answers": answers, **extra}


async def _client(**kw) -> JevClient:
    return JevClient(http_client=httpx.AsyncClient(), api_key="k", **kw)


@respx.mock
async def test_posts_state_model_and_questions_and_reads_answers():
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=_body(
                {"agree": {"noul": 0.9, "confidence": 0.8}},
                usage={"input_tokens": 120, "output_tokens": 8, "cost": 0.0004},
                id="gen-1",
            ),
        )
    )
    client = await _client()
    result = await client.ask(
        {"question": "q"}, {"agree": noul("Do they agree?")}, deadline=_soon()
    )

    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer k"
    payload = __import__("json").loads(sent.content)
    # The endpoint rejects a non-string state with a 400, so structure is JSON-encoded.
    assert payload["state"] == '{\n  "question": "q"\n}'
    assert payload["model"] == "jev-latest"
    assert payload["questions"]["agree"]["type"] == "noul"

    assert result.status == "ok"
    assert read_noul(result.answers["agree"]) == 0.9
    assert result.usage.input == 120
    assert result.cost == 0.0004
    assert result.request_id == "gen-1"
    assert result.questions_asked == 1


@respx.mock
async def test_openrouter_provider_switches_host_path_and_nothing_else():
    route = respx.post("https://openrouter.ai/api/alpha/decisions").mock(
        return_value=httpx.Response(200, json=_body({"a": {"noul": 0.5}}))
    )
    client = JevClient(provider="openrouter", api_key="k", http_client=httpx.AsyncClient())
    result = await client.ask("state", {"a": noul("?")}, deadline=_soon())
    assert result.status == "ok"
    assert route.called


async def test_unknown_provider_is_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown Jev provider"):
        JevClient(provider="nope", http_client=httpx.AsyncClient())


async def test_missing_key_is_an_error_result_not_an_exception():
    client = JevClient(http_client=httpx.AsyncClient(), api_key=None)
    result = await client.ask("s", {"a": noul("?")}, deadline=_soon())
    assert result.status == "error"
    assert "TYPESAFE_API_KEY" in result.error


@respx.mock
async def test_retries_transient_status_then_succeeds():
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, text="slow down"),
            httpx.Response(200, json=_body({"a": {"noul": 0.6}})),
        ]
    )
    client = await _client(max_retries=2)
    result = await client.ask("s", {"a": noul("?")}, deadline=_soon())
    assert result.status == "ok"
    assert result.attempts == 2


@respx.mock
async def test_auth_failure_is_terminal_and_body_is_truncated():
    respx.post(URL).mock(return_value=httpx.Response(401, text="x" * 500))
    client = await _client(max_retries=2)
    result = await client.ask("s", {"a": noul("?")}, deadline=_soon())
    assert result.status == "error"
    assert result.error.startswith("HTTP 401")
    assert len(result.error) < 260
    assert result.attempts == 1


@respx.mock
async def test_answerless_response_is_an_error_not_a_crash():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"model": "jev-latest"}))
    client = await _client()
    result = await client.ask("s", {"a": noul("?")}, deadline=_soon())
    assert result.status == "error"
    assert "no answers" in result.error


@respx.mock
async def test_expired_deadline_short_circuits_without_calling():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_body({})))
    client = await _client()
    result = await client.ask("s", {"a": noul("?")}, deadline=time.monotonic() - 1)
    assert result.status == "error"
    assert "deadline" in result.error
    assert not route.called


async def test_no_questions_is_an_error_result():
    client = await _client()
    result = await client.ask("s", {}, deadline=_soon())
    assert result.status == "error"


@respx.mock
async def test_a_string_state_is_sent_unchanged():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_body({"a": {"noul": 0.5}})))
    client = await _client()
    await client.ask("just text", {"a": noul("?")}, deadline=_soon())
    assert __import__("json").loads(route.calls[0].request.content)["state"] == "just text"


def test_question_builders_match_the_wire_shape():
    """The endpoint validates each criteria shape separately and 400s on a mismatch:
    a noul wants a record keyed true/false, a choice wants a record, a score wants an
    array. Verified against the live API."""
    assert noul("q") == {"type": "noul", "instructions": "q"}
    assert noul("q", yes="y", no="n")["criteria"] == {"true": "y", "false": "n"}
    assert choice("q", ["a", "b"])["criteria"] == {"a": "", "b": ""}
    assert choice("q", {"a": "first"})["criteria"] == {"a": "first"}
    assert score("q", ["low", "high"])["criteria"] == ["low", "high"]
    with pytest.raises(ValueError, match="at least two levels"):
        score("q", ["only"])


def test_question_names_round_trip_even_with_underscores():
    name = qname("support", 3, "deep_seek")
    assert qparts(name) == ["support", "3", "deep_seek"]


def test_readers_fall_back_rather_than_raise():
    assert read_noul(None) == 0.5
    assert read_noul({"noul": 5}) == 1.0
    assert read_score(None) == 0.0
    assert read_confidence({"confidence": "high"}) is None
    assert read_probabilities({"probabilities": {"a": 0.5, "b": "x"}}) == {"a": 0.5}
    assert read_choice({"choice": "Position 4"}, ["3", "4"]) == "4"
    assert read_choice({"choice": "  A  "}, ["a", "b"]) == "a"
    assert read_choice({"choice": "zzz"}, ["a"], fallback="a") == "a"


def _soon() -> float:
    return time.monotonic() + 30
