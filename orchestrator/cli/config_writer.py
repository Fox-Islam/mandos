"""Build and persist ``~/.mandos/config.json`` from configurator answers.

Pure compatibility functions: callers provide :class:`WizardAnswers`, these turn
them into a validated :class:`MandosConfig` and write pretty JSON. Secrets never
appear here - providers carry only an ``api_key_env`` name.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.cli.config_ops import add_member, set_judge
from orchestrator.settings import (
    ContextWindowSource,
    Defaults,
    JudgeConfig,
    MandosConfig,
    ProviderDescriptor,
    _strip_legacy_config,
)

DEFAULT_CONFIG_PATH = "~/.mandos/config.json"
DEFAULT_PRESET_NAME = "council"


@dataclass
class MemberAnswer:
    id: str
    base_url: str
    model: str
    kind: str = "openai"
    catalog_key: str | None = None
    context_window: int | None = None
    resolved_context_window: int | None = None
    context_window_source: ContextWindowSource = "unknown"
    api_key_env: str | None = None
    token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class WizardAnswers:
    members: list[MemberAnswer]
    analysis_model: str
    preset_name: str = DEFAULT_PRESET_NAME
    timeout_s: float = 90
    include_local_only: bool = True
    pricing: dict[str, dict[str, float]] = field(default_factory=dict)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    # The Jev key, like every other secret, goes to .env and never to the config.
    judge_token: str | None = None


def build_config(answers: WizardAnswers) -> MandosConfig:
    """Assemble a validated :class:`MandosConfig`.

    Every member is a panellist; the chosen ``analysis_model`` additionally gets the
    ``judge`` role. Raises if the roster is empty or exceeds 8 (via model validation).
    """
    if not answers.members:
        raise ValueError("a council needs at least one member")

    config: MandosConfig | None = None
    for m in answers.members:
        config = add_member(
            config,
            ProviderDescriptor(
                id=m.id,
                kind=m.kind,
                catalog_key=m.catalog_key,
                base_url=m.base_url,
                model=m.model,
                context_window=m.context_window,
                resolved_context_window=m.resolved_context_window,
                context_window_source=m.context_window_source,
                api_key_env=m.api_key_env,
                headers=m.headers,
                roles=["panel"],
            ),
        )
    if config is None:
        raise ValueError("a council needs at least one member")
    config = set_judge(config, answers.analysis_model)

    data = config.model_dump(by_alias=True, exclude_none=True)
    data["judge"] = answers.judge.model_dump(exclude_none=True)
    data["defaults"] = Defaults(
        preset=answers.preset_name,
        analysis_model=answers.analysis_model,
        timeout_s=answers.timeout_s,
    ).model_dump(exclude_none=True)
    if answers.preset_name != DEFAULT_PRESET_NAME:
        data["presets"][answers.preset_name] = data["presets"].pop(DEFAULT_PRESET_NAME)
    if not answers.include_local_only:
        data["presets"].pop("local-only", None)
    data["pricing"] = {
        k: {"in": v.get("in", 0), "out": v.get("out", 0)} for k, v in answers.pricing.items()
    }
    return MandosConfig.model_validate(_strip_legacy_config(data, warn=True))


def env_from_members(answers: WizardAnswers) -> dict[str, str]:
    """Return ``{api_key_env: token}`` for members that carry a token, plus the Jev
    judge's key when one was supplied."""
    values = {m.api_key_env: m.token for m in answers.members if m.api_key_env and m.token}
    if answers.judge.uses_jev and answers.judge_token:
        values[answers.judge.resolved_api_key_env] = answers.judge_token
    return values


def write_config(config: MandosConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> Path:
    """Write pretty JSON, creating the directory and backing up any existing file."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
    payload = config.model_dump(by_alias=True, exclude_none=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target
