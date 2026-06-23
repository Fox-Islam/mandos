"""Phase 3 — config & roles: validation, JSON/YAML parity, the .env loader."""

from __future__ import annotations

import json

import pytest
import respx

from orchestrator.settings import (
    Defaults,
    ImladrisConfig,
    ProviderDescriptor,
    load_config,
    load_env_file,
)


def _desc(id, roles, **kw):
    return ProviderDescriptor(id=id, base_url=f"http://{id}/v1", model="m", roles=roles, **kw)


def test_analysis_model_must_be_judge_capable():
    with pytest.raises(ValueError, match="analysis_model"):
        ImladrisConfig(
            providers=[_desc("p", ["panel"])],
            defaults=Defaults(analysis_model="p"),
        )


def test_roles_capable_provider_accepted():
    cfg = ImladrisConfig(
        providers=[_desc("p", ["panel", "judge"])],
        defaults=Defaults(analysis_model="p"),
    )
    assert "judge" in cfg.provider_map()["p"].roles


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="unique"):
        ImladrisConfig(providers=[_desc("p", ["panel"]), _desc("p", ["panel"])])


def test_oversized_preset_panel_rejected():
    with pytest.raises(ValueError, match="exceeds 8"):
        ImladrisConfig(
            providers=[_desc(f"p{i}", ["panel"]) for i in range(9)],
            presets={"big": {"panel": [f"p{i}" for i in range(9)]}},
        )


def test_duplicate_preset_panel_members_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        ImladrisConfig(
            providers=[_desc("p", ["panel"])],
            presets={"dup": {"panel": ["p", "p"]}},
        )


def test_preset_non_panel_member_rejected():
    with pytest.raises(ValueError, match="non-panel"):
        ImladrisConfig(
            providers=[_desc("p", ["panel"]), _desc("j", ["judge"])],
            presets={"x": {"panel": ["p", "j"]}},
        )


def test_missing_env_var_fails_fast(monkeypatch):
    monkeypatch.delenv("SOME_KEY", raising=False)
    with pytest.raises(ValueError, match="SOME_KEY"):
        ImladrisConfig(providers=[_desc("p", ["panel"], api_key_env="SOME_KEY")])


def test_json_and_yaml_load_identically(tmp_path):
    content = {
        "providers": [
            {"id": "a", "base_url": "http://a/v1", "model": "m", "roles": ["panel"]},
        ],
        "presets": {"only": {"panel": ["a"]}},
    }
    json_path = tmp_path / "imladris.json"
    yaml_path = tmp_path / "imladris.yaml"
    json_path.write_text(json.dumps(content), encoding="utf-8")
    yaml_path.write_text(json.dumps(content), encoding="utf-8")
    assert load_config(str(json_path)).model_dump() == load_config(str(yaml_path)).model_dump()


def test_legacy_curator_config_loads_with_removed_fields_stripped(tmp_path):
    content = {
        "defaults": {
            "analysis_model": "a",
            "curation_model": "a",
            "anonymize": True,
            "include_raw": False,
        },
        "providers": [
            {
                "id": "a",
                "base_url": "http://a/v1",
                "model": "m",
                "roles": ["panel", "judge", "curator"],
            },
        ],
        "presets": {"only": {"panel": ["a"], "analysis": "a", "curation": "a"}},
    }
    path = tmp_path / "imladris.json"
    path.write_text(json.dumps(content), encoding="utf-8")

    with pytest.warns(UserWarning, match="Ignoring removed Imladris config fields"):
        cfg = load_config(str(path))

    assert cfg.defaults.analysis_model == "a"
    assert cfg.provider_map()["a"].roles == ["panel", "judge"]
    assert cfg.presets["only"].analysis == "a"


def test_config_dtos_forbid_unknown_keys():
    from pydantic import ValidationError as _VE

    with pytest.raises(_VE):
        ProviderDescriptor(id="p", base_url="http://x/v1", model="m", typo_field=1)
    with pytest.raises(_VE):
        Defaults(maxx_tokens=5)


def test_unknown_top_level_config_key_errors_after_strip(tmp_path):
    content = {
        "providers": [{"id": "a", "base_url": "http://a/v1", "model": "m", "roles": ["panel"]}],
        "totally_unknown": 1,
    }
    path = tmp_path / "imladris.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(path))


def test_keyless_remote_provider_warns(monkeypatch):
    with pytest.warns(UserWarning, match="Bearer none"):
        ImladrisConfig(
            providers=[
                ProviderDescriptor(id="cloud", base_url="https://api.vendor.com/v1", model="m")
            ]
        )


def test_keyless_local_provider_does_not_warn(recwarn):
    ImladrisConfig(
        providers=[ProviderDescriptor(id="local", base_url="http://127.0.0.1/v1", model="m")]
    )
    assert not [w for w in recwarn.list if "Bearer none" in str(w.message)]


def test_safe_status_hides_secret_names(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "shhh")
    cfg = ImladrisConfig(providers=[_desc("a", ["panel"], api_key_env="MY_TOKEN")])
    status = cfg.safe_status()
    blob = json.dumps(status)
    assert "MY_TOKEN" not in blob
    assert "shhh" not in blob
    assert status["providers"][0]["requires_secret"] is True


def test_unknown_pricing_key_warns():
    with pytest.warns(UserWarning, match="pricing references unknown"):
        ImladrisConfig(providers=[_desc("a", ["panel"])], pricing={"ghost": {"in": 1, "out": 2}})


@respx.mock
def test_status_makes_no_network_calls():
    cfg = ImladrisConfig(providers=[_desc("a", ["panel"])])
    status = cfg.safe_status()
    assert status["budget"]["providers"][0]["provider_id"] == "a"


def test_status_budget_only_for_enabled_providers():
    cfg = ImladrisConfig(providers=[_desc("on", ["panel"]), _desc("off", ["panel"], enabled=False)])
    status = cfg.safe_status()
    assert {b["provider_id"] for b in status["budget"]["providers"]} == {"on"}
    assert {p["id"] for p in status["providers"]} == {"on", "off"}


def test_context_window_and_budget_fields_are_status_visible():
    cfg = ImladrisConfig(
        providers=[
            _desc(
                "a",
                ["panel"],
                context_window=4096,
                resolved_context_window=8192,
                context_window_source="endpoint",
            )
        ]
    )
    status = cfg.safe_status()
    assert status["providers"][0]["context_window"] == 4096
    assert status["providers"][0]["resolved_context_window"] == 8192
    assert status["providers"][0]["context_window_source"] == "endpoint"
    assert status["budget"]["warning_ratio"] == 0.75
    assert status["budget"]["error_ratio"] == 0.9
    assert status["budget"]["providers"][0]["provider_id"] == "a"
    assert status["budget"]["providers"][0]["context_window"] == 4096
    assert status["budget"]["providers"][0]["state"] == "green"


def test_safe_status_budget_estimate_surfaces_warning_and_over_budget_states():
    cfg = ImladrisConfig(
        defaults=Defaults(max_tokens=80),
        providers=[
            _desc("green", ["panel"], context_window=200),
            _desc("amber", ["panel"], context_window=100),
            _desc("red", ["panel"], context_window=80),
        ],
    )

    states = {
        item["provider_id"]: item["state"] for item in cfg.safe_status()["budget"]["providers"]
    }

    assert states == {"green": "green", "amber": "amber", "red": "red"}


def test_env_loader_sets_unset_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# a comment\nFOO=bar\nBAZ="quoted value"\nexport QUX=fromfile\nINLINE=val  # trailing\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.setenv("QUX", "preset")
    monkeypatch.delenv("BAZ", raising=False)
    monkeypatch.delenv("INLINE", raising=False)
    load_env_file(env)
    import os

    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "quoted value"
    assert os.environ["QUX"] == "preset"
    assert os.environ["INLINE"] == "val"


def test_env_loader_tolerates_missing_file(tmp_path):
    load_env_file(tmp_path / "does-not-exist.env")


def test_env_permission_mode_classification():
    from orchestrator.settings import _mode_is_group_or_other_readable

    assert _mode_is_group_or_other_readable(0o600) is False
    assert _mode_is_group_or_other_readable(0o700) is False
    assert _mode_is_group_or_other_readable(0o640) is True
    assert _mode_is_group_or_other_readable(0o604) is True


def test_safe_status_redacts_header_values():
    cfg = ImladrisConfig(
        providers=[_desc("a", ["panel"], headers={"X-Title": "secretish", "Referer": "http://x"})]
    )
    status = cfg.safe_status()
    assert status["providers"][0]["headers"] == ["Referer", "X-Title"]
    assert "secretish" not in json.dumps(status)
