"""Advisory context-budget calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from orchestrator.model_catalog import resolve_model_metadata

BudgetState = Literal["unknown", "green", "amber", "red"]


@dataclass(frozen=True)
class BudgetEstimate:
    provider_id: str
    model: str
    context_window: int | None
    estimated_tokens: int
    state: BudgetState
    ratio: float | None

    def as_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "context_window": self.context_window,
            "estimated_tokens": self.estimated_tokens,
            "state": self.state,
            "ratio": self.ratio,
        }


def estimate_text_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_request_tokens(
    prompt: str,
    context: str | None,
    expected_output_tokens: int = 0,
) -> int:
    return estimate_text_tokens(prompt) + estimate_text_tokens(context) + expected_output_tokens


def resolve_provider_context_window(provider) -> int | None:
    for attr in ("context_window", "resolved_context_window"):
        value = getattr(provider, attr, None)
        if value:
            return int(value)
    provider_key = getattr(provider, "catalog_key", None) or getattr(provider, "kind", None)
    model = getattr(provider, "model", None)
    if not provider_key or not model:
        return None
    metadata = resolve_model_metadata(str(provider_key), str(model))
    return metadata.context_window if metadata else None


def classify_budget(
    estimated_tokens: int,
    context_window: int | None,
    *,
    warning_ratio: float = 0.75,
    error_ratio: float = 0.9,
) -> tuple[BudgetState, float | None]:
    if not context_window:
        return "unknown", None
    ratio = estimated_tokens / context_window
    if ratio >= error_ratio:
        return "red", ratio
    if ratio >= warning_ratio:
        return "amber", ratio
    return "green", ratio


def estimate_provider_budget(
    provider,
    *,
    prompt: str,
    context: str | None,
    context_window: int | None = None,
    expected_output_tokens: int = 0,
    warning_ratio: float = 0.75,
    error_ratio: float = 0.9,
) -> BudgetEstimate:
    window = context_window or resolve_provider_context_window(provider)
    estimated = estimate_request_tokens(prompt, context, expected_output_tokens)
    state, ratio = classify_budget(
        estimated,
        window,
        warning_ratio=warning_ratio,
        error_ratio=error_ratio,
    )
    return BudgetEstimate(
        provider_id=provider.id,
        model=provider.model,
        context_window=window,
        estimated_tokens=estimated,
        state=state,
        ratio=ratio,
    )
