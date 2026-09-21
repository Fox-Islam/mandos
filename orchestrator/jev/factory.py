"""Build the Jev client from the judge config.

Duck-typed on purpose: ``settings`` already imports this package for the provider
table and default model, so importing ``JudgeConfig`` back would close the loop.
"""

from __future__ import annotations

import httpx

from orchestrator.jev.client import JevClient


def build_jev_client(judge, http_client: httpx.AsyncClient) -> JevClient | None:
    """A configured client, or ``None`` when the selected shape needs no Jev at all.

    Shares the pipeline's pooled ``httpx.AsyncClient`` so the judge call reuses the
    connection rather than paying for a fresh TLS handshake — worth more than everything
    else here put together, per the jevsort latency measurements.
    """
    if not judge.uses_jev:
        return None
    return JevClient(
        provider=judge.provider,
        api_key=judge.api_key,
        base_url=judge.resolved_base_url,
        model=judge.model,
        http_client=http_client,
        timeout_s=judge.timeout_s,
        max_retries=judge.max_retries,
        headers=judge.headers,
    )
