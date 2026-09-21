"""What every judge shape returns, whatever it is made of.

One dataclass for all five shapes so ``panel.py`` never branches on which judge ran:
it reads ``analysis``, records ``analysis_error`` if set, and bills the calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestrator.models import Analysis, JudgeShape, TokenUsage


@dataclass
class JudgeOutcome:
    """A judge run.

    ``analysis`` is ``None`` whenever the judge could not produce one; the pipeline
    still returns every raw panel answer, so the host can always author.

    ``usages`` bills the *generative* calls (extraction, narrative) against the
    configured pricing table. Jev's spend is tracked separately in ``jev_cost``,
    because only OpenRouter prices a decision call and a token count would not convert.
    """

    analysis: Analysis | None = None
    analysis_error: str | None = None
    usages: list[tuple[str, TokenUsage]] = field(default_factory=list)
    analysis_text: str = ""
    shape: JudgeShape = "llm"
    fallback_from: JudgeShape | None = None
    jev_calls: int = 0
    jev_questions: int = 0
    jev_cost: float | None = None
    jev_usage: TokenUsage = field(default_factory=TokenUsage)

    def stamp_calibration(self) -> None:
        """Record on the calibration block how much Jev work produced it.

        The shapes assemble their ``Calibration`` without the outcome in scope, so the
        two counters it declares are filled in here, once, for whichever shape won.
        """
        calibration = getattr(self.analysis, "calibration", None)
        if calibration is not None:
            calibration.questions_asked = self.jev_questions
            calibration.calls = self.jev_calls

    def absorb_jev(self, result) -> None:
        """Roll one :class:`~orchestrator.jev.JevResult` into the running totals."""
        self.jev_calls += 1
        self.jev_questions += result.questions_asked
        self.jev_usage = TokenUsage(
            input=self.jev_usage.input + result.usage.input,
            output=self.jev_usage.output + result.usage.output,
        )
        if result.cost is not None:
            self.jev_cost = (self.jev_cost or 0.0) + result.cost
