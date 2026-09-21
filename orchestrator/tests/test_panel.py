"""Panel fan-out, failure taxonomy, degradation matrix, rendering."""

from __future__ import annotations

import json
from typing import get_args

import pytest
from structlog.testing import capture_logs

from orchestrator import sessions
from orchestrator.fakes import FakeChatProvider
from orchestrator.models import DeliberateRequest, FailureKind, TokenUsage
from orchestrator.panel import failure_response, run_deliberation
from orchestrator.settings import Price
from orchestrator.tests.helpers import (
    ANALYSIS_JSON_IDS,
    all_ok_mapping,
    make_config,
    patch_providers,
)


async def _run(monkeypatch, mapping, **req_kw):
    patch_providers(monkeypatch, mapping)
    return await run_deliberation(DeliberateRequest(prompt="q", **req_kw), make_config())


@pytest.mark.asyncio
async def test_partial_results_one_provider_fails(monkeypatch):
    mapping = all_ok_mapping()
    mapping["b"] = FakeChatProvider("b", error="HTTP 500: boom")
    resp = await _run(monkeypatch, mapping)
    assert resp.meta["ok"] == 1
    assert resp.meta["failed"] == 1
    statuses = {a.id: a.status for a in resp.panel}
    assert statuses == {"a": "ok", "b": "error"}
    assert resp.analysis is not None


@pytest.mark.asyncio
async def test_all_panels_failed_short_circuits(monkeypatch):
    mapping = all_ok_mapping()
    mapping["a"] = FakeChatProvider("a", error="boom")
    mapping["b"] = FakeChatProvider("b", error="boom")
    resp = await _run(monkeypatch, mapping)
    assert resp.meta["ok"] == 0
    assert resp.meta["failure"] == "all_panels_failed"
    assert resp.analysis is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "err,expected",
    [
        ("HTTP 429: slow down", "rate_limited"),
        ("insufficient credits", "insufficient_credits"),
        ("connection refused", "all_panels_failed"),
    ],
)
async def test_failure_taxonomy_classification(monkeypatch, err, expected):
    mapping = all_ok_mapping()
    mapping["a"] = FakeChatProvider("a", error=err)
    mapping["b"] = FakeChatProvider("b", error=err)
    resp = await _run(monkeypatch, mapping)
    assert resp.meta["failure"] == expected


@pytest.mark.asyncio
async def test_all_ok_returns_analysis_and_raw(monkeypatch):
    resp = await _run(monkeypatch, all_ok_mapping())
    assert resp.analysis is not None
    assert {r.id for r in resp.raw_answers} == {"a", "b"}
    assert "judge_error" not in resp.meta


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "panel,fragment",
    [
        (["a", "a"], "duplicate"),
        (["nope"], "unknown or non-panel"),
        (["ja"], "non-panel"),
    ],
)
async def test_bad_panel_returns_typed_envelope_not_raises(monkeypatch, panel, fragment):
    patch_providers(monkeypatch, all_ok_mapping())
    resp = await run_deliberation(DeliberateRequest(prompt="q", panel=panel), make_config())
    assert resp.meta["failure"] == "unexpected_error"
    assert fragment in resp.meta["error"]
    assert resp.text
    assert {"panel_size", "cost_estimate_usd", "budget", "depth"} <= resp.meta.keys()


@pytest.mark.asyncio
async def test_degradation_analysis_failure_keeps_raw(monkeypatch):
    mapping = all_ok_mapping(analysis_text="not json")
    resp = await _run(monkeypatch, mapping)
    assert resp.analysis is None
    assert resp.meta["judge_error"]
    assert {r.id for r in resp.raw_answers} == {"a", "b"}


@pytest.mark.asyncio
async def test_markdown_carries_analysis_and_raw_answers(monkeypatch):
    resp = await _run(monkeypatch, all_ok_mapping())
    assert "## Analysis" in resp.text
    assert "## Raw answers" in resp.text
    assert "only a saw Z" in resp.text


@pytest.mark.asyncio
async def test_a_chain_shorter_than_the_cap_is_allowed(monkeypatch):
    """The guard bounds the evidence loop, not forbid it: a
    caller that fetches what the judge asked for and convenes again must get through."""
    patch_providers(monkeypatch, all_ok_mapping())
    resp = await run_deliberation(DeliberateRequest(prompt="q", depth=1), make_config())
    assert resp.meta.get("failure") is None
    assert resp.meta["depth"] == 1
    assert resp.analysis is not None


async def test_depth_guard_caps_recursion(monkeypatch):
    patch_providers(monkeypatch, all_ok_mapping())
    resp = await run_deliberation(DeliberateRequest(prompt="q", depth=3), make_config())
    assert resp.meta["failure"] == "fusion_invocation_capped"
    assert resp.meta["ok"] == 0


@pytest.mark.asyncio
async def test_request_analysis_model_must_be_judge_role(monkeypatch):
    mapping = all_ok_mapping()
    resp = await _run(monkeypatch, mapping, analysis_model="a")
    assert resp.analysis is None
    assert "judge-role" in resp.meta["judge_error"]
    assert {r.id for r in resp.raw_answers} == {"a", "b"}
    assert len(mapping["a"].requests) == 1
    assert mapping["ja"].requests == []


@pytest.mark.asyncio
async def test_request_analysis_model_unknown_provider_degrades(monkeypatch):
    mapping = all_ok_mapping()
    resp = await _run(monkeypatch, mapping, analysis_model="ghost")
    assert resp.analysis is None
    assert "not an enabled provider" in resp.meta["judge_error"]
    assert {r.id for r in resp.raw_answers} == {"a", "b"}


@pytest.mark.asyncio
async def test_depth_cap_meta_matches_success_key_set(monkeypatch):
    patch_providers(monkeypatch, all_ok_mapping())
    success = await run_deliberation(DeliberateRequest(prompt="q"), make_config())
    capped = await run_deliberation(DeliberateRequest(prompt="q", depth=3), make_config())
    assert capped.meta["failure"] == "fusion_invocation_capped"
    assert set(success.meta) <= set(capped.meta)


@pytest.mark.asyncio
async def test_emitted_failures_are_failurekind_members(monkeypatch):
    valid = set(get_args(FailureKind))
    failed = all_ok_mapping()
    failed["a"] = FakeChatProvider("a", error="boom")
    failed["b"] = FakeChatProvider("b", error="boom")
    r1 = await _run(monkeypatch, failed)
    patch_providers(monkeypatch, all_ok_mapping())
    r2 = await run_deliberation(DeliberateRequest(prompt="q", depth=3), make_config())
    r3 = await run_deliberation(DeliberateRequest(prompt="q", panel=["a", "a"]), make_config())
    for resp in (r1, r2, r3):
        assert resp.meta["failure"] in valid


@pytest.mark.asyncio
async def test_classification_is_deterministic_and_credentials_safe(monkeypatch):
    mixed = all_ok_mapping()
    mixed["a"] = FakeChatProvider("a", error="HTTP 429: slow down")
    mixed["b"] = FakeChatProvider("b", error="insufficient credits")
    assert (await _run(monkeypatch, mixed)).meta["failure"] == "insufficient_credits"

    mixed_rev = all_ok_mapping()
    mixed_rev["a"] = FakeChatProvider("a", error="insufficient credits")
    mixed_rev["b"] = FakeChatProvider("b", error="HTTP 429: slow down")
    assert (await _run(monkeypatch, mixed_rev)).meta["failure"] == "insufficient_credits"

    creds = all_ok_mapping()
    creds["a"] = FakeChatProvider("a", error="HTTP 401: invalid credentials")
    creds["b"] = FakeChatProvider("b", error="HTTP 401: invalid credentials")
    assert (await _run(monkeypatch, creds)).meta["failure"] == "all_panels_failed"


@pytest.mark.asyncio
async def test_cost_estimate_present(monkeypatch):
    resp = await _run(monkeypatch, all_ok_mapping())
    assert "cost_estimate_usd" in resp.meta


@pytest.mark.asyncio
async def test_judge_cost_fallback_counts_question_text(monkeypatch):
    # Only the judge is priced and reports no usage, so its cost comes from the
    # char-estimate over the input it received (which includes the question).
    mapping = all_ok_mapping()
    mapping["ja"] = FakeChatProvider("ja", text=ANALYSIS_JSON_IDS, usage=TokenUsage())
    patch_providers(monkeypatch, mapping)
    config = make_config()
    config.pricing = {"ja": Price.model_validate({"in": 1.0, "out": 1.0})}

    short = await run_deliberation(DeliberateRequest(prompt="hi"), config)
    long = await run_deliberation(DeliberateRequest(prompt="x" * 4000), config)

    assert long.meta["cost_basis"]["by_provider"]["ja"]["usage_source"] == "estimated"
    assert long.meta["cost_estimate_usd"] > short.meta["cost_estimate_usd"]


@pytest.mark.asyncio
async def test_panel_cost_fallback_counts_session_history(monkeypatch, tmp_path):
    # Only a panel provider is priced and reports no usage; in session mode its cost
    # must reflect the reconstructed history it was sent, not just the prompt.
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    mapping = all_ok_mapping()
    mapping["a"] = FakeChatProvider("a", text="answer from a", usage=TokenUsage())
    patch_providers(monkeypatch, mapping)
    config = make_config()
    config.pricing = {"a": Price.model_validate({"in": 1.0, "out": 1.0})}

    await run_deliberation(DeliberateRequest(prompt="x" * 4000, thread_id="hist"), config)
    followup = await run_deliberation(
        DeliberateRequest(prompt="short", thread_id="hist", prior_answer="ok"), config
    )
    fresh = await run_deliberation(DeliberateRequest(prompt="short", thread_id="fresh"), config)

    a_hist = followup.meta["cost_basis"]["by_provider"]["a"]
    a_fresh = fresh.meta["cost_basis"]["by_provider"]["a"]
    assert a_hist["usage_source"] == "estimated"
    assert a_hist["usd"] > a_fresh["usd"]


@pytest.mark.asyncio
async def test_cost_basis_is_advisory_with_coverage(monkeypatch):
    resp = await _run(monkeypatch, all_ok_mapping())
    basis = resp.meta["cost_basis"]
    assert basis["advisory"] is True
    assert {"priced_calls", "unpriced_calls", "estimated_calls", "by_provider"} <= basis.keys()


@pytest.mark.asyncio
async def test_all_branches_carry_cost_keys(monkeypatch):
    patch_providers(monkeypatch, all_ok_mapping())
    success = await run_deliberation(DeliberateRequest(prompt="q"), make_config())
    failed = all_ok_mapping()
    failed["a"] = FakeChatProvider("a", error="boom")
    failed["b"] = FakeChatProvider("b", error="boom")
    fail = await _run(monkeypatch, failed)
    patch_providers(monkeypatch, all_ok_mapping())
    cap = await run_deliberation(DeliberateRequest(prompt="q", depth=3), make_config())
    for resp in (success, fail, cap):
        assert "cost_estimate_usd" in resp.meta
        assert "cost_basis" in resp.meta
        assert resp.meta["contract_version"] == "1"


@pytest.mark.asyncio
async def test_emits_log_events_without_leaking_prompt(monkeypatch):
    patch_providers(monkeypatch, all_ok_mapping())
    secret = "SUPERSECRETPROMPTTOKEN12345"
    with capture_logs() as logs:
        await run_deliberation(DeliberateRequest(prompt=secret), make_config())
    events = {entry["event"] for entry in logs}
    assert {
        "deliberation.start",
        "provider.result",
        "judge.result",
        "deliberation.done",
    } <= events
    assert secret not in json.dumps(logs, default=str)


@pytest.mark.asyncio
async def test_provider_error_log_redacts_error_body(monkeypatch):
    secret = "SUPERSECRETPROMPTTOKEN12345"
    mapping = all_ok_mapping()
    mapping["b"] = FakeChatProvider("b", error=f"HTTP 500: upstream echoed {secret}")
    patch_providers(monkeypatch, mapping)

    with capture_logs() as logs:
        await run_deliberation(DeliberateRequest(prompt=secret), make_config())

    blob = json.dumps(logs, default=str)
    assert secret not in blob
    provider_logs = [
        entry for entry in logs if entry["event"] == "provider.result" and entry["provider"] == "b"
    ]
    assert provider_logs[0]["error_kind"] == "HTTP 500"
    assert "error" not in provider_logs[0]


@pytest.mark.asyncio
async def test_budget_meta_uses_context_window(monkeypatch):
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)
    config = make_config()
    config.providers[0].resolved_context_window = 32
    config.providers[0].context_window_source = "endpoint"
    resp = await run_deliberation(
        DeliberateRequest(prompt="x" * 80, max_tokens=16),
        config,
    )
    budget = {item["provider_id"]: item for item in resp.meta["budget"]}
    assert budget["a"]["state"] == "red"
    assert budget["a"]["context_window"] == 32


@pytest.mark.asyncio
async def test_session_mode_reconstructs_messages_and_stores_turns(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    first = await run_deliberation(DeliberateRequest(prompt="first", thread_id="t1"), make_config())
    assert first.thread_id == "t1"
    assert (tmp_path / "t1.json").exists()

    second = await run_deliberation(
        DeliberateRequest(prompt="second", thread_id="t1", prior_answer="final answer one"),
        make_config(),
    )

    assert second.thread_id == "t1"
    request = mapping["a"].requests[-1]
    assert request.messages is not None
    assert [message.role for message in request.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert request.messages[2].content == "final answer one"


@pytest.mark.asyncio
async def test_corrupt_session_store_degrades_to_fresh_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    (tmp_path / "t1.json").write_text("{not-json", encoding="utf-8")
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    resp = await run_deliberation(DeliberateRequest(prompt="fresh", thread_id="t1"), make_config())

    assert resp.meta["ok"] == 2
    assert "session load failed" in resp.meta["session_warning"]
    assert mapping["a"].requests[-1].messages is not None
    assert [message.role for message in mapping["a"].requests[-1].messages] == [
        "system",
        "user",
    ]


@pytest.mark.asyncio
async def test_session_write_failure_returns_response_with_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))

    def fail_write(session):  # noqa: ARG001
        raise OSError("disk")

    monkeypatch.setattr("orchestrator.panel.write_session", fail_write)
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    resp = await run_deliberation(DeliberateRequest(prompt="fresh", thread_id="t1"), make_config())

    assert resp.meta["ok"] == 2
    assert "session write failed" in resp.meta["session_warning"]


@pytest.mark.asyncio
async def test_session_double_failure_preserves_both_warnings(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))

    def fail_load(thread_id, **kw):  # noqa: ARG001
        raise OSError("load")

    def fail_write(session):  # noqa: ARG001
        raise OSError("write")

    monkeypatch.setattr("orchestrator.panel.load_session", fail_load)
    monkeypatch.setattr("orchestrator.panel.write_session", fail_write)
    patch_providers(monkeypatch, all_ok_mapping())

    resp = await run_deliberation(DeliberateRequest(prompt="q", thread_id="t1"), make_config())

    warning = resp.meta["session_warning"]
    assert "session load failed" in warning
    assert "session write failed" in warning
    assert resp.meta["ok"] == 2


@pytest.mark.asyncio
async def test_session_compaction_is_per_provider_and_budget_uses_messages(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)
    config = make_config(session_max_turns=12, max_tokens=8)
    config.providers[0].context_window = 80
    config.providers[1].context_window = 1000

    for index in range(4):
        await run_deliberation(
            DeliberateRequest(
                prompt=f"turn {index} " + ("x" * 80),
                thread_id="thread",
                prior_answer="answer " + ("y" * 80),
            ),
            config,
        )

    resp = await run_deliberation(
        DeliberateRequest(prompt="current", thread_id="thread", prior_answer="last answer"),
        config,
    )

    small_messages = mapping["a"].requests[-1].messages
    large_messages = mapping["b"].requests[-1].messages
    assert small_messages is not None
    assert large_messages is not None
    assert len(small_messages) < len(large_messages)
    assert resp.compacted is True
    assert resp.meta["compacted_providers"] == ["a"]
    budget = {item["provider_id"]: item for item in resp.meta["budget"]}
    assert budget["a"]["estimated_tokens"] < budget["b"]["estimated_tokens"]
    assert budget["a"]["state"] != "unknown"


async def test_meta_reports_which_judge_ran_and_what_it_cost(monkeypatch):
    """The host must be able to tell a calibrated analysis from a fallback one."""
    from orchestrator.fakes import FakeJevClient
    from orchestrator.settings import JudgeConfig

    jev = FakeJevClient(cost=0.0003)
    monkeypatch.setattr("orchestrator.panel.build_jev_client", lambda judge, client: jev)
    patch_providers(monkeypatch, all_ok_mapping())

    config = make_config(judge=JudgeConfig(shape="matrix"))
    resp = await run_deliberation(DeliberateRequest(prompt="q"), config)

    assert resp.meta["judge_shape"] == "matrix"
    assert resp.meta["judge_fallback_from"] is None
    assert resp.meta["judge_provider"] == "typesafe"
    assert resp.meta["jev_calls"] == 1
    assert resp.meta["jev_questions"] == len(jev.calls[0]["questions"])
    assert resp.meta["cost_basis"]["jev_usd"] == 0.0003
    assert resp.meta["cost_estimate_usd"] >= 0.0003
    assert resp.analysis.calibration.shape == "matrix"


async def test_unpriced_jev_leaves_the_judge_cost_null_rather_than_zero(monkeypatch):
    from orchestrator.fakes import FakeJevClient
    from orchestrator.settings import JudgeConfig

    monkeypatch.setattr(
        "orchestrator.panel.build_jev_client", lambda judge, client: FakeJevClient(cost=None)
    )
    patch_providers(monkeypatch, all_ok_mapping())

    resp = await run_deliberation(
        DeliberateRequest(prompt="q"), make_config(judge=JudgeConfig(shape="matrix"))
    )
    assert resp.meta["cost_basis"]["jev_usd"] is None


async def test_meta_records_the_fallback_when_jev_cannot_deliver(monkeypatch):
    from orchestrator.fakes import FakeJevClient
    from orchestrator.settings import JudgeConfig

    monkeypatch.setattr(
        "orchestrator.panel.build_jev_client",
        lambda judge, client: FakeJevClient(error="HTTP 503: upstream"),
    )
    patch_providers(monkeypatch, all_ok_mapping())

    resp = await run_deliberation(
        DeliberateRequest(prompt="q"), make_config(judge=JudgeConfig(shape="matrix"))
    )
    assert resp.meta["judge_shape"] == "llm"
    assert resp.meta["judge_fallback_from"] == "matrix"
    assert "503" in resp.meta["judge_error"]
    assert resp.analysis.consensus == ["agree on X"]


async def test_the_judge_is_sent_the_same_context_the_panel_was(monkeypatch):
    mapping = all_ok_mapping()
    patch_providers(monkeypatch, mapping)

    await run_deliberation(
        DeliberateRequest(prompt="q", context="shared background"), make_config()
    )
    judge_request = mapping["ja"].requests[0]
    assert "shared background" in judge_request.user


async def test_rendered_markdown_carries_the_numbers_behind_each_finding(monkeypatch):
    from orchestrator.fakes import FakeJevClient
    from orchestrator.jev import qname
    from orchestrator.settings import JudgeConfig

    extraction = json.dumps({"claims": ["everyone agrees"], "blind_spots": []})
    script = {
        qname("support", 0, "a"): {"noul": 0.91},
        qname("support", 0, "b"): {"noul": 0.88},
        qname("contested", 0): {"noul": 0.05},
        qname("standing", 0): {"score": 1.8},
    }
    monkeypatch.setattr(
        "orchestrator.panel.build_jev_client", lambda judge, client: FakeJevClient(script)
    )
    patch_providers(monkeypatch, all_ok_mapping(analysis_text=extraction))

    resp = await run_deliberation(
        DeliberateRequest(prompt="q"), make_config(judge=JudgeConfig(shape="hybrid"))
    )
    assert "*Judge: hybrid.*" in resp.text
    assert "- everyone agrees _(support 0.88-0.91; contested 0.05; standing 1.80/2)_" in resp.text


async def test_every_branch_carries_the_jev_cost_key(monkeypatch):
    """`cost_basis.jev_usd` must be readable unconditionally, and must stay null when
    nothing priced the judging - null and 0.0 are different answers."""
    from orchestrator.fakes import FakeJevClient
    from orchestrator.settings import JudgeConfig

    capped = failure_response("q", failure="fusion_invocation_capped", error="e")
    assert capped.meta["cost_basis"]["jev_usd"] is None

    patch_providers(
        monkeypatch,
        {
            "a": FakeChatProvider("a", error="HTTP 500: down"),
            "b": FakeChatProvider("b", error="HTTP 500: down"),
            "ja": FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
        },
    )
    all_failed = await run_deliberation(DeliberateRequest(prompt="q"), make_config())
    assert all_failed.meta["cost_basis"]["jev_usd"] is None

    monkeypatch.setattr(
        "orchestrator.panel.build_jev_client", lambda judge, client: FakeJevClient(cost=0.0002)
    )
    patch_providers(monkeypatch, all_ok_mapping())
    ok = await run_deliberation(
        DeliberateRequest(prompt="q"), make_config(judge=JudgeConfig(shape="matrix"))
    )
    assert ok.meta["cost_basis"]["jev_usd"] == 0.0002


async def test_progress_is_reported_per_panel_member_and_for_the_judge(monkeypatch):
    """A deliberation is one blocking call that can run a minute and a half; without
    this the host shows nothing at all while several models think."""
    patch_providers(monkeypatch, all_ok_mapping())
    seen: list[tuple[float, float, str]] = []

    async def record(done, total, message):
        seen.append((done, total, message))

    await run_deliberation(DeliberateRequest(prompt="q"), make_config(), on_progress=record)

    assert [s[1] for s in seen] == [3, 3, 3, 3, 3]  # 2 panel members + the judge
    assert [s[0] for s in seen] == [0, 1, 2, 2, 3]
    assert "asking 2 panel members" in seen[0][2]
    assert "answered (ok)" in seen[1][2]
    assert "judging" in seen[3][2]
    assert "judge finished (llm)" in seen[4][2]


async def test_a_host_that_cannot_receive_progress_does_not_lose_its_answer(monkeypatch):
    patch_providers(monkeypatch, all_ok_mapping())

    async def explode(done, total, message):
        raise RuntimeError("no transport")

    resp = await run_deliberation(DeliberateRequest(prompt="q"), make_config(), on_progress=explode)
    assert resp.analysis is not None


async def test_meta_says_whether_another_pass_is_worth_it(monkeypatch):
    """The caller decides whether to loop, so it needs both halves: how many decisive
    gaps are open, and how many passes the guard will still allow."""
    import json as _json

    from orchestrator.fakes import FakeJevClient
    from orchestrator.jev import qname
    from orchestrator.settings import JudgeConfig

    extraction = _json.dumps(
        {
            "claims": ["a claim"],
            "blind_spots": [],
            "missing_evidence": ["a decisive fact", "an irrelevant one"],
        }
    )
    script = {qname("support", 0, pid): {"noul": 0.9} for pid in ("a", "b")} | {
        qname("contested", 0): {"noul": 0.1},
        qname("standing", 0): {"score": 1.5},
        qname("lacked", 0): {"noul": 0.9},
        qname("wouldchange", 0): {"noul": 0.8},
        qname("lacked", 1): {"noul": 0.9},
        qname("wouldchange", 1): {"noul": 0.1},
    }
    monkeypatch.setattr(
        "orchestrator.panel.build_jev_client", lambda judge, client: FakeJevClient(script)
    )
    patch_providers(monkeypatch, all_ok_mapping(analysis_text=extraction))

    config = make_config(judge=JudgeConfig(shape="hybrid"))
    resp = await run_deliberation(DeliberateRequest(prompt="q"), config)

    # Only the gap that would change the answer counts as outstanding.
    assert resp.meta["evidence_outstanding"] == 1
    assert resp.meta["passes_remaining"] == config.defaults.max_depth - 1

    deeper = await run_deliberation(DeliberateRequest(prompt="q", depth=2), config)
    assert deeper.meta["passes_remaining"] == 0


async def test_a_call_can_pick_its_own_judge_shape(monkeypatch):
    """The right shape depends on the question, not the installation, so a caller that
    knows which it is facing can say so."""
    from orchestrator.fakes import FakeJevClient
    from orchestrator.settings import JudgeConfig

    jev = FakeJevClient()
    monkeypatch.setattr("orchestrator.panel.build_jev_client", lambda judge, client: jev)
    patch_providers(monkeypatch, all_ok_mapping())
    config = make_config(judge=JudgeConfig(shape="matrix"))

    default = await run_deliberation(DeliberateRequest(prompt="q"), config)
    assert default.meta["judge_shape"] == "matrix"
    assert default.analysis.calibration.shape == "matrix"

    override = await run_deliberation(DeliberateRequest(prompt="q", judge_shape="llm"), config)
    assert override.meta["judge_shape"] == "llm"
    assert override.analysis.consensus == ["agree on X"]


async def test_the_failure_envelope_reports_the_requested_shape(monkeypatch):
    patch_providers(
        monkeypatch,
        {
            "a": FakeChatProvider("a", error="HTTP 500: down"),
            "b": FakeChatProvider("b", error="HTTP 500: down"),
            "ja": FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
        },
    )
    resp = await run_deliberation(DeliberateRequest(prompt="q", judge_shape="probe"), make_config())
    assert resp.meta["judge_shape"] == "probe"
    assert resp.meta["failure"] == "all_panels_failed"
