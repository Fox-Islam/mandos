from __future__ import annotations

import json
import os

import pytest

from orchestrator.cli.config_ops import (
    add_member,
    compute_issues,
    delete_member,
    load_draft,
    orphaned_env_keys,
    persist,
    plan_token_update,
    regenerate_presets,
    set_judge,
    set_panel,
    update_member,
)
from orchestrator.settings import Defaults, MandosConfig, ProviderDescriptor, load_config


def _member(
    id: str,
    roles: list[str] | None = None,
    *,
    api_key_env: str | None = None,
    enabled: bool = True,
) -> ProviderDescriptor:
    return ProviderDescriptor(
        id=id,
        base_url=f"http://{id}/v1",
        model="m",
        roles=roles or ["panel"],
        api_key_env=api_key_env,
        enabled=enabled,
    )


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _all_roles(id: str = "solo", *, api_key_env: str | None = None) -> ProviderDescriptor:
    return _member(id, ["panel", "judge"], api_key_env=api_key_env)


def test_load_draft_reads_valid_config_and_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("K_KEY", raising=False)
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    env.write_text("K_KEY=secret\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "defaults": {"analysis_model": "cloud", "curation_model": "cloud"},
            "providers": [
                {
                    "id": "cloud",
                    "base_url": "https://example.test/v1",
                    "model": "m",
                    "roles": ["panel", "judge", "curator"],
                    "api_key_env": "K_KEY",
                }
            ],
            "presets": {
                "council": {
                    "panel": ["cloud"],
                    "analysis": "cloud",
                    "curation": "cloud",
                }
            },
            "pricing": {"cloud": {"in": 1.0, "out": 2.0}},
        },
    )

    draft = load_draft(config_path)

    assert draft.source_path == config_path
    assert [provider.id for provider in draft.providers] == ["cloud"]
    assert draft.providers[0].roles == ["panel", "judge"]
    # The Jev judge's key is tracked alongside the members' so the configurator can
    # flag it missing; the default shape needs one and none is set here.
    assert draft.token_present == {"K_KEY": True, "TYPESAFE_API_KEY": False}
    assert draft.pricing["cloud"].input == 1.0
    assert draft.parse_errors == []
    assert draft.full_validation_error is None


def test_load_draft_preserves_config_with_missing_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MISSING_KEY", raising=False)
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "providers": [
                {
                    "id": "cloud",
                    "base_url": "https://example.test/v1",
                    "model": "m",
                    "roles": ["panel"],
                    "api_key_env": "MISSING_KEY",
                }
            ]
        },
    )

    draft = load_draft(config_path)
    issues = compute_issues(draft)

    assert draft.providers[0].id == "cloud"
    assert draft.token_present == {"MISSING_KEY": False, "TYPESAFE_API_KEY": False}
    assert "MISSING_KEY" in (draft.full_validation_error or "")
    assert any(issue.label == "cloud: token not set (MISSING_KEY)" for issue in issues)


def test_load_draft_missing_file_uses_default_target(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MANDOS_CONFIG", raising=False)

    draft = load_draft()
    issues = compute_issues(draft)

    assert draft.source_path == tmp_path / ".mandos/config.json"
    assert draft.providers == []
    assert draft.parse_errors == []
    assert {issue.label for issue in issues} >= {
        "No council members",
        "Judge: not assigned",
        "No enabled panel member",
    }


def test_load_draft_preserves_malformed_provider_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "providers": [
                {"base_url": "https://bad.test/v1", "model": "m"},
                {"id": "ok", "base_url": "http://ok/v1", "model": "m", "roles": ["panel"]},
            ]
        },
    )

    draft = load_draft(config_path)
    issues = compute_issues(draft)

    assert [provider.id for provider in draft.providers] == ["ok"]
    assert len(draft.parse_errors) == 1
    assert draft.parse_errors[0].location == "providers[0]"
    assert any(issue.action == "Manual config repair" for issue in issues)


def test_load_draft_does_not_call_full_config_validator(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "providers": [
                {"id": "ok", "base_url": "http://ok/v1", "model": "m", "roles": ["panel"]},
            ]
        },
    )

    def fail_model_validate(*_args, **_kwargs):
        raise AssertionError("load_draft must not call MandosConfig.model_validate")

    monkeypatch.setattr(MandosConfig, "model_validate", fail_model_validate)

    draft = load_draft(config_path)

    assert [provider.id for provider in draft.providers] == ["ok"]


def test_load_draft_respects_mandos_config_and_persist_writes_back(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "chosen.json"
    monkeypatch.setenv("MANDOS_CONFIG", str(config_path))
    _write_json(
        config_path,
        {
            "providers": [
                {"id": "old", "base_url": "http://old/v1", "model": "m", "roles": ["panel"]}
            ]
        },
    )

    draft = load_draft()
    config = add_member(None, _all_roles("new"))
    written = persist(config, {}, draft.source_path)

    assert draft.source_path == config_path
    assert written == config_path
    assert load_config(str(config_path)).providers[0].id == "new"


def test_delete_member_clears_roles_and_presets():
    config = MandosConfig(
        providers=[
            _member("panel", ["panel"]),
            _member("judge", ["judge"]),
        ],
        defaults=Defaults(analysis_model="judge", preset="council"),
        presets={
            "council": {"panel": ["panel"], "analysis": "judge"},
            "custom": {"panel": ["panel"], "analysis": "judge"},
        },
    )

    updated = delete_member(config, "judge")

    assert updated.defaults.analysis_model is None
    assert all(provider.id != "judge" for provider in updated.providers)
    assert all("judge" not in preset.panel for preset in updated.presets.values())
    assert all(preset.analysis != "judge" for preset in updated.presets.values())


def test_delete_member_rejects_last_enabled_panel_member():
    config = MandosConfig(
        providers=[_all_roles()],
        defaults=Defaults(analysis_model="solo"),
    )

    with pytest.raises(ValueError, match="last enabled panel"):
        delete_member(config, "solo")


def test_role_ops_move_singleton_roles_and_allow_judge_only():
    config = MandosConfig(
        providers=[
            _member("p1", ["panel"]),
            _member("p2", ["panel"]),
            _member("judge", ["judge"]),
        ],
        defaults=Defaults(analysis_model="judge"),
    )

    config = set_judge(config, "p1")
    config = set_panel(config, "p1", False)

    providers = {provider.id: provider for provider in config.providers}
    assert config.defaults.analysis_model == "p1"
    assert "judge" in providers["p1"].roles
    assert "panel" not in providers["p1"].roles
    assert "judge" not in providers["judge"].roles

    with pytest.raises(ValueError, match="enabled panel"):
        set_panel(config, "p2", False)


def test_regenerate_local_only_allows_keyless_judge_only():
    config = MandosConfig(
        providers=[
            _member("panel", ["panel"]),
            _member("judge", ["judge"]),
        ],
        defaults=Defaults(analysis_model="judge"),
    )

    updated = regenerate_presets(config)

    assert updated.presets["local-only"].panel == ["panel"]
    assert updated.presets["local-only"].analysis == "judge"


def test_persist_stages_new_token_before_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NEW_KEY", raising=False)
    config = add_member(
        None,
        _all_roles("cloud", api_key_env="NEW_KEY"),
        token_changes={"NEW_KEY": "secret"},
    )
    target = tmp_path / "config.json"

    persist(config, {"NEW_KEY": "secret"}, target)

    reloaded = load_config(str(target))
    assert reloaded.providers[0].api_key_env == "NEW_KEY"
    assert "secret" not in target.read_text(encoding="utf-8")
    assert "NEW_KEY=secret" in (tmp_path / ".mandos/.env").read_text(encoding="utf-8")


def test_persist_validation_failure_writes_nothing_and_rolls_back_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BAD_KEY", raising=False)
    target = tmp_path / "config.json"
    backup = tmp_path / "config.json.bak"
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")
    backup.write_text("backup\n", encoding="utf-8")
    env.write_text("OLD=1\n", encoding="utf-8")
    invalid = MandosConfig(
        providers=[_member("judge", ["judge"])],
        defaults=Defaults(analysis_model="judge"),
    )

    with pytest.raises(ValueError, match="enabled panel"):
        persist(invalid, {"BAD_KEY": "secret"}, target)

    assert target.read_text(encoding="utf-8") == "original\n"
    assert backup.read_text(encoding="utf-8") == "backup\n"
    assert env.read_text(encoding="utf-8") == "OLD=1\n"
    assert "BAD_KEY" not in os.environ


def test_env_var_rename_with_blank_token_copies_old_value_and_prunes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OLD_KEY", "old-secret")
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    env.write_text("OLD_KEY=old-secret\n", encoding="utf-8")
    config = MandosConfig(
        providers=[_all_roles("cloud", api_key_env="OLD_KEY")],
        defaults=Defaults(analysis_model="cloud"),
    )

    token_plan = plan_token_update(
        config,
        "cloud",
        api_key_env="NEW_KEY",
        token="",
        loaded_env={"OLD_KEY": "old-secret"},
    )
    updated = update_member(
        config, "cloud", api_key_env="NEW_KEY", token_changes=token_plan.changes
    )
    persist(updated, token_plan.changes, tmp_path / "config.json", token_plan.prune_env_keys)
    env_text = env.read_text(encoding="utf-8")

    assert token_plan.changes == {"NEW_KEY": "old-secret"}
    assert "NEW_KEY=old-secret" in env_text
    assert "OLD_KEY" not in env_text


def test_env_var_rename_with_new_token_prunes_old_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OLD_KEY", "old-secret")
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    env.write_text("OLD_KEY=old-secret\n", encoding="utf-8")
    config = MandosConfig(
        providers=[_all_roles("cloud", api_key_env="OLD_KEY")],
        defaults=Defaults(analysis_model="cloud"),
    )

    token_plan = plan_token_update(config, "cloud", api_key_env="NEW_KEY", token="new-secret")
    updated = update_member(
        config, "cloud", api_key_env="NEW_KEY", token_changes=token_plan.changes
    )
    persist(updated, token_plan.changes, tmp_path / "config.json", token_plan.prune_env_keys)
    env_text = env.read_text(encoding="utf-8")

    assert token_plan.changes == {"NEW_KEY": "new-secret"}
    assert token_plan.prune_env_keys == ("OLD_KEY",)
    assert "NEW_KEY=new-secret" in env_text
    assert "OLD_KEY" not in env_text


def test_env_var_rename_missing_old_value_errors_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("OLD_KEY", "old-secret")
    config = MandosConfig(
        providers=[_all_roles("cloud", api_key_env="OLD_KEY")],
        defaults=Defaults(analysis_model="cloud"),
    )
    target = tmp_path / "config.json"
    target.write_text("unchanged\n", encoding="utf-8")

    with pytest.raises(ValueError, match="token required"):
        plan_token_update(config, "cloud", api_key_env="NEW_KEY", token="", loaded_env={})

    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_orphan_key_pruning_removes_unreferenced_deleted_member_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOO_API_KEY", "secret")
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    env.write_text("FOO_API_KEY=secret\n", encoding="utf-8")
    config = MandosConfig(
        providers=[
            _member("cloud", ["panel"], api_key_env="FOO_API_KEY"),
            _all_roles("local"),
        ],
        defaults=Defaults(analysis_model="local"),
    )

    updated = delete_member(config, "cloud")
    persist(updated, {}, tmp_path / "config.json", orphaned_env_keys(config, updated))

    assert "FOO_API_KEY" not in env.read_text(encoding="utf-8")


def test_orphan_key_pruning_keeps_key_referenced_by_another_member(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOO_API_KEY", "secret")
    env = tmp_path / ".mandos/.env"
    env.parent.mkdir(parents=True)
    env.write_text("FOO_API_KEY=secret\n", encoding="utf-8")
    config = MandosConfig(
        providers=[
            _member("cloud1", ["panel"], api_key_env="FOO_API_KEY"),
            _member("cloud2", ["panel"], api_key_env="FOO_API_KEY"),
            _member("judge", ["judge"]),
        ],
        defaults=Defaults(analysis_model="judge"),
    )

    updated = delete_member(config, "cloud1")
    persist(updated, {}, tmp_path / "config.json", orphaned_env_keys(config, updated))

    assert "FOO_API_KEY=secret" in env.read_text(encoding="utf-8")
