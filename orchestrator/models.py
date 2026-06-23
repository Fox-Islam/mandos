from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReasoningEffort = Literal["low", "medium", "high"]

# Single source of truth for the session-id shape (sessions.py compiles this too).
THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

FailureKind = Literal[
    "all_panels_failed",
    "rate_limited",
    "insufficient_credits",
    "fusion_invocation_capped",
    "unexpected_error",
]


class TokenUsage(BaseModel):
    input: int = 0
    output: int = 0


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    system: str = ""
    user: str = ""
    messages: list[ChatMessage] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: ReasoningEffort | None = None


class ChatResult(BaseModel):
    provider_id: str
    model: str
    text: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    latency_ms: int = 0
    attempts: int = 1
    status: str = "ok"
    error: str | None = None


class DeliberateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)
    context: str | None = Field(default=None, max_length=200_000)
    thread_id: str | None = Field(default=None, pattern=THREAD_ID_PATTERN)
    prior_answer: str | None = Field(default=None, max_length=100_000)
    panel: list[str] | None = Field(default=None, min_length=1, max_length=8)
    preset: str | None = None
    analysis_model: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None
    timeout_s: float | None = Field(default=None, gt=0)
    depth: int = 0


class PanelAnswer(BaseModel):
    """Per-provider status metadata only.

    The full answer text lives in :class:`RawAnswer`; it is surfaced to the host
    unconditionally so the harness native model can author from the judge analysis
    and all provider evidence.
    """

    id: str
    model: str | None = None
    status: str
    latency_ms: int = 0
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    error: str | None = None


class RawAnswer(BaseModel):
    id: str
    model: str | None = None
    answer: str


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(default_factory=list)
    claim: str


class Contradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    positions: list[Position] = Field(default_factory=list)


class PartialCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(default_factory=list)
    point: str


class UniqueInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    insight: str


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consensus: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    partial_coverage: list[PartialCoverage] = Field(default_factory=list)
    unique_insights: list[UniqueInsight] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    confidence_notes: str = ""

    @model_validator(mode="after")
    def reject_empty_payload(self):
        if not self.model_fields_set:
            raise ValueError("analysis payload must include at least one analysis field")
        return self


class DeliberationResponse(BaseModel):
    question: str
    thread_id: str | None = None
    compacted: bool = False
    panel: list[PanelAnswer] = Field(default_factory=list)
    analysis: Analysis | None = None
    raw_answers: list[RawAnswer] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
