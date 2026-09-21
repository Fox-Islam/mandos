"""The judge screen: what it persists, and where the Jev key does and does not go."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Input, Select, Static

from orchestrator.cli.tui import MandosApp
from orchestrator.cli.tui.screens.judge import JudgeScreen


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _config_data() -> dict:
    return {
        "defaults": {"preset": "council", "analysis_model": "judge", "timeout_s": 90},
        "judge": {"shape": "hybrid", "provider": "typesafe", "model": "jev-latest"},
        "providers": [
            {
                "id": "panel",
                "base_url": "http://panel/v1",
                "model": "panel-model",
                "roles": ["panel"],
            },
            {
                "id": "judge",
                "base_url": "http://judge/v1",
                "model": "judge-model",
                "roles": ["judge"],
            },
        ],
        "presets": {"council": {"panel": ["panel"], "analysis": "judge"}},
    }


def _harness_status() -> dict[str, bool]:
    return {"claude-code": True, "codex": True, "opencode": True}


async def _open_judge(pilot, app) -> JudgeScreen:
    await pilot.pause()
    app.screen.action_edit_judge()
    await pilot.pause()
    assert isinstance(app.screen, JudgeScreen)
    return app.screen


@pytest.mark.asyncio
async def test_judge_screen_persists_shape_provider_and_key_separately(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".mandos" / ".env"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_judge(pilot, app)
        assert screen.query_one("#judge-shape", Select).value == "hybrid"
        screen.query_one("#judge-shape", Select).value = "matrix"
        screen.query_one("#judge-provider", Select).value = "openrouter"
        screen.query_one("#judge-batch", Input).value = "40"
        screen.query_one("#judge-token", Input).value = "or-secret"
        await pilot.click("#apply-judge")
        await pilot.pause()

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["judge"]["shape"] == "matrix"
    assert written["judge"]["provider"] == "openrouter"
    assert written["judge"]["questions_per_call"] == 40

    # The key lands in .env at 0600 and nowhere near the config.
    assert "or-secret" not in config_path.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=or-secret" in env_path.read_text(encoding="utf-8")
    assert env_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_judge_screen_blank_key_keeps_the_stored_one(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".mandos" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("TYPESAFE_API_KEY=already-there\n", encoding="utf-8")
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_judge(pilot, app)
        screen.query_one("#judge-model", Input).value = "jev-1.13"
        await pilot.click("#apply-judge")
        await pilot.pause()

    assert json.loads(config_path.read_text(encoding="utf-8"))["judge"]["model"] == "jev-1.13"
    assert "already-there" in env_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_judge_screen_rejects_an_invalid_batch_size(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_judge(pilot, app)
        screen.query_one("#judge-batch", Input).value = "0"
        await pilot.click("#apply-judge")
        await pilot.pause()

        status = str(screen.query_one("#judge-status", Static).content)
        assert "questions per call must be a positive integer" in status

    assert "questions_per_call" not in json.loads(config_path.read_text(encoding="utf-8"))["judge"]
