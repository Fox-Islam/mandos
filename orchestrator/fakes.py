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


class FakeJevClient:
    """A deterministic stand-in for :class:`~orchestrator.jev.JevClient`.

    Answers every question in the shape that question asked for, the same way on every
    run, so a judge test asserts on the judge rather than on a model's mood. Scripted
    answers in ``answers`` win; anything unscripted is simulated — a yes at 0.75, the
    first option, rubric level 0 — which mirrors ``FakeTypeSafe`` in the PHP SDK.

    Simulated answers are the wrong default for a test whose assertion depends on the
    *negatives* (every claim looking supported at 0.75 makes everything consensus), so
    such tests script the whole reply.
    """

    def __init__(
        self,
        answers: dict | None = None,
        *,
        error: str | None = None,
        model: str = "jev-fake",
        cost: float | None = None,
        usage: TokenUsage | None = None,
    ):
        self.answers = answers or {}
        self.error = error
        self.model = model
        self.cost = cost
        self.usage = usage if usage is not None else TokenUsage(input=100, output=40)
        self.calls: list[dict] = []

    async def ask(self, state, questions, *, deadline):
        from orchestrator.jev import JevResult

        del deadline
        self.calls.append({"state": state, "questions": questions})
        if self.error:
            return JevResult(
                status="error",
                error=self.error,
                model=self.model,
                questions_asked=len(questions),
            )
        return JevResult(
            answers={name: self._answer(name, q) for name, q in questions.items()},
            model=self.model,
            provider="FakeJev",
            usage=self.usage,
            cost=self.cost,
            questions_asked=len(questions),
        )

    def _answer(self, name: str, question: dict):
        if name in self.answers:
            return self.answers[name]
        kind = question.get("type")
        if kind == "noul":
            return {"noul": 0.75, "confidence": 0.75}
        if kind == "choice":
            options = list(question.get("criteria") or {})
            first = options[0] if options else ""
            rest = 0.5 / max(1, len(options) - 1)
            return {
                "choice": first,
                "confidence": 0.5,
                "probabilities": {o: (0.5 if o == first else rest) for o in options},
            }
        return {"score": 0.0, "confidence": 0.5}
