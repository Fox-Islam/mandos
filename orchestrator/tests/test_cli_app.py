"""Configurator entry points: doctor report + main dispatch."""

from __future__ import annotations

import os

from orchestrator import sessions
from orchestrator.cli.app import detect_wired_harnesses, doctor_report, main
from orchestrator.cli.harness import wire_claude_code
from orchestrator.model_catalog import CacheStatus, CatalogRefreshResult, ModelMetadata
from orchestrator.settings import Defaults, MandosConfig, ProviderDescriptor


def _config(monkeypatch):
    monkeypatch.setenv("K_KEY", "topsecret")
    return MandosConfig(
        providers=[
            ProviderDescriptor(
                id="cloud",
                base_url="https://api.example.com/v1",
                model="big",
                roles=["panel", "judge"],
                api_key_env="K_KEY",
            ),
            ProviderDescriptor(
                id="local", base_url="http://localhost:8000/v1", model="small", roles=["panel"]
            ),
        ],
        defaults=Defaults(analysis_model="cloud", preset="council"),
        presets={"council": {"panel": ["cloud", "local"], "analysis": "cloud"}},
    )


def test_doctor_report_has_roster_roles_and_no_secrets(monkeypatch):
    cfg = _config(monkeypatch)
    report = doctor_report(cfg, {"claude-code": True, "codex": False, "opencode": False})
    assert "cloud" in report and "local" in report
    assert "panel,judge" in report
    assert "curator" not in report.lower()
    assert "off-prem" in report and "local" in report
    assert "Wired harnesses" in report
    assert "claude-code: yes" in report
    assert "topsecret" not in report
    assert "K_KEY" not in report


def test_detect_wired_harnesses(tmp_path):
    wire_claude_code(tmp_path, "global")
    wired = detect_wired_harnesses(tmp_path)
    assert wired["claude-code"] is True
    assert wired["codex"] is False
    assert wired["opencode"] is False


def test_main_help_returns_zero(capsys):
    assert main(["--help"]) == 0
    assert "mandos" in capsys.readouterr().out


def test_clear_sessions_command_clears_local_session_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "DEFAULT_SESSIONS_DIR", str(tmp_path))
    sessions.write_session({"thread_id": "one", "turns": []})
    sessions.write_session({"thread_id": "two", "turns": []})

    assert main(["clear-sessions", "one"]) == 0
    assert "Cleared 1 session one." in capsys.readouterr().out
    assert not (tmp_path / "one.json").exists()
    assert (tmp_path / "two.json").exists()

    # Clearing every session needs --yes; naming one is its own confirmation.
    assert main(["clear-sessions"]) == 1
    out = capsys.readouterr().out
    assert "1 session in" in out and "--yes" in out
    assert (tmp_path / "two.json").exists(), "the guard must not delete anything"

    assert main(["clear-sessions", "--yes"]) == 0
    assert "Cleared 1 sessions." in capsys.readouterr().out
    assert not (tmp_path / "two.json").exists()

    assert main(["clear-sessions"]) == 0
    assert "No sessions to clear." in capsys.readouterr().out


def test_refresh_catalog_command_reports_result(monkeypatch, capsys):
    def fake_refresh(**kwargs):  # noqa: ARG001
        return CatalogRefreshResult(
            refreshed=True,
            models=[ModelMetadata("openai", "gpt-4o", context_window=128000)],
            status=CacheStatus(
                path="cache.json",
                exists=True,
                updated_at=1,
                age_seconds=0,
                stale=False,
            ),
        )

    monkeypatch.setattr("orchestrator.cli.app.refresh_cache_if_stale", fake_refresh)

    assert main(["refresh-catalog"]) == 0
    assert "Refreshed model catalog: 1 models cached." in capsys.readouterr().out


def test_unknown_command_is_an_error_not_the_configurator(capsys):
    """Falling through to the TUI meant `mandos doctr` opened a full-screen app."""
    assert main(["doctr"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'doctr'" in err
    assert "--help" in err


def test_version_is_printed_without_starting_the_configurator(capsys):
    from orchestrator import __version__

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"mandos {__version__}"


def test_config_flag_points_the_readers_at_a_file(tmp_path, monkeypatch, capsys):
    """README documents `--config <path>`; it used to fall through to the TUI."""
    monkeypatch.delenv("MANDOS_CONFIG", raising=False)
    assert main(["--config", str(tmp_path / "nope.yaml"), "doctor"]) == 2
    assert "no config at" in capsys.readouterr().err

    target = tmp_path / "mandos.yaml"
    target.write_text("providers: []\n", encoding="utf-8")
    main(["--config", str(target), "doctor"])
    assert os.environ["MANDOS_CONFIG"] == str(target)
