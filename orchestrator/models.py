from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReasoningEffort = Literal["low", "medium", "high"]

# How the judge produces its analysis. Jev shapes return calibrated probabilities;
# ``llm`` is the generative fallback that Fusion-style deliberation started from.
# ``probe`` does no deliberation at all - it only reports what the answers lacked.
JudgeShape = Literal["hybrid", "matrix", "verify", "probe", "llm"]
# ``unsupported`` has no narrative counterpart: it is a claim the extractor proposed
# that no panel answer backs. Kept because it measures the extraction pass instead of
# the panel.
ClaimRole = Literal[
    "consensus",
    "contradiction",
    "partial_coverage",
    "unique_insight",
    "blind_spot",
    "unsupported",
]

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
    # None follows config. Worth overriding per call because the right shape is a
    # property of the question, not of the installation: `probe` for one that turns
    # on a missing fact, `hybrid` for one the models will genuinely differ on.
    judge_shape: JudgeShape | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None
    timeout_s: float | None = Field(default=None, gt=0)
    # None follows config; False keeps one call's panel blind to the conversation.
    use_conversation: bool | None = None
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


class ClaimSupport(BaseModel):
    """How strongly one panel member's answer backs one claim."""

    model_config = ConfigDict(extra="forbid")
    id: str
    support: float = Field(ge=0, le=1)


class CalibratedClaim(BaseModel):
    """One claim Jev was asked about, with the numbers it answered.

    ``role`` and ``index`` point back into the narrative field this claim produced or
    verified (``consensus[3]``, ``contradictions[0]``, ...), so an author can join the
    prose to its evidence without matching on claim text.
    """

    model_config = ConfigDict(extra="forbid")
    role: ClaimRole
    index: int = Field(ge=0)
    claim: str
    support: list[ClaimSupport] = Field(default_factory=list)
    contested: float | None = Field(default=None, ge=0, le=1)
    # Probability the finding is borne out by the panel. Kept separate from
    # ``standing`` because they are on different scales and mean different things:
    # ``holds`` is a probability, ``standing`` an expectation over a three-level rubric.
    holds: float | None = Field(default=None, ge=0, le=1)
    standing: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class PairAgreement(BaseModel):
    """Probability that two panel answers reach the same conclusion."""

    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=2, max_length=2)
    agreement: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class AnswerProfile(BaseModel):
    """Rubric readings for one panel answer. Each is an expectation over an ordered
    rubric, so it lands between levels."""

    model_config = ConfigDict(extra="forbid")
    id: str
    hedging: float | None = None
    scope: float | None = None
    distinctive: float | None = Field(default=None, ge=0, le=1)
    # P(this answer says outright that it lacked information it needed). Naming *what*
    # is missing takes a generative pass, but noticing that an answer said so does not.
    flagged_gap: float | None = Field(default=None, ge=0, le=1)


class Outlier(BaseModel):
    """Which answer the panel least resembles, and how sure Jev is of that."""

    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[str, float] = Field(default_factory=dict)


class Calibration(BaseModel):
    """The judge's numeric findings.

    Which blocks are populated depends on ``shape``: ``hybrid`` and ``verify`` fill
    ``claims``; ``matrix`` fills ``agreement``, ``per_answer``, ``outlier`` and
    ``panel_agreement``. ``llm`` produces no calibration at all, and the field is then
    ``None`` on the analysis instead of an empty block, so "not measured" and
    "measured as nothing" stay distinguishable.
    """

    model_config = ConfigDict(extra="forbid")
    shape: JudgeShape
    model: str = ""
    provider: str = ""
    claims: list[CalibratedClaim] = Field(default_factory=list)
    agreement: list[PairAgreement] = Field(default_factory=list)
    per_answer: list[AnswerProfile] = Field(default_factory=list)
    outlier: Outlier | None = None
    panel_agreement: float | None = None
    questions_asked: int = 0
    calls: int = 0


class NeedsEvidence(BaseModel):
    """Something the panel said it was missing, and how much it mattered.

    Distinct from ``blind_spots``, which is "nobody addressed this". This is "nobody
    *could* address this, because the information was not in front of them" - the only
    one of the two a caller can act on, by fetching it and asking again.
    """

    model_config = ConfigDict(extra="forbid")
    item: str
    # P(the answers genuinely lacked this instead of merely omitting it). ``None`` when
    # a generative judge asserted the gap instead of Jev measuring it - an asserted gap
    # is still worth acting on, but the absence of a number says which it is.
    lacked: float | None = Field(default=None, ge=0, le=1)
    # P(having it would change the answer). Low means fetching it is not worth a rerun.
    would_change: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consensus: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    partial_coverage: list[PartialCoverage] = Field(default_factory=list)
    unique_insights: list[UniqueInsight] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    needs_evidence: list[NeedsEvidence] = Field(default_factory=list)
    confidence_notes: str = ""
    calibration: Calibration | None = None

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
