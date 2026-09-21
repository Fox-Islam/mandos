"""Several deliberations at once.

A harness can have more than one council in flight, so nothing here may depend on
being the only caller: no shared buffer, no per-call connection pool, and concurrent
turns on one session must serialise, not interleave.
"""

from __future__ import annotations

import asyncio

import pytest

from orchestrator import sessions
from orchestrator.fakes import FakeChatProvider
from orchestrator.http import aclose_shared_client, shared_client
from orchestrator.models import DeliberateRequest
from orchestrator.panel import run_deliberation
from orchestrator.tests.helpers import ANALYSIS_JSON_IDS, make_config, patch_providers


async def test_the_http_client_is_shared_not_rebuilt_per_call():
    """A client per deliberation means a fresh TLS handshake to every provider on every
    call - measured at ~350ms against ~90ms of actual model time."""
    first = shared_client()
    assert shared_client() is first
    await aclose_shared_client()
    assert shared_client() is not first
    await aclose_shared_client()


async def test_closing_the_shared_client_twice_is_harmless():
    shared_client()
    await aclose_shared_client()
    await aclose_shared_client()


async def test_concurrent_deliberations_do_not_bleed_into_each_other(monkeypatch):
    patch_providers(
        monkeypatch,
        {
            "a": FakeChatProvider("a", text="answer from a", delay=0.02),
            "b": FakeChatProvider("b", text="answer from b", delay=0.01),
            "ja": FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
        },
    )
    config = make_config()

    responses = await asyncio.gather(
        *(run_deliberation(DeliberateRequest(prompt=f"question {i}"), config) for i in range(8))
    )

    assert [r.question for r in responses] == [f"question {i}" for i in range(8)]
    assert all(r.meta["ok"] == 2 for r in responses)
    assert all(r.analysis is not None for r in responses)


async def test_concurrent_turns_on_one_thread_serialise(tmp_path, monkeypatch):
    """Two turns on the same thread must not both read turn N and both write turn N+1."""
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    patch_providers(
        monkeypatch,
        {
            "a": FakeChatProvider("a", text="answer from a", delay=0.02),
            "b": FakeChatProvider("b", text="answer from b"),
            "ja": FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
        },
    )
    config = make_config()

    await asyncio.gather(
        *(
            run_deliberation(DeliberateRequest(prompt=f"turn {i}", thread_id="shared"), config)
            for i in range(5)
        )
    )

    stored = sessions.load_session("shared", root=tmp_path)
    assert len(stored["turns"]) == 5
    assert {t["user"] for t in stored["turns"]} == {f"turn {i}" for i in range(5)}


async def test_different_threads_do_not_block_each_other(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    patch_providers(
        monkeypatch,
        {
            "a": FakeChatProvider("a", text="a", delay=0.05),
            "b": FakeChatProvider("b", text="b", delay=0.05),
            "ja": FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
        },
    )
    config = make_config()

    started = asyncio.get_running_loop().time()
    await asyncio.gather(
        *(
            run_deliberation(DeliberateRequest(prompt="q", thread_id=f"t{i}"), config)
            for i in range(4)
        )
    )
    elapsed = asyncio.get_running_loop().time() - started

    # Serialised, four 50ms panels would take >= 200ms. Concurrent, about one.
    assert elapsed < 0.15, f"threads appear to be serialising: {elapsed:.3f}s"


async def test_the_session_lock_map_does_not_grow_without_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    for i in range(50):
        async with sessions.session_lock(f"thread-{i}", root=tmp_path):
            pass
    assert sessions._LOCKS == {}


async def test_a_lock_survives_while_someone_is_waiting_for_it(tmp_path):
    """Refcounting must not drop a lock another waiter still holds a reference to."""
    order: list[str] = []

    async def turn(name: str, hold: float) -> None:
        async with sessions.session_lock("same", root=tmp_path):
            order.append(f"{name}-in")
            await asyncio.sleep(hold)
            order.append(f"{name}-out")

    await asyncio.gather(turn("first", 0.03), turn("second", 0.0))

    assert order in (
        ["first-in", "first-out", "second-in", "second-out"],
        ["second-in", "second-out", "first-in", "first-out"],
    ), order
    assert sessions._LOCKS == {}


@pytest.mark.parametrize("count", [1, 12])
async def test_status_is_safe_to_call_alongside_deliberations(count, monkeypatch):
    patch_providers(
        monkeypatch,
        {
            "a": FakeChatProvider("a", text="a"),
            "b": FakeChatProvider("b", text="b"),
            "ja": FakeChatProvider("ja", text=ANALYSIS_JSON_IDS),
        },
    )
    config = make_config()
    results = await asyncio.gather(
        *(run_deliberation(DeliberateRequest(prompt="q"), config) for _ in range(count)),
        *(asyncio.to_thread(config.safe_status) for _ in range(count)),
    )
    assert len(results) == count * 2
