"""One HTTP client for the whole process.

A client per deliberation means a connection pool per deliberation, and so a fresh TCP
and TLS handshake to every provider on every call. Measured against Jev, that handshake
costs roughly 350ms against 90ms of model time. Sharing one pooled client matters more
the more concurrent calls arrive: a harness may have several councils in flight, and
per-call clients compete for sockets instead of reusing them.

The client is created lazily on the running loop and closed at shutdown. It carries no
default timeout; every caller passes its own, derived from the deliberation's single
absolute deadline.
"""

from __future__ import annotations

import asyncio

import httpx

# Generous enough that eight panel members and a judge never queue behind each other,
# bounded so a runaway caller cannot exhaust the host's file descriptors.
LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=32)

# Longest a provider may tell us to wait before we ignore it and use our own backoff.
# A provider asking for five minutes is not offering a retry, it is asking us to give
# up, and the deliberation's deadline would kill the attempt long before then anyway.
MAX_RETRY_AFTER_S = 30.0

_client: httpx.AsyncClient | None = None
_loop: asyncio.AbstractEventLoop | None = None


def shared_client() -> httpx.AsyncClient:
    """The process-wide client, created on first use.

    Rebuilt if the event loop has changed under it - a pool bound to a closed loop is
    worse than no pool, and tests routinely run each case on a fresh loop.
    """
    global _client, _loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _loop is not loop:
        _client = httpx.AsyncClient(limits=LIMITS, timeout=None)
        _loop = loop
    return _client


def parse_retry_after(headers) -> float | None:
    """Seconds a provider asked us to wait, if it asked and the ask is reasonable.

    Honoured for every provider, not just the one that happened to rate-limit us
    during testing: a 429 or 503 from a chat endpoint means the same thing it means
    from Jev. ``retry-after-ms`` is read too, because some providers send that instead.

    HTTP-date values are ignored, not parsed - they are rare in practice, and
    guessing wrong is worse than falling back to exponential backoff.
    """
    milliseconds = headers.get("retry-after-ms")
    if milliseconds is not None:
        try:
            return min(max(float(milliseconds) / 1000, 0.0), MAX_RETRY_AFTER_S)
        except (TypeError, ValueError):
            pass
    seconds = headers.get("retry-after")
    if seconds is None:
        return None
    try:
        return min(max(float(seconds), 0.0), MAX_RETRY_AFTER_S)
    except (TypeError, ValueError):
        return None


async def aclose_shared_client() -> None:
    """Close the shared client, if there is one. Safe to call when there is not."""
    global _client, _loop
    client, _client, _loop = _client, None, None
    if client is not None and not client.is_closed:
        await client.aclose()
