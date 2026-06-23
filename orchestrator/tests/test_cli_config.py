"""Phase 8 — config + secret writers."""

from __future__ import annotations

import os
import stat

import pytest

from orchestrator.cli.config_writer import (
    MemberAnswer,
    WizardAnswers,
    build_config,
    env_from_members,
    write_config,
)
from orchestrator.cli.secrets import write_env
from orchestrator.settings import load_config


def _members(n: int) -> list[MemberAnswer]:
    return [MemberAnswer(id=f"m{i}", base_url=f"http://m{i}:8000/v1", model="x") for i in range(n)]


def _answers(n: int, analysis: str) -> WizardAnswers:
    return WizardAnswers(members=_members(n), analysis_model=analysis)


def test_build_config_one_member():
    cfg = build_config(_answers(1, "m0"))
    roles = cfg.provider_map()["m0"].roles
    assert {"panel", "judge"} <= set(roles)
    assert cfg.presets["council"].analysis == "m0"


def test_build_config_four_members_with_distinct_judge():
    cfg = build_config(_answers(4, "m1"))
    assert "judge" in cfg.provider_map()["m1"].roles
    assert cfg.presets["council"].panel == ["m0", "m1", "m2", "m3"]
    assert "local-only" in cfg.presets


def test_build_config_eight_members():
    cfg = build_config(_answers(8, "m0"))
    assert len(cfg.presets["council"].panel) == 8


def test_build_config_rejects_zero_members():
    with pytest.raises(ValueError, match="at least one"):
        build_config(WizardAnswers(members=[], analysis_model="x"))


def test_build_config_rejects_more_than_eight():
    with pytest.raises(ValueError, match="exceeds 8"):
        build_config(_answers(9, "m0"))


def test_env_from_members_collects_tokens():
    answers = WizardAnswers(
        members=[
            MemberAnswer(
                id="k", base_url="https://x/v1", model="m", api_key_env="K_KEY", token="secret"
            ),
            MemberAnswer(id="local", base_url="http://localhost/v1", model="m"),
        ],
        analysis_model="k",
    )
    assert env_from_members(answers) == {"K_KEY": "secret"}


def test_write_config_no_tokens_pretty_and_backs_up(tmp_path):
    answers = WizardAnswers(
        members=[
            MemberAnswer(
                id="k", base_url="https://x/v1", model="m", api_key_env="K_KEY", token="secret"
            ),
        ],
        analysis_model="k",
    )
    os.environ["K_KEY"] = "secret"
    try:
        cfg = build_config(answers)
    finally:
        del os.environ["K_KEY"]
    path = tmp_path / "config.json"
    write_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    assert "secret" not in text
    assert '"api_key_env": "K_KEY"' in text
    assert "\n  " in text
    write_config(cfg, path)
    assert (tmp_path / "config.json.bak").exists()


def test_config_round_trips_through_load(tmp_path):
    cfg = build_config(_answers(2, "m0"))
    path = tmp_path / "config.json"
    write_config(cfg, path)
    reloaded = load_config(str(path))
    assert reloaded.defaults.analysis_model == "m0"
    assert reloaded.presets["council"].analysis == "m0"


def test_write_env_sets_0600_and_merges(tmp_path):
    path = tmp_path / ".env"
    write_env({"A": "1"}, path)
    write_env({"B": "2"}, path)
    body = path.read_text(encoding="utf-8")
    assert "A=1" in body and "B=2" in body
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_env_quotes_values_with_spaces(tmp_path):
    path = tmp_path / ".env"
    write_env({"A": "has space"}, path)
    assert 'A="has space"' in path.read_text(encoding="utf-8")
