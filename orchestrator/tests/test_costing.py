"""Advisory cost estimation: exact values, coverage, char-estimate fallback."""

from __future__ import annotations

from orchestrator.costing import CostInput, estimate_cost
from orchestrator.models import TokenUsage
from orchestrator.settings import Price


def _price(inp: float, out: float) -> Price:
    return Price.model_validate({"in": inp, "out": out})


def test_fully_priced_exact_value():
    calls = [CostInput("p", TokenUsage(input=500_000, output=1_000_000))]
    basis = estimate_cost(calls, {"p": _price(2.0, 3.0)})
    assert basis["usd"] == 4.0
    assert basis["priced_calls"] == 1
    assert basis["unpriced_calls"] == 0
    assert basis["advisory"] is True
    assert basis["by_provider"]["p"]["usage_source"] == "reported"


def test_partially_priced_counts_unpriced():
    calls = [
        CostInput("p", TokenUsage(input=1_000_000, output=0)),
        CostInput("q", TokenUsage(input=1_000_000, output=0)),
    ]
    basis = estimate_cost(calls, {"p": _price(2.0, 0.0)})
    assert basis["usd"] == 2.0
    assert basis["priced_calls"] == 1
    assert basis["unpriced_calls"] == 1
    assert basis["by_provider"]["q"]["usage_source"] == "unpriced"


def test_zero_priced_is_zero_but_counted():
    calls = [CostInput("p", TokenUsage(input=10, output=10)), CostInput("q", TokenUsage())]
    basis = estimate_cost(calls, {})
    assert basis["usd"] == 0.0
    assert basis["priced_calls"] == 0
    assert basis["unpriced_calls"] == 2


def test_missing_usage_falls_back_to_char_estimate():
    calls = [CostInput("p", TokenUsage(input=0, output=0), input_text="x" * 8, output_text="y" * 4)]
    basis = estimate_cost(calls, {"p": _price(1.0, 1.0)})
    assert basis["estimated_calls"] == 1
    assert basis["by_provider"]["p"]["usage_source"] == "estimated"
    assert basis["usd"] == round((2 / 1_000_000) * 1.0 + (1 / 1_000_000) * 1.0, 6)


def test_catalog_pricing_fills_in_when_the_config_names_no_price():
    """A user who never wrote a `pricing:` block still gets a real number."""
    from orchestrator.costing import CostInput, estimate_cost
    from orchestrator.models import TokenUsage

    basis = estimate_cost(
        [CostInput("a", TokenUsage(input=1_000_000, output=1_000_000), catalog_price=(2.0, 6.0))],
        {},
    )
    assert basis["usd"] == 8.0
    assert basis["unpriced_calls"] == 0
    assert basis["by_provider"]["a"]["price_source"] == "catalog"


def test_configured_pricing_is_user_truth_and_beats_the_catalog():
    from orchestrator.costing import CostInput, estimate_cost
    from orchestrator.models import TokenUsage
    from orchestrator.settings import Price

    basis = estimate_cost(
        [CostInput("a", TokenUsage(input=1_000_000, output=0), catalog_price=(99.0, 99.0))],
        {"a": Price(**{"in": 1.0, "out": 1.0})},
    )
    assert basis["usd"] == 1.0
    assert basis["by_provider"]["a"]["price_source"] == "config"


def test_a_provider_neither_source_prices_stays_unpriced():
    from orchestrator.costing import CostInput, estimate_cost
    from orchestrator.models import TokenUsage

    basis = estimate_cost([CostInput("a", TokenUsage(input=10, output=10))], {})
    assert basis["unpriced_calls"] == 1
    assert basis["by_provider"]["a"]["usage_source"] == "unpriced"
