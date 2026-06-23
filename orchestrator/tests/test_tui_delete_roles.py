from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Select, Static

from orchestrator.cli.tui import ImladrisApp
from orchestrator.cli.tui.screens.delete_member import DeleteMemberScreen
from orchestrator.cli.tui.screens.roles import RolesScreen


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _harness_status() -> dict[str, bool]:
    return {"claude-code": True, "codex": True, "opencode": True}


def _config_data() -> dict:
    return {
        "defaults": {"analysis_model": "judge"},
        "providers": [
            {
                "id": "panel",
                "base_url": "http://panel/v1",
                "model": "panel-model",
                "roles": ["panel"],
                "api_key_env": "PANEL_KEY",
            },
            {
                "id": "judge",
                "base_url": "http://judge/v1",
                "model": "judge-model",
                "roles": ["judge"],
            },
            {
                "id": "candidate",
                "base_url": "http://candidate/v1",
                "model": "candidate-model",
                "roles": [],
            },
            {
                "id": "other",
                "base_url": "http://other/v1",
                "model": "other-model",
                "roles": ["panel"],
                "api_key_env": "OTHER_KEY",
            },
        ],
    }


def _space_id_config_data() -> dict:
    return {
        "defaults": {"analysis_model": None},
        "providers": [
            {
                "id": "nemotron council member",
                "kind": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "roles": ["panel"],
                "api_key_env": "OPENROUTER_API_KEY",
            },
            {
                "id": "deeseek endpoint",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "roles": ["panel"],
                "api_key_env": "DEEPSEEK_API_KEY",
            },
        ],
    }


def _status(app: ImladrisApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


async def _open_delete(app: ImladrisApp, pilot, cursor_down: int = 0) -> DeleteMemberScreen:
    for _ in range(cursor_down):
        await pilot.press("down")
    await pilot.press("left", "down", "down", "enter")
    await pilot.pause()
    assert isinstance(app.screen, DeleteMemberScreen)
    return app.screen


async def _open_roles(app: ImladrisApp, pilot) -> RolesScreen:
    await pilot.press("left", "down", "down", "down", "enter")
    await pilot.pause()
    assert isinstance(app.screen, RolesScreen)
    return app.screen


async def _wait_for_dashboard(app: ImladrisApp, pilot) -> None:
    for _ in range(20):
        await pilot.pause(0.05)
        if not isinstance(app.screen, DeleteMemberScreen | RolesScreen):
            return
    raise AssertionError("dashboard did not resume")


@pytest.mark.asyncio
async def test_delete_plain_panel_member_removes_roster_preset_and_env_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PANEL_KEY", "panel-secret")
    monkeypatch.setenv("OTHER_KEY", "other-secret")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text("PANEL_KEY=panel-secret\nOTHER_KEY=other-secret\n", encoding="utf-8")

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_delete(app, pilot)
        delete_button = app.screen.query_one("#confirm-delete", Button)
        cancel_button = app.screen.query_one("#cancel-delete", Button)
        assert delete_button.has_focus

        await pilot.press("down")
        assert cancel_button.has_focus

        await pilot.press("up")
        assert delete_button.has_focus

        await pilot.click("#confirm-delete")
        await pilot.pause()

    saved = _read_json(config_path)
    env_text = env.read_text(encoding="utf-8")
    assert [provider["id"] for provider in saved["providers"]] == ["judge", "candidate", "other"]
    assert saved["presets"]["council"]["panel"] == ["other"]
    assert "PANEL_KEY" not in env_text
    assert "OTHER_KEY=other-secret" in env_text


@pytest.mark.asyncio
async def test_delete_uses_env_file_secrets_without_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PANEL_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text("PANEL_KEY=panel-secret\nOTHER_KEY=other-secret\n", encoding="utf-8")

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_delete(app, pilot)
        await pilot.click("#confirm-delete")
        await _wait_for_dashboard(app, pilot)

    saved = _read_json(config_path)
    env_text = env.read_text(encoding="utf-8")
    assert [provider["id"] for provider in saved["providers"]] == ["judge", "candidate", "other"]
    assert "PANEL_KEY" not in env_text
    assert "OTHER_KEY=other-secret" in env_text


@pytest.mark.asyncio
async def test_delete_judge_clears_assignment_and_last_panel_delete_is_blocked(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PANEL_KEY", "panel-secret")
    config_path = tmp_path / "config.json"
    data = _config_data()
    data["providers"] = data["providers"][:3]
    _write_json(config_path, data)
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text("PANEL_KEY=panel-secret\n", encoding="utf-8")

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_delete(app, pilot, cursor_down=1)
        assert "judge" in _status(app, "#delete-warning")
        await pilot.click("#confirm-delete")
        await _wait_for_dashboard(app, pilot)

        assert _read_json(config_path)["defaults"].get("analysis_model") is None
        await _open_delete(app, pilot)
        await pilot.click("#confirm-delete")
        await pilot.pause()

        assert "last enabled panel" in _status(app, "#delete-status")
        assert _read_json(config_path)["providers"][0]["id"] == "panel"


@pytest.mark.asyncio
async def test_reassign_judge_to_non_panel_member(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PANEL_KEY", "panel-secret")
    monkeypatch.setenv("OTHER_KEY", "other-secret")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text("PANEL_KEY=panel-secret\nOTHER_KEY=other-secret\n", encoding="utf-8")

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_roles(app, pilot)
        assert app.screen.query_one("#judge-select", Select).has_focus

        await pilot.press("down")
        assert app.screen.query_one("#panel-panel", Checkbox).has_focus

        await pilot.press("up")
        assert app.screen.query_one("#judge-select", Select).has_focus

        app.screen.query_one("#judge-select", Select).value = "candidate"
        app.screen.query_one("#panel-candidate", Checkbox).value = False
        await pilot.click("#apply-roles")
        await pilot.pause()

    saved = _read_json(config_path)
    providers = {provider["id"]: provider for provider in saved["providers"]}
    assert saved["defaults"]["analysis_model"] == "candidate"
    assert "judge" in providers["candidate"]["roles"]
    assert "panel" not in providers["candidate"]["roles"]


@pytest.mark.asyncio
async def test_reassign_roles_supports_member_ids_with_spaces(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _space_id_config_data())
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text(
        "OPENROUTER_API_KEY=openrouter-secret\nDEEPSEEK_API_KEY=deepseek-secret\n",
        encoding="utf-8",
    )

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(140, 40)) as pilot:
        app.screen.action_reassign_roles()
        await pilot.pause()
        assert isinstance(app.screen, RolesScreen)
        app.screen.query_one("#judge-select", Select).value = "nemotron council member"
        await pilot.click("#apply-roles")
        await _wait_for_dashboard(app, pilot)

    saved = _read_json(config_path)
    providers = {provider["id"]: provider for provider in saved["providers"]}
    assert saved["defaults"]["analysis_model"] == "nemotron council member"
    assert "judge" in providers["nemotron council member"]["roles"]
    assert saved["presets"]["council"]["panel"] == [
        "nemotron council member",
        "deeseek endpoint",
    ]


@pytest.mark.asyncio
async def test_roles_apply_uses_env_file_secrets_without_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PANEL_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text("PANEL_KEY=panel-secret\nOTHER_KEY=other-secret\n", encoding="utf-8")

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_roles(app, pilot)
        app.screen.query_one("#judge-select", Select).value = "candidate"
        await pilot.click("#apply-roles")
        await _wait_for_dashboard(app, pilot)

    saved = _read_json(config_path)
    assert saved["defaults"]["analysis_model"] == "candidate"


@pytest.mark.asyncio
async def test_unchecking_last_panel_member_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PANEL_KEY", "panel-secret")
    config_path = tmp_path / "config.json"
    data = _config_data()
    data["providers"] = data["providers"][:3]
    _write_json(config_path, data)
    env = tmp_path / ".imladris/.env"
    env.parent.mkdir(parents=True)
    env.write_text("PANEL_KEY=panel-secret\n", encoding="utf-8")

    app = ImladrisApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_roles(app, pilot)
        app.screen.query_one("#panel-panel", Checkbox).value = False
        await pilot.click("#apply-roles")
        await pilot.pause()

        assert "enabled panel" in _status(app, "#roles-status")
        assert _read_json(config_path)["providers"][0]["roles"] == ["panel"]
