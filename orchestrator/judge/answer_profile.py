"""Per-answer readings, shared by the shapes that want them.

Support scores say what an answer claims. These say how it reads: whether it commits
to a position, how much of the question it covers, whether it contributes anything the
others do not, and whether it admits to missing something.

That last one is how ``matrix`` reports gaps at all. Naming *what* is missing needs a
generative pass - Jev answers questions, it does not invent them - but noticing that
an answer said it was working blind does not, so the shape with no generative model in
it can still tell a caller there is something to go and find.

An author weighing a panel needs all of it: a claim backed by three evasive answers
that each addressed a third of the question is not the same finding as one backed by
three committed, complete ones, and the support numbers alone cannot tell them apart.
"""

from __future__ import annotations

from typing import Any

from orchestrator.jev import noul, qname, read_noul, read_optional_score, score
from orchestrator.models import AnswerProfile

HEDGING_RUBRIC = [
    "States its position plainly and commits to it",
    "Commits, but qualifies the important parts",
    "Avoids committing to a position at all",
]

SCOPE_RUBRIC = [
    "Answers a small part of what was asked",
    "Answers most of what was asked",
    "Answers everything that was asked",
]


def profile_questions(answer_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Three questions per panel member. Batched with everything else, so free."""
    questions: dict[str, dict[str, Any]] = {}
    for provider_id in answer_ids:
        questions[qname("hedging", provider_id)] = score(
            f"How much does the answer from '{provider_id}' hedge?", HEDGING_RUBRIC
        )
        questions[qname("scope", provider_id)] = score(
            f"How much of the question does the answer from '{provider_id}' address?",
            SCOPE_RUBRIC,
        )
        questions[qname("gap", provider_id)] = noul(
            f"Does the answer from '{provider_id}' say outright that it is missing "
            "information it needed, or that it is assuming something it could not "
            "check?",
            yes="It names something it lacks, assumes, or asks for",
            no="It answers without flagging any missing input",
        )
        questions[qname("distinct", provider_id)] = noul(
            f"Does the answer from '{provider_id}' make a substantive point that no "
            "other answer makes?",
            yes="It raises something the others leave out entirely",
            no="Everything it raises appears in at least one other answer",
        )
    return questions


def read_profiles(answer_ids: list[str], replies: dict[str, Any]) -> list[AnswerProfile]:
    return [
        AnswerProfile(
            id=provider_id,
            hedging=read_optional_score(replies.get(qname("hedging", provider_id))),
            scope=read_optional_score(replies.get(qname("scope", provider_id))),
            distinctive=read_noul(replies.get(qname("distinct", provider_id))),
            flagged_gap=read_noul(replies.get(qname("gap", provider_id))),
        )
        for provider_id in answer_ids
    ]
