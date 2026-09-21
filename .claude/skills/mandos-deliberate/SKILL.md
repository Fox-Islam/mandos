---
name: mandos-deliberate
description: Convene a panel of independent AI models to deliberate on a hard question via the Mandos MCP server (stdio). A calibrated decision model (Jev) then judges what the panel actually established, returning consensus, contradictions, partial coverage, unique insights and blind spots — each with the probabilities it was derived from — plus all raw panel answers. Use for research, expert critique, "compare and contrast", architecture/design trade-offs, or any task where being wrong is costly. You remain the final author.
---

# Mandos Deliberation

## When to use

Reach for Mandos when a task is hard enough that one model's answer is risky:
research questions, design/architecture trade-offs, "compare X and Y", expert
critique, or anything where being wrong is costly. For quick lookups, answer
directly — deliberation costs N panel calls plus a judge pass.

Panellists answer from what they know unless a provider is configured with
provider-side web search, and none of them can reach your machine. See *Gather before
you convene*.

## Gather before you convene

The panel has no access to your machine. It cannot read files, run commands or query a
database, and it never will — the harness holds that access behind a permission model
that asks before it reads or runs anything, and an MCP server executing tools on its own
to feed a third-party panel would route around it.

So when a question depends on the user's code, data or environment, **you** gather it
first and pass it as `context`:

1. Read the files, run the query, check the versions — whatever the question turns on.
2. Put the actual content in `context`, not a description of it. "The migration in
   `db/0042.sql`" tells the panel nothing; the migration tells it everything.
3. Say what you could not get. A panel that knows the schema is unavailable reasons
   differently from one that assumes a shape.

If the capture hook is installed, anything already in the conversation reaches the
panel without being retyped, but files you have not yet read are not in the
conversation either. Read first.

For public information, a panel member can be configured with provider-side web search;
check `mandos_status` to see whether yours has tools before assuming the panel can look
anything up.

## Quick Start

Mandos is an MCP **stdio** server — no HTTP endpoint, port, or token. Your harness
spawns `mandos-mcp`; call its tools directly. The `/council` prompt wraps the one-shot
flow. Use `/council-session` for a sustained back-and-forth with the same council.

Call the **`mandos`** tool:

- `prompt` (required), `context` (optional; goes to every panellist **and** the judge)
- `preset` or `panel` — which of *your* configured providers answer
- `analysis_model` — override the generative analyst (the judge *shape* is config-only)
- `thread_id` — session mode; Mandos reads/writes
  `~/.mandos/sessions/<thread_id>.json` and reconstructs `messages[]`
- `prior_answer` — in session mode, your previously authored answer for this thread,
  stored as the assistant turn

Inspect configuration with **`mandos_status`** (roster, roles, judge, presets, budget
estimates — never secrets or env-var names). Clear sessions with
**`mandos_clear_sessions`**.

## Reading a calibrated analysis

This is the part that differs from an ordinary LLM-judge tool. `analysis.calibration`
carries the numbers each finding was derived from; `role` and `index` point back into
the narrative field, so `("consensus", 0)` is the evidence for `consensus[0]`.

| reading | meaning | how to use it |
|---|---|---|
| `support[].support` | P(that panel member's answer backs this claim) | A consensus whose **lowest** supporter sits near 0.6 is a weak consensus. Say so. |
| `contested` | P(the answers genuinely disagree, rather than differ in wording) | High contested with a real split is the finding worth surfacing. |
| `holds` | P(this finding is borne out by the answers) — `verify` shape, and blind spots | Below ~0.5 the analyst claimed something the panel does not support. Do not repeat it as fact. |
| `standing` | 0–2 rubric: how well the panel as a whole supports the claim | A tiebreaker between findings, not a verdict. |
| `agreement[]` | pairwise P(two answers reach the same conclusion) — `matrix` shape | A low mean means the panel did not converge; report the disagreement rather than picking a side. |
| `per_answer[]` | `hedging` and `scope` (0–2 rubrics), `distinctive` (probability) | A high-hedging, low-scope answer deserves less weight. |
| `outlier` | which answer is least like the others | Worth reading closely — it is either the insight or the error. |

**Numbers are evidence, not permission.** A 0.9 does not make a claim true; it means
the panel's answers back it consistently. The panel can be consistently wrong.

## When the panel says it was missing something

`analysis.needs_evidence` lists what the panel found itself lacking, each with
`lacked` (it genuinely did not have this, rather than merely not mentioning it) and
`would_change` (having it would change the answer). The list is sorted by
`would_change`, so the item worth acting on is first.

This is the panel telling you what to fetch, which beats guessing up front:

1. Read the top item. If `would_change` is high and you can get it — read the file, run
   the query, check the version — get it.
2. Call `mandos` again with it added to `context`, and `depth=1`.
3. If you cannot get it, say so in your answer. An assumption the panel flagged should
   not reach the reader as a fact.

Do not loop more than once. A second pass with the decisive evidence is worth it; a
third rarely is, and the depth guard caps the chain regardless.

## Workflow

1. If `meta.ok == 0`, every panellist failed and the tool returns a clean
   `meta.failure` — say so instead of inventing evidence; let the harness fall back.
2. Send the user's question as `prompt`. Add `context` for files, constraints and any
   facts the panel could not know.
3. Pick a `preset` (use a `local-only` preset when the data must not leave the
   network) or an explicit `panel`.
4. Check `meta.judge_shape`. If `meta.judge_fallback_from` is set, the calibrated
   judge did not run and the analysis is an ordinary generative one — weigh it
   accordingly and mention it if the user is relying on the numbers.
5. **You are the final author.** Read the `analysis` — especially `blind_spots` and
   `contradictions` — the `calibration` numbers, and the `raw_answers`. Then write,
   preserving genuine disagreements and caveats rather than smoothing them away.
6. For a session, reuse the `thread_id` each turn and pass your last answer as
   `prior_answer`. If the host drops it, Mandos backfills from stored analysis.
7. Verify any low-confidence claim before relying on it.

## Response Handling

- `panel[]` — per-provider status metadata (id, status, latency, tokens). Answer text
  is in `raw_answers[]`; partial results are normal.
- `analysis` — `consensus` / `contradictions` / `partial_coverage` / `unique_insights`
  / `blind_spots` / `needs_evidence` / `confidence_notes`, plus `calibration`. `null`
  if no judge shape could produce one. Do not treat consensus as proof.
- `raw_answers[]` — all successful panel answers, returned unconditionally.
- `meta` — timings, ok/failed counts, `judge_shape`, `judge_fallback_from`,
  `judge_error`, `jev_calls` / `jev_questions`, cost estimate, budget status, session
  warnings, and a typed `failure` if the deliberation degraded.
- `thread_id` / `compacted` — on session responses; `compacted` means older local
  history was omitted from the provider request.
- `text` — a Markdown rendering of the same content, with each finding's numbers
  inline.
