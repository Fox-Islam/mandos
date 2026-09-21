"""The environment-variable screen: what it shows, and where a value goes."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from orchestrator.cli.config_ops import loaded_env_values
from orchestrator.cli.tui import MandosApp
from orchestrator.cli.tui.screens.env_vars import EnvVarsScreen, field_id


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _harness_status() -> dict[str, bool]:
    return {"claude-code": False, "codex": False, "opencode": False}


def _config_data() -> dict:
    return {
        "defaults": {"analysis_model": "panel"},
        "judge": {"shape": "llm"},
        "providers": [
            {
                "id": "panel",
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "api_key_env": "PANEL_KEY",
                "roles": ["panel", "judge"],
            }
        ],
    }


async def _open(pilot, app: MandosApp) -> EnvVarsScreen:
    app.screen.action_edit_env_vars()
    await pilot.pause()
    await pilot.pause()
    assert isinstance(app.screen, EnvVarsScreen)
    return app.screen


@pytest.mark.asyncio
async def test_screen_names_the_keys_and_never_renders_a_value(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PANEL_KEY", "super-secret")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open(pilot, app)
        rendered = " ".join(str(w.content) for w in screen.query(Static))

        assert "PANEL_KEY" in rendered
        assert "(set)" in rendered
        assert "super-secret" not in rendered
        # the field starts empty, so a value cannot be read back out of it
        assert screen.query_one(f"#{field_id('PANEL_KEY')}", Input).value == ""
        assert screen.query_one(f"#{field_id('PANEL_KEY')}", Input).password is True


@pytest.mark.asyncio
async def test_unset_key_is_reported_as_such(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PANEL_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open(pilot, app)
        rendered = " ".join(str(w.content) for w in screen.query(Static))
        assert "PANEL_KEY" in rendered and "(not set)" in rendered


@pytest.mark.asyncio
async def test_saving_writes_the_env_at_0600_and_leaves_the_config_alone(tmp_path, monkeypatch):
    """PANEL_KEY is unset here, so the config cannot be validated.

    That is the state this screen exists for, and routing the save through the config
    writer made it the one state where saving failed.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PANEL_KEY", raising=False)
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".mandos" / ".env"
    _write_json(config_path, _config_data())
    before = config_path.read_text(encoding="utf-8")
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open(pilot, app)
        screen.query_one(f"#{field_id('PANEL_KEY')}", Input).value = "panel-secret"
        await pilot.click("#save-env")
        await pilot.pause()

    assert "PANEL_KEY=panel-secret" in env_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "panel-secret" not in config_path.read_text(encoding="utf-8")
    assert json.loads(config_path.read_text(encoding="utf-8")) == json.loads(before)


@pytest.mark.asyncio
async def test_a_new_variable_needs_a_usable_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PANEL_KEY", "x")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open(pilot, app)
        screen.query_one("#env-new-name", Input).value = "not a name"
        screen.query_one("#env-new-value", Input).value = "v"
        await pilot.click("#save-env")
        await pilot.pause()

        assert isinstance(app.screen, EnvVarsScreen), "an invalid name must not close the screen"
        assert "not a valid environment variable name" in str(
            screen.query_one("#env-status", Static).content
        )


@pytest.mark.asyncio
async def test_opening_the_screen_creates_the_env_file_with_known_names(tmp_path, monkeypatch):
    """A first run has no env file, so there was nothing to fill in."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PANEL_KEY", raising=False)
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".mandos" / ".env"
    _write_json(config_path, _config_data())
    assert not env_path.exists()
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open(pilot, app)
        assert env_path.exists()
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

        written = env_path.read_text(encoding="utf-8")
        for name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
            assert f"{name}=" in written
        # seeded names carry no value, so nothing reads as configured
        assert loaded_env_values(env_path).get("TYPESAFE_API_KEY") in (None, "")

        rendered = " ".join(str(w.content) for w in screen.query(Static))
        assert "OPENROUTER_API_KEY" in rendered and "PANEL_KEY" in rendered


@pytest.mark.asyncio
async def test_sync_adds_missing_names_and_removes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PANEL_KEY", raising=False)
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".mandos" / ".env"
    _write_json(config_path, _config_data())
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text('KEPT=mine\nOPENROUTER_API_KEY="already here"\n', encoding="utf-8")
    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open(pilot, app)
        await pilot.click("#sync-env")
        await pilot.pause()

        written = env_path.read_text(encoding="utf-8")
        assert "KEPT=mine" in written, "sync must not drop a name it does not know"
        assert 'OPENROUTER_API_KEY="already here"' in written, "an existing value must survive"
        assert "MISTRAL_API_KEY=" in written, "a missing name must be added"
        assert isinstance(app.screen, EnvVarsScreen)
        assert "Added" in str(screen.query_one("#env-status", Static).content)
