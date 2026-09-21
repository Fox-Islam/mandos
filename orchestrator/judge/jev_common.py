"""Shared plumbing for the Jev-backed judge shapes: the state they send and how they
batch their questions.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from orchestrator.jev import JevClient, JevResult
from orchestrator.models import RawAnswer

# Jev answers a batch in parallel, so a call costs its round trip and almost nothing
# per question. Batching is therefore the only lever that matters, and the cap exists
# to bound one request body instead of to save money. Configurable per deployment.
DEFAULT_QUESTIONS_PER_CALL = 60


def build_state(
    question: str,
    answers: list[RawAnswer],
    context: str | None = None,
) -> dict[str, Any]:
    """The JSON state every Jev judge question is asked about.

    Structured, not flattened to prose: the answers stay keyed by real provider
    id so a question can name one ("does deepseek's answer support this?") and Jev can
    find it. ``context`` is included for the same reason the panel gets it - a judge
    scoring coverage against half a question scores the wrong thing.
    """
    state: dict[str, Any] = {"question": question}
    if context:
        state["context"] = context
    state["answers"] = {answer.id: answer.answer for answer in answers}
    return state


def chunk_questions(
    questions: dict[str, dict[str, Any]],
    size: int = DEFAULT_QUESTIONS_PER_CALL,
) -> Iterator[dict[str, dict[str, Any]]]:
    """Split a question map into request-sized batches, preserving insertion order."""
    if size < 1:
        raise ValueError("question batch size must be at least 1")
    items = list(questions.items())
    for start in range(0, len(items), size):
        yield dict(items[start : start + size])


async def ask_all(
    client: JevClient,
    state: dict[str, Any],
    questions: dict[str, dict[str, Any]],
    *,
    deadline: float,
    outcome,
    batch_size: int = DEFAULT_QUESTIONS_PER_CALL,
) -> tuple[dict[str, Any], str | None]:
    """Ask every question, in as few calls as the batch size allows.

    Returns the merged answer map and the first error encountered. A partial failure is
    reported but not discarded: answers that did come back are still usable, and the
    shapes treat a missing answer as maximal uncertainty instead of as a crash.
    """
    merged: dict[str, Any] = {}
    error: str | None = None
    for batch in chunk_questions(questions, batch_size):
        result: JevResult = await client.ask(state, batch, deadline=deadline)
        outcome.absorb_jev(result)
        if result.status != "ok":
            error = error or result.error
            continue
        merged.update(result.answers)
    if not merged and error is None:
        error = "Jev returned no answers"
    return merged, error
