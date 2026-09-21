from __future__ import annotations

import json
import tomllib

import pytest
from textual.widgets import Checkbox, Input, Static

from orchestrator.cli.tui import MandosApp
from orchestrator.cli.tui.screens.harness import HarnessScreen


def _dashboard_text(app: MandosApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


async def _open_harness(app: MandosApp, pilot) -> HarnessScreen:
    await pilot.press("down", "enter")
    await pilot.pause()
    assert isinstance(app.screen, HarnessScreen)
    return app.screen


@pytest.mark.asyncio
async def test_wire_claude_code_from_tui_refreshes_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = MandosApp(config_path=tmp_path / "missing.json", home=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        await _open_harness(app, pilot)
        claude = app.screen.query_one("#harness-claude-code", Checkbox)
        codex = app.screen.query_one("#harness-codex", Checkbox)
        assert claude.has_focus

        await pilot.press("down")
        assert codex.has_focus

        await pilot.press("up")
        assert claude.has_focus
        assert claude.value is False
        claude.value = True
        await pilot.click("#apply-harnesses")
        await pilot.pause()

        claude = tmp_path / ".claude.json"
        data = json.loads(claude.read_text(encoding="utf-8"))
        assert data["mcpServers"]["mandos"]["command"] == "mandos-mcp"
        assert data["mcpServers"]["mandos"]["env"]["MANDOS_CONFIG"] == "~/.mandos/config.json"
        assert "claude-code yes" in _dashboard_text(app, "#harness")


@pytest.mark.asyncio
async def test_wire_codex_from_tui_sets_tool_output_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = MandosApp(config_path=tmp_path / "missing.json", home=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        await _open_harness(app, pilot)
        app.screen.query_one("#harness-codex", Checkbox).value = True
        app.screen.query_one("#codex-output-limit", Input).value = "128000"
        await pilot.click("#apply-harnesses")
        await pilot.pause()

    codex = tmp_path / ".codex" / "config.toml"
    data = tomllib.loads(codex.read_text(encoding="utf-8"))
    assert data["tool_output_token_limit"] == 128000
    assert data["mcp_servers"]["mandos"]["command"] == "mandos-mcp"
