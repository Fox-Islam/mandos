from __future__ import annotations

import ipaddress
import json
import os
import stat
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.jev.client import DEFAULT_MODEL as JEV_DEFAULT_MODEL
from orchestrator.jev.client import PROVIDERS as JEV_PROVIDERS
from orchestrator.models import JudgeShape
from orchestrator.transcript import DEFAULT_MAX_AGE_S

Role = Literal["panel", "judge"]
ContextWindowSource = Literal["override", "endpoint", "modelsdev", "unknown"]


def is_local_base_url(base_url: str) -> bool:
    """True when ``base_url`` points at this host or the LAN (on-prem): loopback,
    a private IP, a single-label intranet hostname, or a ``.local``/``.internal``
    domain. Used for sovereignty egress disclosure and the keyless-remote warning;
    never decides anything secret-related."""
    host = (urlparse(base_url).hostname or "").lower()
    if not host or host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        pass
    if "." not in host:
        return True
    return host.endswith(".local") or host.endswith(".internal")


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str | None = None
    analysis_model: str | None = None
    timeout_s: float = 90
    max_depth: int = 1
    # 1024 truncated real panel answers into stubs and, worse, cut the generative
    # judge off mid-JSON on any panel worth convening. OpenRouter's Fusion allows
    # 16000 per inner call; 4096 is a middle that answers properly without inviting
    # an eight-member panel to write essays.
    max_tokens: int | None = 4096
    # The generative judge and the claim extractor summarise the whole panel, so they
    # need more room than any single panellist.
    analysis_max_tokens: int | None = 8192
    temperature: float = 0.2
    budget_warning_ratio: float = Field(default=0.75, gt=0, lt=1)
    budget_error_ratio: float = Field(default=0.9, gt=0, le=1)
    session_max_turns: int = Field(default=12, ge=1)
    # Sessions hold prompts and panel answers; keeping them forever is a slowly
    # growing disclosure. 0 disables pruning.
    session_max_age_days: float = Field(default=30, ge=0)


class ContextConfig(BaseModel):
    """What the panel is told about the conversation so far.

    ``from_transcript`` reads the capture a harness hook leaves in
    ``~/.mandos/context`` (see :mod:`orchestrator.transcript`) and prepends it to the
    panel prompt, so the panel sees the discussion rather than the calling model's
    retyped summary of it. Without the hook installed there is nothing to read and the
    setting does nothing.

    On by default: the panel already receives this conversation, filtered through the
    calling model's judgement. What changes is completeness, so the bounds matter —
    ``max_turns`` and ``max_chars`` cap it, credentials are stripped before anything is
    written to disk, and a capture older than ``max_age_s`` is ignored as belonging to
    a different task.
    """

    model_config = ConfigDict(extra="forbid")
    from_transcript: bool = True
    max_turns: int = Field(default=12, ge=1)
    max_chars: int = Field(default=24_000, ge=100)
    max_age_s: float = Field(default=DEFAULT_MAX_AGE_S, ge=0)


class JudgeConfig(BaseModel):
    """The Jev judge.

    ``shape`` decides what runs (see :mod:`orchestrator.judge`). The default is
    ``hybrid``, the only shape that returns claim-level narrative — consensus,
    contradictions, attributed insights — *and* the numbers behind it.

    ``matrix`` is cheaper, marginally faster and needs no ``defaults.analysis_model``
    at all, being the only shape with no generative model in the judging loop; it
    returns agreement numbers and an outlier rather than a narrative. ``llm`` needs no
    Jev. Every Jev shape falls back to the analyst only if Jev cannot deliver.

    ``api_key_env`` names the environment variable holding the key — never the key
    itself, and the name is not returned by ``safe_status``. Left unset it follows the
    provider, so switching provider switches which key is read, exactly as the PHP SDK
    does.
    """

    model_config = ConfigDict(extra="forbid")
    shape: JudgeShape = "hybrid"
    provider: Literal["typesafe", "openrouter"] = "typesafe"
    model: str = JEV_DEFAULT_MODEL
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_s: float = 30
    max_retries: int = 2
    # Jev answers a batch in parallel, so this bounds one request body rather than
    # cost: raising it makes a judge round trip wider, not slower.
    questions_per_call: int = Field(default=60, ge=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @property
    def resolved_api_key_env(self) -> str:
        return self.api_key_env or JEV_PROVIDERS[self.provider]["api_key_env"]

    @property
    def resolved_base_url(self) -> str:
        return self.base_url or JEV_PROVIDERS[self.provider]["base_url"]

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.resolved_api_key_env)

    @property
    def uses_jev(self) -> bool:
        return self.shape != "llm"

    @property
    def needs_analysis_model(self) -> bool:
        """``matrix`` is the only shape with no generative call in it. The others
        either write the analysis or propose what Jev should grade."""
        return self.shape in ("llm", "hybrid", "verify")


class ProviderDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["openai", "openrouter"] = "openai"
    catalog_key: str | None = None
    base_url: str
    model: str
    context_window: int | None = Field(default=None, gt=0)
    resolved_context_window: int | None = Field(default=None, gt=0)
    context_window_source: ContextWindowSource = "unknown"
    roles: list[Role] = Field(default_factory=lambda: ["panel"])
    enabled: bool = True
    api_key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    # Provider-executed tools, passed through verbatim (OpenRouter: web_search,
    # web_fetch). Client-executed "function" tools are rejected -- see
    # _validate_provider_tools.
    tools: list[dict[str, Any]] = Field(default_factory=list)
    max_tool_calls: int | None = Field(default=None, ge=1)
    timeout_s: float = 60
    max_retries: int = 1

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    panel: list[str] = Field(default_factory=list)
    analysis: str | None = None


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: float = Field(default=0, alias="in")
    output: float = Field(default=0, alias="out")


def _require_role(
    enabled: dict[str, ProviderDescriptor], pid: str | None, role: Role, where: str
) -> None:
    if pid is None:
        return
    p = enabled.get(pid)
    if not p or role not in p.roles:
        raise ValueError(
            f"{where} must reference an enabled provider with the '{role}' role: {pid!r}"
        )


def _validate_provider_tools(enabled: dict[str, ProviderDescriptor]) -> None:
    """Only tools the *provider* executes may be declared.

    A ``{"type": "function"}`` tool is one the client is expected to run: the model
    would answer with ``tool_calls`` and wait for a result Mandos has no way to supply,
    so the panel member returns nothing useful.

    Running them here would be worse than useless. The harness already has filesystem,
    shell and database access behind a permission model that asks before it reads or
    runs anything; an MCP subprocess quietly executing tools to feed a third-party
    panel is exactly what that model exists to prevent. When a question depends on the
    user's files or data, the *calling model* gathers it and passes it as ``context``.
    """
    for provider in enabled.values():
        for tool in provider.tools:
            kind = str(tool.get("type", ""))
            if not kind:
                raise ValueError(f"provider {provider.id!r} has a tool with no 'type'")
            if kind == "function":
                raise ValueError(
                    f"provider {provider.id!r} declares a client-executed 'function' "
                    "tool; Mandos only passes through provider-executed tools "
                    "(e.g. 'openrouter:web_search'). Gather local data in the calling "
                    "model and pass it as `context` instead."
                )


def _validate_enabled_secrets(enabled: dict[str, ProviderDescriptor]) -> None:
    for p in enabled.values():
        if p.api_key_env and not os.environ.get(p.api_key_env):
            raise ValueError(f"missing environment variable {p.api_key_env} for provider {p.id}")


def _warn_keyless_remote_providers(enabled: dict[str, ProviderDescriptor]) -> None:
    """Non-fatal: a remote (off-prem) provider with no ``api_key_env`` will send
    ``Authorization: Bearer none`` and fail remotely with a confusing 401 rather than
    a clear local config error. Keyless local/LAN endpoints (vLLM, etc.)
    are legitimate and never warned."""
    for p in enabled.values():
        if p.api_key_env is None and not is_local_base_url(p.base_url):
            warnings.warn(
                f"provider {p.id!r} targets a remote base_url with no api_key_env; "
                "a cloud endpoint will receive 'Authorization: Bearer none' (likely 401)",
                stacklevel=2,
            )


def _validate_preset(name: str, preset: Preset, enabled: dict[str, ProviderDescriptor]) -> None:
    if not preset.panel:
        raise ValueError(f"preset {name} has empty panel")
    if len(preset.panel) > 8:
        raise ValueError(f"preset {name} panel exceeds 8 members")
    if len(preset.panel) != len(set(preset.panel)):
        raise ValueError(f"preset {name} panel contains duplicate providers")
    unknown = [pid for pid in preset.panel if pid not in enabled]
    if unknown:
        raise ValueError(f"preset {name} references unknown providers: {unknown}")
    non_panel = [pid for pid in preset.panel if "panel" not in enabled[pid].roles]
    if non_panel:
        raise ValueError(f"preset {name} references non-panel providers: {non_panel}")
    _require_role(enabled, preset.analysis, "judge", f"preset {name} analysis")


class MandosConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: Defaults = Field(default_factory=Defaults)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    providers: list[ProviderDescriptor]
    presets: dict[str, Preset] = Field(default_factory=dict)
    pricing: dict[str, Price] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self):
        ids = [p.id for p in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("provider ids must be unique")
        enabled = {p.id: p for p in self.providers if p.enabled}
        if not enabled:
            raise ValueError("at least one provider must be enabled")
        _validate_enabled_secrets(enabled)
        _validate_provider_tools(enabled)
        _warn_keyless_remote_providers(enabled)
        self._warn_unknown_pricing_keys()
        _require_role(enabled, self.defaults.analysis_model, "judge", "defaults.analysis_model")
        for name, preset in self.presets.items():
            _validate_preset(name, preset, enabled)
        self._validate_judge()
        return self

    def _validate_judge(self) -> None:
        """Warn, never raise, on a judge that cannot run as configured.

        A missing Jev key or a Jev shape with no generative analyst behind it is a real
        misconfiguration, but it is not worth refusing to load over: the pipeline
        degrades to the fallback judge and records ``meta.judge_error``, and the panel
        answers still come back. Refusing here would take the whole deliberation down
        to save the analysis, which is the wrong trade (golden rule: partial results
        are normal).
        """
        if self.judge.uses_jev and not self.judge.api_key:
            warnings.warn(
                f"judge.shape={self.judge.shape!r} needs Jev, but "
                f"{self.judge.resolved_api_key_env} is unset; the judge will fall back "
                "to defaults.analysis_model",
                stacklevel=2,
            )
        if self.judge.needs_analysis_model and not self.defaults.analysis_model:
            what = (
                "writes the analysis"
                if self.judge.shape == "llm"
                else "proposes the claims Jev decides"
                if self.judge.shape == "hybrid"
                else "writes the analysis Jev verifies"
            )
            warnings.warn(
                f"judge.shape={self.judge.shape!r} has no defaults.analysis_model, which "
                f"{what}; deliberations will return raw answers with no analysis",
                stacklevel=2,
            )

    def _warn_unknown_pricing_keys(self) -> None:
        """Non-fatal: a ``pricing`` key that matches no provider id is a likely typo
        that would silently cost $0 for that provider."""
        ids = {p.id for p in self.providers}
        unknown = sorted(k for k in self.pricing if k not in ids)
        if unknown:
            warnings.warn(f"pricing references unknown provider ids: {unknown}", stacklevel=2)

    def provider_map(self) -> dict[str, ProviderDescriptor]:
        return {p.id: p for p in self.providers if p.enabled}

    def safe_status(self) -> dict:
        """Non-secret config snapshot.

        Never leaks secret values or ``api_key_env`` *names*; reports only whether a
        provider requires a secret (plan §6.2, §14). The ``budget`` block holds
        **advisory** context-budget estimates computed for *enabled* providers; this
        reads the on-disk model-catalog cache (no network) and so can vary with cache
        freshness.
        """

        def provider_view(p: ProviderDescriptor) -> dict:
            data = {k: v for k, v in p.model_dump().items() if k != "api_key_env"}
            data["requires_secret"] = p.api_key_env is not None
            data["egress"] = "on-prem" if is_local_base_url(p.base_url) else "off-prem"
            data["headers"] = sorted(p.headers)
            return data

        from orchestrator.budget import estimate_provider_budget

        expected_output = self.defaults.max_tokens or 0
        provider_budgets = [
            estimate_provider_budget(
                p,
                prompt="",
                context=None,
                expected_output_tokens=expected_output,
                warning_ratio=self.defaults.budget_warning_ratio,
                error_ratio=self.defaults.budget_error_ratio,
            ).as_dict()
            for p in self.provider_map().values()
        ]

        judge_base_url = self.judge.resolved_base_url
        judge_view = self.judge.model_dump(exclude={"api_key_env", "headers"})
        judge_view["requires_secret"] = self.judge.uses_jev
        judge_view["base_url"] = judge_base_url
        judge_view["egress"] = "on-prem" if is_local_base_url(judge_base_url) else "off-prem"
        judge_view["headers"] = sorted(self.judge.headers)

        return {
            "defaults": self.defaults.model_dump(),
            "judge": judge_view,
            "context": self.context.model_dump(),
            "providers": [provider_view(p) for p in self.providers],
            "presets": {k: v.model_dump() for k, v in self.presets.items()},
            "pricing": {k: v.model_dump(by_alias=True) for k, v in self.pricing.items()},
            "budget": {
                "warning_ratio": self.defaults.budget_warning_ratio,
                "error_ratio": self.defaults.budget_error_ratio,
                "providers": provider_budgets,
            },
        }


def _resolve_path(path: str | None) -> Path:
    home = Path.home()
    candidates = [
        path,
        os.environ.get("MANDOS_CONFIG"),
        "./mandos.json",
        "./mandos.yaml",
        "./mandos.yml",
        str(home / ".mandos/config.json"),
        str(home / ".mandos/config.yaml"),
        str(home / ".mandos/config.yml"),
    ]
    for item in candidates:
        if item and Path(item).exists():
            return Path(item)
    raise FileNotFoundError("No Mandos config found")


def _strip_legacy_defaults(stripped: dict, removed: set[str]) -> None:
    defaults = stripped.get("defaults")
    if isinstance(defaults, dict):
        for key in ("curation_model", "anonymize", "include_raw"):
            if key in defaults:
                defaults.pop(key, None)
                removed.add(f"defaults.{key}")


def _strip_legacy_provider_roles(stripped: dict, removed: set[str]) -> None:
    providers = stripped.get("providers")
    if not isinstance(providers, list):
        return
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        roles = provider.get("roles")
        if isinstance(roles, list) and "curator" in roles:
            provider["roles"] = [role for role in roles if role != "curator"]
            removed.add("providers[].roles[curator]")


def _strip_legacy_presets(stripped: dict, removed: set[str]) -> None:
    presets = stripped.get("presets")
    if isinstance(presets, dict):
        for preset in presets.values():
            if isinstance(preset, dict) and "curation" in preset:
                preset.pop("curation", None)
                removed.add("presets[].curation")


def _strip_legacy_config(data: Any, *, warn: bool = False) -> Any:
    """Leniently strip curator/anonymization keys from older config files."""
    if not isinstance(data, dict):
        return data

    stripped = deepcopy(data)
    removed: set[str] = set()
    _strip_legacy_defaults(stripped, removed)
    _strip_legacy_provider_roles(stripped, removed)
    _strip_legacy_presets(stripped, removed)

    if warn and removed:
        warnings.warn(
            "Ignoring removed Mandos config fields: " + ", ".join(sorted(removed)),
            stacklevel=2,
        )
    return stripped


def load_config(path: str | None = None) -> MandosConfig:
    resolved = _resolve_path(path)
    text = resolved.read_text(encoding="utf-8")
    if resolved.suffix.lower() == ".json":
        data = json.loads(text) if text.strip() else {}
    else:
        data = yaml.safe_load(text) or {}
    return MandosConfig.model_validate(_strip_legacy_config(data, warn=True))


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Parse one ``.env`` line into ``(key, value)``, or ``None`` to skip it."""
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if key.startswith("export "):
        key = key[len("export ") :].strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    elif "#" in value:
        value = value.split("#", 1)[0].strip()
    return key, value


def _mode_is_group_or_other_readable(mode: int) -> bool:
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


def _warn_if_env_permissive(target: Path) -> None:
    """On POSIX, warn (never the contents) if the ``.env`` is group/other-readable —
    defense-in-depth for invariant 4. No-op on Windows."""
    if os.name != "posix":
        return
    try:
        mode = target.stat().st_mode
    except OSError:
        return
    if _mode_is_group_or_other_readable(mode):
        warnings.warn(
            f"{target} is group/other-readable; restrict it to 0600 to protect secrets",
            stacklevel=2,
        )


def load_env_file(path: str | Path | None = None) -> None:
    """Load ``~/.mandos/.env`` into the environment without overriding set vars.

    Tiny stdlib loader (no new dependency): tolerates a missing file, ignores blank
    lines and ``#`` comments, accepts an optional ``export`` prefix, and unwraps a
    single layer of matching single/double quotes. Existing environment variables
    win — already-exported secrets are never clobbered (plan §10).
    """
    target = Path(path) if path is not None else Path.home() / ".mandos/.env"
    if not target.exists():
        return
    _warn_if_env_permissive(target)
    for raw in target.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw.strip())
        if parsed is None:
            continue
        key, value = parsed
        if key not in os.environ:
            os.environ[key] = value
