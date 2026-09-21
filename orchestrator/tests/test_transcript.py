"""Conversation capture: redaction, bounds, staleness, and what the panel is told."""

from __future__ import annotations

import time

import pytest

from orchestrator import transcript
from orchestrator.models import DeliberateRequest
from orchestrator.panel import run_deliberation
from orchestrator.settings import ContextConfig
from orchestrator.tests.helpers import all_ok_mapping, make_config, patch_providers


def test_credentials_never_reach_the_file(tmp_path):
    turns = [
        {
            "role": "user",
            "content": "here is sk-abcdefghijklmnopqrstuvwxyz and ghp_0123456789abcdefghij",
        },
        {"role": "user", "content": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfi"},
        {"role": "assistant", "content": "Authorization: Bearer abcdefghijklmnopqrs"},
    ]
    transcript.write_capture(turns, key="k", root=tmp_path)

    raw = (tmp_path / "k.json").read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in raw
    assert "ghp_0123456789abcdefghij" not in raw
    assert "wJalrXUtnFEMI" not in raw
    assert "abcdefghijklmnopqrs" not in raw
    assert "redacted" in raw


def test_the_capture_is_written_private(tmp_path):
    path = transcript.write_capture([{"role": "user", "content": "hi"}], key="k", root=tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_only_the_most_recent_turns_are_kept(tmp_path):
    turns = [{"role": "user", "content": f"turn {i}"} for i in range(30)]
    transcript.write_capture(turns, key="k", root=tmp_path, max_turns=3)
    kept = transcript.read_capture(key="k", root=tmp_path)
    assert [t["content"] for t in kept] == ["turn 27", "turn 28", "turn 29"]


def test_the_character_budget_is_spent_from_the_newest_backwards(tmp_path):
    turns = [
        {"role": "user", "content": "OLD" * 100},
        {"role": "user", "content": "NEW" * 10},
    ]
    transcript.write_capture(turns, key="k", root=tmp_path, max_chars=60)
    kept = transcript.read_capture(key="k", root=tmp_path)
    assert kept[-1]["content"] == "NEW" * 10
    assert len("".join(t["content"] for t in kept)) <= 60 + len("...")


def test_a_stale_capture_is_ignored(tmp_path):
    transcript.write_capture([{"role": "user", "content": "old news"}], key="k", root=tmp_path)
    fresh = transcript.read_capture(key="k", root=tmp_path, max_age_s=3600)
    stale = transcript.read_capture(key="k", root=tmp_path, max_age_s=60, now=time.time() + 3600)
    assert fresh and not stale


def test_an_unreadable_capture_degrades_to_nothing(tmp_path):
    (tmp_path / "k.json").write_text("{not json", encoding="utf-8")
    assert transcript.read_capture(key="k", root=tmp_path) == []
    assert transcript.read_capture(key="missing", root=tmp_path) == []


def test_two_projects_do_not_read_each_others_conversation():
    assert transcript.capture_key(cwd="/a") != transcript.capture_key(cwd="/b")
    assert transcript.capture_key(session_id="s1") != transcript.capture_key(session_id="s2")
    assert transcript.capture_key(cwd="/a") == transcript.capture_key(cwd="/a")


async def test_the_panel_is_shown_the_conversation_when_one_was_captured(monkeypatch, tmp_path):
    transcript.write_capture(
        [{"role": "user", "content": "we are migrating off Redis"}], key="k", root=tmp_path
    )
    monkeypatch.setattr("orchestrator.panel.capture_key", lambda *a, **k: "k")
    monkeypatch.setattr(
        "orchestrator.panel.read_capture",
        lambda **kw: transcript.read_capture(root=tmp_path, **kw),
    )
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    await run_deliberation(DeliberateRequest(prompt="what next?"), make_config())
    sent = mapping["a"].requests[0].user
    assert "CONVERSATION SO FAR" in sent
    assert "migrating off Redis" in sent
    assert sent.rstrip().endswith("what next?")


async def test_one_call_can_keep_the_panel_blind_to_it(monkeypatch, tmp_path):
    transcript.write_capture(
        [{"role": "user", "content": "private tangent"}], key="k", root=tmp_path
    )
    monkeypatch.setattr("orchestrator.panel.capture_key", lambda *a, **k: "k")
    monkeypatch.setattr(
        "orchestrator.panel.read_capture",
        lambda **kw: transcript.read_capture(root=tmp_path, **kw),
    )
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    await run_deliberation(DeliberateRequest(prompt="q", use_conversation=False), make_config())
    assert "private tangent" not in mapping["a"].requests[0].user


async def test_config_can_turn_it_off_entirely(monkeypatch, tmp_path):
    transcript.write_capture([{"role": "user", "content": "nope"}], key="k", root=tmp_path)
    monkeypatch.setattr("orchestrator.panel.capture_key", lambda *a, **k: "k")
    monkeypatch.setattr(
        "orchestrator.panel.read_capture",
        lambda **kw: transcript.read_capture(root=tmp_path, **kw),
    )
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    config = make_config()
    config.context = ContextConfig(from_transcript=False)
    await run_deliberation(DeliberateRequest(prompt="q"), config)
    assert "nope" not in mapping["a"].requests[0].user


async def test_no_capture_composes_exactly_as_before(monkeypatch):
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)
    await run_deliberation(DeliberateRequest(prompt="q", context="c"), make_config())
    assert mapping["a"].requests[0].user == "q\n\nCONTEXT:\nc"


def test_status_reports_whether_the_panel_sees_the_conversation():
    status = make_config().safe_status()
    assert status["context"]["from_transcript"] is True
    assert status["context"]["max_turns"] == 12


@pytest.mark.parametrize("text", ["", "nothing secret here"])
def test_redaction_leaves_ordinary_text_alone(text):
    assert transcript.redact(text) == text
