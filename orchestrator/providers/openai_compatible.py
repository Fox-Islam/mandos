from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from orchestrator.attribution import attribution_headers
from orchestrator.http import parse_retry_after
from orchestrator.models import ChatRequest, ChatResult, TokenUsage
from orchestrator.settings import ProviderDescriptor

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})
_RETRY_JITTER_MILLISECONDS = 100


def _retry_jitter_seconds() -> float:
    return secrets.randbelow(_RETRY_JITTER_MILLISECONDS + 1) / 1000


@dataclass(frozen=True)
class _AttemptOutcome:
    """Result of one HTTP attempt: a terminal ``result`` or a retryable ``error``."""

    result: ChatResult | None = None
    error: str = ""
    # Seconds the provider asked us to wait, when it said so.
    retry_after: float | None = None


class OpenAiCompatibleProvider:
    """The single provider kind: an OpenAI-compatible ``/chat/completions`` call.

    Delegates the request + response parsing to the maintained ``openai`` async
    client, sharing the panel's pooled ``httpx.AsyncClient``. The SDK's own retries
    are disabled (``max_retries=0``) so this loop remains the single owner of the
    overall-deadline budget (plan §7, §8.3): it retries the transient statuses in
    ``_RETRYABLE_STATUSES`` (408/409/425/429) plus all ``>=500`` and
    connection/timeout errors with exponential backoff + jitter, treats every other
    4xx (auth/validation) as terminal, and records every failure as a structured
    error rather than raising into the batch.
    """

    def __init__(self, descriptor: ProviderDescriptor, http_client: httpx.AsyncClient):
        self.descriptor = descriptor
        self.client = http_client
        self.id = descriptor.id
        self.model = descriptor.model
        self.kind = descriptor.kind

    def _client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self.descriptor.base_url,
            api_key=self.descriptor.api_key or "none",
            max_retries=0,
            # Attribution first so a provider's own headers can override it.
            default_headers={
                **attribution_headers(self.descriptor.base_url, self.descriptor.kind),
                **self.descriptor.headers,
            },
            http_client=self.client,
        )

    async def complete(self, request: ChatRequest, *, deadline: float) -> ChatResult:
        started = time.perf_counter()
        client = self._client()
        kwargs = self._build_kwargs(request)

        max_attempts = self.descriptor.max_retries + 1
        last_error = "unknown error"
        made = 0
        for attempt in range(max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = "overall deadline exceeded"
                break
            timeout = min(self.descriptor.timeout_s, remaining)
            made += 1
            outcome = await self._attempt(client, kwargs, timeout, started, made)
            if outcome.result is not None:
                return outcome.result
            last_error = outcome.error
            if attempt + 1 < max_attempts and await self._backoff(
                attempt, deadline, outcome.retry_after
            ):
                continue
            return self._err(last_error, started, attempts=made)
        return self._err(last_error, started, attempts=made)

    def _build_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.descriptor.model,
            "messages": self._build_messages(request),
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        extra: dict[str, Any] = {}
        if request.reasoning_effort:
            extra["reasoning_effort"] = request.reasoning_effort
        if self.descriptor.tools:
            # Provider-executed only (config rejects the rest), so there is no tool
            # loop here: the provider runs them and returns a finished message.
            kwargs["tools"] = list(self.descriptor.tools)
            if self.descriptor.max_tool_calls is not None:
                extra["max_tool_calls"] = self.descriptor.max_tool_calls
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    def _build_messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        if request.messages is not None:
            return [message.model_dump() for message in request.messages]
        return [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]

    async def _attempt(
        self,
        client: AsyncOpenAI,
        kwargs: dict[str, Any],
        request_timeout: float,
        started: float,
        attempts: int,
    ) -> _AttemptOutcome:
        """One HTTP attempt. Returns a terminal result, or a retryable error string."""
        try:
            completion = await client.chat.completions.create(**kwargs, timeout=request_timeout)
        except APIStatusError as exc:
            message = self._status_error_message(exc)
            if exc.status_code in _RETRYABLE_STATUSES or exc.status_code >= 500:
                return _AttemptOutcome(error=message, retry_after=self._retry_after(exc))
            return _AttemptOutcome(result=self._err(message, started, attempts=attempts))
        except APIConnectionError as exc:
            return _AttemptOutcome(error=str(exc) or type(exc).__name__)
        except Exception as exc:  # noqa: BLE001
            return _AttemptOutcome(
                result=self._err(str(exc) or type(exc).__name__, started, attempts=attempts)
            )
        return _AttemptOutcome(result=self._success_result(completion, started, attempts))

    @staticmethod
    def _retry_after(exc: APIStatusError) -> float | None:
        try:
            return parse_retry_after(exc.response.headers)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _status_error_message(exc: APIStatusError) -> str:
        detail = exc.message
        try:
            detail = exc.response.text[:200]
        except Exception:  # noqa: BLE001
            pass
        return f"HTTP {exc.status_code}: {detail}"

    def _success_result(self, completion: Any, started: float, attempts: int = 1) -> ChatResult:
        choice = completion.choices[0] if completion.choices else None
        answer = choice.message if choice else None
        usage = completion.usage
        text = (answer.content or "") if answer else ""
        finish_reason = choice.finish_reason if choice else None
        if finish_reason == "tool_calls" and not text:
            # The model asked us to run something. Config forbids client-executed
            # tools, so this means the endpoint did not execute one it advertised --
            # a recorded error, not a silent empty answer.
            return self._err(
                "provider returned tool_calls but ran no tool; only provider-executed "
                "tools are supported",
                started,
                attempts=attempts,
            )
        return ChatResult(
            provider_id=self.id,
            model=completion.model or self.descriptor.model,
            text=text,
            finish_reason=finish_reason,
            usage=TokenUsage(
                input=usage.prompt_tokens if usage else 0,
                output=usage.completion_tokens if usage else 0,
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
        )

    async def _backoff(
        self, attempt: int, deadline: float, retry_after: float | None = None
    ) -> bool:
        """Sleep before the next attempt, bounded by the deadline.

        A provider's own ``Retry-After`` wins over exponential backoff: it knows when
        it will be ready and we are guessing. Returns False when there is no time left
        to retry.
        """
        wait = retry_after if retry_after is not None else min(2.0, 0.2 * (2**attempt))
        wait += _retry_jitter_seconds()
        if time.monotonic() + wait >= deadline:
            return False
        await asyncio.sleep(wait)
        return True

    def _err(self, error: str, started: float | None = None, *, attempts: int = 1) -> ChatResult:
        latency = int((time.perf_counter() - started) * 1000) if started else 0
        return ChatResult(
            provider_id=self.id,
            model=self.descriptor.model,
            status="error",
            error=error,
            latency_ms=latency,
            attempts=attempts,
        )
