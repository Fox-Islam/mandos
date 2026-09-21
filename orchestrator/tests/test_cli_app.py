"""Phase 10 — configurator entry points: doctor report + main dispatch."""

from __future__ import annotations

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

    assert main(["clear-sessions"]) == 0
    assert "Cleared 1 sessions." in capsys.readouterr().out
    assert not (tmp_path / "two.json").exists()


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
