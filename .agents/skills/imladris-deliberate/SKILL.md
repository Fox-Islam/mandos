---
name: imladris-deliberate
description: Convene a panel of independent AI models to deliberate on a hard question via the Imladris MCP server (stdio), returning structured analysis (consensus, contradictions, partial coverage, unique insights, blind spots) plus all raw panel answers. Use for research, expert critique, "compare and contrast", architecture/design trade-offs, or any task where being wrong is costly. You remain the final author.
---

# Imladris Deliberation

## When to use

Reach for Imladris when a task is hard enough that one model's answer is risky:
research questions, design/architecture trade-offs, "compare X and Y", expert
critique, or anything where being wrong is costly. For quick lookups, answer
directly — deliberation costs ~N panel calls + an analysis call.

## Quick Start

Imladris is an MCP **stdio** server — there is no HTTP endpoint, port, or token.
Your harness spawns `imladris-mcp`; call its tools directly. The `/council` prompt
wraps the one-shot flow below. Use `/council-session` when the user wants a sustained
back-and-forth with the same council.

Call the **`imladris`** tool:

- `prompt` (required), `context` (optional extra context for every panellist)
- `preset` or `panel` — which of *your* configured providers answer
- `analysis_model` — override which provider runs the analysis judge
- `thread_id` — present means session mode; Imladris reads/writes the local
  `~/.imladris/sessions/<thread_id>.json` file and reconstructs `messages[]`
- `prior_answer` — in session mode, pass your previously authored answer for this
  thread so Imladris can store it as the assistant turn

Inspect configuration and health with **`imladris_status`** (resolved roster, roles,
presets, budget thresholds, budget estimates — never secrets or env-var names). Clear
local session files with **`imladris_clear_sessions`**.

## Workflow

1. If `meta.ok == 0`, every panellist failed and the tool returns a clean
   `meta.failure` — say so instead of inventing evidence; let the harness fall back.
2. Send the user's question as `prompt`. Add `context` for files/constraints.
3. Pick a `preset` (your configured presets — use a `local-only` preset when the
   data must not leave the network) or an explicit `panel`.
4. **You are the final author.** Read the `analysis` — especially `blind_spots` and
   `contradictions` — and the `raw_answers`, then write the answer, preserving
   genuine disagreements and caveats rather than smoothing them away.
5. For a session, use the same `thread_id` on each turn and pass your last answer as
   `prior_answer`. If the host drops it, Imladris backfills from stored analysis.
6. When the analysis flags a claim as low-confidence, verify it before relying on it.

## Response Handling

- `panel[]` — per-provider status metadata (id, status, latency, tokens). The raw
  answer text is returned in `raw_answers[]`; partial results are normal.
- `analysis` — `consensus` / `contradictions` / `partial_coverage` / `unique_insights`
  / `blind_spots` / `confidence_notes`. Do not treat consensus as proof.
- `raw_answers[]` — all successful panel answers, returned unconditionally.
- `meta` — timings, ok/failed counts, analysis provider id, cost estimate,
  budget status, session warnings, and a typed `failure` if the deliberation degraded.
- `thread_id` / `compacted` — present on session responses; `compacted` means older
  local history was omitted from the provider request.
- `text` — a Markdown rendering of the same content.
