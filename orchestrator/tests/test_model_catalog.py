from __future__ import annotations

import httpx
import respx

from orchestrator.model_catalog import (
    ModelMetadata,
    _models_from_provider,
    cache_status,
    fetch_live_model_metadata,
    fetch_modelsdev_catalog,
    merge_catalogs,
    models_for_provider,
    normalize_model_id,
    npm_catalog_allowed,
    read_cache,
    refresh_cache_if_stale,
    resolve_model_metadata,
    write_cache,
)


def test_normalize_model_id_and_npm_allowlist():
    assert normalize_model_id("  GPT-4O  Mini ") == "gpt-4o mini"
    assert normalize_model_id("qwen2.5-coder-2025-01-01") == "qwen2-5-coder"
    assert normalize_model_id("qwen2_5.coder") == "qwen2-5-coder"
    assert npm_catalog_allowed("@ai-sdk/openai-compatible") is True
    assert npm_catalog_allowed("@ai-sdk/anthropic") is False
    assert npm_catalog_allowed("left-pad") is False


def test_cache_round_trip_and_override_precedence(tmp_path):
    cache = tmp_path / "models.json"
    cached = [ModelMetadata("custom", "model-a", context_window=1000, source="cache")]
    override = [ModelMetadata("custom", "model-a", context_window=2000, source="override")]

    write_cache(cached, cache)
    assert read_cache(cache)[0].context_window == 1000
    merged = merge_catalogs(read_cache(cache), override)
    assert merged[0].context_window == 2000


def test_cache_status_uses_cache_timestamp_and_marks_stale(tmp_path):
    cache = tmp_path / "models.json"
    write_cache([ModelMetadata("custom", "fresh", context_window=1000, source="cache")], cache)

    fresh = cache_status(cache, max_age_seconds=10, now=cache_status(cache).updated_at)
    assert fresh.exists is True
    assert fresh.stale is False

    old = cache_status(cache, max_age_seconds=10, now=(fresh.updated_at or 0) + 11)
    assert old.stale is True


def test_seed_resolve_and_provider_filter(tmp_path):
    models = models_for_provider("openai", cache_path=tmp_path / "missing.json")
    assert any(model.id == "gpt-4o-mini" for model in models)
    resolved = resolve_model_metadata(
        "openai",
        "GPT-4O-MINI",
        cache_path=tmp_path / "missing.json",
    )
    assert resolved is not None
    assert resolved.context_window == 128000


def test_refresh_cache_if_stale_skips_network_for_fresh_cache(tmp_path):
    cache = tmp_path / "models.json"
    cached = [ModelMetadata("custom", "cached", context_window=1000, source="cache")]
    write_cache(cached, cache)

    result = refresh_cache_if_stale(
        cache_path=cache,
        url="https://unused.test/api.json",
        now=cache_status(cache).updated_at,
    )

    assert result.refreshed is False
    assert result.error is None
    assert result.models == cached


@respx.mock
def test_refresh_cache_if_stale_refreshes_old_cache(tmp_path):
    cache = tmp_path / "models.json"
    write_cache([ModelMetadata("openai", "old", context_window=1000, source="cache")], cache)
    updated_at = cache_status(cache).updated_at or 0
    respx.get("https://models.test/api.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "openai": {
                    "id": "openai",
                    "npm": "@ai-sdk/openai",
                    "models": {
                        "gpt-4o": {
                            "id": "gpt-4o",
                            "name": "GPT-4o",
                            "limit": {"context": 128000},
                        }
                    },
                }
            },
        )
    )

    result = refresh_cache_if_stale(
        cache_path=cache,
        url="https://models.test/api.json",
        max_age_seconds=10,
        now=updated_at + 11,
    )

    assert result.refreshed is True
    assert resolve_model_metadata("openai", "gpt-4o", cache_path=cache).context_window == 128000


@respx.mock
def test_refresh_cache_if_stale_falls_back_to_cache_on_fetch_failure(tmp_path):
    cache = tmp_path / "models.json"
    cached = [ModelMetadata("custom", "cached", context_window=1000, source="cache")]
    write_cache(cached, cache)
    updated_at = cache_status(cache).updated_at or 0
    respx.get("https://models.test/api.json").mock(return_value=httpx.Response(500))

    result = refresh_cache_if_stale(
        cache_path=cache,
        url="https://models.test/api.json",
        max_age_seconds=10,
        now=updated_at + 11,
    )

    assert result.refreshed is False
    assert result.error == "models.dev fetch failed"
    assert result.models == cached


@respx.mock
def test_fetch_live_model_metadata_parses_context_window():
    respx.get("https://api.example.test/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-a", "context_window": 4096},
                    {"id": "model-b", "context_length": "8192"},
                    {"id": "model-c", "metadata": {"context_length": 16384}},
                    {"id": "model-d", "limits": {"max_context_length": 32768}},
                ]
            },
        )
    )
    models = fetch_live_model_metadata(
        "https://api.example.test/v1",
        provider_key="custom",
        token="secret",
    )
    assert models is not None
    assert [model.context_window for model in models] == [4096, 8192, 16384, 32768]


@respx.mock
def test_fetch_live_model_metadata_returns_none_on_failure():
    respx.get("https://api.example.test/v1/models").mock(
        return_value=httpx.Response(500, text="boom")
    )
    assert fetch_live_model_metadata("https://api.example.test/v1", provider_key="custom") is None


@respx.mock
def test_fetch_modelsdev_catalog_filters_to_openai_compatible_npm_packages():
    respx.get("https://models.test/api.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "openai": {
                    "id": "openai",
                    "npm": "@ai-sdk/openai",
                    "models": {
                        "gpt-4o": {
                            "id": "gpt-4o",
                            "name": "GPT-4o",
                            "limit": {"context": 128000, "output": 16384},
                        }
                    },
                },
                "anthropic": {
                    "id": "anthropic",
                    "npm": "@ai-sdk/anthropic",
                    "models": {
                        "claude": {
                            "id": "claude",
                            "name": "Claude",
                            "limit": {"context": 200000},
                        }
                    },
                },
            },
        )
    )

    models = fetch_modelsdev_catalog(url="https://models.test/api.json")

    assert models == [
        ModelMetadata(
            provider_key="openai",
            id="gpt-4o",
            display_name="GPT-4o",
            context_window=128000,
            source="modelsdev",
        )
    ]


def test_modelsdev_pricing_survives_the_parse():
    """models.dev nests prices under ``cost``. Dropping that block left all 6450
    refreshed models unpriced, so ``cost_estimate_usd`` reported 0 for every panel."""
    payload = {
        "openrouter": {
            "id": "openrouter",
            "npm": "@ai-sdk/openai-compatible",
            "models": {
                "google/gemini-3-flash-preview": {
                    "id": "google/gemini-3-flash-preview",
                    "name": "Gemini 3 Flash Preview",
                    "limit": {"context": 1048576},
                    "cost": {"input": 0.5, "output": 3},
                }
            },
        }
    }
    models = _models_from_provider("openrouter", payload["openrouter"])
    assert len(models) == 1
    assert (models[0].input_cost, models[0].output_cost) == (0.5, 3.0)


def test_cache_memo_is_invalidated_when_the_cache_is_refreshed(tmp_path):
    """The catalog is memoised because re-parsing 1.5MB per lookup blocked the event
    loop. A refresh must still be visible immediately."""
    cache = tmp_path / "catalog.json"
    write_cache([ModelMetadata(provider_key="openrouter", id="m", input_cost=1.0)], cache)
    assert resolve_model_metadata("openrouter", "m", cache_path=cache).input_cost == 1.0

    write_cache([ModelMetadata(provider_key="openrouter", id="m", input_cost=2.0)], cache)
    assert resolve_model_metadata("openrouter", "m", cache_path=cache).input_cost == 2.0
