"""The config files shipped in `config/` must stay loadable.

Nothing imports them, so they drift silently: the example carried `max_depth: 1` long
after the default became 3, and a judge shape list that predated `probe`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.settings import load_config

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SHIPPED = sorted(CONFIG_DIR.glob("mandos.*.yaml"))


def _keys(monkeypatch) -> None:
    for name in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "MINIMAX_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "test-key")


def test_there_are_shipped_configs() -> None:
    assert SHIPPED, "no config/mandos.*.yaml found"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_shipped_config_loads(path: Path, monkeypatch) -> None:
    _keys(monkeypatch)
    config = load_config(str(path))

    assert config.providers, f"{path.name} names no providers"
    ids = {provider.id for provider in config.providers}

    for name, preset in config.presets.items():
        assert set(preset.panel) <= ids, f"{path.name}: preset {name} names an unknown member"
        if preset.analysis:
            assert preset.analysis in ids, f"{path.name}: preset {name} names an unknown analyst"

    analyst = config.defaults.analysis_model
    if analyst:
        assert analyst in ids, f"{path.name}: defaults.analysis_model is not a member"
        judges = {p.id for p in config.providers if "judge" in p.roles}
        assert analyst in judges, f"{path.name}: defaults.analysis_model lacks the judge role"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_shipped_config_lists_every_judge_shape(path: Path) -> None:
    """The comment beside `shape:` is where a reader sees the options.

    Checking the whole file instead passes on any mention anywhere, which is how a
    list missing `probe` survived while the word appeared further down.
    """
    from orchestrator.models import JudgeShape

    lines = path.read_text(encoding="utf-8").splitlines()
    index = next((i for i, ln in enumerate(lines) if ln.strip().startswith("shape:")), None)
    if index is None:
        pytest.skip("no judge block")

    # Either style counts: a trailing comment on `shape:`, or the comment block
    # introducing the judge above it. The window keeps a mention elsewhere in the
    # file from standing in for the list.
    window = [lines[index].partition("#")[2]]
    for line in reversed(lines[max(0, index - 12) : index]):
        stripped = line.strip()
        if stripped.startswith("#"):
            window.append(stripped)
        elif stripped and not stripped.startswith("judge:"):
            break
    inline = {token.strip() for token in window[0].split("|")}
    # A block list names the shape first on its line; prose mentioning it nearby is
    # not a list entry and must not stand in for one.
    heads = {
        stripped.split()[0]
        for line in window[1:]
        for stripped in [line.lstrip("#").strip()]
        if stripped
    }
    listed = inline | heads
    assert listed - {""}, f"{path.name}: nothing beside `shape:` lists the options"
    for shape in JudgeShape.__args__:  # type: ignore[attr-defined]
        assert shape in listed, f"{path.name}: the list beside `shape:` omits {shape!r}"
