"""Pure config loading, mutation, and persistence helpers for the configurator TUI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from orchestrator.cli.secrets import DEFAULT_ENV_PATH, expand_user_path, write_env
from orchestrator.settings import (
    Defaults,
    ImladrisConfig,
    Preset,
    Price,
    ProviderDescriptor,
    _strip_legacy_config,
)

DEFAULT_DRAFT_TARGET = "~/.imladris/config.json"
COUNCIL_PRESET = "council"
LOCAL_ONLY_PRESET = "local-only"
_UNCHANGED = object()
_MISSING = object()


@dataclass(frozen=True)
class ParseError:
    location: str
    message: str
    raw: Any = None


@dataclass(frozen=True)
class Issue:
    label: str
    action: str


@dataclass
class Draft:
    source_path: Path
    env_path: Path
    providers: list[ProviderDescriptor] = field(default_factory=list)
    defaults: Defaults = field(default_factory=Defaults)
    presets: dict[str, Preset] = field(default_factory=dict)
    pricing: dict[str, Price] = field(default_factory=dict)
    token_present: dict[str, bool] = field(default_factory=dict)
    parse_errors: list[ParseError] = field(default_factory=list)
    full_validation_error: str | None = None

    def to_config(self) -> ImladrisConfig:
        if self.parse_errors:
            raise ValueError("cannot modify a config with malformed records")
        staged = _stage_environment(_validation_stage_values(env_path=self.env_path))
        try:
            return ImladrisConfig.model_validate(
                _config_data(self.defaults, self.providers, self.presets, self.pricing)
            )
        except Exception as exc:
            raise ValueError(_validation_message(exc)) from exc
        finally:
            _restore_environment(staged)


@dataclass(frozen=True)
class TokenUpdate:
    changes: dict[str, str]
    prune_env_keys: tuple[str, ...] = ()


ConfigSource = ImladrisConfig | Draft | None


def _default_target_path() -> Path:
    return expand_user_path(DEFAULT_DRAFT_TARGET)


def default_env_path(home: str | Path | None = None) -> Path:
    if home is not None:
        return expand_user_path(home) / ".imladris/.env"
    return expand_user_path(DEFAULT_ENV_PATH)


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and exc.errors():
        err = exc.errors()[0]
        loc = ".".join(str(part) for part in err.get("loc", ()))
        message = str(err.get("msg", exc))
        return f"{loc}: {message}" if loc else message
    return str(exc)


def _parse_env_file(path: str | Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    target = expand_user_path(path)
    values: dict[str, str] = {}
    if not target.exists():
        return values
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        elif "#" in value:
            value = value.split("#", 1)[0].strip()
        values[key] = value
    return values


def _loaded_env(path: str | Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    values = _parse_env_file(path)
    values.update(os.environ)
    return values


def loaded_env_values(path: str | Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Return env-file values overlaid by the current process environment."""
    return _loaded_env(path)


def _stage_environment(values: dict[str, str]) -> dict[str, str | object]:
    previous: dict[str, str | object] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key, _MISSING)
        os.environ[key] = value
    return previous


def _restore_environment(previous: dict[str, str | object]) -> None:
    for key, value in previous.items():
        if value is _MISSING:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)


def _validation_stage_values(
    token_changes: dict[str, str] | None = None,
    *,
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> dict[str, str]:
    values = {
        key: value for key, value in _parse_env_file(env_path).items() if key not in os.environ
    }
    values.update({key: value for key, value in (token_changes or {}).items() if key and value})
    return values


def _resolve_draft_path(path: str | Path | None) -> Path:
    if path is not None:
        explicit = expand_user_path(path)
        return explicit

    candidates = [
        os.environ.get("IMLADRIS_CONFIG"),
        "./imladris.json",
        "./imladris.yaml",
        "./imladris.yml",
        DEFAULT_DRAFT_TARGET,
        "~/.imladris/config.yaml",
        "~/.imladris/config.yml",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = expand_user_path(candidate)
        if resolved.exists():
            return resolved
    return _default_target_path()


def _read_config_data(path: Path) -> tuple[dict[str, Any], list[ParseError]]:
    if not path.exists():
        return {}, []
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text) if text.strip() else {}
        else:
            data = yaml.safe_load(text) or {}
    except Exception as exc:
        return {}, [ParseError("config", _validation_message(exc))]
    if not isinstance(data, dict):
        return {}, [ParseError("config", "config root must be a mapping", data)]
    return _strip_legacy_config(data), []


def _parse_providers(data: dict[str, Any], errors: list[ParseError]) -> list[ProviderDescriptor]:
    raw_providers = data.get("providers", [])
    if raw_providers is None:
        raw_providers = []
    if not isinstance(raw_providers, list):
        errors.append(ParseError("providers", "providers must be a list", raw_providers))
        return []

    providers: list[ProviderDescriptor] = []
    for index, raw in enumerate(raw_providers):
        if not isinstance(raw, dict):
            errors.append(ParseError(f"providers[{index}]", "provider row must be a mapping", raw))
            continue
        try:
            providers.append(ProviderDescriptor.model_validate(raw))
        except ValidationError as exc:
            errors.append(ParseError(f"providers[{index}]", _validation_message(exc), raw))
    return providers


def _parse_defaults(data: dict[str, Any], errors: list[ParseError]) -> Defaults:
    raw_defaults = data.get("defaults", {})
    if raw_defaults is None:
        raw_defaults = {}
    try:
        return Defaults.model_validate(raw_defaults)
    except ValidationError as exc:
        errors.append(ParseError("defaults", _validation_message(exc), raw_defaults))
        return Defaults()


def _parse_presets(data: dict[str, Any], errors: list[ParseError]) -> dict[str, Preset]:
    raw_presets = data.get("presets", {})
    if raw_presets is None:
        raw_presets = {}
    if not isinstance(raw_presets, dict):
        errors.append(ParseError("presets", "presets must be a mapping", raw_presets))
        return {}

    presets: dict[str, Preset] = {}
    for name, raw in raw_presets.items():
        try:
            presets[str(name)] = Preset.model_validate(raw)
        except ValidationError as exc:
            errors.append(ParseError(f"presets.{name}", _validation_message(exc), raw))
    return presets


def _parse_pricing(data: dict[str, Any], errors: list[ParseError]) -> dict[str, Price]:
    raw_pricing = data.get("pricing", {})
    if raw_pricing is None:
        raw_pricing = {}
    if not isinstance(raw_pricing, dict):
        errors.append(ParseError("pricing", "pricing must be a mapping", raw_pricing))
        return {}

    pricing: dict[str, Price] = {}
    for name, raw in raw_pricing.items():
        try:
            pricing[str(name)] = Price.model_validate(raw)
        except ValidationError as exc:
            errors.append(ParseError(f"pricing.{name}", _validation_message(exc), raw))
    return pricing


def _config_data(
    defaults: Defaults,
    providers: list[ProviderDescriptor],
    presets: dict[str, Preset],
    pricing: dict[str, Price],
) -> dict[str, Any]:
    return {
        "defaults": defaults.model_dump(exclude_none=True),
        "providers": [p.model_dump(exclude_none=True) for p in providers],
        "presets": {name: preset.model_dump(exclude_none=True) for name, preset in presets.items()},
        "pricing": {name: price.model_dump(by_alias=True) for name, price in pricing.items()},
    }


def _role_error(
    enabled: dict[str, ProviderDescriptor], provider_id: str | None, role: str, where: str
) -> str | None:
    if provider_id is None:
        return None
    provider = enabled.get(provider_id)
    if provider is None or role not in provider.roles:
        return f"{where} must reference an enabled provider with the '{role}' role: {provider_id!r}"
    return None


def _preset_error(name: str, preset: Preset, enabled: dict[str, ProviderDescriptor]) -> str | None:
    if not preset.panel:
        return f"preset {name} has empty panel"
    if len(preset.panel) > 8:
        return f"preset {name} panel exceeds 8 members"
    if len(preset.panel) != len(set(preset.panel)):
        return f"preset {name} panel contains duplicate providers"
    unknown = [provider_id for provider_id in preset.panel if provider_id not in enabled]
    if unknown:
        return f"preset {name} references unknown providers: {unknown}"
    non_panel = [
        provider_id for provider_id in preset.panel if "panel" not in enabled[provider_id].roles
    ]
    if non_panel:
        return f"preset {name} references non-panel providers: {non_panel}"
    return _role_error(enabled, preset.analysis, "judge", f"preset {name} analysis")


def _full_validation_error_for_draft(
    providers: list[ProviderDescriptor],
    defaults: Defaults,
    presets: dict[str, Preset],
    loaded_env: dict[str, str],
) -> str | None:
    ids = [p.id for p in providers]
    if len(ids) != len(set(ids)):
        return "provider ids must be unique"

    enabled = {p.id: p for p in providers if p.enabled}
    if not enabled:
        return "at least one provider must be enabled"

    for provider in enabled.values():
        if provider.api_key_env and not loaded_env.get(provider.api_key_env):
            return f"missing environment variable {provider.api_key_env} for provider {provider.id}"

    if error := _role_error(enabled, defaults.analysis_model, "judge", "defaults.analysis_model"):
        return error
    for name, preset in presets.items():
        if error := _preset_error(name, preset, enabled):
            return error

    return None


def load_draft(
    path: str | Path | None = None,
    *,
    env_path: str | Path | None = None,
) -> Draft:
    """Load config rows without requiring full config validity."""
    source_path = _resolve_draft_path(path)
    resolved_env_path = default_env_path() if env_path is None else expand_user_path(env_path)
    data, parse_errors = _read_config_data(source_path)
    providers = _parse_providers(data, parse_errors)
    defaults = _parse_defaults(data, parse_errors)
    presets = _parse_presets(data, parse_errors)
    pricing = _parse_pricing(data, parse_errors)

    loaded_env = _loaded_env(resolved_env_path)
    token_present = {
        p.api_key_env: bool(loaded_env.get(p.api_key_env)) for p in providers if p.api_key_env
    }

    full_validation_error = None
    if source_path.exists() and not parse_errors:
        full_validation_error = _full_validation_error_for_draft(
            providers, defaults, presets, loaded_env
        )

    return Draft(
        source_path=source_path,
        env_path=resolved_env_path,
        providers=providers,
        defaults=defaults,
        presets=presets,
        pricing=pricing,
        token_present=token_present,
        parse_errors=parse_errors,
        full_validation_error=full_validation_error,
    )


def compute_issues(draft: Draft, harness_status: dict[str, bool] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    enabled = {p.id: p for p in draft.providers if p.enabled}

    if not draft.providers:
        issues.append(Issue("No council members", "Add member"))

    judge_id = draft.defaults.analysis_model
    judge = enabled.get(judge_id) if judge_id else None
    if judge is None or "judge" not in judge.roles:
        issues.append(Issue("Judge: not assigned", "Reassign roles"))

    if not any(p.enabled and "panel" in p.roles for p in draft.providers):
        issues.append(Issue("No enabled panel member", "Reassign roles"))

    for provider in draft.providers:
        if (
            provider.enabled
            and provider.api_key_env
            and not draft.token_present.get(provider.api_key_env, False)
        ):
            issues.append(
                Issue(
                    f"{provider.id}: token not set ({provider.api_key_env})",
                    "Edit member",
                )
            )

    for error in draft.parse_errors:
        issues.append(Issue(f"Malformed {error.location}: {error.message}", "Manual config repair"))

    if draft.full_validation_error:
        issues.append(
            Issue(f"Invalid config: {draft.full_validation_error}", "Manual config repair")
        )

    if harness_status is not None and not any(harness_status.values()):
        issues.append(Issue("No harness wired", "Wire harnesses"))

    return issues


def _provider_to_data(provider: ProviderDescriptor | dict[str, Any]) -> dict[str, Any]:
    if isinstance(provider, ProviderDescriptor):
        return provider.model_dump(exclude_none=True)
    try:
        return ProviderDescriptor.model_validate(provider).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValueError(_validation_message(exc)) from exc


def _data_from_source(source: ConfigSource) -> dict[str, Any]:
    if source is None:
        return _config_data(Defaults(), [], {}, {})
    if isinstance(source, Draft):
        if source.parse_errors:
            raise ValueError("cannot modify a config with malformed records")
        return _config_data(source.defaults, source.providers, source.presets, source.pricing)
    return source.model_dump(by_alias=True, exclude_none=True)


def _dedupe_roles(roles: list[str]) -> list[str]:
    return list(dict.fromkeys(roles))


def _roles(provider: dict[str, Any]) -> list[str]:
    raw_roles = provider.get("roles") or []
    return [str(role) for role in raw_roles]


def _set_roles(provider: dict[str, Any], roles: list[str]) -> None:
    provider["roles"] = _dedupe_roles(roles)


def _find_provider_data(data: dict[str, Any], provider_id: str) -> dict[str, Any]:
    for provider in data["providers"]:
        if provider.get("id") == provider_id:
            return provider
    raise ValueError(f"unknown provider: {provider_id}")


def _replace_preset_id(preset: dict[str, Any], old_id: str, new_id: str) -> None:
    panel = preset.get("panel") or []
    preset["panel"] = [new_id if item == old_id else item for item in panel]
    if preset.get("analysis") == old_id:
        preset["analysis"] = new_id


def _remove_preset_id(preset: dict[str, Any], provider_id: str) -> None:
    preset["panel"] = [item for item in preset.get("panel") or [] if item != provider_id]
    if preset.get("analysis") == provider_id:
        preset["analysis"] = None


def _preset_to_data(preset: Any) -> dict[str, Any]:
    if isinstance(preset, Preset):
        return preset.model_dump(exclude_none=True)
    if isinstance(preset, dict):
        return dict(preset)
    return {}


def _preset_valid(preset: dict[str, Any], enabled: dict[str, dict[str, Any]]) -> bool:
    panel = preset.get("panel") or []
    if not panel or len(panel) > 8 or len(panel) != len(set(panel)):
        return False
    if any(pid not in enabled or "panel" not in _roles(enabled[pid]) for pid in panel):
        return False

    analysis = preset.get("analysis")
    if analysis is not None:
        provider = enabled.get(analysis)
        if provider is None or "judge" not in _roles(provider):
            return False

    return True


def _carryover_presets(
    existing: dict[str, Any], enabled: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    presets: dict[str, dict[str, Any]] = {}
    for name, raw_preset in existing.items():
        if name in {COUNCIL_PRESET, LOCAL_ONLY_PRESET}:
            continue
        preset = _preset_to_data(raw_preset)
        if _preset_valid(preset, enabled):
            presets[str(name)] = preset
    return presets


def _keyless_role_holder(
    enabled: dict[str, dict[str, Any]], provider_id: str | None, role: str
) -> bool:
    if provider_id is None:
        return False
    provider = enabled.get(provider_id)
    return bool(provider and role in _roles(provider) and not provider.get("api_key_env"))


def _regenerate_presets_data(data: dict[str, Any]) -> None:
    defaults = data.setdefault("defaults", {})
    providers = data.setdefault("providers", [])
    enabled = {p["id"]: p for p in providers if p.get("enabled", True) and p.get("id")}
    presets = _carryover_presets(data.get("presets") or {}, enabled)

    panel = [p["id"] for p in providers if p.get("enabled", True) and "panel" in _roles(p)]
    analysis = defaults.get("analysis_model")
    if panel:
        presets[COUNCIL_PRESET] = {
            "panel": panel,
            "analysis": analysis,
        }

    keyless_panel = [
        p["id"]
        for p in providers
        if p.get("enabled", True) and "panel" in _roles(p) and not p.get("api_key_env")
    ]
    if keyless_panel and _keyless_role_holder(enabled, analysis, "judge"):
        presets[LOCAL_ONLY_PRESET] = {
            "panel": keyless_panel,
            "analysis": analysis,
        }

    default_preset = defaults.get("preset")
    if default_preset not in presets:
        defaults["preset"] = COUNCIL_PRESET if COUNCIL_PRESET in presets else None

    data["presets"] = presets


def _assert_config_ops_invariants(config: ImladrisConfig) -> None:
    enabled = {p.id: p for p in config.providers if p.enabled}
    if not any("panel" in p.roles for p in enabled.values()):
        raise ValueError("at least one enabled panel member is required")

    judges = [p.id for p in enabled.values() if "judge" in p.roles]
    if len(judges) > 1:
        raise ValueError("at most one judge may be assigned")
    if config.defaults.analysis_model is None:
        if judges:
            raise ValueError("judge role must be cleared when defaults.analysis_model is empty")
    elif judges != [config.defaults.analysis_model]:
        raise ValueError("defaults.analysis_model must match the assigned judge role")


def _validate_mutation_data(
    data: dict[str, Any],
    *,
    token_changes: dict[str, str] | None = None,
) -> ImladrisConfig:
    staged = _stage_environment(_validation_stage_values(token_changes))
    try:
        try:
            config = ImladrisConfig.model_validate(data)
        except Exception as exc:
            raise ValueError(_validation_message(exc)) from exc
        _assert_config_ops_invariants(config)
        return config
    finally:
        _restore_environment(staged)


def _finalize_mutation(
    data: dict[str, Any],
    *,
    token_changes: dict[str, str] | None = None,
) -> ImladrisConfig:
    _regenerate_presets_data(data)
    return _validate_mutation_data(data, token_changes=token_changes)


def regenerate_presets(config: ImladrisConfig) -> ImladrisConfig:
    data = _data_from_source(config)
    return _finalize_mutation(data)


def add_member(
    config: ConfigSource,
    member: ProviderDescriptor | dict[str, Any],
    *,
    token_changes: dict[str, str] | None = None,
) -> ImladrisConfig:
    data = _data_from_source(config)
    member_data = _provider_to_data(member)
    member_id = member_data["id"]
    if any(provider.get("id") == member_id for provider in data["providers"]):
        raise ValueError(f"provider id already exists: {member_id}")

    member_roles = _roles(member_data) or ["panel"]
    _set_roles(member_data, member_roles)
    if "judge" in member_roles:
        for provider in data["providers"]:
            _set_roles(provider, [role for role in _roles(provider) if role != "judge"])
        data["defaults"]["analysis_model"] = member_id

    data["providers"].append(member_data)
    return _finalize_mutation(data, token_changes=token_changes)


def _rename_member_references(data: dict[str, Any], old_id: str, new_id: str) -> None:
    if data["defaults"].get("analysis_model") == old_id:
        data["defaults"]["analysis_model"] = new_id
    for preset in (data.get("presets") or {}).values():
        _replace_preset_id(preset, old_id, new_id)


def _disable_member(data: dict[str, Any], provider: dict[str, Any], new_id: str) -> None:
    if data["defaults"].get("analysis_model") == new_id:
        data["defaults"]["analysis_model"] = None
    _set_roles(provider, [role for role in _roles(provider) if role != "judge"])


def _apply_member_role_changes(data: dict[str, Any], provider: dict[str, Any], new_id: str) -> None:
    roles = _roles(provider)
    if "judge" in roles:
        for item in data["providers"]:
            if item is not provider:
                _set_roles(item, [role for role in _roles(item) if role != "judge"])
        data["defaults"]["analysis_model"] = new_id
    elif data["defaults"].get("analysis_model") == new_id:
        data["defaults"]["analysis_model"] = None


def update_member(
    config: ImladrisConfig,
    provider_id: str,
    *,
    token_changes: dict[str, str] | None = None,
    **changes: Any,
) -> ImladrisConfig:
    data = _data_from_source(config)
    provider = _find_provider_data(data, provider_id)
    old_id = str(provider["id"])
    updated = dict(provider)
    updated.update(changes)
    updated = _provider_to_data(updated)
    new_id = str(updated["id"])

    if old_id != new_id and any(
        item.get("id") == new_id for item in data["providers"] if item is not provider
    ):
        raise ValueError(f"provider id already exists: {new_id}")

    provider.clear()
    provider.update(updated)

    if old_id != new_id:
        _rename_member_references(data, old_id, new_id)
    if not provider.get("enabled", True):
        _disable_member(data, provider, new_id)
    if "roles" in changes:
        _apply_member_role_changes(data, provider, new_id)

    return _finalize_mutation(data, token_changes=token_changes)


def delete_member(config: ConfigSource, provider_id: str) -> ImladrisConfig:
    data = _data_from_source(config)
    provider = _find_provider_data(data, provider_id)
    enabled_panel = [
        item for item in data["providers"] if item.get("enabled", True) and "panel" in _roles(item)
    ]
    if provider.get("enabled", True) and "panel" in _roles(provider) and len(enabled_panel) == 1:
        raise ValueError("cannot delete the last enabled panel member")

    data["providers"] = [item for item in data["providers"] if item.get("id") != provider_id]
    if data["defaults"].get("analysis_model") == provider_id:
        data["defaults"]["analysis_model"] = None
    for preset in (data.get("presets") or {}).values():
        _remove_preset_id(preset, provider_id)

    return _finalize_mutation(data)


def set_judge(config: ConfigSource, provider_id: str | None) -> ImladrisConfig:
    data = _data_from_source(config)
    if provider_id is not None:
        provider = _find_provider_data(data, provider_id)
        if not provider.get("enabled", True):
            raise ValueError(f"judge must be an enabled provider: {provider_id}")
    else:
        provider = None

    for item in data["providers"]:
        _set_roles(item, [role for role in _roles(item) if role != "judge"])
    data["defaults"]["analysis_model"] = provider_id
    if provider is not None:
        _set_roles(provider, [*_roles(provider), "judge"])
    return _finalize_mutation(data)


def set_panel(config: ConfigSource, provider_id: str, enabled: bool) -> ImladrisConfig:
    data = _data_from_source(config)
    provider = _find_provider_data(data, provider_id)
    roles = [role for role in _roles(provider) if role != "panel"]
    if enabled:
        roles.insert(0, "panel")
    _set_roles(provider, roles)
    return _finalize_mutation(data)


def referenced_env_keys(config: ImladrisConfig) -> set[str]:
    return {p.api_key_env for p in config.providers if p.api_key_env}


def orphaned_env_keys(
    before: ImladrisConfig,
    after: ImladrisConfig,
    candidates: list[str] | tuple[str, ...] | set[str] | None = None,
) -> tuple[str, ...]:
    before_keys = referenced_env_keys(before)
    candidate_keys = set(candidates) if candidates is not None else before_keys
    after_keys = referenced_env_keys(after)
    return tuple(sorted(key for key in candidate_keys if key and key in before_keys - after_keys))


def _token_update_with_token(
    token: str, old_env: str | None, new_env: str | None | object
) -> TokenUpdate:
    if not new_env:
        raise ValueError("api_key_env is required when a token is provided")
    prune = [old_env] if (new_env != old_env and old_env) else []
    return TokenUpdate(changes={str(new_env): token}, prune_env_keys=tuple(prune))


def _token_update_env_change(
    old_env: str | None, new_env: str | None | object, loaded_env: dict[str, str] | None
) -> TokenUpdate:
    changes: dict[str, str] = {}
    if new_env:
        env = loaded_env if loaded_env is not None else _loaded_env()
        if old_env and env.get(old_env):
            changes[str(new_env)] = env[old_env]
        else:
            raise ValueError(f"token required for {new_env}")
    prune = [old_env] if old_env else []
    return TokenUpdate(changes=changes, prune_env_keys=tuple(prune))


def plan_token_update(
    config: ImladrisConfig,
    provider_id: str,
    *,
    api_key_env: str | None | object = _UNCHANGED,
    token: str | None = None,
    loaded_env: dict[str, str] | None = None,
) -> TokenUpdate:
    provider = next((p for p in config.providers if p.id == provider_id), None)
    if provider is None:
        raise ValueError(f"unknown provider: {provider_id}")

    old_env = provider.api_key_env
    new_env = old_env if api_key_env is _UNCHANGED else api_key_env

    if token:
        return _token_update_with_token(token, old_env, new_env)
    if new_env != old_env:
        return _token_update_env_change(old_env, new_env, loaded_env)
    return TokenUpdate(changes={}, prune_env_keys=())


def persist(
    config: ImladrisConfig,
    token_changes: dict[str, str],
    target_path: str | Path,
    prune_env_keys: list[str] | tuple[str, ...] | set[str] = (),
    *,
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> Path:
    token_values = {key: value for key, value in token_changes.items() if key and value}
    staged_values = _validation_stage_values(token_values, env_path=env_path)
    previous = _stage_environment(staged_values)
    try:
        try:
            validated = ImladrisConfig.model_validate(config.model_dump(by_alias=True))
            _assert_config_ops_invariants(validated)
        except Exception as exc:
            raise ValueError(_validation_message(exc)) from exc
    except Exception:
        _restore_environment(previous)
        raise

    from orchestrator.cli.config_writer import write_config

    target = write_config(validated, target_path)
    still_referenced = referenced_env_keys(validated)
    effective_prune = [
        key
        for key in prune_env_keys
        if key and key not in still_referenced and key not in token_values
    ]
    write_env(token_values, env_path, prune_keys=effective_prune)
    for key in effective_prune:
        if previous.get(key) is _MISSING:
            os.environ.pop(key, None)
    return target
