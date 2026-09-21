# Judge Shapes and the Analysis Contract

> This page is the source of truth for what the judge returns. It replaces the
> earlier "Fusion structured analysis and curation" page: the curation pass was
> removed on 2026-06-19, and the generative analyst was demoted to a fallback on
> 2026-09-21 when Jev became the judge.

## Pipeline

```text
panel providers -> judge (one of four shapes) -> host author
```

The judge analyses; it never authors. Whatever shape runs, all successful raw panel
answers are returned unconditionally, so the host can write the final answer even when
the judge produced nothing.

## The shapes

| shape | generative calls | Jev calls | narrative | calibration |
|---|---|---|---|---|
| `hybrid` | 1 (claim extraction) | 1+ | derived from Jev's numbers | `claims[]`, `per_answer[]` |
| `matrix` | 0 | 1+ | none | `agreement[]`, `per_answer[]`, `outlier`, `panel_agreement` |
| `verify` | 1 (the analysis) | 1+ | written by the analyst | `claims[]` (one `holds` per finding) |
| `llm` | 1 (the analysis) | 0 | written by the analyst | `null` |

`calibration` is `null` — not an empty block — when nothing was measured, so "not
measured" and "measured as nothing" stay distinguishable.

### hybrid

1. One temperature-0 call asks an analyst for the propositions worth testing and the
   gaps worth checking. It does **not** judge, rank or attribute; it only lists.
   Capped at 12 claims and 6 blind spots.
2. One Jev call asks, for each claim: *does the answer from `<id>` support this?*
   (once per panel member), *do the answers genuinely disagree about this?*, and a
   three-level rubric for how well the panel as a whole supports it. Each proposed
   blind spot gets *is this genuinely left unaddressed by every answer?* The same call
   also reads each answer for hedging, scope and distinctiveness, so the author can
   tell a claim backed by three committed, complete answers from one backed by three
   evasive, partial ones. Support scores alone cannot distinguish them, and a measured
   run showed a panel of three wrong models talking a correct author out of its answer.
3. The narrative is **derived** from those probabilities:

| outcome | condition |
|---|---|
| `contradictions[]` | contested ≥ 0.5, **and** at least one member above 0.4 and one at or below it |
| `consensus[]` | every panel member scored ≥ 0.6 |
| `unique_insights[]` | exactly one member scored ≥ 0.6 |
| `partial_coverage[]` | some but not all members scored ≥ 0.6 |
| *(dropped from the narrative)* | no member scored ≥ 0.6 — kept as `role: "unsupported"` |
| `blind_spots[]` | unaddressed ≥ 0.5 |
| `needs_evidence[]` | lacked ≥ 0.5, sorted by how much having it would change the answer |

Checked in that order, so a contested claim is settled before the backing tests run.

The gap between the 0.6 backing threshold and the 0.4 dissent threshold is
deliberate: an answer that simply did not address a claim lands in the middle and must
not be read as either agreement or dissent. That is what `partial_coverage` carries.

A contested claim is split at the **lower** threshold — see
[A threshold that had to move](#a-threshold-that-had-to-move).

An `unsupported` claim measures the *extraction* pass, not the panel — the analyst
proposed something no answer turned out to back. `confidence_notes` reports the count.

### needs_evidence

The extraction pass also lists information the answers did not have and would have
needed — a file, a schema, a version, a measurement — taken from what they say they are
assuming, guessing at or asking for. Jev then settles two questions per item: *did the
answers lack this, rather than simply not mention it?* and *would having it change the
answer?*

This is deliberately **not** `blind_spots`. A blind spot is something nobody addressed;
this is something nobody *could* address, because it was not in front of them. Only the
second is actionable, and only by the caller — the harness is the one party that can
read the file or run the query. So it is reported as a field and never as a failure:
the first pass still returns a real analysis, and whether to fetch and call again is the
calling model's decision, capped by the `depth` guard when it chains.

`would_change` is what decides whether a second pass is worth it, so the list is sorted
by it.

### matrix

One Jev call, no generative model. For an eight-member panel: 28 pairwise agreement
questions, three readings per answer (hedging, scope, distinctiveness), plus an
outlier choice and an overall agreement rubric — 54 questions in one round trip.

Produces no `consensus`/`contradictions`; the narrative fields stay empty and the
reading lives entirely in `calibration`.

### verify

The generative analyst runs unchanged, then one Jev call puts each of its findings as
a yes/no: is this consensus item actually supported by every answer? is this
contradiction real? is this insight really unique to that member? Nothing is rewritten
or deleted — each finding keeps its index and gains `holds`, and the tally is appended
to `confidence_notes`.

## Measured

Against a fixed four-answer panel on a contested technical question, three repeats,
`jev-latest` via OpenRouter (2026-09-21). The fixture is built so each branch of the
hybrid derivation has exactly one case and the correct finding is known in advance;
extraction and analyst output are held constant so the only variable is Jev.

| shape | correct findings | wall (median) | Jev calls | questions | USD per judgement |
|---|---|---|---|---|---|
| `hybrid` | 9/9 | 419 ms | 1 | 44 | $0.00018 |
| `matrix` | 4/4 | 405 ms | 1 | 20 | $0.00008 |
| `verify` | 6/6 | 432 ms | 1 | 6 | $0.00005 |

Two things that decide the default:

**Wall clock is flat in question count.** 6 questions and 44 questions both land near
420ms, because a call is answered in parallel and costs its round trip. There is no
reason for a shape to ask less than it wants to know.

**The probabilities separate cleanly.** On the claim only one member made, that member
scored 0.98 and the rest 0.04. On the claim three of four made, the three scored
0.97–0.99 and the fourth 0.04. A member that implied a claim without stating it scored
0.76 against 0.98 for the two that stated it outright — the graded floor a threshold
alone would have hidden.

`hybrid` is the default: it is the only shape that produces the full narrative *and*
its evidence, for one analyst call plus $0.0002. `matrix` is for when no chat provider
should be in the loop at all. `verify` is the cheapest but can only grade what the
analyst chose to write, so it inherits the analyst's omissions.

### A threshold that had to move

The first live run exposed a defect in this derivation rather than in Jev. The lone
member backing the contested option scored **0.59–0.60** across repeats while the
other three sat at 0.02. Splitting a contested claim at `SUPPORT_HIGH` (0.6) therefore
produced a contradiction twice and `unsupported` once — on a third of runs the panel's
sharpest disagreement, which Jev had flagged at `contested = 0.85`, vanished from the
analysis entirely.

A contested claim is now split at `SUPPORT_LOW` instead: when Jev says the panel
disagrees, the question is who is on which side, and the right cut is "clearly rejects"
versus "does not clearly reject" — not the stricter bar used to *assert* a consensus.
Uncontested claims still need real backing. Both cases are pinned by tests.

## The response

```json
{
  "consensus": ["string"],
  "contradictions": [
    { "topic": "string", "positions": [{ "ids": ["provider-id"], "claim": "string" }] }
  ],
  "partial_coverage": [{ "ids": ["provider-id"], "point": "string" }],
  "unique_insights": [{ "id": "provider-id", "insight": "string" }],
  "blind_spots": ["string"],
  "needs_evidence": [
    { "item": "string", "lacked": 0.95, "would_change": 0.9, "confidence": 0.9 }
  ],
  "confidence_notes": "string",
  "calibration": {
    "shape": "hybrid",
    "model": "jev-latest",
    "claims": [
      {
        "role": "consensus",
        "index": 0,
        "claim": "string",
        "support": [{ "id": "provider-id", "support": 0.91 }],
        "contested": 0.05,
        "holds": null,
        "standing": 1.8,
        "confidence": 0.77
      }
    ],
    "agreement": [{ "ids": ["a", "b"], "agreement": 0.9, "confidence": 0.8 }],
    "per_answer": [{ "id": "a", "hedging": 0.4, "scope": 1.8, "distinctive": 0.2 }],
    "outlier": { "id": "c", "confidence": 0.7, "probabilities": { "c": 0.7 } },
    "panel_agreement": 1.2,
    "questions_asked": 27,
    "calls": 1
  }
}
```

`confidence` on a claim is **derived**, not reported: Jev returns a `confidence` for
`choice` and `score` answers but not for `noul`, and every claim-level judgement is a
noul. A probability is its own confidence, so the distance from maximal uncertainty is
mapped onto [0, 1] — 0.5 becomes 0.0, either extreme becomes 1.0. A reported
confidence, where one exists, still wins.

`role` and `index` point back into the narrative field a claim produced or verified
(`consensus[0]`, `contradictions[2]`, …), so an author joins prose to evidence without
matching on claim text.

`holds` and `standing` are deliberately separate fields: `holds` is a probability in
[0, 1] that a finding is borne out; `standing` is an expectation over a three-level
rubric. Collapsing them would silently mix scales.

The tool response around it:

- `question`, `panel[]`, `raw_answers[]`, `meta`, `text`, `thread_id`, `compacted`
  are unchanged from the panel contract
- `analysis` is the object above, or `null` when no shape could produce one
- `meta.judge_shape` is the shape that **actually ran**, `meta.judge_fallback_from`
  the one that was requested if it differs, and `meta.judge_error` why
- `meta.jev_calls` / `meta.jev_questions` count the decision calls;
  `meta.cost_basis.jev_usd` is Jev's own reported charge, or `null` when the provider
  did not price it

## Degradation

Each shape has an ordered fallback chain, tried until one produces an analysis:

| shape | chain |
|---|---|
| `hybrid` | `matrix`, then `llm` |
| `matrix` | `llm` |
| `verify` | `llm` (its analysis survives ungraded) |

`hybrid` falls to `matrix` first on purpose. Most of what stops it — an analyst that is
unreachable, or that proposed nothing because the panel agreed — says nothing about
whether Jev is reachable, and dropping straight to a generative judge would throw away
the calibration for no reason. Only when Jev itself is gone does anything reach `llm`.

| Situation | Response |
|---|---|
| Jev errors, times out or is unkeyed | fall back through the chain to `llm`; `judge_fallback_from` set |
| Jev fails and no analyst is configured | `analysis: null`, `meta.judge_error` |
| A batch partially fails | the analysis stands on the answers that came back; a missing answer reads as maximal uncertainty; `meta.judge_error` says so |
| `hybrid` extraction returns nothing usable | fall back to `matrix`, which needs no analyst |
| `verify` verification fails | the analyst's analysis survives with `calibration: null`; shape reports as `llm` |
| The `llm` judge returns malformed JSON | `meta.judge_error`, `analysis: null` |

Raw panel answers return in every one of these cases.
