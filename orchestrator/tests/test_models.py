"""Deliberation contract: Analysis + bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.models import (
    Analysis,
    ChatMessage,
    ChatRequest,
    DeliberateRequest,
    DeliberationResponse,
    RawAnswer,
)


def test_analysis_partial_coverage_carries_ids():
    analysis = Analysis.model_validate(
        {
            "consensus": ["a"],
            "contradictions": [{"topic": "t", "positions": [{"ids": ["A"], "claim": "c"}]}],
            "partial_coverage": [{"ids": ["B"], "point": "only B covered this"}],
            "unique_insights": [{"id": "A", "insight": "i"}],
            "blind_spots": ["w"],
            "confidence_notes": "ok",
        }
    )
    assert analysis.partial_coverage[0].ids == ["B"]
    assert analysis.contradictions[0].positions[0].ids == ["A"]
    assert analysis.unique_insights[0].id == "A"


def test_removed_request_fields_are_ignored():
    req = DeliberateRequest(prompt="hi")
    assert not hasattr(req, "include_raw")
    assert not hasattr(req, "anonymize")
    assert not hasattr(req, "curation_model")


def test_response_shape_is_analysis_plus_raw_answers():
    response = DeliberationResponse(
        question="q",
        analysis=Analysis(consensus=["yes"]),
        raw_answers=[RawAnswer(id="a", answer="raw")],
    )
    data = response.model_dump()
    assert data["analysis"]["consensus"] == ["yes"]
    assert data["raw_answers"][0]["answer"] == "raw"
    assert "curated_evidence" not in data
    assert "curation_notes" not in data


def test_chat_request_accepts_messages_seam():
    req = ChatRequest(
        messages=[
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="q"),
        ]
    )
    assert req.messages is not None
    assert req.system == ""
    assert req.user == ""


def test_session_request_and_response_fields():
    req = DeliberateRequest(prompt="q", thread_id="abc", prior_answer="answer")
    assert req.thread_id == "abc"
    resp = DeliberationResponse(question="q", thread_id="abc", compacted=True)
    assert resp.thread_id == "abc"
    assert resp.compacted is True


def test_thread_id_pattern_is_enforced():
    assert DeliberateRequest(prompt="q", thread_id=None).thread_id is None
    assert DeliberateRequest(prompt="q", thread_id="A_b-9").thread_id == "A_b-9"
    assert DeliberateRequest(prompt="q", thread_id="x" * 64).thread_id == "x" * 64
    for bad in ("bad/thread", "with space", "", "x" * 65, "dotted.id"):
        with pytest.raises(ValidationError):
            DeliberateRequest(prompt="q", thread_id=bad)


def test_panel_is_bounded_one_to_eight():
    assert DeliberateRequest(prompt="p", panel=None).panel is None
    assert len(DeliberateRequest(prompt="p", panel=["x"] * 8).panel) == 8
    with pytest.raises(ValidationError):
        DeliberateRequest(prompt="p", panel=[])
    with pytest.raises(ValidationError):
        DeliberateRequest(prompt="p", panel=["x"] * 9)


def test_context_and_prior_answer_are_bounded():
    assert DeliberateRequest(prompt="p", context="x" * 10, prior_answer="y" * 10)
    with pytest.raises(ValidationError):
        DeliberateRequest(prompt="p", context="x" * 200_001)
    with pytest.raises(ValidationError):
        DeliberateRequest(prompt="p", prior_answer="y" * 100_001)


def test_analysis_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        Analysis.model_validate({"consensus": ["x"], "final_answer": "smuggled"})


def test_analysis_rejects_empty_payload():
    with pytest.raises(ValidationError):
        Analysis.model_validate({})
