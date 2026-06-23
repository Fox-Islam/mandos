from __future__ import annotations

import asyncio

import pytest

from orchestrator.models import Analysis, RawAnswer
from orchestrator.sessions import (
    PERSISTED_TURNS_CAP,
    append_turn,
    apply_prior_answer,
    assistant_content_for_turn,
    build_messages,
    clear_sessions,
    estimate_messages_tokens,
    load_session,
    session_lock,
    session_path,
    validate_thread_id,
    write_session,
)


def test_thread_id_validation_and_path_containment(tmp_path):
    assert validate_thread_id("abc_123-X") == "abc_123-X"
    for bad in ("", "../x", "x/y", "a" * 65):
        with pytest.raises(ValueError):
            session_path(bad, root=tmp_path)

    path = session_path("abc", root=tmp_path)
    assert path.parent == tmp_path
    assert path.name == "abc.json"


def test_session_write_load_clear(tmp_path):
    data = {"thread_id": "abc", "turns": [{"prompt": "q"}]}
    path = write_session(data, root=tmp_path)
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()
    assert load_session("abc", root=tmp_path)["created"]
    assert load_session("abc", root=tmp_path)["turns"][0]["prompt"] == "q"
    assert clear_sessions(thread_id="abc", root=tmp_path) == 1
    assert not path.exists()


def test_prior_answer_analysis_and_placeholder_fallbacks():
    turn = {"user": "q", "assistant": "final answer"}
    assert assistant_content_for_turn(turn) == "final answer"

    legacy_turn = {"prompt": "q", "prior_answer": "legacy final answer"}
    assert assistant_content_for_turn(legacy_turn) == "legacy final answer"

    analysis = Analysis(consensus=["c"], confidence_notes="steady").model_dump()
    assert "Consensus: c" in assistant_content_for_turn({"user": "q", "analysis": analysis})
    assert "no final answer" in assistant_content_for_turn({"user": "q"})


def test_build_messages_compacts_and_applies_prior_answer():
    session = {"thread_id": "abc", "turns": []}
    raw = [RawAnswer(id="a", model="m", answer="raw")]
    append_turn(session, prompt="q1", context=None, raw_answers=raw, analysis=None, compacted=False)
    apply_prior_answer(session, "answer 1")
    append_turn(
        session,
        prompt="q2",
        context="ctx",
        raw_answers=raw,
        analysis=None,
        compacted=False,
    )

    assert session["turns"][0]["assistant"] == "answer 1"
    assert session["turns"][0]["panel_raw"][0]["answer"] == "raw"

    messages, compacted = build_messages(
        session,
        system="sys",
        prompt="q3",
        context=None,
        max_history_turns=1,
    )
    assert compacted is True
    assert messages[0].role == "system"
    assert any("compacted" in message.content for message in messages)
    assert [message.content for message in messages if message.role == "user"] == [
        "q2\n\nCONTEXT:\nctx",
        "q3",
    ]


def test_build_messages_compacts_against_context_window():
    session = {"thread_id": "abc", "turns": []}
    raw = [RawAnswer(id="a", model="m", answer="raw")]
    for index in range(4):
        append_turn(
            session,
            prompt=f"q{index} " + ("x" * 80),
            context=None,
            raw_answers=raw,
            analysis=None,
            compacted=False,
        )
        apply_prior_answer(session, "answer " + ("y" * 80))

    messages, compacted = build_messages(
        session,
        system="sys",
        prompt="current",
        context=None,
        max_history_turns=12,
        context_window=80,
        expected_output_tokens=8,
        warning_ratio=0.75,
    )

    assert compacted is True
    assert any("compacted" in message.content for message in messages)
    assert estimate_messages_tokens(messages, expected_output_tokens=8) <= 60


def test_persisted_turns_are_capped_keeping_newest():
    session = {"thread_id": "abc", "turns": []}
    raw = [RawAnswer(id="a", model="m", answer="raw")]
    total = PERSISTED_TURNS_CAP + 25
    for index in range(total):
        append_turn(
            session,
            prompt=f"q{index}",
            context=None,
            raw_answers=raw,
            analysis=None,
            compacted=False,
        )
    assert len(session["turns"]) == PERSISTED_TURNS_CAP
    assert session["turns"][0]["user"] == f"q{total - PERSISTED_TURNS_CAP}"
    assert session["turns"][-1]["user"] == f"q{total - 1}"


@pytest.mark.asyncio
async def test_session_lock_serializes_same_thread(tmp_path):
    order: list[str] = []

    async def run(label: str) -> None:
        async with session_lock("same-thread", root=tmp_path):
            order.append(f"start-{label}")
            await asyncio.sleep(0.01)
            order.append(f"end-{label}")

    await asyncio.gather(run("a"), run("b"))

    assert order in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )
