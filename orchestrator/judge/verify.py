"""The verify judge: the generative analyst still writes, but no longer gets the last
word.

Fusion's analyst asserts a consensus and you take its word for it. This shape keeps
that analyst exactly as it was — same prompt, same schema — and then puts every one of
its findings to Jev as a yes/no question: is this consensus item actually supported by
every answer it covers? Is this contradiction a real disagreement or two models
phrasing one position differently?

Nothing is rewritten or deleted. Each finding keeps its place and gains a number, so a
low one is visible to the author rather than quietly dropped by a second model's
judgement. In practice it is the cheapest way to find out how much of a generative
analyst's output survives contact with a calibrated one.
"""

from __future__ import annotations

from typing import Any

from orchestrator.jev import JevClient, noul, qname, read_confidence, read_noul
from orchestrator.judge.jev_common import DEFAULT_QUESTIONS_PER_CALL, ask_all, build_state
from orchestrator.judge.outcome import JudgeOutcome
from orchestrator.models import Analysis, CalibratedClaim, Calibration, RawAnswer

# Below this, a finding is reported as one the panel does not bear out.
VERIFIED = 0.5


def build_questions(analysis: Analysis) -> dict[str, dict[str, Any]]:
    """One yes/no question per finding the analyst produced."""
    questions: dict[str, dict[str, Any]] = {}
    for index, point in enumerate(analysis.consensus):
        questions[qname("consensus", index)] = noul(
            f"Is this supported by every one of the panel answers?\n\n{point}",
            yes="Every answer states this or entails it",
            no="At least one answer contradicts it, or does not address it",
        )
    for index, contradiction in enumerate(analysis.contradictions):
        positions = "\n".join(f"- [{', '.join(p.ids)}] {p.claim}" for p in contradiction.positions)
        questions[qname("contradiction", index)] = noul(
            "Is this a real disagreement between the answers, rather than the same "
            f"position stated differently?\n\n{contradiction.topic}\n{positions}",
            yes="The positions are incompatible; both cannot be acted on",
            no="They differ in wording, emphasis or detail only",
        )
    for index, coverage in enumerate(analysis.partial_coverage):
        named = ", ".join(coverage.ids) or "the answers named"
        questions[qname("partial_coverage", index)] = noul(
            f"Is this point made by {named} and absent from the other answers?\n\n{coverage.point}",
            yes="Exactly those answers make it",
            no="Other answers make it too, or those answers do not",
        )
    for index, insight in enumerate(analysis.unique_insights):
        questions[qname("unique_insight", index)] = noul(
            f"Is this point made only by '{insight.id}'?\n\n{insight.insight}",
            yes=f"Only '{insight.id}' raises it",
            no="At least one other answer raises it too",
        )
    for index, gap in enumerate(analysis.blind_spots):
        questions[qname("blind_spot", index)] = noul(
            f"Is this genuinely left unaddressed by every one of the panel answers?\n\n{gap}",
            yes="No answer addresses it, even briefly",
            no="At least one answer addresses it",
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
    """Verify ``outcome.analysis`` in place.

    The caller has already run the generative judge, so ``outcome.analysis`` is set.
    Returns an error string when verification could not run; the analysis survives
    either way, unannotated.
    """
    analysis = outcome.analysis
    if analysis is None:
        return "nothing to verify"

    questions = build_questions(analysis)
    if not questions:
        return "the analyst produced no findings to verify"

    state = build_state(question, answers, context)
    replies, error = await ask_all(
        client, state, questions, deadline=deadline, outcome=outcome, batch_size=batch_size
    )
    if not replies:
        return error or "Jev returned no answers"

    claims = _collect(analysis, replies)
    analysis.calibration = Calibration(shape="verify", model=client.model, claims=claims)
    analysis.confidence_notes = _append_notes(analysis.confidence_notes, claims)
    return f"partial Jev batch failure: {error}" if error is not None else None


def _collect(analysis: Analysis, replies: dict[str, Any]) -> list[CalibratedClaim]:
    """Attach one probability to each finding, keyed by the field and position it came
    from so an author can join the number back to the prose."""
    texts: list[tuple[str, int, str]] = []
    texts += [("consensus", i, p) for i, p in enumerate(analysis.consensus)]
    texts += [("contradiction", i, c.topic) for i, c in enumerate(analysis.contradictions)]
    texts += [("partial_coverage", i, c.point) for i, c in enumerate(analysis.partial_coverage)]
    texts += [("unique_insight", i, u.insight) for i, u in enumerate(analysis.unique_insights)]
    texts += [("blind_spot", i, g) for i, g in enumerate(analysis.blind_spots)]

    claims = []
    for role, index, text in texts:
        reply = replies.get(qname(role, index))
        if reply is None:
            continue
        claims.append(
            CalibratedClaim(
                role=role,
                index=index,
                claim=text,
                holds=read_noul(reply),
                confidence=read_confidence(reply),
            )
        )
    return claims


def _append_notes(existing: str, claims: list[CalibratedClaim]) -> str:
    """Add the verification tally to whatever the analyst wrote about its confidence.

    Appended rather than replacing it: the analyst's own hedging is still worth
    reading next to the measurement of how well it held up.
    """
    if not claims:
        return existing
    weak = [c for c in claims if (c.holds or 0) < VERIFIED]
    verdict = (
        f"Jev verified {len(claims)} findings; all held."
        if not weak
        else (
            f"Jev verified {len(claims)} findings; {len(weak)} were not borne out by "
            "the panel answers (see calibration.claims)."
        )
    )
    return f"{existing} {verdict}".strip() if existing else verdict
