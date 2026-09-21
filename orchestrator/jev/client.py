"""Async client for Jev, TypeSafe's System One decision endpoint.

TypeSafe's official ``typesafe-sdk`` is not a dependency: this client
issues its one POST over the pooled ``httpx`` client the rest of the pipeline uses, so
the judge call reuses a warm connection instead of opening its own pool.
``phox/typesafe-sdk-php`` was the reference for the wire format. This is the minimum the
pipeline needs: one POST of
``{state, model, questions}``, deadline-aware retries, and **every failure recorded as
data, not raised** - a judge that throws would take the raw panel answers down
with it, which the golden rules forbid.

Both providers serve the same request and answer bodies; the provider only decides the
host, the path and which environment variable holds the key.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from orchestrator.attribution import attribution_headers
from orchestrator.http import parse_retry_after
from orchestrator.models import TokenUsage

# Retried for the same reasons the chat provider retries them: transient by nature and
# safe to repeat, because a decision call has no side effects.
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})
_RETRY_JITTER_MILLISECONDS = 100

PROVIDERS: dict[str, dict[str, str]] = {
    "typesafe": {
        "label": "TypeSafe",
        "base_url": "https://api.typesafe.ai",
        "path": "/v1/systemone",
        "api_key_env": "TYPESAFE_API_KEY",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai",
        "path": "/api/alpha/decisions",
        "api_key_env": "OPENROUTER_API_KEY",
    },
}

DEFAULT_MODEL = "jev-latest"


def _retry_jitter_seconds() -> float:
    return secrets.randbelow(_RETRY_JITTER_MILLISECONDS + 1) / 1000


@dataclass
class JevResult:
    """One System One call. ``status != "ok"`` means ``answers`` is empty and
    ``error`` explains why; callers degrade, they do not raise."""

    answers: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    provider: str = ""
    request_id: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost: float | None = None
    questions_asked: int = 0
    # Seconds the provider asked us to wait before retrying, when it said so.
    retry_after: float | None = None
    latency_ms: int = 0
    attempts: int = 1
    status: str = "ok"
    error: str | None = None


class JevClient:
    """A configured Jev endpoint.

    Shares the pipeline's pooled ``httpx.AsyncClient``: the jevsort measurements put a
    fresh TCP+TLS handshake at ~350ms against ~90ms of actual model time, so connection
    reuse is the single biggest lever on how a Jev judge feels.
    """

    def __init__(
        self,
        *,
        provider: str = "typesafe",
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        http_client: httpx.AsyncClient,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        headers: dict[str, str] | None = None,
    ):
        if provider not in PROVIDERS:
            raise ValueError(
                f"unknown Jev provider {provider!r}; expected one of {sorted(PROVIDERS)}"
            )
        self.provider = provider
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.client = http_client
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.headers = dict(headers or {})
        self._base_url = (base_url or PROVIDERS[provider]["base_url"]).rstrip("/")

    @property
    def url(self) -> str:
        return self._base_url + PROVIDERS[self.provider]["path"]

    async def ask(
        self,
        state: Any,
        questions: dict[str, dict[str, Any]],
        *,
        deadline: float,
    ) -> JevResult:
        """Ask every question in one call.

        Question count is nearly free - the jevsort runs measured one choice at
        ~100ms against twelve scores at ~78ms of server time, because Jev answers a
        batch in parallel. So judge shapes should build the widest call they can rather
        than looping; the cost of a call is its round trip.
        """
        if not questions:
            return JevResult(status="error", error="no questions to ask", provider=self.provider)
        if not self.api_key:
            return JevResult(
                status="error",
                error=f"no Jev API key; set {PROVIDERS[self.provider]['api_key_env']}",
                provider=self.provider,
            )

        started = time.perf_counter()
        body = {"state": _encode_state(state), "model": self.model, "questions": questions}
        last_error = "unknown error"
        made = 0
        for attempt in range(self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = "overall deadline exceeded"
                break
            made += 1
            outcome, retryable = await self._attempt(body, min(self.timeout_s, remaining))
            if not retryable:
                outcome.latency_ms = int((time.perf_counter() - started) * 1000)
                outcome.attempts = made
                outcome.questions_asked = len(questions)
                return outcome
            last_error = outcome.error or last_error
            if attempt < self.max_retries and await self._backoff(
                attempt, deadline, outcome.retry_after
            ):
                continue
            break
        return JevResult(
            status="error",
            error=last_error,
            provider=self.provider,
            model=self.model,
            attempts=made,
            questions_asked=len(questions),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _attempt(
        self, body: dict[str, Any], request_timeout: float
    ) -> tuple[JevResult, bool]:
        """One HTTP attempt. Returns the result and whether it is worth retrying."""
        try:
            response = await self.client.post(
                self.url,
                json=body,
                timeout=request_timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    **attribution_headers(self._base_url, self.provider),
                    **self.headers,
                },
            )
        except httpx.TimeoutException as exc:
            return JevResult(status="error", error=f"timeout: {exc}", provider=self.provider), True
        except httpx.HTTPError as exc:
            message = str(exc) or type(exc).__name__
            return JevResult(status="error", error=message, provider=self.provider), True

        if response.status_code >= 400:
            # Truncated: an error body can echo the state we sent, which may be large
            # and is not ours to log in full.
            detail = response.text[:200]
            result = JevResult(
                status="error",
                error=f"HTTP {response.status_code}: {detail}",
                provider=self.provider,
                request_id=_request_id_header(response),
                retry_after=parse_retry_after(response.headers),
            )
            retryable = response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500
            return result, retryable

        try:
            data = response.json()
        except ValueError:
            return (
                JevResult(
                    status="error",
                    error="response was not JSON",
                    provider=self.provider,
                ),
                False,
            )
        return self._parse(data, response), False

    def _parse(self, data: Any, response: httpx.Response) -> JevResult:
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            return JevResult(
                status="error",
                error="response carried no answers",
                provider=self.provider,
            )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = usage.get("cost")
        return JevResult(
            answers=data["answers"],
            model=str(data.get("model") or self.model),
            provider=str(data.get("provider") or PROVIDERS[self.provider]["label"]),
            request_id=str(data.get("id")) if data.get("id") else _request_id_header(response),
            usage=TokenUsage(
                input=int(usage.get("input_tokens") or 0),
                output=int(usage.get("output_tokens") or 0),
            ),
            cost=float(cost) if isinstance(cost, int | float) else None,
        )

    async def _backoff(
        self, attempt: int, deadline: float, retry_after: float | None = None
    ) -> bool:
        """Sleep before the next attempt. A provider's own ``Retry-After`` wins over
        our guess, since it knows when it will be ready and we do not."""
        wait = retry_after if retry_after is not None else min(2.0, 0.2 * (2**attempt))
        wait += _retry_jitter_seconds()
        if time.monotonic() + wait >= deadline:
            return False
        await asyncio.sleep(wait)
        return True


def _encode_state(state: Any) -> str:
    """The endpoint requires ``state`` to be a string; a raw object is rejected with a
    400 ``invalid_union``.

    Callers still build structured state - keeping the answers keyed by real provider
    id is what lets a question name one - so the structure is JSON-encoded here, which
    is what the PHP SDK does with an array state. Indented because the value is read by
    a language model, and the indentation costs a rounding error against answers that
    run to paragraphs.
    """
    if isinstance(state, str):
        return state
    return json.dumps(state, indent=2, ensure_ascii=False, default=str)


def _request_id_header(response: httpx.Response) -> str | None:
    """TypeSafe and OpenRouter name the request id differently; either is worth
    keeping, because it is what a support ticket about a bad answer needs."""
    return response.headers.get("x-typesafe-request-id") or response.headers.get("x-generation-id")
