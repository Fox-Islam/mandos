"""The matrix judge: Jev alone, no generative model anywhere in the loop.

Every other shape needs an LLM to write something before Jev can grade it. This one
does not, and that makes it the only judge that works with no chat provider configured
for the judge role at all - and the only one whose output contains no sentence a model
made up.

What it gives up is claim-level attribution: nothing here can say *what* two answers
disagree about, only how likely it is that they disagree. What it gives back is a
reading an author can act on immediately - which answers agree, which one is the
odd one out, which are hedging, which only answered part of the question - in one
round trip of about 90ms of model time.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from orchestrator.jev import (
    JevClient,
    choice,
    noul,
    qname,
    read_choice,
    read_confidence,
    read_noul,
    read_probabilities,
    read_score,
    score,
)
from orchestrator.judge.answer_profile import profile_questions, read_profiles
from orchestrator.judge.jev_common import DEFAULT_QUESTIONS_PER_CALL, ask_all, build_state
from orchestrator.judge.outcome import JudgeOutcome
from orchestrator.models import (
    Analysis,
    Calibration,
    Outlier,
    PairAgreement,
    RawAnswer,
)

PANEL_AGREEMENT_RUBRIC = [
    "The answers reach incompatible conclusions",
    "The answers broadly agree but differ on substance",
    "The answers reach the same conclusion",
]


def build_questions(answer_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Pairwise agreement, three readings per answer, and two panel-wide questions.

    An eight-member panel is 28 pairs plus 24 readings plus 2 - 54 questions, one call.
    """
    questions: dict[str, dict[str, Any]] = {}
    for left, right in combinations(answer_ids, 2):
        questions[qname("agree", left, right)] = noul(
            f"Do the answers from '{left}' and '{right}' reach the same conclusion?",
            yes="Someone acting on either answer would do the same thing",
            no="They point to different conclusions, or one rules out what the other advises",
        )
    questions.update(profile_questions(answer_ids))
    if len(answer_ids) > 1:
        questions["outlier"] = choice("Which answer is least like the others?", list(answer_ids))
        questions["panel_agreement"] = score(
            "How much do the panel answers agree with each other overall?",
            PANEL_AGREEMENT_RUBRIC,
        )
    return questions


async def run(
    question: str,
    answers: list[RawAnswer],
    client: JevClient,
    *,
    deadline: float,
    context: str | None = None,
    outcome: JudgeOutcome,
    batch_size: int = DEFAULT_QUESTIONS_PER_CALL,
) -> str | None:
    """Measure the panel with Jev and set ``outcome.analysis``.

    Returns an error string when nothing came back; ``None`` on success.
    """
    answer_ids = [answer.id for answer in answers]
    questions = build_questions(answer_ids)
    if not questions:
        return "a single-member panel has nothing to compare"

    state = build_state(question, answers, context)
    replies, error = await ask_all(
        client, state, questions, deadline=deadline, outcome=outcome, batch_size=batch_size
    )
    if not replies:
        return error or "Jev returned no answers"

    outcome.analysis = _assemble(answer_ids, replies, model=client.model)
    if error is not None:
        outcome.analysis_error = f"partial Jev batch failure: {error}"
    return None


def _assemble(answer_ids: list[str], replies: dict[str, Any], *, model: str) -> Analysis:
    agreement = []
    for left, right in combinations(answer_ids, 2):
        reply = replies.get(qname("agree", left, right))
        agreement.append(
            PairAgreement(
                ids=[left, right],
                agreement=read_noul(reply),
                confidence=read_confidence(reply),
            )
        )

    per_answer = read_profiles(answer_ids, replies)

    outlier = None
    if "outlier" in replies:
        reply = replies["outlier"]
        outlier = Outlier(
            id=read_choice(reply, answer_ids),
            confidence=read_confidence(reply),
            probabilities=read_probabilities(reply),
        )

    panel_agreement = (
        read_score(replies["panel_agreement"]) if "panel_agreement" in replies else None
    )

    return Analysis(
        confidence_notes=_notes(agreement, panel_agreement, outlier),
        calibration=Calibration(
            shape="matrix",
            model=model,
            agreement=agreement,
            per_answer=per_answer,
            outlier=outlier,
            panel_agreement=panel_agreement,
        ),
    )


def _notes(
    agreement: list[PairAgreement],
    panel_agreement: float | None,
    outlier: Outlier | None,
) -> str:
    """One derived sentence. The matrix shape writes no prose, so this is the only
    text in its output, and every number in it was answered, not asserted."""
    if not agreement:
        return "No pairs to compare."
    mean = sum(pair.agreement for pair in agreement) / len(agreement)
    parts = [f"Mean pairwise agreement {mean:.2f} over {len(agreement)} pairs"]
    if panel_agreement is not None:
        parts.append(f"panel agreement {panel_agreement:.2f} of 2")
    if outlier and outlier.id:
        confidence = f" at {outlier.confidence:.2f}" if outlier.confidence is not None else ""
        parts.append(f"least similar answer: {outlier.id}{confidence}")
    return "; ".join(parts) + "."
