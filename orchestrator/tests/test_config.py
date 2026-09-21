"""Phase 3 — config & roles: validation, JSON/YAML parity, the .env loader."""

from __future__ import annotations

import json

import pytest
import respx

from orchestrator.settings import (
    Defaults,
    MandosConfig,
    ProviderDescriptor,
    load_config,
    load_env_file,
)


def _desc(id, roles, **kw):
    return ProviderDescriptor(id=id, base_url=f"http://{id}/v1", model="m", roles=roles, **kw)


def test_analysis_model_must_be_judge_capable():
    with pytest.raises(ValueError, match="analysis_model"):
        MandosConfig(
            providers=[_desc("p", ["panel"])],
            defaults=Defaults(analysis_model="p"),
        )


def test_roles_capable_provider_accepted():
    cfg = MandosConfig(
        providers=[_desc("p", ["panel", "judge"])],
        defaults=Defaults(analysis_model="p"),
    )
    assert "judge" in cfg.provider_map()["p"].roles


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="unique"):
        MandosConfig(providers=[_desc("p", ["panel"]), _desc("p", ["panel"])])


def test_oversized_preset_panel_rejected():
    with pytest.raises(ValueError, match="exceeds 8"):
        MandosConfig(
            providers=[_desc(f"p{i}", ["panel"]) for i in range(9)],
            presets={"big": {"panel": [f"p{i}" for i in range(9)]}},
        )


def test_duplicate_preset_panel_members_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        MandosConfig(
            providers=[_desc("p", ["panel"])],
            presets={"dup": {"panel": ["p", "p"]}},
        )


def test_preset_non_panel_member_rejected():
    with pytest.raises(ValueError, match="non-panel"):
        MandosConfig(
            providers=[_desc("p", ["panel"]), _desc("j", ["judge"])],
            presets={"x": {"panel": ["p", "j"]}},
        )


def test_missing_env_var_fails_fast(monkeypatch):
    monkeypatch.delenv("SOME_KEY", raising=False)
    with pytest.raises(ValueError, match="SOME_KEY"):
        MandosConfig(providers=[_desc("p", ["panel"], api_key_env="SOME_KEY")])


def test_json_and_yaml_load_identically(tmp_path):
    content = {
        "providers": [
            {"id": "a", "base_url": "http://a/v1", "model": "m", "roles": ["panel"]},
        ],
        "presets": {"only": {"panel": ["a"]}},
    }
    json_path = tmp_path / "mandos.json"
    yaml_path = tmp_path / "mandos.yaml"
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
    path = tmp_path / "mandos.json"
    path.write_text(json.dumps(content), encoding="utf-8")

    with pytest.warns(UserWarning, match="Ignoring removed Mandos config fields"):
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
    path = tmp_path / "mandos.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(path))


def test_keyless_remote_provider_warns(monkeypatch):
    with pytest.warns(UserWarning, match="Bearer none"):
        MandosConfig(
            providers=[
                ProviderDescriptor(id="cloud", base_url="https://api.vendor.com/v1", model="m")
            ]
        )


def test_keyless_local_provider_does_not_warn(recwarn):
    MandosConfig(
        providers=[ProviderDescriptor(id="local", base_url="http://127.0.0.1/v1", model="m")]
    )
    assert not [w for w in recwarn.list if "Bearer none" in str(w.message)]


def test_safe_status_hides_secret_names(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "shhh")
    cfg = MandosConfig(providers=[_desc("a", ["panel"], api_key_env="MY_TOKEN")])
    status = cfg.safe_status()
    blob = json.dumps(status)
    assert "MY_TOKEN" not in blob
    assert "shhh" not in blob
    assert status["providers"][0]["requires_secret"] is True


def test_unknown_pricing_key_warns():
    with pytest.warns(UserWarning, match="pricing references unknown"):
        MandosConfig(providers=[_desc("a", ["panel"])], pricing={"ghost": {"in": 1, "out": 2}})


@respx.mock
def test_status_makes_no_network_calls():
    cfg = MandosConfig(providers=[_desc("a", ["panel"])])
    status = cfg.safe_status()
    assert status["budget"]["providers"][0]["provider_id"] == "a"


def test_status_budget_only_for_enabled_providers():
    cfg = MandosConfig(providers=[_desc("on", ["panel"]), _desc("off", ["panel"], enabled=False)])
    status = cfg.safe_status()
    assert {b["provider_id"] for b in status["budget"]["providers"]} == {"on"}
    assert {p["id"] for p in status["providers"]} == {"on", "off"}


def test_context_window_and_budget_fields_are_status_visible():
    cfg = MandosConfig(
        # max_tokens is pinned so this measures the budget fields rather than whatever
        # the default output ceiling happens to be.
        defaults=Defaults(max_tokens=1024),
        providers=[
            _desc(
                "a",
                ["panel"],
                context_window=4096,
                resolved_context_window=8192,
                context_window_source="endpoint",
            )
        ],
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
    cfg = MandosConfig(
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
    cfg = MandosConfig(
        providers=[_desc("a", ["panel"], headers={"X-Title": "secretish", "Referer": "http://x"})]
    )
    status = cfg.safe_status()
    assert status["providers"][0]["headers"] == ["Referer", "X-Title"]
    assert "secretish" not in json.dumps(status)


def _judge_cfg(**kw):
    from orchestrator.settings import JudgeConfig

    return MandosConfig(
        providers=[_desc("a", ["panel"]), _desc("ja", ["judge"])],
        defaults=Defaults(analysis_model="ja"),
        judge=JudgeConfig(**kw),
    )


def test_judge_defaults_to_jev_on_typesafe(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    cfg = _judge_cfg()
    assert cfg.judge.shape == "hybrid"
    assert cfg.judge.uses_jev
    assert cfg.judge.resolved_base_url == "https://api.typesafe.ai"
    assert cfg.judge.resolved_api_key_env == "TYPESAFE_API_KEY"
    assert cfg.judge.api_key == "k"


def test_switching_provider_switches_which_key_is_read(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cfg = _judge_cfg(provider="openrouter")
    assert cfg.judge.resolved_api_key_env == "OPENROUTER_API_KEY"
    assert cfg.judge.resolved_base_url == "https://openrouter.ai"
    assert cfg.judge.api_key == "or-key"


def test_an_explicit_api_key_env_survives_a_provider_switch(monkeypatch):
    monkeypatch.setenv("MY_JEV_KEY", "mine")
    cfg = _judge_cfg(provider="openrouter", api_key_env="MY_JEV_KEY")
    assert cfg.judge.resolved_api_key_env == "MY_JEV_KEY"
    assert cfg.judge.api_key == "mine"


def test_missing_jev_key_warns_rather_than_refusing_to_load(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.warns(UserWarning, match="TYPESAFE_API_KEY is unset"):
        cfg = _judge_cfg()
    assert cfg.judge.shape == "hybrid"


def test_llm_shape_needs_no_jev_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error")
        cfg = _judge_cfg(shape="llm")
    assert not cfg.judge.uses_jev


def test_matrix_shape_needs_no_generative_analyst(monkeypatch):
    from orchestrator.settings import JudgeConfig

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error")
        MandosConfig(
            providers=[_desc("a", ["panel"])],
            judge=JudgeConfig(shape="matrix"),
        )


def test_hybrid_without_an_analyst_warns_because_nothing_proposes_claims(monkeypatch):
    from orchestrator.settings import JudgeConfig

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with pytest.warns(UserWarning, match="proposes the claims Jev decides"):
        MandosConfig(providers=[_desc("a", ["panel"])], judge=JudgeConfig(shape="hybrid"))


def test_safe_status_reports_the_judge_without_naming_its_key_variable(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    status = _judge_cfg().safe_status()["judge"]
    assert status["shape"] == "hybrid"
    assert status["provider"] == "typesafe"
    assert status["requires_secret"] is True
    assert status["egress"] == "off-prem"
    assert "api_key_env" not in status
    assert "TYPESAFE_API_KEY" not in json.dumps(status)


def test_a_self_hosted_jev_endpoint_reports_as_on_prem(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    status = _judge_cfg(base_url="http://localhost:9000").safe_status()["judge"]
    assert status["egress"] == "on-prem"
