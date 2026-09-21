from __future__ import annotations

import json
import os
import tempfile

import httpx
import pytest
import respx
from pydantic import ValidationError

from orchestrator.json_utils import parse_lenient
from orchestrator.models import ChatMessage, ChatRequest
from orchestrator.providers.factory import build_provider
from orchestrator.providers.openai_compatible import OpenAiCompatibleProvider
from orchestrator.settings import ProviderDescriptor, load_config


def test_lenient_json_parser_handles_fences():
    assert parse_lenient('noise ```json\n{"a": 1}\n```') == {"a": 1}


def test_lenient_json_recovers_outer_brace_past_bad_fence():
    assert parse_lenient('prose {"real": 1} ```json\nnot json\n```') == {"real": 1}


def test_lenient_json_tries_each_fenced_block():
    text = '```\nnot json\n```\n```json\n{"ok": true}\n```'
    assert parse_lenient(text) == {"ok": True}


def test_build_provider_is_always_openai_compatible():
    for kind in ("openai", "openrouter"):
        desc = ProviderDescriptor(id="p", kind=kind, base_url="http://x/v1", model="m")
        assert isinstance(build_provider(desc, None), OpenAiCompatibleProvider)


def test_anthropic_kind_is_rejected():
    with pytest.raises(ValidationError):
        ProviderDescriptor(id="p", kind="anthropic", base_url="http://x/v1", model="m")


def test_config_validation_missing_secret():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(
            "providers:\n- id: p\n  base_url: http://x/v1\n  model: m\n  api_key_env: MISSING_KEY\n"
        )
    try:
        with pytest.raises(ValueError, match="MISSING_KEY"):
            load_config(f.name)
    finally:
        os.unlink(f.name)


@pytest.mark.asyncio
@respx.mock
async def test_openai_provider_success_and_4xx_and_retry():
    desc = ProviderDescriptor(id="p", base_url="https://example.test/v1", model="m", max_retries=1)
    async with httpx.AsyncClient() as client:
        provider = OpenAiCompatibleProvider(desc, client)
        route = respx.post("https://example.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "m",
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                },
            )
        )
        result = await provider.complete(ChatRequest(system="s", user="u"), deadline=9999999999)
        assert route.called
        assert result.status == "ok"
        assert result.text == "ok"
        assert result.usage.output == 2

        respx.post("https://example.test/v1/chat/completions").mock(
            return_value=httpx.Response(401, text="bad")
        )
        result = await provider.complete(ChatRequest(system="s", user="u"), deadline=9999999999)
        assert result.status == "error"
        assert "HTTP 401" in (result.error or "")

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, text="oops")
            return httpx.Response(200, json={"choices": [{"message": {"content": "retry ok"}}]})

        respx.post("https://example.test/v1/chat/completions").mock(side_effect=handler)
        result = await provider.complete(ChatRequest(system="s", user="u"), deadline=9999999999)
        assert result.status == "ok"
        assert result.text == "retry ok"
        assert calls["n"] == 2


@pytest.mark.asyncio
async def test_complete_never_raises_on_response_validation_error(monkeypatch):
    """A 2xx body that fails SDK validation is recorded terminally, not raised, with
    latency/model preserved, so invariant 6 no longer leans on the panel catch-all."""
    from openai import APIResponseValidationError

    desc = ProviderDescriptor(id="p", base_url="https://example.test/v1", model="m", max_retries=2)

    calls = {"n": 0}

    class _Boom:
        async def create(self, **kwargs):
            calls["n"] += 1
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(200, request=request, json={"bad": "shape"})
            raise APIResponseValidationError(response=response, body=None, message="bad shape")

    class _FakeClient:
        class chat:
            completions = _Boom()

    async with httpx.AsyncClient() as client:
        provider = OpenAiCompatibleProvider(desc, client)
        monkeypatch.setattr(provider, "_client", lambda: _FakeClient())
        result = await provider.complete(ChatRequest(system="s", user="u"), deadline=9999999999)

    assert result.status == "error"
    assert "bad shape" in (result.error or "")
    assert result.model == "m"
    assert calls["n"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_no_mandos_depth_header_is_sent():
    captured = {}

    def handler(request):
        captured["headers"] = {k.lower() for k in request.headers}
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    desc = ProviderDescriptor(id="p", base_url="https://example.test/v1", model="m")
    respx.post("https://example.test/v1/chat/completions").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        provider = OpenAiCompatibleProvider(desc, client)
        await provider.complete(ChatRequest(system="s", user="u"), deadline=9999999999)
    assert "x-mandos-depth" not in captured["headers"]


@pytest.mark.asyncio
@respx.mock
async def test_openai_provider_sends_messages_verbatim_when_present():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    desc = ProviderDescriptor(id="p", base_url="https://example.test/v1", model="m")
    respx.post("https://example.test/v1/chat/completions").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        provider = OpenAiCompatibleProvider(desc, client)
        await provider.complete(
            ChatRequest(
                messages=[
                    ChatMessage(role="system", content="sys"),
                    ChatMessage(role="user", content="old"),
                    ChatMessage(role="assistant", content="answer"),
                    ChatMessage(role="user", content="new"),
                ]
            ),
            deadline=9999999999,
        )

    assert captured["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "new"},
    ]
