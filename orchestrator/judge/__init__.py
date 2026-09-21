"""The deliberation judge, in four shapes.

Three of them put the judging to **Jev**, TypeSafe's System One decision model, which
answers named questions with calibrated probabilities instead of writing prose. The
fourth is the generative analyst this project started from, kept as the fallback.

``hybrid``
    An LLM proposes claims, Jev decides them. The full narrative schema, with every
    finding carrying the numbers it was derived from.
``matrix``
    Jev alone. Pairwise agreement, per-answer rubrics and an outlier — no prose, and
    no generative model anywhere in the loop.
``verify``
    The generative analyst writes, then Jev grades what it wrote. Every finding keeps
    its place and gains a probability that it holds.
``llm``
    The generative analyst alone. Fusion-style analysis, uncalibrated.

Whatever shape runs, the rules from the pipeline's golden rules hold: the judge
analyses and never authors, a failure is recorded rather than raised, and the raw panel
answers come back regardless so the host can always write the final answer.

**Degradation.** A Jev shape that cannot produce an analysis falls back to ``llm`` when
a judge-role chat provider is configured, recording both the original failure and
``fallback_from``. ``matrix`` is the only shape that needs no chat provider at all, so
it is also the only one that still works when the judge role is unconfigured.
"""

from __future__ import annotations

from orchestrator.jev import JevClient
from orchestrator.judge import hybrid, matrix, verify
from orchestrator.judge.extract import extract_claims
from orchestrator.judge.jev_common import DEFAULT_QUESTIONS_PER_CALL, build_state
from orchestrator.judge.llm import (
    ANALYSIS_SYSTEM,
    build_judge_user,
    render_answers,
    run_deliberation_judge,
)
from orchestrator.judge.outcome import JudgeOutcome
from orchestrator.models import JudgeShape, RawAnswer

__all__ = [
    "ANALYSIS_SYSTEM",
    "DEFAULT_QUESTIONS_PER_CALL",
    "JudgeOutcome",
    "build_judge_user",
    "build_state",
    "render_answers",
    "run_deliberation_judge",
    "run_judge",
]


async def run_judge(
    question: str,
    answers: list[RawAnswer],
    *,
    shape: JudgeShape,
    deadline: float,
    context: str | None = None,
    jev_client: JevClient | None = None,
    analysis_provider=None,
    max_tokens: int | None = None,
    batch_size: int = DEFAULT_QUESTIONS_PER_CALL,
) -> JudgeOutcome:
    """Run ``shape``, falling back to the generative judge if it cannot deliver.

    Never raises. ``outcome.analysis is None`` means no analysis could be produced and
    ``outcome.analysis_error`` says why; the caller returns the raw answers anyway.
    """
    outcome = JudgeOutcome(shape=shape)
    if not answers:
        outcome.analysis_error = "no successful panel answers to analyse"
        return outcome

    error = await _run_shape(
        shape,
        question,
        answers,
        outcome,
        deadline=deadline,
        context=context,
        jev_client=jev_client,
        analysis_provider=analysis_provider,
        max_tokens=max_tokens,
        batch_size=batch_size,
    )
    if error:
        outcome.analysis_error = error

    if outcome.analysis is not None:
        # ``verify`` reaching here with an error means the analyst's findings stand but
        # were never graded, which is the ``llm`` shape by another name. Say so.
        if error and shape == "verify":
            outcome.shape, outcome.fallback_from = "llm", "verify"
        return outcome

    if shape == "llm" or analysis_provider is None:
        return outcome

    fallback = await run_deliberation_judge(
        question,
        answers,
        analysis_provider,
        deadline=deadline,
        max_tokens=max_tokens,
        context=context,
    )
    outcome.analysis = fallback.analysis
    outcome.analysis_text = fallback.analysis_text
    outcome.usages.extend(fallback.usages)
    outcome.shape, outcome.fallback_from = "llm", shape
    if fallback.analysis_error:
        outcome.analysis_error = f"{error}; fallback judge also failed: {fallback.analysis_error}"
    return outcome


async def _run_shape(
    shape: JudgeShape,
    question: str,
    answers: list[RawAnswer],
    outcome: JudgeOutcome,
    *,
    deadline: float,
    context: str | None,
    jev_client: JevClient | None,
    analysis_provider,
    max_tokens: int | None,
    batch_size: int,
) -> str | None:
    if shape == "llm":
        result = await run_deliberation_judge(
            question,
            answers,
            analysis_provider,
            deadline=deadline,
            max_tokens=max_tokens,
            context=context,
        )
        outcome.analysis = result.analysis
        outcome.analysis_text = result.analysis_text
        outcome.usages.extend(result.usages)
        return result.analysis_error

    if jev_client is None:
        return f"shape {shape!r} needs a Jev judge; none is configured"

    if shape == "matrix":
        return await matrix.run(
            question,
            answers,
            jev_client,
            deadline=deadline,
            context=context,
            outcome=outcome,
            batch_size=batch_size,
        )

    if shape == "hybrid":
        extraction = await extract_claims(
            question,
            answers,
            analysis_provider,
            deadline=deadline,
            max_tokens=max_tokens,
            context=context,
        )
        if extraction.provider_id and extraction.usage is not None:
            outcome.usages.append((extraction.provider_id, extraction.usage))
        outcome.analysis_text = extraction.text
        if extraction.error:
            return f"claim extraction failed: {extraction.error}"
        return await hybrid.run(
            question,
            answers,
            extraction,
            jev_client,
            deadline=deadline,
            context=context,
            outcome=outcome,
            batch_size=batch_size,
        )

    # verify: the analyst writes first, then Jev grades what it wrote.
    analyst = await run_deliberation_judge(
        question,
        answers,
        analysis_provider,
        deadline=deadline,
        max_tokens=max_tokens,
        context=context,
    )
    outcome.analysis = analyst.analysis
    outcome.analysis_text = analyst.analysis_text
    outcome.usages.extend(analyst.usages)
    if analyst.analysis is None:
        return analyst.analysis_error or "the analyst produced no analysis to verify"
    return await verify.run(
        question,
        answers,
        jev_client,
        deadline=deadline,
        context=context,
        outcome=outcome,
        batch_size=batch_size,
    )
