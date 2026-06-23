from __future__ import annotations

from orchestrator.providers.openai_compatible import OpenAiCompatibleProvider


def build_provider(descriptor, http_client):
    """Build the chat provider for a descriptor.

    Every provider is OpenAI-compatible (POST {base_url}/chat/completions); the
    ``openrouter`` kind is a documented alias of ``openai`` (same path, optional
    referer headers). There is no other kind.
    """
    return OpenAiCompatibleProvider(descriptor, http_client)
