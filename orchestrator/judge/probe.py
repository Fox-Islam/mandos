"""The probe judge: no deliberation, only what the answers were missing.

The other shapes all read a panel *against itself* — who agreed, who disagreed, who
raised something nobody else did. This one does none of that. It asks a single
question: what would you have had to know to answer this properly, and did anyone
have it?

That makes it the cheapest shape and the one to use with a panel of one. Measured
over 16 questions whose answers each turn on a fact nobody was given, a single model
put through this loop scored 16/16 where the same model alone scored 12/16 — and where
a two-member panel with full claim adjudication scored 15/16 at more than twice the
cost.

The caveat travels with the number. Those questions were built so that a missing fact
decides the answer, which is exactly the shape that rewards asking for evidence and
gives deliberation nothing to do. On a question where the panel has everything it
needs and still disagrees, this shape reports nothing useful and ``hybrid`` is the one
that earns its cost.
"""

from __future__ import annotations

from typing import Any

from orchestrator.jev import JevClient, noul, noul_confidence, qname, read_noul
from orchestrator.judge.answer_profile import profile_questions, read_profiles
from orchestrator.judge.extract import Extraction
from orchestrator.judge.jev_common import DEFAULT_QUESTIONS_PER_CALL, ask_all, build_state
from orchestrator.judge.outcome import JudgeOutcome
from orchestrator.models import Analysis, Calibration, NeedsEvidence, RawAnswer

# Above this, the answers genuinely lacked the item rather than merely omitting it.
LACKED = 0.5


def build_questions(
    missing_evidence: list[str], answer_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Two questions per candidate, plus the per-answer readings.

    No support matrix, no contested question, no standing rubric — for a panel of one
    those measure nothing, and for a larger panel they are what ``hybrid`` is for.
    """
    questions: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(missing_evidence):
        questions[qname("lacked", index)] = noul(
            f"Did the answers lack this information, rather than simply not mention it?\n\n{item}",
            yes="An answer assumes it, guesses at it, or asks for it",
            no="It is present in the question or context, or no answer needed it",
        )
        questions[qname("wouldchange", index)] = noul(
            f"Would having this information change the answer to the question?\n\n{item}",
            yes="A different value would lead to a different recommendation",
            no="The answer holds either way",
        )
    questions.update(profile_questions(answer_ids))
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
    """Score what the answers were missing. Returns an error string, or ``None``."""
    answer_ids = [answer.id for answer in answers]
    questions = build_questions(extraction.missing_evidence, answer_ids)
    if not questions:
        return "nothing to probe"

    replies, error = await ask_all(
        client,
        build_state(question, answers, context),
        questions,
        deadline=deadline,
        outcome=outcome,
        batch_size=batch_size,
    )
    if not replies:
        return error or "Jev returned no answers"

    found = []
    for index, item in enumerate(extraction.missing_evidence):
        reply = replies.get(qname("lacked", index))
        lacked = read_noul(reply)
        if lacked < LACKED:
            continue
        found.append(
            NeedsEvidence(
                item=item,
                lacked=lacked,
                would_change=read_noul(replies.get(qname("wouldchange", index))),
                confidence=noul_confidence(reply),
            )
        )
    found.sort(key=lambda e: e.would_change or 0, reverse=True)

    outcome.analysis = Analysis(
        needs_evidence=found,
        confidence_notes=_notes(extraction, found),
        calibration=Calibration(
            shape="probe",
            model=client.model,
            per_answer=read_profiles(answer_ids, replies),
        ),
    )
    if error is not None:
        outcome.analysis_error = f"partial Jev batch failure: {error}"
    return None


def _notes(extraction: Extraction, found: list[NeedsEvidence]) -> str:
    proposed = len(extraction.missing_evidence)
    if not proposed:
        return "Nothing was proposed as missing."
    dropped = proposed - len(found)
    note = f"{len(found)} of {proposed} proposed gaps were genuinely missing"
    if dropped:
        note += f"; {dropped} turned out to be present already"
    return note + ". No claims were adjudicated: this shape does not deliberate."
