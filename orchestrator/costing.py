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
    # Per-million-token prices from the model catalog, used when the config names no
    # price for this provider. Configured pricing is user truth and still wins.
    catalog_price: tuple[float, float] | None = None


def _prices(configured, catalog) -> tuple[float | None, float | None, str]:
    """Configured pricing is user truth and wins; the catalog fills the gaps."""
    if configured:
        return configured.input, configured.output, "config"
    if catalog:
        return catalog[0], catalog[1], "catalog"
    return None, None, "none"


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
        configured = pricing.get(call.provider_id) if pricing else None
        price_in, price_out, price_source = _prices(configured, call.catalog_price)
        if price_in is None:
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
        usd = (in_tok / 1_000_000) * price_in + (out_tok / 1_000_000) * (price_out or 0.0)
        total += usd
        entry = by_provider.setdefault(call.provider_id, {"usd": 0.0, "usage_source": source})
        entry["usd"] = round(entry["usd"] + usd, 6)
        entry["usage_source"] = source
        entry["price_source"] = price_source
    return {
        "usd": round(total, 6),
        "advisory": True,
        "priced_calls": priced,
        "unpriced_calls": unpriced,
        "estimated_calls": estimated,
        "by_provider": by_provider,
    }
