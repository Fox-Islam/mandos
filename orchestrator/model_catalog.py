"""Offline-first provider/model metadata catalog.

The catalog deliberately avoids executing third-party package code. Bundled seed
data and optional JSON cache files are the only normal inputs; live ``/models``
fetching is an explicit refresh path.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

MODELSDEV_API_URL = "https://models.dev/api.json"
OPENAI_COMPATIBLE_NPM_PACKAGES = frozenset(
    {
        "@ai-sdk/openai-compatible",
        "@ai-sdk/openai",
        "@ai-sdk/groq",
        "@ai-sdk/xai",
        "@ai-sdk/mistral",
        "@ai-sdk/cerebras",
        "@ai-sdk/deepinfra",
        "@ai-sdk/togetherai",
        "@ai-sdk/perplexity",
        "@openrouter/ai-sdk-provider",
    }
)
DEFAULT_CACHE_PATH = "~/.mandos/model-catalog-cache.json"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DATE_SUFFIX_RE = re.compile(r"-20\d{2}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ModelMetadata:
    provider_key: str
    id: str
    display_name: str | None = None
    context_window: int | None = None
    # USD per million tokens. Carried so `cost_estimate_usd` can be right by default
    # instead of relying on a hand-maintained `pricing:` block that silently reports
    # every provider as unpriced when nobody fills it in.
    input_cost: float | None = None
    output_cost: float | None = None
    source: str = "seed"

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider_key": self.provider_key,
            "id": self.id,
            "source": self.source,
        }
        if self.display_name:
            data["display_name"] = self.display_name
        if self.context_window:
            data["context_window"] = self.context_window
        if self.input_cost is not None:
            data["input_cost"] = self.input_cost
        if self.output_cost is not None:
            data["output_cost"] = self.output_cost
        return data


@dataclass(frozen=True)
class CacheStatus:
    path: str
    exists: bool
    updated_at: float | None
    age_seconds: float | None
    stale: bool


@dataclass(frozen=True)
class CatalogRefreshResult:
    refreshed: bool
    models: list[ModelMetadata]
    status: CacheStatus
    error: str | None = None


def normalize_model_id(value: str) -> str:
    """Normalize a model id for comparisons, not for display."""
    normalized = " ".join(value.strip().lower().split())
    normalized = normalized.replace("_", "-").replace(".", "-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return _DATE_SUFFIX_RE.sub("", normalized)


def npm_catalog_allowed(package_name: str) -> bool:
    """Return whether an npm catalog source is allowlisted.

    Mandos never installs or executes npm packages; this gate exists so any
    future imported package data path remains explicit.
    """
    return package_name in OPENAI_COMPATIBLE_NPM_PACKAGES


def _metadata_from_dict(raw: dict[str, Any], *, source: str) -> ModelMetadata | None:
    model_id = raw.get("id")
    provider_key = raw.get("provider_key") or raw.get("provider")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    if not isinstance(provider_key, str) or not provider_key.strip():
        return None
    context = _coerce_context_window(raw)
    display = raw.get("display_name") or raw.get("name")
    input_cost, output_cost = _coerce_costs(raw)
    return ModelMetadata(
        provider_key=provider_key.strip(),
        id=model_id.strip(),
        display_name=str(display).strip() if display else None,
        context_window=context,
        input_cost=input_cost,
        output_cost=output_cost,
        source=str(raw.get("source") or source),
    )


def _coerce_costs(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    """Per-million-token prices, from whichever shape the source uses.

    models.dev nests them under ``cost`` as USD per million; OpenRouter's own catalog
    uses ``pricing`` as USD per token. Both are read, and the per-token form is scaled,
    because a price that is out by a factor of a million is worse than no price at all.
    """
    nested = raw.get("cost")
    if isinstance(nested, dict):
        found = (_as_price(nested.get("input")), _as_price(nested.get("output")))
        if found != (None, None):
            return found
    pricing = raw.get("pricing")
    if isinstance(pricing, dict):
        prompt, completion = _as_price(pricing.get("prompt")), _as_price(pricing.get("completion"))
        if (prompt, completion) != (None, None):
            return (
                prompt * 1_000_000 if prompt is not None else None,
                completion * 1_000_000 if completion is not None else None,
            )
    return _as_price(raw.get("input_cost")), _as_price(raw.get("output_cost"))


def _as_price(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _coerce_context_window(raw: dict[str, Any]) -> int | None:
    for key in (
        "context_window",
        "context_length",
        "contextLength",
        "context_size",
        "max_context_length",
        "max_context_tokens",
        "max_model_len",
    ):
        value = raw.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    for nested_key in ("metadata", "limits", "top_provider"):
        nested = raw.get(nested_key)
        if isinstance(nested, dict):
            context = _coerce_context_window(nested)
            if context is not None:
                return context
    return None


def load_seed_catalog() -> list[ModelMetadata]:
    resource = files("orchestrator.data").joinpath("model_catalog_seed.json")
    data = json.loads(resource.read_text(encoding="utf-8"))
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    return [
        model
        for item in models
        if isinstance(item, dict)
        for model in [_metadata_from_dict(item, source="seed")]
        if model is not None
    ]


def _read_cache_payload(path: str | Path = DEFAULT_CACHE_PATH) -> Any:
    target = Path(path).expanduser()
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_updated_at(path: str | Path = DEFAULT_CACHE_PATH, payload: Any = None) -> float | None:
    target = Path(path).expanduser()
    if not target.exists():
        return None
    if isinstance(payload, dict):
        try:
            parsed = float(payload.get("updated_at"))
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    try:
        return target.stat().st_mtime
    except OSError:
        return None


def cache_status(
    path: str | Path = DEFAULT_CACHE_PATH,
    *,
    max_age_seconds: int = CACHE_MAX_AGE_SECONDS,
    now: float | None = None,
) -> CacheStatus:
    target = Path(path).expanduser()
    payload = _read_cache_payload(target)
    updated_at = _cache_updated_at(target, payload)
    current = time.time() if now is None else now
    age = None if updated_at is None else max(0.0, current - updated_at)
    stale = bool(target.exists() and age is not None and age > max_age_seconds)
    return CacheStatus(
        path=str(target),
        exists=target.exists(),
        updated_at=updated_at,
        age_seconds=age,
        stale=stale,
    )


def read_cache(path: str | Path = DEFAULT_CACHE_PATH) -> list[ModelMetadata]:
    data = _read_cache_payload(path)
    if data is None:
        return []
    models = data.get("models") if isinstance(data, dict) else data
    if not isinstance(models, list):
        return []
    return [
        model
        for item in models
        if isinstance(item, dict)
        for model in [_metadata_from_dict(item, source="cache")]
        if model is not None
    ]


def write_cache(models: list[ModelMetadata], path: str | Path = DEFAULT_CACHE_PATH) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": time.time(), "models": [model.as_dict() for model in models]}
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def merge_catalogs(*catalogs: list[ModelMetadata]) -> list[ModelMetadata]:
    """Merge catalogs in order; later entries override earlier metadata."""
    merged: dict[tuple[str, str], ModelMetadata] = {}
    for catalog in catalogs:
        for model in catalog:
            key = (model.provider_key, normalize_model_id(model.id))
            merged[key] = model
    return sorted(merged.values(), key=lambda m: (m.provider_key, normalize_model_id(m.id)))


def models_for_provider(
    provider_key: str,
    *,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    overrides: list[ModelMetadata] | None = None,
) -> list[ModelMetadata]:
    catalog = merge_catalogs(load_seed_catalog(), read_cache(cache_path), overrides or [])
    return [model for model in catalog if model.provider_key == provider_key]


def resolve_model_metadata(
    provider_key: str,
    model_id: str,
    *,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    overrides: list[ModelMetadata] | None = None,
) -> ModelMetadata | None:
    wanted = normalize_model_id(model_id)
    for model in models_for_provider(provider_key, cache_path=cache_path, overrides=overrides):
        if normalize_model_id(model.id) == wanted:
            return model
    return None


def metadata_from_provider_override(provider) -> ModelMetadata | None:
    provider_key = getattr(provider, "catalog_key", None) or getattr(provider, "kind", None)
    model = getattr(provider, "model", None)
    context_window = getattr(provider, "context_window", None)
    if not provider_key or not model or not context_window:
        return None
    return ModelMetadata(
        provider_key=str(provider_key),
        id=str(model),
        context_window=int(context_window),
        source="override",
    )


def fetch_live_model_metadata(
    base_url: str,
    *,
    provider_key: str,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 15,
) -> list[ModelMetadata] | None:
    """Fetch ``GET {base_url}/models`` and parse best-effort metadata.

    Returns ``None`` on any failure so callers can fall back to bundled/cache data.
    """
    request_headers = dict(headers or {})
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(
            base_url.rstrip("/") + "/models",
            headers=request_headers,
            timeout=timeout_s,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    parsed = [
        model
        for item in items
        if isinstance(item, dict)
        for model in [
            _metadata_from_dict(
                {"provider_key": provider_key, **item, "source": "live"},
                source="live",
            )
        ]
        if model is not None
    ]
    return parsed or None


def _modelsdev_metadata(
    provider_key: str, provider_data: dict, model_id: str, model_data: dict
) -> ModelMetadata | None:
    limit = model_data.get("limit")
    raw_context = limit.get("context") if isinstance(limit, dict) else None
    return _metadata_from_dict(
        {
            "provider_key": str(provider_data.get("id") or provider_key),
            "id": str(model_data.get("id") or model_id),
            "display_name": model_data.get("name"),
            "context_window": raw_context,
            "source": "modelsdev",
        },
        source="modelsdev",
    )


def _models_from_provider(provider_key: str, provider_data: dict) -> list[ModelMetadata]:
    models = provider_data.get("models")
    if not isinstance(models, dict):
        return []
    parsed: list[ModelMetadata] = []
    for model_id, model_data in models.items():
        if not isinstance(model_id, str) or not isinstance(model_data, dict):
            continue
        metadata = _modelsdev_metadata(provider_key, provider_data, model_id, model_data)
        if metadata is not None:
            parsed.append(metadata)
    return parsed


def fetch_modelsdev_catalog(
    *,
    url: str = MODELSDEV_API_URL,
    timeout_s: float = 15,
) -> list[ModelMetadata] | None:
    """Fetch models.dev ``api.json`` and return OpenAI-compatible model metadata.

    The response is parsed as data only. Mandos never installs or executes the
    provider npm packages listed by models.dev.
    """
    try:
        response = httpx.get(url, timeout=timeout_s)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    parsed: list[ModelMetadata] = []
    for provider_key, provider_data in data.items():
        if not isinstance(provider_data, dict):
            continue
        npm = provider_data.get("npm")
        if not isinstance(npm, str) or not npm_catalog_allowed(npm):
            continue
        parsed.extend(_models_from_provider(provider_key, provider_data))
    return parsed or None


def refresh_cache_from_live(
    base_url: str,
    *,
    provider_key: str,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
) -> list[ModelMetadata] | None:
    live = fetch_live_model_metadata(
        base_url,
        provider_key=provider_key,
        token=token,
        headers=headers,
    )
    if live is None:
        return None
    merged = merge_catalogs(read_cache(cache_path), live)
    write_cache(merged, cache_path)
    return live


def refresh_cache_from_modelsdev(
    *,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    url: str = MODELSDEV_API_URL,
) -> list[ModelMetadata] | None:
    live = fetch_modelsdev_catalog(url=url)
    if live is None:
        return None
    merged = merge_catalogs(read_cache(cache_path), live)
    write_cache(merged, cache_path)
    return live


def refresh_cache_if_stale(
    *,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    url: str = MODELSDEV_API_URL,
    max_age_seconds: int = CACHE_MAX_AGE_SECONDS,
    force: bool = False,
    now: float | None = None,
) -> CatalogRefreshResult:
    status = cache_status(cache_path, max_age_seconds=max_age_seconds, now=now)
    cached = read_cache(cache_path)
    if not force and not status.stale:
        return CatalogRefreshResult(
            refreshed=False,
            models=cached or load_seed_catalog(),
            status=status,
        )

    live = fetch_modelsdev_catalog(url=url)
    if live is None:
        return CatalogRefreshResult(
            refreshed=False,
            models=cached or load_seed_catalog(),
            status=status,
            error="models.dev fetch failed",
        )

    merged = merge_catalogs(cached, live)
    write_cache(merged, cache_path)
    return CatalogRefreshResult(
        refreshed=True,
        models=merged,
        status=cache_status(cache_path, max_age_seconds=max_age_seconds, now=now),
    )
