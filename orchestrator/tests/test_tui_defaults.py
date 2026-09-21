from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Input, Select, Static

from orchestrator.cli.tui import MandosApp
from orchestrator.cli.tui.screens.defaults import DefaultsScreen


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _config_data() -> dict:
    return {
        "defaults": {
            "preset": "council",
            "analysis_model": "judge",
            "timeout_s": 90,
            "max_depth": 1,
            "max_tokens": 1024,
            "temperature": 0.2,
        },
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
        "presets": {
            "council": {
                "panel": ["panel"],
                "analysis": "judge",
            }
        },
    }


def _harness_status() -> dict[str, bool]:
    return {"claude-code": True, "codex": True, "opencode": True}


@pytest.mark.asyncio
async def test_run_defaults_screen_persists_execution_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.screen.action_run_defaults()
        await pilot.pause()
        assert isinstance(app.screen, DefaultsScreen)

        assert app.screen.query_one("#preset-select", Select).value == "council"
        app.screen.query_one("#max-tokens", Input).value = "16000"
        app.screen.query_one("#timeout-s", Input).value = "120"
        app.screen.query_one("#temperature", Input).value = "0"
        app.screen.query_one("#max-depth", Input).value = "2"
        await pilot.click("#apply-defaults")
        await pilot.pause()

    defaults = _read_json(config_path)["defaults"]
    assert defaults["preset"] == "council"
    assert defaults["max_tokens"] == 16000
    assert defaults["timeout_s"] == 120
    assert defaults["temperature"] == 0
    assert defaults["max_depth"] == 2


@pytest.mark.asyncio
async def test_run_defaults_screen_rejects_invalid_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.screen.action_run_defaults()
        await pilot.pause()

        app.screen.query_one("#temperature", Input).value = "3"
        await pilot.click("#apply-defaults")
        await pilot.pause()

        status = str(app.screen.query_one("#defaults-status", Static).content)
        assert "temperature must be between 0 and 2" in status
        assert _read_json(config_path)["defaults"]["temperature"] == 0.2
