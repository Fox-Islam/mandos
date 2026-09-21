"""Repo-level consistency checks for documentation that is duplicated or mirrors code.

Nothing at runtime reads these files, so they drift silently. Each check below is here
because the drift it catches had already happened once.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.judge import FALLBACKS
from orchestrator.models import JudgeShape

ROOT = Path(__file__).resolve().parents[2]
SKILLS = (
    ROOT / ".claude" / "skills" / "mandos-deliberate" / "SKILL.md",
    ROOT / ".agents" / "skills" / "mandos-deliberate" / "SKILL.md",
)


def test_the_two_skill_copies_are_identical() -> None:
    """One skill, two harness directories. An edit to either must be made to both."""
    claude, agents = (path.read_text(encoding="utf-8") for path in SKILLS)
    assert claude == agents, "SKILL.md copies have drifted; copy one over the other"


def test_every_judge_shape_is_documented() -> None:
    """The shapes page is the contract; a shape missing from it is undocumented API."""
    page = (ROOT / "docs" / "architecture" / "03-judge-shapes.md").read_text(encoding="utf-8")
    for shape in JudgeShape.__args__:  # type: ignore[attr-defined]
        assert f"`{shape}`" in page, f"judge shape {shape!r} is absent from 03-judge-shapes.md"


def test_documented_fallback_chains_match_the_code() -> None:
    """The degradation table drifted from FALLBACKS when `probe` was added."""
    page = (ROOT / "docs" / "architecture" / "03-judge-shapes.md").read_text(encoding="utf-8")
    table = page.split("## Degradation", 1)[1]
    for shape, chain in FALLBACKS.items():
        if not chain:
            continue
        row = next((line for line in table.splitlines() if line.startswith(f"| `{shape}`")), None)
        assert row is not None, f"no degradation row for {shape!r}"
        for fallback in chain:
            assert f"`{fallback}`" in row, f"{shape!r} falls back to {fallback!r}, not in its row"
