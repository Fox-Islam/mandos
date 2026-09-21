"""The four judge shapes: what each derives, and how each degrades.

Every Jev call here is a deterministic fake. The assertions are about the judge's
reasoning from probabilities — that a claim two models back and one denies becomes a
contradiction rather than a consensus — not about Jev's own accuracy, which is
measured in ``local/`` against the live API rather than in the suite.
"""

from __future__ import annotations

import json
import time

import pytest

from orchestrator.fakes import FakeChatProvider, FakeJevClient
from orchestrator.jev import qname
from orchestrator.judge import run_judge
from orchestrator.judge.hybrid import SUPPORT_HIGH, SUPPORT_LOW
from orchestrator.models import RawAnswer

ANSWERS = [
    RawAnswer(id="a", model="m", answer="answer from a"),
    RawAnswer(id="b", model="m", answer="answer from b"),
    RawAnswer(id="c", model="m", answer="answer from c"),
]

EXTRACTION = json.dumps(
    {
        "claims": ["everyone agrees", "a and b only", "a alone", "disputed", "nobody"],
        "blind_spots": ["real gap", "not a gap"],
    }
)


def _extractor(text: str = EXTRACTION) -> FakeChatProvider:
    return FakeChatProvider("ja", text=text)


def _support(index: int, values: dict[str, float]) -> dict:
    return {qname("support", index, pid): {"noul": v} for pid, v in values.items()}


HIGH, LOW = SUPPORT_HIGH + 0.2, SUPPORT_LOW - 0.2


def _hybrid_script() -> dict:
    script: dict = {}
    script |= _support(0, {"a": HIGH, "b": HIGH, "c": HIGH})
    script |= _support(1, {"a": HIGH, "b": HIGH, "c": 0.5})
    script |= _support(2, {"a": HIGH, "b": 0.5, "c": 0.5})
    script |= _support(3, {"a": HIGH, "b": LOW, "c": LOW})
    script |= _support(4, {"a": LOW, "b": LOW, "c": LOW})
    for index in range(5):
        script[qname("contested", index)] = {"noul": 0.9 if index == 3 else 0.1}
        script[qname("standing", index)] = {"score": 1.5}
    script[qname("blind", 0)] = {"noul": 0.9}
    script[qname("blind", 1)] = {"noul": 0.1}
    return script


async def _run(shape: str, jev, provider=None, **kw):
    return await run_judge(
        "q",
        ANSWERS,
        shape=shape,
        deadline=time.monotonic() + 30,
        jev_client=jev,
        analysis_provider=provider,
        **kw,
    )


async def test_hybrid_sorts_claims_by_who_supports_them():
    jev = FakeJevClient(_hybrid_script())
    outcome = await _run("hybrid", jev, _extractor())

    analysis = outcome.analysis
    assert outcome.shape == "hybrid"
    assert analysis.consensus == ["everyone agrees"]
    assert [pc.point for pc in analysis.partial_coverage] == ["a and b only"]
    assert analysis.partial_coverage[0].ids == ["a", "b"]
    assert [u.insight for u in analysis.unique_insights] == ["a alone"]
    assert analysis.unique_insights[0].id == "a"
    assert [c.topic for c in analysis.contradictions] == ["disputed"]
    assert analysis.contradictions[0].positions[0].ids == ["a"]
    assert analysis.contradictions[0].positions[1].ids == ["b", "c"]


async def test_hybrid_keeps_unsupported_claims_out_of_the_narrative_but_in_the_numbers():
    outcome = await _run("hybrid", FakeJevClient(_hybrid_script()), _extractor())
    analysis = outcome.analysis

    narrated = (
        analysis.consensus
        + [c.topic for c in analysis.contradictions]
        + [pc.point for pc in analysis.partial_coverage]
        + [u.insight for u in analysis.unique_insights]
    )
    assert "nobody" not in narrated
    unsupported = [c for c in analysis.calibration.claims if c.role == "unsupported"]
    assert [c.claim for c in unsupported] == ["nobody"]
    assert "backed by no answer" in analysis.confidence_notes


async def test_hybrid_drops_a_blind_spot_the_panel_did_address():
    outcome = await _run("hybrid", FakeJevClient(_hybrid_script()), _extractor())
    assert outcome.analysis.blind_spots == ["real gap"]
    assert "were in fact addressed" in outcome.analysis.confidence_notes


async def test_hybrid_attaches_the_numbers_each_finding_was_derived_from():
    outcome = await _run("hybrid", FakeJevClient(_hybrid_script()), _extractor())
    calibration = outcome.analysis.calibration

    assert calibration.shape == "hybrid"
    consensus = next(c for c in calibration.claims if c.role == "consensus")
    assert {s.id for s in consensus.support} == {"a", "b", "c"}
    assert min(s.support for s in consensus.support) >= SUPPORT_HIGH
    contradiction = next(c for c in calibration.claims if c.role == "contradiction")
    assert contradiction.contested == 0.9


async def test_hybrid_asks_one_question_per_claim_and_member_in_one_call():
    jev = FakeJevClient(_hybrid_script())
    await _run("hybrid", jev, _extractor())

    assert len(jev.calls) == 1
    questions = jev.calls[0]["questions"]
    # 5 claims x (3 members + contested + standing) + 2 blind spots
    assert len(questions) == 5 * 5 + 2


async def test_batching_splits_wide_question_sets_across_calls():
    jev = FakeJevClient(_hybrid_script())
    await _run("hybrid", jev, _extractor(), batch_size=10)

    assert len(jev.calls) == 3
    assert sum(len(call["questions"]) for call in jev.calls) == 27


async def test_the_judge_is_shown_the_context_the_panel_saw():
    jev = FakeJevClient(_hybrid_script())
    await _run("hybrid", jev, _extractor(), context="the background")

    assert jev.calls[0]["state"]["context"] == "the background"
    assert set(jev.calls[0]["state"]["answers"]) == {"a", "b", "c"}


async def test_matrix_needs_no_generative_model_at_all():
    jev = FakeJevClient(
        {
            qname("agree", "a", "b"): {"noul": 0.9},
            qname("agree", "a", "c"): {"noul": 0.2},
            qname("agree", "b", "c"): {"noul": 0.3},
            "outlier": {"choice": "c", "confidence": 0.7, "probabilities": {"c": 0.7}},
            "panel_agreement": {"score": 1.2},
        }
    )
    outcome = await _run("matrix", jev)

    calibration = outcome.analysis.calibration
    assert outcome.shape == "matrix"
    assert calibration.panel_agreement == 1.2
    assert calibration.outlier.id == "c"
    assert {tuple(p.ids): p.agreement for p in calibration.agreement}[("a", "b")] == 0.9
    assert {p.id for p in calibration.per_answer} == {"a", "b", "c"}
    assert outcome.analysis.consensus == []


async def test_matrix_leaves_an_unanswered_rubric_null_rather_than_zero():
    jev = FakeJevClient()
    jev.answers = {}
    outcome = await _run("matrix", jev)
    profile = outcome.analysis.calibration.per_answer[0]
    # The fake answers every question, so a rubric reading is present and 0.0 is a
    # measurement. The null case is covered by dropping the answer entirely.
    assert profile.hedging == 0.0

    jev_missing = FakeJevClient({qname("hedging", "a"): {"note": "unreadable"}})
    outcome = await _run("matrix", jev_missing)
    assert outcome.analysis.calibration.per_answer[0].hedging is None


async def test_verify_annotates_the_analyst_without_rewriting_it():
    analysis_json = json.dumps(
        {
            "consensus": ["holds up", "does not hold up"],
            "contradictions": [],
            "partial_coverage": [],
            "unique_insights": [],
            "blind_spots": [],
            "confidence_notes": "the analyst was fairly sure",
        }
    )
    jev = FakeJevClient(
        {
            qname("consensus", 0): {"noul": 0.95},
            qname("consensus", 1): {"noul": 0.1},
        }
    )
    outcome = await _run("verify", jev, FakeChatProvider("ja", text=analysis_json))

    assert outcome.shape == "verify"
    assert outcome.analysis.consensus == ["holds up", "does not hold up"]
    holds = {c.index: c.holds for c in outcome.analysis.calibration.claims}
    assert holds == {0: 0.95, 1: 0.1}
    assert "the analyst was fairly sure" in outcome.analysis.confidence_notes
    assert "1 were not borne out" in outcome.analysis.confidence_notes


async def test_a_jev_failure_falls_back_to_the_generative_judge():
    from orchestrator.tests.helpers import ANALYSIS_JSON_IDS

    jev = FakeJevClient(error="HTTP 503: upstream")
    outcome = await _run("matrix", jev, FakeChatProvider("ja", text=ANALYSIS_JSON_IDS))

    assert outcome.shape == "llm"
    assert outcome.fallback_from == "matrix"
    assert outcome.analysis.consensus == ["agree on X"]
    assert "503" in outcome.analysis_error


async def test_a_jev_failure_with_no_fallback_configured_returns_no_analysis():
    outcome = await _run("matrix", FakeJevClient(error="boom"))
    assert outcome.analysis is None
    assert "boom" in outcome.analysis_error


async def test_a_shape_that_needs_jev_says_so_when_none_is_configured():
    outcome = await _run("matrix", None)
    assert outcome.analysis is None
    assert "needs a Jev judge" in outcome.analysis_error


async def test_hybrid_falls_back_when_the_extractor_returns_nothing_usable():
    outcome = await _run(
        "hybrid",
        FakeJevClient(_hybrid_script()),
        FakeChatProvider("ja", text='{"notes": "no claims here"}'),
    )
    assert outcome.shape == "llm"
    assert outcome.fallback_from == "hybrid"
    assert "extraction proposed nothing" in outcome.analysis_error


async def test_verify_keeps_the_analysis_when_verification_cannot_run():
    from orchestrator.tests.helpers import ANALYSIS_JSON_IDS

    outcome = await _run(
        "verify",
        FakeJevClient(error="gone"),
        FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
    )
    assert outcome.analysis is not None
    assert outcome.analysis.calibration is None
    assert outcome.shape == "llm"
    assert outcome.fallback_from == "verify"


async def test_no_panel_answers_is_reported_not_judged():
    outcome = await run_judge(
        "q", [], shape="matrix", deadline=time.monotonic() + 5, jev_client=FakeJevClient()
    )
    assert outcome.analysis is None
    assert "no successful panel answers" in outcome.analysis_error


async def test_jev_spend_and_call_counts_are_recorded():
    jev = FakeJevClient(_hybrid_script(), cost=0.0002)
    outcome = await _run("hybrid", jev, _extractor(), batch_size=10)

    assert outcome.jev_calls == 3
    assert outcome.jev_questions == 27
    assert outcome.jev_cost == pytest.approx(0.0006)
    assert outcome.jev_usage.input == 300


async def test_a_contested_claim_survives_a_backer_sitting_on_the_threshold():
    """A minority position scoring just under SUPPORT_HIGH must still be reported as a
    contradiction rather than falling through to `unsupported`.

    Measured against the live API: the lone model recommending a contested option
    scored 0.59-0.60 across repeats while the other three sat at 0.02, so splitting a
    contested claim at SUPPORT_HIGH lost the panel's sharpest disagreement on a third
    of runs.
    """
    script = {
        qname("support", 0, "a"): {"noul": 0.02},
        qname("support", 0, "b"): {"noul": 0.02},
        qname("support", 0, "c"): {"noul": SUPPORT_HIGH - 0.01},
        qname("contested", 0): {"noul": 0.85},
        qname("standing", 0): {"score": 1.0},
    }
    extraction = FakeChatProvider(
        "ja", text=json.dumps({"claims": ["disputed"], "blind_spots": []})
    )
    outcome = await _run("hybrid", FakeJevClient(script), extraction)

    analysis = outcome.analysis
    assert [c.topic for c in analysis.contradictions] == ["disputed"]
    assert analysis.contradictions[0].positions[0].ids == ["c"]
    assert analysis.contradictions[0].positions[1].ids == ["a", "b"]
    assert not [c for c in analysis.calibration.claims if c.role == "unsupported"]


async def test_an_uncontested_claim_still_needs_real_backing():
    """The looser split applies only to contested claims; a claim nobody backs and
    nobody disputes must stay `unsupported`."""
    script = {qname("support", 0, pid): {"noul": 0.5} for pid in ("a", "b", "c")} | {
        qname("contested", 0): {"noul": 0.1},
        qname("standing", 0): {"score": 0.2},
    }
    extraction = FakeChatProvider("ja", text=json.dumps({"claims": ["vague"], "blind_spots": []}))
    outcome = await _run("hybrid", FakeJevClient(script), extraction)

    assert outcome.analysis.consensus == []
    assert outcome.analysis.contradictions == []
    assert [c.role for c in outcome.analysis.calibration.claims] == ["unsupported"]
