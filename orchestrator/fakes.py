from __future__ import annotations

import asyncio
import time

from orchestrator.models import ChatResult, TokenUsage


class FakeChatProvider:
    def __init__(
        self,
        id: str,
        text: str = "answer",
        delay: float = 0,
        error: str | None = None,
        model: str = "fake",
        usage: TokenUsage | None = None,
    ):
        self.id = id
        self.kind = "fake"
        self.text = text
        self.delay = delay
        self.error = error
        self.model = model
        self.usage = usage if usage is not None else TokenUsage(input=10, output=20)
        self.requests = []

    async def complete(self, request, *, deadline: float):
        del deadline
        self.requests.append(request)
        start = time.perf_counter()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            return ChatResult(
                provider_id=self.id,
                model=self.model,
                status="error",
                error=self.error,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        return ChatResult(
            provider_id=self.id,
            model=self.model,
            text=self.text,
            usage=self.usage,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
