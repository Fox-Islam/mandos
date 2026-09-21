"""The deliberation judge, in five shapes.

Four of them put the judging to **Jev**, TypeSafe's System One decision model, which
answers named questions with calibrated probabilities instead of writing prose. The
fifth is the generative analyst this project started from, kept as the fallback.

``hybrid``
    An LLM proposes claims, Jev decides them. The full narrative schema, with every
    finding carrying the numbers it was derived from.
``matrix``
    Jev alone. Pairwise agreement, per-answer rubrics and an outlier - no prose, and
    no generative model anywhere in the loop.
``verify``
    The generative analyst writes, then Jev grades what it wrote. Every finding keeps
    its place and gains a probability that it holds.
``probe``
    No deliberation at all. One cheap generative call lists what the answers did not
    have, Jev scores each for whether it was genuinely missing and whether having it
    would change the answer, and nothing else is measured. The shape to use with a
    panel of one, or whenever the question turns on a missing fact instead of on a
    disagreement.
``llm``
    The generative analyst alone. Fusion-style analysis, uncalibrated.

Whatever shape runs, the rules from the pipeline's golden rules hold: the judge
analyses and never authors, a failure is recorded, not raised, and the raw panel
answers come back regardless so the host can always write the final answer.

**Degradation.** Each shape has an ordered fallback chain, tried until one produces an
analysis; ``fallback_from`` records what was asked for. ``hybrid`` falls to ``matrix``
before ``llm``, because most of what stops it - an analyst that is unreachable, or that
proposed nothing to adjudicate because the panel agreed - says nothing about whether
Jev is reachable, and dropping straight to a generative judge throws away the
calibration for no reason. Only when nothing in the chain delivers does the analysis
come back ``None``.

The chain does not branch on *which* provider failed. Any of them can fail
transiently, and one bad call is not evidence that the next will be: ``matrix`` sends a
much smaller batch than ``hybrid``, so it is a real recovery path even when Jev has
just refused a wide one. Skipping it would trade a cheap retry for a permanent
downgrade on the strength of a single error.
``matrix`` is the only shape that needs no chat provider at all, so it is also the only
one that still works when the judge role is unconfigured.
"""

from __future__ import annotations

from orchestrator.jev import JevClient
from orchestrator.judge import hybrid, matrix, probe, verify
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

# Ordered fallbacks per shape. ``hybrid`` stays inside Jev first: an
# extraction that proposed nothing is a statement about the analyst, not about Jev.
FALLBACKS: dict[JudgeShape, tuple[JudgeShape, ...]] = {
    "hybrid": ("matrix", "llm"),
    "matrix": ("llm",),
    "verify": ("llm",),
    # `probe` asks a deliberate question and answers nothing else, so falling back to a
    # shape that deliberates would return something the caller did not ask for.
    "probe": (),
    "llm": (),
}

__all__ = [
    "FALLBACKS",
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

    errors: list[str] = []
    for attempt in (shape, *FALLBACKS.get(shape, ())):
        if attempt == "llm" and analysis_provider is None:
            continue
        error = await _run_shape(
            attempt,
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
            errors.append(f"{attempt}: {error}")

        if outcome.analysis is not None:
            # ``verify`` reaching here with an error means the analyst's findings stand
            # but were never graded, which is the ``llm`` shape by another name.
            outcome.stamp_calibration()
            if error and attempt == "verify":
                outcome.shape, outcome.fallback_from = "llm", shape
                outcome.analysis_error = "; ".join(errors)
                return outcome
            outcome.shape = attempt
            outcome.fallback_from = None if attempt == shape else shape
            if errors:
                outcome.analysis_error = "; ".join(errors)
            return outcome

    outcome.shape = shape
    outcome.analysis_error = "; ".join(errors) or "no judge could produce an analysis"
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

    if shape == "probe":
        extraction = await extract_claims(
            question,
            answers,
            analysis_provider,
            deadline=deadline,
            max_tokens=max_tokens,
            context=context,
            evidence_only=True,
        )
        if extraction.provider_id and extraction.usage is not None:
            outcome.usages.append((extraction.provider_id, extraction.usage))
        outcome.analysis_text = extraction.text
        if extraction.error:
            return f"evidence extraction failed: {extraction.error}"
        return await probe.run(
            question,
            answers,
            extraction,
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
