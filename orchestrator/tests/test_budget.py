from __future__ import annotations

from orchestrator.budget import classify_budget, estimate_provider_budget, estimate_text_tokens
from orchestrator.settings import ProviderDescriptor


def test_estimate_text_tokens_is_advisory_and_nonzero():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("abcde") == 2


def test_budget_thresholds():
    assert classify_budget(10, None)[0] == "unknown"
    assert classify_budget(10, 100)[0] == "green"
    assert classify_budget(80, 100)[0] == "amber"
    assert classify_budget(95, 100)[0] == "red"


def test_provider_budget_uses_context_window_override():
    provider = ProviderDescriptor(
        id="p",
        base_url="https://api.example.test/v1",
        model="m",
        context_window=20,
    )
    budget = estimate_provider_budget(
        provider,
        prompt="x" * 40,
        context=None,
        expected_output_tokens=8,
    )
    assert budget.context_window == 20
    assert budget.state == "red"
