"""The judge, against the real Jev API. Opt-in: `pytest -m live`.

    ./.venv/bin/python -m pytest -m live orchestrator/tests/test_jev_live.py

Needs TYPESAFE_API_KEY or OPENROUTER_API_KEY. One call per test, a few thousandths of
a cent each.

Why this is committed instead of a throwaway script: the thresholds in
``judge/hybrid.py`` were not chosen, they were *measured*, and one of them had to move
because a live run showed the panel's sharpest disagreement vanishing on a third of
attempts. Nothing offline can catch that regressing - the fake answers whatever it is
told to. This is a small fixed panel with a known correct reading, so a change in the
derivation, the question wording, or Jev itself shows up as a failure instead of as a
quietly worse analysis.

The panel below is written so each branch of the hybrid derivation has exactly one
case: a claim all four make, one three of four make, one they genuinely split on, two
that only a single member raises, and one no answer supports at all.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from orchestrator.jev import JevClient
from orchestrator.judge import hybrid, matrix
from orchestrator.judge.extract import Extraction
from orchestrator.judge.outcome import JudgeOutcome
from orchestrator.models import RawAnswer

pytestmark = pytest.mark.live

QUESTION = (
    "Should we use PostgreSQL LISTEN/NOTIFY as the transport for our background job "
    "queue? We run about 50 jobs a minute behind PgBouncer."
)

ANSWERS = [
    RawAnswer(
        id="alpha",
        model="fixture",
        answer=(
            "No. LISTEN/NOTIFY is a pub/sub signal, not a queue: if no session is "
            "listening when NOTIFY fires, the notification is dropped and nothing "
            "replays it. The payload is also capped at 8000 bytes. Use a jobs table "
            "and pull work with SELECT ... FOR UPDATE SKIP LOCKED."
        ),
    ),
    RawAnswer(
        id="beta",
        model="fixture",
        answer=(
            "I would not. Notifications are lost when no listener is connected, and "
            "the payload limit is 8000 bytes. Specific to your setup: PgBouncer in "
            "transaction pooling mode breaks LISTEN/NOTIFY outright, because a LISTEN "
            "registered on one server connection is not there on the next. A jobs "
            "table with SKIP LOCKED avoids all of it."
        ),
    ),
    RawAnswer(
        id="gamma",
        model="fixture",
        answer=(
            "At 50 jobs a minute, yes, LISTEN/NOTIFY is a reasonable transport and I "
            "would use it. The 8000-byte payload cap means you send an id rather than "
            "the job, and a notification raised while no one is listening is gone, but "
            "both are handled by writing the job to a table first and treating NOTIFY "
            "purely as a wake-up, with a slow SKIP LOCKED poll as a safety net."
        ),
    ),
    RawAnswer(
        id="delta",
        model="fixture",
        answer=(
            "No, not as the transport. A notification raised while nothing is "
            "listening is discarded, so every deploy is a window where work vanishes. "
            "One subtlety people miss is that NOTIFY is only delivered when the "
            "transaction commits, so you cannot use it to signal progress inside a "
            "long transaction. Put the work in a durable store and have workers pull."
        ),
    ),
]

EXTRACTION = Extraction(
    claims=[
        "A NOTIFY raised while no session is listening is discarded and never replayed.",
        "The NOTIFY payload is limited to 8000 bytes.",
        "LISTEN/NOTIFY is a suitable primary transport for this job queue.",
        "PgBouncer in transaction pooling mode breaks LISTEN/NOTIFY.",
        "NOTIFY is only delivered when the surrounding transaction commits.",
        "All four answers recommend replacing Postgres with Redis for this queue.",
    ],
    blind_spots=[
        "What happens to in-flight jobs during a database failover or restart.",
        "None of the answers mentions SELECT ... FOR UPDATE SKIP LOCKED.",
    ],
    missing_evidence=[
        "The size of an individual job payload in bytes.",
        "The rate at which jobs are enqueued.",
    ],
)


def _api_key() -> tuple[str, str]:
    for env in (Path.cwd() / ".env", Path.home() / ".mandos" / ".env"):
        if not env.exists():
            continue
        for raw in env.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                key, _, value = raw.partition("=")
                name = key.removeprefix("export ").strip()
                os.environ.setdefault(name, value.strip().strip("\"'"))
    if os.environ.get("TYPESAFE_API_KEY"):
        return "typesafe", os.environ["TYPESAFE_API_KEY"]
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter", os.environ["OPENROUTER_API_KEY"]
    pytest.skip("no TYPESAFE_API_KEY or OPENROUTER_API_KEY")


@pytest.fixture
async def jev():
    provider, key = _api_key()
    async with httpx.AsyncClient() as http:
        yield JevClient(
            provider=provider, api_key=key, http_client=http, timeout_s=120, max_retries=2
        )


def _claim(analysis, needle: str):
    return next((c for c in analysis.calibration.claims if needle.lower() in c.claim.lower()), None)


def _backers(analysis, needle: str) -> set[str]:
    claim = _claim(analysis, needle)
    return {s.id for s in claim.support if s.support >= hybrid.SUPPORT_HIGH} if claim else set()


async def test_hybrid_reads_the_panel_correctly(jev):
    outcome = JudgeOutcome(shape="hybrid")
    error = await hybrid.run(
        QUESTION, ANSWERS, EXTRACTION, jev, deadline=time.monotonic() + 180, outcome=outcome
    )
    assert error is None, error
    analysis = outcome.analysis

    # Every answer states it, so it is consensus.
    assert _claim(analysis, "never replayed").role == "consensus"
    # delta never mentions the cap, so it is coverage instead of consensus.
    assert _backers(analysis, "8000 bytes") == {"alpha", "beta", "gamma"}
    # gamma recommends it and the rest reject it: a real disagreement.
    assert _claim(analysis, "suitable primary transport").role == "contradiction"
    # Raised by exactly one member each.
    assert _backers(analysis, "PgBouncer") == {"beta"}
    assert _backers(analysis, "transaction commits") == {"delta"}
    # Proposed by the extractor, backed by nobody: an extraction error, not a finding.
    assert _claim(analysis, "Redis").role == "unsupported"
    # A genuine gap survives; a proposed one that three answers address does not.
    assert any("failover" in b.lower() for b in analysis.blind_spots)
    assert not any("skip locked" in b.lower() for b in analysis.blind_spots)


async def test_a_minority_position_survives_the_support_threshold(jev):
    """The regression this file exists for.

    The lone member backing the contested option scores around 0.6 - right on
    SUPPORT_HIGH. Splitting a contested claim at that bar dropped the finding entirely
    whenever it landed a hundredth under, so the panel's sharpest disagreement
    disappeared on roughly a third of runs. It must come back as a contradiction with
    gamma on one side and the other three on the other, whichever side of 0.6 it lands.
    """
    outcome = JudgeOutcome(shape="hybrid")
    await hybrid.run(
        QUESTION, ANSWERS, EXTRACTION, jev, deadline=time.monotonic() + 180, outcome=outcome
    )
    contested = _claim(outcome.analysis, "suitable primary transport")
    assert contested.contested >= hybrid.CONTESTED
    assert contested.role == "contradiction"

    positions = outcome.analysis.contradictions[0].positions
    assert positions[0].ids == ["gamma"]
    assert set(positions[1].ids) == {"alpha", "beta", "delta"}


async def test_hybrid_reports_how_each_answer_reads(jev):
    outcome = JudgeOutcome(shape="hybrid")
    await hybrid.run(
        QUESTION, ANSWERS, EXTRACTION, jev, deadline=time.monotonic() + 180, outcome=outcome
    )
    profiles = {p.id: p for p in outcome.analysis.calibration.per_answer}
    assert set(profiles) == {"alpha", "beta", "gamma", "delta"}
    assert all(p.scope is not None and p.hedging is not None for p in profiles.values())
    # beta and delta each raise something no one else does.
    assert profiles["beta"].distinctive > 0.5


async def test_matrix_finds_the_odd_one_out(jev):
    outcome = JudgeOutcome(shape="matrix")
    error = await matrix.run(
        QUESTION, ANSWERS, jev, deadline=time.monotonic() + 180, outcome=outcome
    )
    assert error is None, error
    calibration = outcome.analysis.calibration

    assert calibration.outlier.id == "gamma"
    pairs = {frozenset(p.ids): p.agreement for p in calibration.agreement}
    # The two that reject it for the same reasons agree more than either does with the
    # one that recommends it.
    assert pairs[frozenset({"alpha", "beta"})] > pairs[frozenset({"alpha", "gamma"})]


async def test_it_separates_what_the_panel_lacked_from_what_it_was_given(jev):
    """The half of a gap a caller can act on.

    The payload size is nowhere in the question and every answer reasons around the
    8000-byte cap without knowing it, so it was genuinely lacked and would change the
    recommendation. The enqueue rate is stated in the question itself, so however
    relevant it is, fetching it again buys nothing.
    """
    outcome = JudgeOutcome(shape="hybrid")
    await hybrid.run(
        QUESTION, ANSWERS, EXTRACTION, jev, deadline=time.monotonic() + 180, outcome=outcome
    )
    items = {e.item: e for e in outcome.analysis.needs_evidence}

    payload = next((v for k, v in items.items() if "payload" in k.lower()), None)
    assert payload is not None, f"expected the payload size to be reported: {list(items)}"
    assert payload.lacked >= hybrid.LACKED
    assert not any("rate at which" in k.lower() for k in items)
