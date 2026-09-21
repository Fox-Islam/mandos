"""App attribution headers for OpenRouter.

OpenRouter groups spend and rankings by the app that made the call, reading
``X-Title`` and ``HTTP-Referer``. Send neither and every Mandos deliberation shows up
on the user's activity page as "Unknown", which is worse than cosmetic: a council call
fans out to several models at once, so an unattributed panel looks like unexplained
spend from nowhere.

Only OpenRouter endpoints get these headers. They are an OpenRouter convention, and
sending a referer to every configured endpoint would leak which tool is calling to
hosts that never asked.

Overridable two ways, neither of which needs a config-schema change: set
``MANDOS_APP_TITLE`` / ``MANDOS_APP_URL`` in the environment, or put ``X-Title`` in a
provider's own ``headers``, which wins over both. Setting either variable to an empty
string suppresses that header entirely.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_APP_TITLE = "Mandos"
DEFAULT_APP_URL = "https://github.com/Fox-Islam/mandos"

_OPENROUTER_HOST = "openrouter.ai"


def is_openrouter(base_url: str, kind: str | None = None) -> bool:
    """True for an OpenRouter endpoint.

    ``kind`` is the configured provider kind, which is authoritative - someone
    fronting OpenRouter with their own gateway still wants their calls attributed.
    The host check catches the opposite mistake: ``kind: openai`` pointed straight at
    ``openrouter.ai``.
    """
    if kind == "openrouter":
        return True
    host = (urlparse(base_url).hostname or "").lower()
    return host == _OPENROUTER_HOST or host.endswith("." + _OPENROUTER_HOST)


def _configured(env_var: str, default: str) -> str:
    """The environment wins, and an empty value means "send nothing"."""
    value = os.environ.get(env_var)
    return default if value is None else value


def attribution_headers(base_url: str, kind: str | None = None) -> dict[str, str]:
    """Headers identifying Mandos to OpenRouter, or ``{}`` for any other endpoint."""
    if not is_openrouter(base_url, kind):
        return {}
    headers = {}
    title = _configured("MANDOS_APP_TITLE", DEFAULT_APP_TITLE)
    url = _configured("MANDOS_APP_URL", DEFAULT_APP_URL)
    if title:
        headers["X-Title"] = title
    if url:
        headers["HTTP-Referer"] = url
    return headers
