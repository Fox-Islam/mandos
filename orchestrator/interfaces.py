"""Small Protocol seams so panel/judge stay testable with deterministic fakes.

Keep provider, orchestrator, and judge dependencies behind Protocols (DESIGN §8).
"""

from __future__ import annotations

from typing import Protocol


class ChatProvider(Protocol):
    """A single non-streaming chat completion against one configured provider.

    Implementations must honour the deadline and record a structured error on
    transport/HTTP failure rather than raising into the batch (DESIGN §8.3).
    """

    id: str

    async def complete(self, request, *, deadline: float): ...
