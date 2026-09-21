"""Jev's three question primitives and the readers for their answers.

Jev (TypeSafe's System One model) does not write prose: you hand it a *state* and a
map of named *questions*, and it answers each one in the shape that question asked
for. That is the whole reason it makes a better deliberation judge than a generative
model — there is no JSON to parse out of a fenced code block, and every answer
carries a calibrated probability rather than an adjective.

The wire format mirrors ``phox/typesafe-sdk-php``: each question is
``{"type": ..., "instructions": ..., "criteria": ...}`` keyed by the name its answer
comes back under. ``criteria`` is the option map for a choice, the ordered rubric for
a score, and the optional yes/no guidance for a noul.

Every reader tolerates a malformed or missing answer and falls back rather than
raising: one odd reply must not fail a deliberation (mirrors the panel's
partial-results rule).
"""

from __future__ import annotations

from typing import Any

# A question name must survive a round trip as a JSON object key and be matched back
# to the claim/provider it was generated for, so the builders below keep callers from
# inventing their own separators. See ``qname``.
_NAME_SEP = "__"


def qname(*parts: str | int) -> str:
    """Build a question name from its parts.

    Judge shapes generate one question per (claim, provider) or (provider, provider)
    pair and must map the answer back afterwards. Centralising the separator means
    :func:`qparts` is always its exact inverse, so a provider id containing an
    underscore cannot silently split a name in two.
    """
    return _NAME_SEP.join(str(p).replace(_NAME_SEP, "_") for p in parts)


def qparts(name: str) -> list[str]:
    """Inverse of :func:`qname`."""
    return name.split(_NAME_SEP)


def noul(instructions: str, *, yes: str | None = None, no: str | None = None) -> dict[str, Any]:
    """A yes/no question. The answer is ``noul``: the probability of yes.

    ``yes``/``no`` describe what each outcome means. They are optional but sharply
    improve calibration on anything a model could read two ways, which is most of what
    a judge asks.

    On the wire the two outcomes are keyed ``"true"``/``"false"``; the endpoint rejects
    any other keys. The Python arguments stay ``yes``/``no`` because that is how the
    question reads at the call site.
    """
    question: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if yes is not None or no is not None:
        question["criteria"] = {"true": yes or "", "false": no or ""}
    return question


def choice(instructions: str, options: dict[str, str] | list[str]) -> dict[str, Any]:
    """A pick-one question. The answer is a ``choice`` label plus ``probabilities``.

    ``options`` may be a list of self-explanatory labels or a label -> description map;
    either way it goes out as a map, because the endpoint rejects an array here (unlike
    a score rubric, which must be one).
    """
    criteria = {label: "" for label in options} if isinstance(options, list) else dict(options)
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, levels: list[str]) -> dict[str, Any]:
    """A level on an ordered rubric, scored from zero in the order given. Unlike a
    choice, this one goes out as an array.

    The answer is an *expectation*, so it falls between levels: a 0.4 on a
    ``[low, medium, high]`` rubric means the model leans low but is not certain. That
    is the point — a judge that reports 1.4 tells the author more than one that picks
    "medium" and hides the doubt.
    """
    if len(levels) < 2:
        raise ValueError("a score rubric needs at least two levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def read_noul(answer: Any, fallback: float = 0.5) -> float:
    """Probability of yes, or ``fallback`` (maximal uncertainty) if unreadable."""
    if isinstance(answer, dict) and isinstance(answer.get("noul"), int | float):
        return _clamp(float(answer["noul"]))
    return fallback


def read_choice(answer: Any, options: list[str], fallback: str | None = None) -> str | None:
    """The selected label, matched leniently against ``options``.

    Jev occasionally returns a label phrased slightly differently from the one it was
    offered ("Position 4" for "4"). Exact match first, then case-insensitive, then a
    bare integer inside the text, then ``fallback``.
    """
    raw = answer.get("choice") if isinstance(answer, dict) else None
    if not isinstance(raw, str):
        return fallback
    if raw in options:
        return raw
    lowered = raw.strip().lower()
    loose = next((o for o in options if o.lower() == lowered), None)
    if loose is not None:
        return loose
    digits = "".join(c for c in raw if c.isdigit() or c == "-")
    return digits if digits and digits in options else fallback


def read_score(answer: Any, fallback: float = 0.0) -> float:
    """The rubric expectation, or ``fallback`` if unreadable."""
    if isinstance(answer, dict) and isinstance(answer.get("score"), int | float):
        return float(answer["score"])
    return fallback


def read_optional_score(answer: Any) -> float | None:
    """The rubric expectation, or ``None`` when there is no readable one.

    Distinct from :func:`read_score` because a missing rubric answer must not become
    ``0.0``: on a ``[committed, qualified, evasive]`` rubric that reads as "states its
    position plainly", which is a claim nobody made.
    """
    if isinstance(answer, dict) and isinstance(answer.get("score"), int | float):
        return float(answer["score"])
    return None


def noul_confidence(answer: Any) -> float | None:
    """How decisive a yes/no answer was, in [0, 1].

    Jev reports a ``confidence`` for ``choice`` and ``score`` answers but **not** for
    ``noul`` — verified against the live API. Every claim-level judgement is a noul, so
    reading ``confidence`` straight off one always yields ``None`` and the field is
    dead where it matters most.

    A probability is its own confidence, though: 0.97 and 0.03 are both decisive
    answers and 0.52 is not. This maps the distance from maximal uncertainty onto
    [0, 1], so 0.5 -> 0.0 and either extreme -> 1.0. A reported confidence, if one ever
    appears, still wins.
    """
    reported = read_confidence(answer)
    if reported is not None:
        return reported
    if not isinstance(answer, dict) or not isinstance(answer.get("noul"), int | float):
        return None
    return abs(_clamp(float(answer["noul"])) - 0.5) * 2


def read_confidence(answer: Any) -> float | None:
    """The model's confidence in its own answer, or ``None`` when it reported none.

    ``None`` and ``0.0`` are different answers and must not be collapsed.
    """
    if isinstance(answer, dict) and isinstance(answer.get("confidence"), int | float):
        return _clamp(float(answer["confidence"]))
    return None


def read_probabilities(answer: Any) -> dict[str, float]:
    """The per-outcome distribution, or ``{}`` when the answer carried none."""
    probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
    if not isinstance(probabilities, dict):
        return {}
    return {
        str(k): _clamp(float(v)) for k, v in probabilities.items() if isinstance(v, int | float)
    }


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
