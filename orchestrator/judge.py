from __future__ import annotations

from dataclasses import dataclass, field

from orchestrator.json_utils import parse_lenient
from orchestrator.models import Analysis, ChatRequest, RawAnswer, TokenUsage

ANALYSIS_SYSTEM = (
    "You are a deliberation judge. ANALYSE the panel answers; do not curate, merge, "
    "or write a final answer. Preserve contradictions (never smooth them into "
    "consensus), preserve minority-but-plausible claims, and keep every caveat, "
    "assumption, source-limit, or uncertainty that affects the answer. Attribute "
    "contradictions, partial_coverage, and unique_insights to the real provider ids "
    "you were given. Output STRICT JSON only, with this exact shape:\n"
    '{"consensus": ["..."], '
    '"contradictions": [{"topic": "...", "positions": '
    '[{"ids": ["provider-id"], "claim": "..."}]}], '
    '"partial_coverage": [{"ids": ["provider-id"], "point": "..."}], '
    '"unique_insights": [{"id": "provider-id", "insight": "..."}], '
    '"blind_spots": ["..."], "confidence_notes": "..."}'
)


@dataclass
class JudgeOutcome:
    analysis: Analysis | None = None
    analysis_error: str | None = None
    usages: list[tuple[str, TokenUsage]] = field(default_factory=list)
    analysis_text: str = ""


def render_answers(answers: list[RawAnswer]) -> str:
    parts = ["ANSWERS:"]
    for answer in answers:
        parts.append(f"\n[{answer.id}]\n{answer.answer}")
    return "\n".join(parts)


def build_judge_user(question: str, answers: list[RawAnswer]) -> str:
    """The judge's user-message content. Single source of truth so the advisory cost
    estimate counts exactly what the judge is sent (the question plus the answers)."""
    return f"QUESTION:\n{question}\n\n{render_answers(answers)}"


async def run_deliberation_judge(
    question: str,
    answers: list[RawAnswer],
    analysis_provider,
    *,
    deadline: float,
    max_tokens: int | None = None,
) -> JudgeOutcome:
    """Run the analysis judge at temperature 0.

    The judge sees real provider ids. If JSON parsing or schema validation fails,
    the caller still returns all raw answers and records ``meta.judge_error``.

    The judge shares the panel's single absolute ``deadline`` (no extension —
    invariant 1). Enforcement is provider-internal by contract: the provider checks
    the remaining budget each attempt and returns a graceful error result when it is
    exhausted, so a near-expired deadline degrades to ``meta.judge_error`` +
    ``analysis=None`` rather than running unbounded.
    """
    outcome = JudgeOutcome()
    if not answers:
        outcome.analysis_error = "no successful panel answers to analyse"
        return outcome

    if analysis_provider is None:
        outcome.analysis_error = "no analysis provider configured"
        return outcome

    analysis_result = await analysis_provider.complete(
        ChatRequest(
            system=ANALYSIS_SYSTEM,
            user=build_judge_user(question, answers),
            max_tokens=max_tokens,
            temperature=0,
        ),
        deadline=deadline,
    )
    outcome.usages.append((analysis_result.provider_id, analysis_result.usage))
    outcome.analysis_text = analysis_result.text
    if analysis_result.status != "ok":
        outcome.analysis_error = analysis_result.error or "analysis call failed"
        return outcome

    parsed = parse_lenient(analysis_result.text)
    if parsed is None:
        outcome.analysis_error = "analysis returned malformed JSON"
        return outcome

    try:
        outcome.analysis = Analysis.model_validate(parsed)
    except Exception as exc:
        outcome.analysis_error = f"analysis schema error: {exc}"
    return outcome
