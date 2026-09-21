from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.containers import Horizontal
from textual.widgets import DataTable, OptionList, Static

from orchestrator.cli.app import main
from orchestrator.cli.tui import MandosApp
from orchestrator.cli.tui.screens.dashboard import DashboardScreen
from orchestrator.cli.tui.screens.member_form import MemberFormScreen
from orchestrator.model_catalog import CacheStatus, CatalogRefreshResult, ModelMetadata


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _harness_status(ok: bool = True) -> dict[str, bool]:
    return {"claude-code": ok, "codex": ok, "opencode": ok}


def _dashboard_text(app: MandosApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


def _roster_text(app: MandosApp) -> str:
    table = app.screen.query_one("#roster", DataTable)
    rows = [
        " ".join(str(cell) for cell in table.get_row_at(index)) for index in range(table.row_count)
    ]
    return "\n".join(rows)


def _action_menu(app: MandosApp) -> OptionList:
    return app.screen.query_one("#action-menu", OptionList)


def _action_labels(app: MandosApp) -> list[str]:
    menu = _action_menu(app)
    return [str(menu.get_option_at_index(index).prompt) for index in range(menu.option_count)]


async def _wait_for(pilot, predicate, message: str) -> None:
    for _ in range(80):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError(message)


def _config_data(*, model: str = "gpt-4o", api_key_env: str = "OPENAI_KEY") -> dict:
    return {
        "defaults": {"analysis_model": "openai"},
        "providers": [
            {
                "id": "openai",
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": model,
                "roles": ["panel", "judge"],
                "api_key_env": api_key_env,
            },
            {
                "id": "local",
                "kind": "openai",
                "base_url": "http://localhost:8000/v1",
                "model": "qwen2.5-32b-instruct",
                "roles": ["panel"],
            },
        ],
    }


@pytest.mark.asyncio
async def test_dashboard_renders_roster_roles_and_config_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    env.write_text("OPENAI_KEY=secret\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        roster = _roster_text(app)

        assert "openai" in roster
        assert "local" in roster
        assert "gpt-4o" in roster
        assert "qwen2.5-32b-instruct" in roster
        assert "OPENAI_KEY" in roster
        assert "panel | judge" in roster
        assert "panel" in roster
        assert "~/config.json" in _dashboard_text(app, "#paths")
        # The Jev judge is what decides; the chat analyst is named as its support.
        assert "Judge = jev-latest via typesafe, key missing" in _dashboard_text(app, "#roles")
        assert "analyst openai (gpt-4o)" in _dashboard_text(app, "#roles")
        assert "Budget = green" in _dashboard_text(app, "#roles")
        assert "Curator" not in _dashboard_text(app, "#roles")


@pytest.mark.asyncio
async def test_dashboard_empty_config_renders_issues_and_add_affordance(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "missing.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        issues = _dashboard_text(app, "#issues")

        assert "No council members" in issues
        assert "Judge: not assigned" in issues
        assert _action_labels(app) == [
            "Add member",
            "Wire harnesses",
            "Edit judge",
            "Edit run defaults",
            "Refresh model catalog",
            "Quit",
        ]
        assert _action_menu(app).has_focus


@pytest.mark.asyncio
async def test_empty_dashboard_arrow_menu_opens_add_member(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "missing.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        menu = _action_menu(app)

        assert menu.has_focus
        assert menu.option_count == 6
        assert menu.highlighted == 0

        await pilot.press("down")
        assert menu.highlighted == 1

        await pilot.press("up")
        assert menu.highlighted == 0

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, MemberFormScreen)


@pytest.mark.asyncio
async def test_dashboard_has_no_letter_shortcut_actions_or_footer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "missing.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        for key in ("a", "e", "d", "r", "w", "q"):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_dashboard_with_members_arrow_keys_move_roster_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_KEY", "secret")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#roster", DataTable)

        assert table.has_focus
        assert table.cursor_row == 0

        await pilot.press("down")
        assert table.cursor_row == 1

        await pilot.press("left")
        assert _action_menu(app).has_focus

        await pilot.press("right")
        assert table.has_focus


@pytest.mark.asyncio
async def test_dashboard_is_active_screen_with_nonzero_region(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "missing.json"

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert isinstance(app.screen, DashboardScreen)
        assert app.screen.region.width > 0
        assert app.screen.region.height > 0


@pytest.mark.asyncio
async def test_dashboard_text_round_trips_through_utf8(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LONG_KEY", "secret")
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        _config_data(
            model="provider-family/extremely-long-model-name-that-should-truncate",
            api_key_env="LONG_KEY",
        ),
    )

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()

        texts = [
            _dashboard_text(app, selector)
            for selector in ("#brand", "#paths", "#roles", "#issues", "#harness")
        ]
        texts.append(_roster_text(app))
        brand = _dashboard_text(app, "#brand")

    for text in texts:
        assert text.encode("utf-8").decode("utf-8") == text

    assert "✦ Council Configurator ✦" in brand


@pytest.mark.asyncio
async def test_dashboard_missing_token_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()

        assert "openai: token not set (OPENAI_KEY)" in _dashboard_text(app, "#issues")


@pytest.mark.asyncio
async def test_dashboard_budget_gauge_renders_green_amber_and_red(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "defaults": {
                "analysis_model": "green",
                "max_tokens": 80,
                "budget_warning_ratio": 0.75,
                "budget_error_ratio": 0.9,
            },
            "providers": [
                {
                    "id": "green",
                    "kind": "openai",
                    "base_url": "http://green/v1",
                    "model": "m",
                    "context_window": 200,
                    "roles": ["panel", "judge"],
                },
                {
                    "id": "amber",
                    "kind": "openai",
                    "base_url": "http://amber/v1",
                    "model": "m",
                    "context_window": 100,
                    "roles": ["panel"],
                },
                {
                    "id": "red",
                    "kind": "openai",
                    "base_url": "http://red/v1",
                    "model": "m",
                    "context_window": 80,
                    "roles": ["panel"],
                },
            ],
        },
    )

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        roster = _roster_text(app)

        assert "green 40%" in roster
        assert "amber 80%" in roster
        assert "red 100%" in roster
        assert "Budget = red" in _dashboard_text(app, "#roles")


@pytest.mark.asyncio
async def test_dashboard_refresh_catalog_action_reports_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "missing.json"

    def fake_refresh(**kwargs):  # noqa: ARG001
        return CatalogRefreshResult(
            refreshed=True,
            models=[ModelMetadata("openai", "gpt-4o", context_window=128000)],
            status=CacheStatus(
                path=str(tmp_path / ".mandos/model-catalog-cache.json"),
                exists=True,
                updated_at=1,
                age_seconds=0,
                stale=False,
            ),
        )

    monkeypatch.setattr(
        "orchestrator.cli.tui.screens.dashboard.refresh_cache_if_stale",
        fake_refresh,
    )

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("down", "down", "down", "down", "enter")
        await _wait_for(
            pilot,
            lambda: "Catalog  refreshed 1 models" in _dashboard_text(app, "#harness"),
            "catalog refresh status did not render",
        )


@pytest.mark.asyncio
async def test_dashboard_respects_mandos_config_and_default_missing_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_KEY", "secret")
    config_path = tmp_path / "chosen.json"
    _write_json(config_path, _config_data())
    monkeypatch.setenv("MANDOS_CONFIG", str(config_path))

    app = MandosApp(home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()

        assert "~/chosen.json" in _dashboard_text(app, "#paths")

    monkeypatch.delenv("MANDOS_CONFIG", raising=False)
    missing_app = MandosApp(home=tmp_path, harness_status=_harness_status())
    async with missing_app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert "~/.mandos/config.json" in _dashboard_text(missing_app, "#paths")


@pytest.mark.asyncio
async def test_dashboard_malformed_provider_row_keeps_running_and_disables_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "providers": [
                {"base_url": "https://bad.example/v1", "model": "m"},
                {"id": "ok", "base_url": "http://ok/v1", "model": "m", "roles": ["panel"]},
            ]
        },
    )

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert "Manual config repair" in _dashboard_text(app, "#issues")
        assert _action_labels(app) == ["Wire harnesses", "Quit"]
        assert "ok" in _roster_text(app)


@pytest.mark.asyncio
async def test_dashboard_malformed_json_surfaces_manual_repair_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    config_path.write_text("{bad json\n", encoding="utf-8")

    app = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status(False))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        issues = _dashboard_text(app, "#issues")

        assert "Malformed config" in issues
        assert "Manual config repair" in issues
        assert _action_labels(app) == ["Wire harnesses", "Quit"]


@pytest.mark.asyncio
async def test_dashboard_branding_and_responsive_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LONG_KEY", "secret")
    config_path = tmp_path / "config.json"
    long_model = "provider-family/extremely-long-model-name-that-should-truncate"
    _write_json(config_path, _config_data(model=long_model, api_key_env="LONG_KEY"))

    small = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with small.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        small_header = _dashboard_text(small, "#brand")
        small_roster = _roster_text(small)
        small_brand = small.screen.query_one("#brand", Static)
        small_body = small.screen.query_one("#dashboard-body", Horizontal)

        assert "Council Configurator" in small_header
        assert "◆" in small_header
        assert len(small_header.splitlines()) == 7
        assert small_brand.region.height == 7
        assert small_body.region.y > small_brand.region.y
        assert "..." in small_roster
        assert "Quit" in _action_labels(small)

    roomy = MandosApp(config_path=config_path, home=tmp_path, harness_status=_harness_status())
    async with roomy.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        roomy_header = _dashboard_text(roomy, "#brand")
        outer_title = str(roomy.screen.query_one("#dashboard").border_title)

        assert "Council Configurator" in roomy_header
        assert len(roomy_header.splitlines()) == 7
        assert "mandos" in outer_title
        assert "Wire harnesses" in _action_labels(roomy)


@pytest.mark.asyncio
async def test_dashboard_quit_action_exits_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = MandosApp(config_path=tmp_path / "missing.json", home=tmp_path)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("down", "down", "down", "down", "down", "enter")

    assert app.return_value == 0


def test_main_help_and_doctor_paths_remain_textual_free(tmp_path, monkeypatch, capsys):
    assert main(["--help"]) == 0
    assert "full-screen configurator TUI" in capsys.readouterr().out

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_KEY", "secret")
    config_path = tmp_path / "config.json"
    _write_json(config_path, _config_data())
    monkeypatch.setenv("MANDOS_CONFIG", str(config_path))

    assert main(["doctor"]) == 0
    assert "Mandos configuration" in capsys.readouterr().out
