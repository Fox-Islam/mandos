"""One HTTP client for the whole process.

Every deliberation used to build its own ``httpx.AsyncClient``, which meant its own
connection pool, which meant a fresh TCP and TLS handshake to every provider on every
call. Measured against Jev, that handshake costs roughly 350ms where the model itself
costs 90 — so a server answering several tool calls was spending most of its time
saying hello.

Sharing one pooled client across deliberations fixes that, and matters more the more
concurrent calls arrive: a harness may have several councils in flight, and with a
client per call they compete for sockets instead of reusing them.

The client is created lazily on the running loop and closed at shutdown. It carries no
default timeout on purpose — every caller passes its own, derived from the
deliberation's single absolute deadline.
"""

from __future__ import annotations

import asyncio

import httpx

# Generous enough that eight panel members and a judge never queue behind each other,
# bounded so a runaway caller cannot exhaust the host's file descriptors.
LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=32)

_client: httpx.AsyncClient | None = None
_loop: asyncio.AbstractEventLoop | None = None


def shared_client() -> httpx.AsyncClient:
    """The process-wide client, created on first use.

    Rebuilt if the event loop has changed under it — a pool bound to a closed loop is
    worse than no pool, and tests routinely run each case on a fresh loop.
    """
    global _client, _loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _loop is not loop:
        _client = httpx.AsyncClient(limits=LIMITS, timeout=None)
        _loop = loop
    return _client


async def aclose_shared_client() -> None:
    """Close the shared client, if there is one. Safe to call when there is not."""
    global _client, _loop
    client, _client, _loop = _client, None, None
    if client is not None and not client.is_closed:
        await client.aclose()
