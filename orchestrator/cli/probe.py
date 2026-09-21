"""Connection test + model discovery for the configurator.

Token-safe: a token is sent as a bearer header but never appears in any returned
status, detail string, or log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from orchestrator.model_catalog import ModelMetadata, fetch_live_model_metadata

ProbeStatus = Literal["ok", "auth_error", "unreachable", "model_not_found", "other"]


@dataclass
class ProbeResult:
    status: ProbeStatus
    detail: str = ""


def _auth_headers(token: str | None, headers: dict[str, str] | None) -> dict[str, str]:
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def list_models(
    base_url: str, token: str | None = None, headers: dict[str, str] | None = None
) -> list[str] | None:
    """``GET {base_url}/models`` → list of model ids, or ``None`` on any failure.

    ``None`` signals the configurator to fall back to free-text model entry.
    """
    metadata = list_model_metadata(base_url, token, headers=headers)
    if metadata is None:
        return None
    models = [model.id for model in metadata]
    return models or None


def list_model_metadata(
    base_url: str,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    *,
    provider_key: str = "custom",
) -> list[ModelMetadata] | None:
    return fetch_live_model_metadata(
        base_url,
        provider_key=provider_key,
        token=token,
        headers=headers,
    )


def check_member(
    base_url: str,
    token: str | None,
    model: str,
    headers: dict[str, str] | None = None,
) -> ProbeResult:
    """A minimal completion ping mapped to a coarse status. Never leaks the token."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    try:
        resp = httpx.post(url, json=payload, headers=_auth_headers(token, headers), timeout=30)
    except httpx.TransportError as exc:
        return ProbeResult("unreachable", type(exc).__name__)
    if resp.status_code in (401, 403):
        return ProbeResult("auth_error", f"HTTP {resp.status_code}")
    if resp.status_code == 404:
        return ProbeResult("model_not_found", f"HTTP {resp.status_code}")
    if resp.status_code == 400 and "model" in resp.text.lower():
        return ProbeResult("model_not_found", "HTTP 400 (model)")
    if 200 <= resp.status_code < 300:
        return ProbeResult("ok")
    return ProbeResult("other", f"HTTP {resp.status_code}")
