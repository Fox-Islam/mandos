"""Shared test helpers (not a test module).

Deterministic fakes + config builders so panel/judge tests never touch the network.
"""

from __future__ import annotations

from orchestrator.fakes import FakeChatProvider
from orchestrator.settings import (
    Defaults,
    JudgeConfig,
    MandosConfig,
    ProviderDescriptor,
)

ANALYSIS_JSON_IDS = (
    '{"consensus": ["agree on X"], '
    '"contradictions": [{"topic": "T", "positions": [{"ids": ["a"], "claim": "ca"}, '
    '{"ids": ["b"], "claim": "cb"}]}], '
    '"partial_coverage": [{"ids": ["b"], "point": "only b covered Y"}], '
    '"unique_insights": [{"id": "a", "insight": "only a saw Z"}], '
    '"blind_spots": ["nobody addressed W"], "confidence_notes": "fairly reliable"}'
)


def desc(id: str, roles: list[str], **kw) -> ProviderDescriptor:
    return ProviderDescriptor(id=id, base_url=f"http://{id}/v1", model="m", roles=roles, **kw)


def make_config(analysis: str | None = "ja", judge: JudgeConfig | None = None, **defaults_kw):
    """A config for the generative judge by default.

    ``judge`` defaults to ``shape="llm"`` so the pipeline tests exercise the fallback
    analyst without a Jev key; the Jev shapes are covered by ``test_jev_judge.py``,
    which builds its own.
    """
    providers = [
        desc("a", ["panel"]),
        desc("b", ["panel"]),
        desc("ja", ["judge"]),
    ]
    defaults_kw.setdefault("timeout_s", 30)
    return MandosConfig(
        providers=providers,
        judge=judge or JudgeConfig(shape="llm"),
        defaults=Defaults(analysis_model=analysis, **defaults_kw),
    )


def patch_providers(monkeypatch, mapping: dict[str, FakeChatProvider]) -> None:
    """Replace panel.build_provider with one that returns the fake for each id."""

    def fake_build(descriptor, client):
        return mapping[descriptor.id]

    monkeypatch.setattr("orchestrator.panel.build_provider", fake_build)


def all_ok_mapping(analysis_text: str = ANALYSIS_JSON_IDS):
    return {
        "a": FakeChatProvider("a", text="answer from a"),
        "b": FakeChatProvider("b", text="answer from b"),
        "ja": FakeChatProvider("ja", text=analysis_text),
    }
