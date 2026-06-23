"""Provider resilience: deadline, retry policy, backoff/jitter, catch-all."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from orchestrator.fakes import FakeChatProvider
from orchestrator.models import ChatRequest
from orchestrator.panel import _call_provider
from orchestrator.providers.openai_compatible import OpenAiCompatibleProvider
from orchestrator.settings import ProviderDescriptor

URL = "https://example.test/v1/chat/completions"
_OK = {"choices": [{"message": {"content": "ok"}}]}


def _provider(client, **kw):
    desc = ProviderDescriptor(id="p", base_url="https://example.test/v1", model="m", **kw)
    return OpenAiCompatibleProvider(desc, client)


async def _always_retry(attempt, deadline):  # noqa: ARG001
    return True


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "status,retryable",
    [
        (408, True),
        (409, True),
        (425, True),
        (429, True),
        (500, True),
        (503, True),
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (422, False),
    ],
)
async def test_status_retry_policy(monkeypatch, status, retryable):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(status, text="transient")
        return httpx.Response(200, json=_OK)

    respx.post(URL).mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        provider = _provider(client, max_retries=1)
        monkeypatch.setattr(provider, "_backoff", _always_retry)
        result = await provider.complete(
            ChatRequest(system="s", user="u"), deadline=time.monotonic() + 30
        )

    if retryable:
        assert result.status == "ok"
        assert calls["n"] == 2
    else:
        assert result.status == "error"
        assert f"HTTP {status}" in (result.error or "")
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_complete_short_circuits_on_expired_deadline():
    async with httpx.AsyncClient() as client:
        provider = _provider(client, max_retries=2)
        result = await provider.complete(
            ChatRequest(system="s", user="u"), deadline=time.monotonic() - 1
        )
    assert result.status == "error"
    assert "overall deadline exceeded" in (result.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_retries_then_recovers(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=_OK)

    respx.post(URL).mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        provider = _provider(client, max_retries=1)
        monkeypatch.setattr(provider, "_backoff", _always_retry)
        result = await provider.complete(
            ChatRequest(system="s", user="u"), deadline=time.monotonic() + 30
        )
    assert result.status == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_exhausts_max_retries(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    respx.post(URL).mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        provider = _provider(client, max_retries=2)
        monkeypatch.setattr(provider, "_backoff", _always_retry)
        result = await provider.complete(
            ChatRequest(system="s", user="u"), deadline=time.monotonic() + 30
        )
    assert result.status == "error"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_backoff_jitter_range_and_deadline_bound(monkeypatch):
    sleeps: list[float] = []
    jitter_calls = 0

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    def fake_jitter():
        nonlocal jitter_calls
        jitter_calls += 1
        return 0.05

    monkeypatch.setattr("orchestrator.providers.openai_compatible.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "orchestrator.providers.openai_compatible._retry_jitter_seconds", fake_jitter
    )

    async with httpx.AsyncClient() as client:
        provider = _provider(client)
        assert await provider._backoff(0, deadline=time.monotonic() + 100) is True
        assert await provider._backoff(0, deadline=time.monotonic() + 0.001) is False

    assert jitter_calls == 2
    assert sleeps == [pytest.approx(0.25)]


@pytest.mark.asyncio
async def test_call_provider_records_raised_exception():
    class _Boom:
        id = "boom"
        model = "m"

        async def complete(self, request, *, deadline):  # noqa: ARG002
            raise RuntimeError("kaboom")

    result = await _call_provider(_Boom(), ChatRequest(system="s", user="u"), time.monotonic() + 30)
    assert result.status == "error"
    assert "kaboom" in (result.error or "")


@pytest.mark.asyncio
async def test_call_provider_deadline_short_circuit_skips_call():
    fake = FakeChatProvider("p")
    result = await _call_provider(fake, ChatRequest(system="s", user="u"), time.monotonic() - 1)
    assert result.status == "error"
    assert "overall deadline exceeded" in (result.error or "")
    assert fake.requests == []
