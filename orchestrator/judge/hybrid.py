"""The hybrid judge: an LLM proposes claims, Jev decides them.

This is the shape that keeps Fusion's analysis schema while replacing its judgement.
A cheap generative pass lists what is worth testing (``extract.py``); Jev then answers,
for every claim and every panel member, "does this answer support this?" — and the
consensus / contradictions / partial coverage / unique insights that come back are
*derived from those probabilities*, not asserted by a model that felt like it.

The practical difference: a Fusion analyst that calls something consensus is making a
claim you cannot check. Here, "consensus" means every panel member scored above
:data:`SUPPORT_HIGH` on that proposition and Jev put the odds of disagreement below
:data:`CONTESTED`, and both numbers are returned with the finding.
"""

from __future__ import annotations

from typing import Any

from orchestrator.jev import (
    JevClient,
    noul,
    qname,
    read_confidence,
    read_noul,
    read_score,
    score,
)
from orchestrator.judge.extract import Extraction
from orchestrator.judge.jev_common import DEFAULT_QUESTIONS_PER_CALL, ask_all, build_state
from orchestrator.judge.outcome import JudgeOutcome
from orchestrator.models import (
    Analysis,
    CalibratedClaim,
    Calibration,
    ClaimSupport,
    Contradiction,
    PartialCoverage,
    Position,
    RawAnswer,
    UniqueInsight,
)

# A panel member is counted as backing a claim above SUPPORT_HIGH and as rejecting it
# below SUPPORT_LOW. The gap between them is deliberate: an answer that simply did not
# address a claim lands in the middle, and must not be read as either agreement or
# dissent. That distinction is what ``partial_coverage`` exists to carry.
SUPPORT_HIGH = 0.6
SUPPORT_LOW = 0.4

# Above this, Jev thinks the answers genuinely disagree rather than differ in wording.
CONTESTED = 0.5

# Above this, a proposed blind spot is treated as real.
BLIND_SPOT = 0.5

STANDING_RUBRIC = [
    "No answer supports this",
    "Some answers support this, weakly or in passing",
    "The answers support this clearly and consistently",
]


def build_questions(
    claims: list[str],
    blind_spots: list[str],
    answer_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """One question per (claim, panel member), plus two per claim and one per gap.

    For 12 claims over a 3-member panel that is 66 questions — one round trip, because
    question count is very nearly free and only the round trip is not.
    """
    questions: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims):
        for provider_id in answer_ids:
            questions[qname("support", index, provider_id)] = noul(
                f"Does the answer from '{provider_id}' support this claim?\n\n{claim}",
                yes=f"'{provider_id}' states this, or states something that entails it",
                no=(
                    f"'{provider_id}' contradicts this, or does not address it at all. "
                    "Not addressing it is a no."
                ),
            )
        questions[qname("contested", index)] = noul(
            f"Do the panel answers genuinely disagree about this claim?\n\n{claim}",
            yes="At least one answer asserts this and at least one denies it",
            no="The answers agree, or differ only in wording, emphasis or detail",
        )
        questions[qname("standing", index)] = score(
            f"How well do the panel answers as a whole support this claim?\n\n{claim}",
            STANDING_RUBRIC,
        )
    for index, gap in enumerate(blind_spots):
        questions[qname("blind", index)] = noul(
            f"Is this genuinely left unaddressed by every one of the panel answers?\n\n{gap}",
            yes="No answer addresses it, even briefly",
            no="At least one answer addresses it",
        )
    return questions


async def run(
    question: str,
    answers: list[RawAnswer],
    extraction: Extraction,
    client: JevClient,
    *,
    deadline: float,
    context: str | None = None,
    outcome: JudgeOutcome,
    batch_size: int = DEFAULT_QUESTIONS_PER_CALL,
) -> str | None:
    """Adjudicate ``extraction`` with Jev and set ``outcome.analysis``.

    Returns an error string when no analysis could be produced, so the caller can fall
    back; ``None`` on success.
    """
    answer_ids = [answer.id for answer in answers]
    questions = build_questions(extraction.claims, extraction.blind_spots, answer_ids)
    if not questions:
        return "nothing to adjudicate"

    state = build_state(question, answers, context)
    replies, error = await ask_all(
        client, state, questions, deadline=deadline, outcome=outcome, batch_size=batch_size
    )
    if not replies:
        return error or "Jev returned no answers"

    outcome.analysis = _assemble(
        extraction, answer_ids, replies, shape="hybrid", model=client.model
    )
    if error is not None:
        # Some batch failed but others answered. The analysis stands on what came back
        # (a missing answer reads as maximal uncertainty), and the host is told.
        outcome.analysis_error = f"partial Jev batch failure: {error}"
    return None


def _assemble(
    extraction: Extraction,
    answer_ids: list[str],
    replies: dict[str, Any],
    *,
    shape: str,
    model: str,
) -> Analysis:
    """Turn Jev's answers into the narrative schema plus the numbers behind it."""
    consensus: list[str] = []
    contradictions: list[Contradiction] = []
    partial_coverage: list[PartialCoverage] = []
    unique_insights: list[UniqueInsight] = []
    blind_spots: list[str] = []
    calibrated: list[CalibratedClaim] = []
    unsupported = 0

    for index, claim in enumerate(extraction.claims):
        support = [
            ClaimSupport(
                id=provider_id,
                support=read_noul(replies.get(qname("support", index, provider_id))),
            )
            for provider_id in answer_ids
        ]
        contested_reply = replies.get(qname("contested", index))
        contested = read_noul(contested_reply)
        standing = read_score(replies.get(qname("standing", index)), fallback=0.0)

        backers = [s.id for s in support if s.support >= SUPPORT_HIGH]
        dissenters = [s.id for s in support if s.support <= SUPPORT_LOW]
        # When Jev says the panel disagrees, the question is who is on which side, and
        # the right cut is "clearly rejects" vs "does not clearly reject" — not the
        # stricter bar used to *assert* a consensus. Splitting a contested claim at
        # SUPPORT_HIGH loses the finding entirely whenever the lone dissenting voice
        # lands just under it, which is exactly where a real minority position sits:
        # measured live, the one model recommending the contested option scored 0.59
        # to 0.60 across repeats while the other three sat at 0.02, so a SUPPORT_HIGH
        # split turned the panel's sharpest disagreement into "nobody backed this" on
        # a third of runs. A contradiction is the most valuable thing the judge finds;
        # it must not hinge on a hundredth of a point.
        leaning = [s.id for s in support if s.support > SUPPORT_LOW]

        if contested >= CONTESTED and leaning and dissenters:
            role, position = "contradiction", len(contradictions)
            contradictions.append(
                Contradiction(
                    topic=claim,
                    positions=[
                        Position(ids=leaning, claim=claim),
                        Position(ids=dissenters, claim=f"Does not support: {claim}"),
                    ],
                )
            )
        elif len(backers) == len(answer_ids) and answer_ids:
            role, position = "consensus", len(consensus)
            consensus.append(claim)
        elif len(backers) == 1:
            role, position = "unique_insight", len(unique_insights)
            unique_insights.append(UniqueInsight(id=backers[0], insight=claim))
        elif backers:
            role, position = "partial_coverage", len(partial_coverage)
            partial_coverage.append(PartialCoverage(ids=backers, point=claim))
        else:
            role, position = "unsupported", unsupported
            unsupported += 1

        calibrated.append(
            CalibratedClaim(
                role=role,
                index=position,
                claim=claim,
                support=support,
                contested=contested,
                standing=standing,
                confidence=read_confidence(contested_reply),
            )
        )

    for index, gap in enumerate(extraction.blind_spots):
        reply = replies.get(qname("blind", index))
        probability = read_noul(reply)
        if probability < BLIND_SPOT:
            continue
        calibrated.append(
            CalibratedClaim(
                role="blind_spot",
                index=len(blind_spots),
                claim=gap,
                holds=probability,
                confidence=read_confidence(reply),
            )
        )
        blind_spots.append(gap)

    return Analysis(
        consensus=consensus,
        contradictions=contradictions,
        partial_coverage=partial_coverage,
        unique_insights=unique_insights,
        blind_spots=blind_spots,
        confidence_notes=_notes(extraction, calibrated, unsupported),
        calibration=Calibration(shape=shape, model=model, claims=calibrated),
    )


def _notes(extraction: Extraction, calibrated: list[CalibratedClaim], unsupported: int) -> str:
    """A factual summary, derived rather than written.

    No model authored this sentence, which is the point: every number in it is one Jev
    answered.
    """
    claims = len(extraction.claims)
    if not claims:
        return "No claims were adjudicated."
    contested = sum(
        1 for c in calibrated if c.role != "blind_spot" and (c.contested or 0) >= CONTESTED
    )
    parts = [
        f"Jev adjudicated {claims} claim{'s' if claims != 1 else ''}",
        f"{contested} contested",
    ]
    if unsupported:
        parts.append(f"{unsupported} backed by no answer (proposed but not found in the panel)")
    dropped = len(extraction.blind_spots) - sum(1 for c in calibrated if c.role == "blind_spot")
    if dropped > 0:
        parts.append(f"{dropped} proposed blind spot(s) were in fact addressed")
    return "; ".join(parts) + "."
