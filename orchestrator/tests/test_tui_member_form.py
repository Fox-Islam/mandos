from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import respx
from httpx import Response
from textual.widgets import Input, Select, Static
from textual.widgets._select import SelectOverlay

from orchestrator.cli.catalog import catalog_options
from orchestrator.cli.tui import MandosApp
from orchestrator.cli.tui.screens.member_form import MemberFormScreen


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _config_data(
    *,
    member_id: str = "openai",
    model: str = "old-model",
    api_key_env: str = "OPENAI_KEY",
) -> dict:
    return {
        "defaults": {"analysis_model": member_id},
        "providers": [
            {
                "id": member_id,
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": model,
                "roles": ["panel", "judge"],
                "api_key_env": api_key_env,
            }
        ],
    }


def _harness_status() -> dict[str, bool]:
    return {"claude-code": True, "codex": True, "opencode": True}


def _field(app: MandosApp, selector: str) -> Input:
    return app.screen.query_one(selector, Input)


def _status(app: MandosApp) -> str:
    return str(app.screen.query_one("#form-status", Static).content)


def _select_values(select: Select) -> list[str]:
    return [str(value) for _, value in select._options if value is not Select.NULL]


async def _wait_for(pilot, predicate: Callable[[], bool], message: str) -> None:
    for _ in range(80):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError(message)


async def _open_add(app: MandosApp, pilot) -> MemberFormScreen:
    await pilot.press("left", "enter")
    await pilot.pause()
    assert isinstance(app.screen, MemberFormScreen)
    return app.screen


async def _open_edit(app: MandosApp, pilot) -> MemberFormScreen:
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, MemberFormScreen)
    return app.screen


def test_catalog_options_are_featured_first_and_searchable():
    options = catalog_options()
    labels = [label for label, _ in options]

    assert labels[0].startswith("Featured - ")
    assert options[-1][1] == "custom"
    assert [value for _, value in catalog_options("studio")] == ["lmstudio", "custom"]


@pytest.mark.asyncio
async def test_add_member_form_arrow_keys_move_between_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_add(app, pilot)

        assert app.screen.query_one("#preset", Select).has_focus

        await pilot.press("down")
        assert _field(app, "#preset-search").has_focus

        await pilot.press("down")
        assert _field(app, "#member-id").has_focus

        await pilot.press("down")
        assert app.screen.query_one("#kind", Select).has_focus

        await pilot.press("up")
        assert _field(app, "#member-id").has_focus


@pytest.mark.asyncio
async def test_add_member_form_search_filters_all_provider_presets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_add(app, pilot)
        preset = app.screen.query_one("#preset", Select)

        assert _select_values(preset)[0] == "openrouter"

        _field(app, "#preset-search").value = "studio"
        await pilot.pause()

        assert _select_values(preset) == ["lmstudio", "custom"]
        preset.value = "lmstudio"
        await pilot.pause()
        assert _field(app, "#base-url").value == "http://localhost:1234/v1"


@pytest.mark.asyncio
async def test_add_member_form_arrow_keys_navigate_expanded_select(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_add(app, pilot)

        preset = app.screen.query_one("#preset", Select)
        preset.value = "openrouter"
        preset.focus()

        await pilot.press("enter")
        await pilot.pause()
        overlay = preset.query_one(SelectOverlay)
        assert preset.expanded is True
        assert overlay.has_focus
        assert overlay.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert preset.expanded is True
        assert overlay.has_focus
        assert overlay.highlighted == 1
        assert not _field(app, "#member-id").has_focus

        await pilot.press("up")
        await pilot.pause()
        assert preset.expanded is True
        assert overlay.has_focus
        assert overlay.highlighted == 0


@pytest.mark.asyncio
async def test_add_keyed_member_fetches_models_tests_connection_and_persists_token(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    config_path = tmp_path / "config.json"

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.openai.com/v1/models").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-4o-mini", "context_window": 4096},
                        {"id": "gpt-4o", "context_window": 128000},
                    ]
                },
            )
        )
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=Response(200, json={"choices": []})
        )

        app = MandosApp(
            config_path=config_path,
            home=tmp_path,
            harness_status=_harness_status(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_add(app, pilot)

            app.screen.query_one("#preset", Select).value = "openai"
            await pilot.pause()
            assert _field(app, "#base-url").value == "https://api.openai.com/v1"
            assert _field(app, "#api-key-env").value == "OPENAI_API_KEY"
            assert app.screen.query_one("#kind", Select).value == "openai"
            _field(app, "#api-key-env").value = "OPENAI_KEY"
            _field(app, "#member-id").value = "openai"
            _field(app, "#token").value = "new-secret"

            await pilot.click("#fetch-models")
            await _wait_for(
                pilot,
                lambda: app.screen.query_one("#model-select", Select).value == "gpt-4o-mini",
                "model select was not populated",
            )
            assert "Loaded 2 models" in _status(app)

            await pilot.click("#test-connection")
            await _wait_for(pilot, lambda: "Connection: ok" in _status(app), "probe did not pass")
            assert "new-secret" not in _status(app)

            await pilot.click("#save-member")
            await pilot.pause()

    saved = _read_json(config_path)
    assert saved["providers"][0]["id"] == "openai"
    assert saved["providers"][0]["model"] == "gpt-4o-mini"
    assert "context_window" not in saved["providers"][0]
    assert saved["providers"][0]["resolved_context_window"] == 4096
    assert saved["providers"][0]["context_window_source"] == "endpoint"
    assert saved["providers"][0]["api_key_env"] == "OPENAI_KEY"
    assert "new-secret" not in config_path.read_text(encoding="utf-8")
    assert "OPENAI_KEY=new-secret" in (tmp_path / ".mandos/.env").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_add_catalog_prefill_updates_kind_for_openrouter(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_add(app, pilot)
        app.screen.query_one("#preset", Select).value = "openrouter"
        await pilot.pause()

        assert _field(app, "#base-url").value == "https://openrouter.ai/api/v1"
        assert _field(app, "#api-key-env").value == "OPENROUTER_API_KEY"
        assert app.screen.query_one("#kind", Select).value == "openrouter"


@pytest.mark.asyncio
async def test_manual_context_window_is_persisted_as_user_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_add(app, pilot)
        _field(app, "#member-id").value = "local"
        app.screen.query_one("#kind", Select).value = "openai"
        _field(app, "#base-url").value = "http://localhost:8000/v1"
        _field(app, "#model-input").value = "local-model"
        _field(app, "#context-window").value = "12345"

        await pilot.click("#save-member")
        await pilot.pause()

    saved = _read_json(config_path)
    provider = saved["providers"][0]
    assert provider["context_window"] == 12345
    assert provider["context_window_source"] == "override"
    assert "resolved_context_window" not in provider


@pytest.mark.asyncio
async def test_edit_model_and_token_then_blank_token_preserves_existing_value(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    env_path = tmp_path / ".mandos/.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OPENAI_KEY=old-secret\n", encoding="utf-8")

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_edit(app, pilot)
        screen._set_model_choices(["new-model"], "new-model")
        _field(app, "#token").value = "new-secret"
        await pilot.click("#save-member")
        await pilot.pause()

        screen = await _open_edit(app, pilot)
        assert app.screen.query_one("#model-select", Select).value == "new-model"
        screen._set_model_choices(["blank-token-model"], "blank-token-model")
        _field(app, "#token").value = ""
        await pilot.click("#save-member")
        await pilot.pause()

    saved = _read_json(config_path)
    assert saved["providers"][0]["model"] == "blank-token-model"
    assert "OPENAI_KEY=new-secret" in env_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_api_key_env_blank_token_copies_old_value_and_prunes_old_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OLD_KEY", raising=False)
    monkeypatch.delenv("NEW_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data(api_key_env="OLD_KEY"))
    env_path = tmp_path / ".mandos/.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OLD_KEY=old-secret\n", encoding="utf-8")

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_edit(app, pilot)
        _field(app, "#api-key-env").value = "NEW_KEY"
        _field(app, "#token").value = ""
        await pilot.click("#save-member")
        await pilot.pause()

    saved = _read_json(config_path)
    env_text = env_path.read_text(encoding="utf-8")
    assert saved["providers"][0]["api_key_env"] == "NEW_KEY"
    assert "NEW_KEY=old-secret" in env_text
    assert "OLD_KEY" not in env_text


@pytest.mark.asyncio
async def test_duplicate_id_and_empty_model_show_inline_errors_without_writing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    monkeypatch.delenv("SECOND_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    original = config_path.read_text(encoding="utf-8")
    env_path = tmp_path / ".mandos/.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OPENAI_KEY=old-secret\n", encoding="utf-8")

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_add(app, pilot)
        _field(app, "#member-id").value = "openai"
        app.screen.query_one("#kind", Select).value = "openai"
        _field(app, "#base-url").value = "https://api.openai.com/v1"
        _field(app, "#api-key-env").value = "SECOND_KEY"
        _field(app, "#token").value = "second-secret"
        _field(app, "#model-input").value = "new-model"
        await pilot.click("#save-member")
        await pilot.pause()

        assert "provider id already exists: openai" in _status(app)
        assert config_path.read_text(encoding="utf-8") == original

        _field(app, "#member-id").value = "second"
        _field(app, "#model-input").value = ""
        app.screen.action_save()
        await pilot.pause()

        assert "model is required" in _status(app)
        assert config_path.read_text(encoding="utf-8") == original

        _field(app, "#model-input").value = "new-model"
        _field(app, "#token").value = ""
        app.screen.action_save()
        await pilot.pause()

        assert "token required for SECOND_KEY" in _status(app)
        assert config_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_escape_mid_edit_discards_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    env_path = tmp_path / ".mandos/.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OPENAI_KEY=old-secret\n", encoding="utf-8")
    original_config = config_path.read_text(encoding="utf-8")
    original_env = env_path.read_text(encoding="utf-8")

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_edit(app, pilot)
        _field(app, "#member-id").value = "changed"
        _field(app, "#token").value = "changed-secret"
        await pilot.press("escape")
        await pilot.pause()

    assert config_path.read_text(encoding="utf-8") == original_config
    assert env_path.read_text(encoding="utf-8") == original_env


@pytest.mark.asyncio
async def test_probe_failure_falls_back_to_free_text_model_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    config_path = tmp_path / "config.json"

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.openai.com/v1/models").mock(return_value=Response(500))

        app = MandosApp(
            config_path=config_path,
            home=tmp_path,
            harness_status=_harness_status(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_add(app, pilot)
            app.screen.query_one("#kind", Select).value = "openai"
            _field(app, "#base-url").value = "https://api.openai.com/v1"
            _field(app, "#api-key-env").value = "OPENAI_KEY"
            _field(app, "#token").value = "secret"

            await pilot.click("#fetch-models")
            await _wait_for(
                pilot,
                lambda: "Model list unavailable" in _status(app),
                "model-list fallback did not render",
            )

            assert app.screen.query_one("#model-select", Select).disabled is True
            assert _field(app, "#model-input").disabled is False
