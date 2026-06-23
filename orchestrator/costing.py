from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from orchestrator.budget import estimate_text_tokens
from orchestrator.models import TokenUsage


@dataclass(frozen=True)
class CostInput:
    """One billable call for advisory cost estimation (a panel or judge call)."""

    provider_id: str
    usage: TokenUsage
    input_text: str = ""
    output_text: str = ""


def estimate_cost(calls: Iterable[CostInput], pricing) -> dict:
    """Advisory USD estimate over panel + judge calls.

    Cost is **always advisory**. Providers with no ``pricing`` entry contribute 0 and
    are counted in ``unpriced_calls`` (so a partial total is never read as
    authoritative). A priced call whose response omitted token ``usage`` falls back to
    a ~chars/4 estimate (flagged ``usage_source: "estimated"``), so a budget-red /
    cost-zero state cannot co-occur for one call. Units: USD; pricing is per 1M tokens.
    """
    total = 0.0
    priced = unpriced = estimated = 0
    by_provider: dict[str, dict] = {}
    for call in calls:
        price = pricing.get(call.provider_id) if pricing else None
        if not price:
            unpriced += 1
            by_provider.setdefault(call.provider_id, {"usd": 0.0, "usage_source": "unpriced"})
            continue
        priced += 1
        in_tok, out_tok = call.usage.input, call.usage.output
        source = "reported"
        if in_tok == 0 and out_tok == 0:
            in_tok = estimate_text_tokens(call.input_text)
            out_tok = estimate_text_tokens(call.output_text)
            source = "estimated"
            estimated += 1
        usd = (in_tok / 1_000_000) * price.input + (out_tok / 1_000_000) * price.output
        total += usd
        entry = by_provider.setdefault(call.provider_id, {"usd": 0.0, "usage_source": source})
        entry["usd"] = round(entry["usd"] + usd, 6)
        entry["usage_source"] = source
    return {
        "usd": round(total, 6),
        "advisory": True,
        "priced_calls": priced,
        "unpriced_calls": unpriced,
        "estimated_calls": estimated,
        "by_provider": by_provider,
    }
