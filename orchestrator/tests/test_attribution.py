"""OpenRouter app attribution: who the spend is labelled as, and who gets told."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from orchestrator.attribution import attribution_headers, is_openrouter
from orchestrator.jev import JevClient, noul
from orchestrator.models import ChatRequest
from orchestrator.providers.openai_compatible import OpenAiCompatibleProvider
from orchestrator.settings import ProviderDescriptor

CHAT_BODY = {
    "id": "x",
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


def _descriptor(**kw) -> ProviderDescriptor:
    return ProviderDescriptor(id="p", base_url="https://openrouter.ai/api/v1", model="m", **kw)


async def _send(descriptor: ProviderDescriptor):
    async with httpx.AsyncClient() as client:
        provider = OpenAiCompatibleProvider(descriptor, client)
        await provider.complete(ChatRequest(user="q"), deadline=time.monotonic() + 30)


def test_openrouter_is_recognised_by_kind_and_by_host():
    assert is_openrouter("https://gateway.internal/v1", kind="openrouter")
    assert is_openrouter("https://openrouter.ai/api/v1")
    assert is_openrouter("https://api.openrouter.ai/v1")
    assert not is_openrouter("https://api.deepseek.com/v1")
    assert not is_openrouter("http://localhost:8000/v1")


def test_nothing_is_sent_to_endpoints_that_did_not_ask():
    """A referer identifying the caller has no business going to every host."""
    assert attribution_headers("https://api.deepseek.com/v1", "openai") == {}


@respx.mock
async def test_an_openrouter_call_is_labelled_mandos():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_BODY)
    )
    await _send(_descriptor(kind="openrouter"))

    headers = route.calls[0].request.headers
    assert headers["x-title"] == "Mandos"
    assert headers["http-referer"] == "https://github.com/Fox-Islam/mandos"


@respx.mock
async def test_a_local_provider_is_not_labelled():
    route = respx.post("http://localhost:8000/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_BODY)
    )
    await _send(ProviderDescriptor(id="local", base_url="http://localhost:8000/v1", model="m"))
    headers = route.calls[0].request.headers
    assert "x-title" not in headers
    assert "http-referer" not in headers


@respx.mock
async def test_a_provider_header_overrides_the_default():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_BODY)
    )
    await _send(_descriptor(kind="openrouter", headers={"X-Title": "My Council"}))
    assert route.calls[0].request.headers["x-title"] == "My Council"


@respx.mock
async def test_the_environment_renames_or_suppresses_it(monkeypatch):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_BODY)
    )
    monkeypatch.setenv("MANDOS_APP_TITLE", "Council of Elrond")
    monkeypatch.setenv("MANDOS_APP_URL", "")
    await _send(_descriptor(kind="openrouter"))

    headers = route.calls[0].request.headers
    assert headers["x-title"] == "Council of Elrond"
    assert "http-referer" not in headers


@respx.mock
async def test_the_jev_judge_is_labelled_on_openrouter_only():
    decisions = respx.post("https://openrouter.ai/api/alpha/decisions").mock(
        return_value=httpx.Response(200, json={"model": "jev", "answers": {"a": {"noul": 0.5}}})
    )
    systemone = respx.post("https://api.typesafe.ai/v1/systemone").mock(
        return_value=httpx.Response(200, json={"model": "jev", "answers": {"a": {"noul": 0.5}}})
    )
    async with httpx.AsyncClient() as http:
        for provider in ("openrouter", "typesafe"):
            client = JevClient(provider=provider, api_key="k", http_client=http)
            await client.ask("s", {"a": noul("?")}, deadline=time.monotonic() + 30)

    assert decisions.calls[0].request.headers["x-title"] == "Mandos"
    assert "x-title" not in systemone.calls[0].request.headers


def test_an_empty_title_suppresses_only_that_header(monkeypatch):
    monkeypatch.setenv("MANDOS_APP_TITLE", "")
    headers = attribution_headers("https://openrouter.ai/api/v1")
    assert "X-Title" not in headers
    assert headers["HTTP-Referer"] == "https://github.com/Fox-Islam/mandos"


def test_both_empty_sends_nothing_at_all(monkeypatch):
    monkeypatch.setenv("MANDOS_APP_TITLE", "")
    monkeypatch.setenv("MANDOS_APP_URL", "")
    assert attribution_headers("https://openrouter.ai/api/v1") == {}


@respx.mock
async def test_provider_executed_tools_are_passed_through():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_BODY)
    )
    await _send(
        _descriptor(
            kind="openrouter",
            tools=[{"type": "openrouter:web_search"}, {"type": "openrouter:web_fetch"}],
            max_tool_calls=4,
        )
    )
    body = __import__("json").loads(route.calls[0].request.content)
    assert [t["type"] for t in body["tools"]] == [
        "openrouter:web_search",
        "openrouter:web_fetch",
    ]
    assert body["max_tool_calls"] == 4


@respx.mock
async def test_a_panel_member_with_no_tools_sends_no_tools_key():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_BODY)
    )
    await _send(_descriptor(kind="openrouter"))
    assert "tools" not in __import__("json").loads(route.calls[0].request.content)


@respx.mock
async def test_an_unexecuted_tool_call_is_a_recorded_error_not_an_empty_answer():
    import time as _time

    from orchestrator.models import ChatRequest as _ChatRequest

    body = {
        "id": "x",
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None},
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with httpx.AsyncClient() as client:
        provider = OpenAiCompatibleProvider(
            _descriptor(kind="openrouter", tools=[{"type": "openrouter:web_search"}]), client
        )
        result = await provider.complete(_ChatRequest(user="q"), deadline=_time.monotonic() + 30)
    assert result.status == "error"
    assert "ran no tool" in result.error


def test_a_client_executed_function_tool_is_refused_at_config_time():
    from orchestrator.settings import Defaults, MandosConfig

    with pytest.raises(ValueError, match="client-executed 'function' tool"):
        MandosConfig(
            defaults=Defaults(),
            providers=[
                ProviderDescriptor(
                    id="p",
                    base_url="https://openrouter.ai/api/v1",
                    model="m",
                    tools=[{"type": "function", "function": {"name": "read_file"}}],
                )
            ],
        )
