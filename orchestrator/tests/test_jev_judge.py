"""The four judge shapes: what each derives, and how each degrades.

Every Jev call here is a deterministic fake. The assertions are about the judge's
reasoning from probabilities - that a claim two models back and one denies becomes a
contradiction instead of a consensus - not about Jev's own accuracy, which is
measured in ``local/`` against the live API instead of in the suite.
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
    # 5 claims x (3 members + contested + standing), 4 readings per member, 2 gaps
    assert len(questions) == 5 * 5 + 3 * 4 + 2


async def test_batching_splits_wide_question_sets_across_calls():
    jev = FakeJevClient(_hybrid_script())
    await _run("hybrid", jev, _extractor(), batch_size=10)

    assert len(jev.calls) == 4
    assert sum(len(call["questions"]) for call in jev.calls) == 39


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


async def test_a_failed_extraction_falls_back_to_matrix_not_to_a_generative_judge():
    """An analyst that proposed nothing says nothing about whether Jev is reachable.
    Dropping straight to a generative judge would throw away the calibration."""
    outcome = await _run(
        "hybrid",
        FakeJevClient(_hybrid_script()),
        FakeChatProvider("ja", text='{"notes": "no claims here"}'),
    )
    assert outcome.shape == "matrix"
    assert outcome.fallback_from == "hybrid"
    assert "extraction proposed nothing" in outcome.analysis_error
    assert outcome.analysis.calibration.shape == "matrix"


async def test_hybrid_reaches_the_generative_judge_only_when_jev_is_gone():
    from orchestrator.tests.helpers import ANALYSIS_JSON_IDS

    outcome = await _run(
        "hybrid",
        FakeJevClient(error="HTTP 503: upstream"),
        FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
    )
    assert outcome.shape == "llm"
    assert outcome.fallback_from == "hybrid"
    assert "503" in outcome.analysis_error
    assert outcome.analysis.consensus == ["agree on X"]


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

    assert outcome.jev_calls == 4
    assert outcome.jev_questions == 39
    assert outcome.jev_cost == pytest.approx(0.0008)
    assert outcome.jev_usage.input == 400


async def test_a_contested_claim_survives_a_backer_sitting_on_the_threshold():
    """A minority position scoring just under SUPPORT_HIGH must still be reported as a
    contradiction instead of falling through to `unsupported`.

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


async def test_hybrid_reports_how_each_answer_reads_not_just_what_it_claims():
    """The reading that lets an author discount a weak panellist instead of counting
    it as a vote."""
    script = _hybrid_script() | {
        qname("hedging", "a"): {"score": 2.0},
        qname("scope", "a"): {"score": 0.3},
        qname("distinct", "a"): {"noul": 0.1},
    }
    outcome = await _run("hybrid", FakeJevClient(script), _extractor())

    profiles = {p.id: p for p in outcome.analysis.calibration.per_answer}
    assert set(profiles) == {"a", "b", "c"}
    assert profiles["a"].hedging == 2.0
    assert profiles["a"].scope == 0.3
    assert profiles["a"].distinctive == 0.1


def _evidence_extractor() -> FakeChatProvider:
    return FakeChatProvider(
        "ja",
        text=json.dumps(
            {
                "claims": ["everyone agrees"],
                "blind_spots": [],
                "missing_evidence": [
                    "the schema of the jobs table",
                    "the deploy cadence",
                    "something already in the prompt",
                ],
            }
        ),
    )


async def test_the_panel_reports_what_it_was_missing():
    """Distinct from blind spots: this is what the panel *could not* know, which is the
    only half of the two a caller can act on."""
    script = {qname("support", 0, pid): {"noul": 0.9} for pid in ("a", "b", "c")} | {
        qname("contested", 0): {"noul": 0.05},
        qname("standing", 0): {"score": 1.8},
        qname("lacked", 0): {"noul": 0.95},
        qname("wouldchange", 0): {"noul": 0.9},
        qname("lacked", 1): {"noul": 0.8},
        qname("wouldchange", 1): {"noul": 0.2},
        qname("lacked", 2): {"noul": 0.1},
        qname("wouldchange", 2): {"noul": 0.9},
    }
    outcome = await _run("hybrid", FakeJevClient(script), _evidence_extractor())

    needs = outcome.analysis.needs_evidence
    # The item the panel already had is dropped however much it would matter.
    assert [e.item for e in needs] == ["the schema of the jobs table", "the deploy cadence"]
    # Sorted so the one worth a second pass reads first.
    assert needs[0].would_change == 0.9
    assert needs[0].lacked == 0.95
    assert needs[0].confidence == pytest.approx(0.9)


async def test_nothing_missing_means_no_evidence_block():
    outcome = await _run("hybrid", FakeJevClient(_hybrid_script()), _extractor())
    assert outcome.analysis.needs_evidence == []


async def test_matrix_notices_an_answer_that_says_it_is_working_blind():
    """`matrix` cannot name what is missing - that needs a generative pass - but it can
    report that the panel said something was."""
    jev = FakeJevClient({qname("gap", "a"): {"noul": 0.92}, qname("gap", "b"): {"noul": 0.05}})
    outcome = await _run("matrix", jev)

    flags = {p.id: p.flagged_gap for p in outcome.analysis.calibration.per_answer}
    assert flags["a"] == 0.92
    assert flags["b"] == 0.05


async def test_the_generative_judge_reports_gaps_too():
    """Asserted instead of measured, so `lacked` stays null - but a caller can still
    act on it, and the missing number says which kind of claim it is."""
    analysis_json = json.dumps(
        {
            "consensus": ["something"],
            "contradictions": [],
            "partial_coverage": [],
            "unique_insights": [],
            "blind_spots": [],
            "needs_evidence": [{"item": "the row count of the jobs table", "would_change": 0.8}],
            "confidence_notes": "",
        }
    )
    outcome = await _run("llm", None, FakeChatProvider("ja", text=analysis_json))

    assert len(outcome.analysis.needs_evidence) == 1
    item = outcome.analysis.needs_evidence[0]
    assert item.item == "the row count of the jobs table"
    assert item.would_change == 0.8
    assert item.lacked is None


async def test_one_failed_jev_call_does_not_write_off_the_next_one():
    """A shape that needs Jev is still tried after another Jev shape failed.

    `matrix` asks a much smaller batch than `hybrid`, so it is a real recovery path
    when Jev has just refused a wide one - a transient 529 under load is not evidence
    that the endpoint is gone. The chain does not branch on which provider failed.
    """
    calls: list[int] = []

    class FlakyJev(FakeJevClient):
        async def ask(self, state, questions, *, deadline):
            calls.append(len(questions))
            # Refuse the wide batch, answer the narrow one.
            if len(questions) > 25:
                self.error = "HTTP 529: system_overloaded"
            else:
                self.error = None
            return await super().ask(state, questions, deadline=deadline)

    outcome = await _run("hybrid", FlakyJev(), _extractor())

    assert outcome.shape == "matrix"
    assert outcome.fallback_from == "hybrid"
    assert outcome.analysis.calibration.shape == "matrix"
    assert len(calls) == 2, "matrix must still get its turn"


def _probe_extractor() -> FakeChatProvider:
    return FakeChatProvider(
        "ja",
        text=json.dumps(
            {"missing_evidence": ["the row count", "the query plan", "already stated"]}
        ),
    )


async def test_probe_reports_gaps_and_deliberates_about_nothing():
    script = {
        qname("lacked", 0): {"noul": 0.95},
        qname("wouldchange", 0): {"noul": 0.9},
        qname("lacked", 1): {"noul": 0.8},
        qname("wouldchange", 1): {"noul": 0.3},
        qname("lacked", 2): {"noul": 0.05},
        qname("wouldchange", 2): {"noul": 0.9},
    }
    jev = FakeJevClient(script)
    outcome = await _run("probe", jev, _probe_extractor())

    analysis = outcome.analysis
    assert outcome.shape == "probe"
    assert analysis.calibration.shape == "probe"
    # Sorted by what a second pass would be worth; the one already stated is dropped.
    assert [e.item for e in analysis.needs_evidence] == ["the row count", "the query plan"]
    assert analysis.needs_evidence[0].would_change == 0.9
    # It measures nothing about agreement, by design.
    assert analysis.consensus == []
    assert analysis.contradictions == []
    assert analysis.calibration.claims == []
    assert analysis.calibration.agreement == []
    assert "does not deliberate" in analysis.confidence_notes


async def test_probe_is_cheaper_than_hybrid_on_the_same_panel():
    """No support matrix, no contested question, no standing rubric."""
    probe_jev, hybrid_jev = FakeJevClient(), FakeJevClient()
    await _run("probe", probe_jev, _probe_extractor())
    await _run("hybrid", hybrid_jev, _extractor())

    probe_qs = len(probe_jev.calls[0]["questions"])
    hybrid_qs = sum(len(c["questions"]) for c in hybrid_jev.calls)
    assert probe_qs < hybrid_qs, (probe_qs, hybrid_qs)


async def test_probe_works_with_a_single_member_panel():
    """The configuration the benchmark measured: one model, no deliberation."""
    one = [RawAnswer(id="solo", model="m", answer="an answer that assumes a row count")]
    outcome = await run_judge(
        "q",
        one,
        shape="probe",
        deadline=time.monotonic() + 30,
        jev_client=FakeJevClient({qname("lacked", 0): {"noul": 0.9}}),
        analysis_provider=FakeChatProvider(
            "ja", text=json.dumps({"missing_evidence": ["the row count"]})
        ),
    )
    assert outcome.shape == "probe"
    assert [e.item for e in outcome.analysis.needs_evidence] == ["the row count"]
    assert [p.id for p in outcome.analysis.calibration.per_answer] == ["solo"]


async def test_probe_does_not_fall_back_to_a_shape_that_deliberates():
    """It was asked a specific question; answering a different one would be worse than
    saying nothing."""
    from orchestrator.tests.helpers import ANALYSIS_JSON_IDS

    outcome = await _run(
        "probe",
        FakeJevClient(error="HTTP 503: down"),
        FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
    )
    assert outcome.analysis is None
    assert outcome.shape == "probe"
    assert outcome.fallback_from is None


@pytest.mark.asyncio
async def test_calibration_reports_the_jev_work_that_produced_it():
    """``questions_asked``/``calls`` are in the documented response shape, and were
    declared but never assigned, so every analysis reported 0 for both."""
    answers = [
        RawAnswer(id="a", model="m", answer="Split the monolith."),
        RawAnswer(id="b", model="m", answer="Fix the test suite first."),
    ]
    outcome = await run_judge(
        "monolith or services?",
        answers,
        shape="matrix",
        jev_client=FakeJevClient(),
        analysis_provider=None,
        deadline=time.monotonic() + 30,
    )
    calibration = outcome.analysis.calibration
    assert calibration.calls == outcome.jev_calls == 1
    assert calibration.questions_asked == outcome.jev_questions > 0
