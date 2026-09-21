"""The generative half of the hybrid judge: turn panel answers into candidate claims.

Jev answers questions; it does not invent them. Something has to read the panel and
propose *what to adjudicate*, and that is a genuinely generative job. So the hybrid
shape spends one cheap LLM call here and then hands every judgement to Jev.

This pass is deliberately dumb. It does not decide what is true, what is agreed, or
who is right — it only lists the propositions worth testing and the gaps worth
checking. Every verdict comes back as a calibrated probability from the next stage,
which is the whole point of the split: the model that is good at reading prose never
gets to grade it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestrator.json_utils import parse_lenient
from orchestrator.models import ChatRequest, RawAnswer, TokenUsage

# Claim count drives question count, which drives nothing much — Jev answers a batch in
# parallel, so 12 claims across 8 providers is still one round trip. The cap is about
# keeping the *extraction* call small and the analysis readable, not about Jev's cost.
MAX_CLAIMS = 12
MAX_BLIND_SPOTS = 6
MAX_MISSING = 10

EXTRACT_SYSTEM = (
    "You read answers from a panel of independent models and list what is worth "
    "checking. You do NOT judge, rank, merge, or answer the question yourself.\n"
    "- claims: the substantive propositions the answers make, each a single "
    "self-contained sentence stating one testable thing. Include propositions only "
    "one answer makes and propositions the answers appear to disagree about — those "
    "are the most valuable. Do not say who said what; that is measured later. Do not "
    "hedge, attribute, or editorialise. At most "
    f"{MAX_CLAIMS}.\n"
    "- blind_spots: things a careful reader would expect an answer to this question "
    "to address that no answer appears to address. At most "
    f"{MAX_BLIND_SPOTS}.\n"
    "- missing_evidence: facts that are not in the question and that would change the "
    "answer if they came back one way rather than another. Ask yourself what you "
    "would have to measure to be sure, and list that. Include anything the answers "
    "say they are assuming or guessing at, and also anything none of them thought to "
    "check. Name the measurement, not the subject: 'what proportion of rows have "
    "status = pending', not 'the database'; 'how many partitions there are relative "
    "to consumers', not 'the Kafka configuration'. Omit anything the question already "
    "states. Propose generously -- each one is scored afterwards and the weak ones are "
    f"dropped, so a plausible candidate costs nothing and a missing one costs the "
    f"answer. At most {MAX_MISSING}.\n"
    'Output STRICT JSON only: {"claims": ["..."], "blind_spots": ["..."], '
    '"missing_evidence": ["..."]}'
)


@dataclass
class Extraction:
    claims: list[str] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    error: str | None = None
    text: str = ""
    provider_id: str = ""
    usage: TokenUsage | None = None

    def is_empty(self) -> bool:
        return not self.claims and not self.blind_spots and not self.missing_evidence


async def extract_claims(
    question: str,
    answers: list[RawAnswer],
    provider,
    *,
    deadline: float,
    max_tokens: int | None = None,
    context: str | None = None,
) -> Extraction:
    """One temperature-0 call proposing claims and blind spots.

    Returns an :class:`Extraction` carrying ``error`` rather than raising, so a failed
    extraction degrades the judge instead of the deliberation.
    """
    if provider is None:
        return Extraction(error="no extraction provider configured")

    from orchestrator.judge.llm import build_judge_user

    result = await provider.complete(
        ChatRequest(
            system=EXTRACT_SYSTEM,
            user=build_judge_user(question, answers, context),
            max_tokens=max_tokens,
            temperature=0,
        ),
        deadline=deadline,
    )
    extraction = Extraction(text=result.text, provider_id=result.provider_id, usage=result.usage)
    if result.status != "ok":
        extraction.error = result.error or "extraction call failed"
        return extraction

    parsed = parse_lenient(result.text)
    if parsed is None:
        extraction.error = "extraction returned malformed JSON"
        return extraction

    extraction.claims = _strings(parsed.get("claims"), MAX_CLAIMS)
    extraction.blind_spots = _strings(parsed.get("blind_spots"), MAX_BLIND_SPOTS)
    extraction.missing_evidence = _strings(parsed.get("missing_evidence"), MAX_MISSING)
    if extraction.is_empty():
        extraction.error = "extraction proposed nothing to adjudicate"
    return extraction


def _strings(value, limit: int) -> list[str]:
    """Tolerant list-of-strings read: drop anything that is not a non-empty string
    rather than failing the whole extraction over one bad element."""
    if not isinstance(value, list):
        return []
    cleaned = [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return cleaned[:limit]
