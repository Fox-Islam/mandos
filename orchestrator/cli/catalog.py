"""Data-only catalog of OpenAI-compatible provider endpoints.

Names no default council - these are friendly presets for the *base_url* and the
conventional env-var name, nothing more. ``custom`` lets the user type any
OpenAI-compatible endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    display_name: str
    base_url: str
    default_api_key_env: str | None
    supports_model_list: bool
    docs_url: str
    kind: str = "openai"
    featured: bool = True
    default_context_window: int | None = None


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        True,
        "https://openrouter.ai/docs",
        kind="openrouter",
    ),
    CatalogEntry(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        True,
        "https://platform.openai.com/docs",
    ),
    CatalogEntry(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY",
        True,
        "https://api-docs.deepseek.com",
    ),
    CatalogEntry(
        "minimax",
        "MiniMax",
        "https://api.minimax.io/v1",
        "MINIMAX_API_KEY",
        True,
        "https://www.minimax.io/platform",
    ),
    CatalogEntry(
        "groq",
        "Groq",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        True,
        "https://console.groq.com/docs",
    ),
    CatalogEntry(
        "together",
        "Together AI",
        "https://api.together.xyz/v1",
        "TOGETHER_API_KEY",
        True,
        "https://docs.together.ai",
    ),
    CatalogEntry(
        "fireworks",
        "Fireworks AI",
        "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY",
        True,
        "https://docs.fireworks.ai",
    ),
    CatalogEntry(
        "mistral",
        "Mistral",
        "https://api.mistral.ai/v1",
        "MISTRAL_API_KEY",
        True,
        "https://docs.mistral.ai",
    ),
    CatalogEntry(
        "vllm",
        "Local vLLM",
        "http://localhost:8000/v1",
        None,
        True,
        "https://docs.vllm.ai",
    ),
    CatalogEntry(
        "ollama",
        "Ollama (local)",
        "http://localhost:11434/v1",
        None,
        True,
        "https://ollama.com",
    ),
    CatalogEntry(
        "lmstudio",
        "LM Studio (local)",
        "http://localhost:1234/v1",
        None,
        True,
        "https://lmstudio.ai/docs",
    ),
    CatalogEntry(
        "opencode",
        "OpenCode Zen",
        "https://opencode.ai/zen/v1",
        "OPENCODE_API_KEY",
        True,
        "https://opencode.ai/docs/zen",
    ),
    CatalogEntry(
        "opencode-go",
        "OpenCode Go (subscription)",
        "https://opencode.ai/zen/go/v1",
        "OPENCODE_API_KEY",
        True,
        "https://opencode.ai/docs/go",
    ),
    CatalogEntry(
        "custom",
        "Custom (OpenAI-compatible)",
        "",
        None,
        True,
        "",
        featured=False,
    ),
)

_BY_KEY = {entry.key: entry for entry in CATALOG}


def get_entry(key: str) -> CatalogEntry | None:
    return _BY_KEY.get(key)


def catalog_options(query: str | None = None) -> list[tuple[str, str]]:
    normalized = " ".join((query or "").lower().split())

    def matches(entry: CatalogEntry) -> bool:
        if not normalized:
            return True
        haystack = " ".join(
            [
                entry.key,
                entry.display_name,
                entry.base_url,
                entry.default_api_key_env or "",
                entry.kind,
            ]
        ).lower()
        return normalized in haystack

    featured = [entry for entry in CATALOG if entry.featured]
    others = [entry for entry in CATALOG if not entry.featured]
    ordered = [entry for entry in [*featured, *others] if matches(entry)]
    custom = _BY_KEY["custom"]
    if custom not in ordered:
        ordered.append(custom)
    return [
        (f"{'Featured - ' if entry.featured else ''}{entry.display_name}", entry.key)
        for entry in ordered
    ]
