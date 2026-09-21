# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The design and current contracts live in **`docs/architecture/`** (overview,
pipeline-workflow, judge-shapes, configuration-reference, security, deployment,
dependencies).

> **Stack: Python, stdio-only, like thorondor.** Python 3.13 + `fastmcp`, run as an
> MCP **stdio** subprocess.

> **Name: Mandos.** The slug `mandos` is the MCP **tool id** and server id
> (`FastMCP("mandos")`), the console scripts (`mandos`, `mandos-mcp`), and the
> skill name. The user-facing command/prompt is `/council`. Treat `mandos` as a
> single token; keep it stable.

## What this is

A **Python MCP server** whose judge is **Jev**, TypeSafe's System One decision model.
Fan a prompt out to a configurable panel of **OpenAI-compatible** LLM providers, then
ask Jev named questions about what the answers established - claim by claim, model by
model - and let the **harness's own native model** author the final answer from the
calibrated analysis plus all raw panel answers. Portable across Claude Code, Codex,
OpenCode, and any MCP-aware harness. Installed via the one-liner installer and
configured via the full-screen `mandos` TUI.

It began as a port of OpenRouter's *Fusion* and keeps its panel stage and analysis
schema (consensus / contradictions / partial coverage / unique insights / blind
spots) so the two stay comparable. What changed is the judging: Fusion asserts,
Mandos measures.

## Golden rules (these shape the whole design - internalise them)

- **The harness's native model is always the FINAL AUTHOR.** An MCP server cannot
  reliably call back into it (MCP `sampling` is unimplemented in CC/Codex). So the
  analysis judge runs **API-side and finishes before the tool returns**; the native
  model authors only after, in the outer loop.
- **The judge analyses; it never merges or authors.** Panel → judge, then the native
  author writes from `analysis` + all raw answers. The curator and anonymization
  passes were removed; `settings.py` still strips their config keys on load.
- **Jev decides; generative models only propose.** A finding must be *derived from a
  measurement*, never asserted by a model. If you add a judgement, add the question
  that settles it and the threshold that reads its answer - do not let a chat model
  write the verdict. The generative analyst may propose claims (`hybrid`) or draft an
  analysis for grading (`verify`); it is the fallback, not the default.
- **Five shapes, one dispatcher.** `hybrid` / `matrix` / `verify` / `probe` / `llm` all
  return the same `JudgeOutcome` so `panel.py` never branches on which judge ran.
  `matrix` is the only shape with no generative call in it, and so the only one that
  works with no chat provider in the judge role. `probe` is the only one that does not
  deliberate: it scores `needs_evidence` and nothing else, so it alone has no fallback.
- **Batch Jev, never loop it.** A decision call costs its round trip and almost
  nothing per question (~90ms of model time for one question or sixty). Build the
  widest call the shape allows; `judge.questions_per_call` bounds the request body,
  not the spend.
- **The engine is stateless for one-shot `/council`** (derived from `prompt` +
  `context`) apart from a per-request depth guard. **Sessions are the one stateful
  component:** `/council-session` owns a local on-prem store.
- **Secrets come from env vars referenced by name** (`api_key_env`), stored in
  `~/.mandos/.env` (0600) and loaded at startup. Never hardcode keys, never put
  them in config files, never log them, never return them from `mandos_status`.
- **Every provider is OpenAI-compatible** (`/v1/chat/completions`) - OpenRouter,
  DeepSeek, MiniMax, local vLLM, OpenAI, Groq, Together, … There is **no special
  case**. (The old "Anthropic is the only special case" rule is retired; the only
  Anthropic model in the loop is the harness's native Claude, the final author.)
- **Partial panel results are normal.** A provider failure is a *recorded error in
  the result*, never a raised exception that fails the batch. If `ok == 0`,
  short-circuit to a clean error so the host can fall back.
- **Every judge failure degrades, none raises.** A Jev shape that cannot deliver
  falls back to the generative analyst and records `meta.judge_fallback_from`; the
  generative judge's JSON parsing strips fences/preamble and, on parse/schema failure,
  records `meta.judge_error` and returns `analysis: null`. Raw panel answers are
  returned unconditionally regardless, so the host can always author. Never crash.
- **A half-configured judge warns; it does not block loading.** A missing Jev key or a
  shape with no analyst behind it is a `UserWarning`, not a `ValueError` - refusing to
  load would take the panel down to save the analysis.
- **The judge sees what the panel saw.** `context` goes to the panellists *and* the
  judge. A judge scoring coverage against half a question scores the wrong thing.
- **Mandos never runs a tool on the user's behalf.** Provider-executed tools are
  passed through; a client-executed `function` tool is refused at config load. The
  harness owns filesystem, shell and database access behind permission prompts, and
  an MCP subprocess running tools to feed a third-party panel routes around them.
  Local evidence reaches the panel because the *calling model* gathers it into
  `context`.
- **Anything captured from the conversation is redacted on write, not on read.** A
  secret must never reach `~/.mandos/context/` in the first place.
- **stdio only** - no Docker, no REST, no exposed port, no bearer token.
- **Commit policy:** commit/stage only when the user explicitly asks; never add AI
  tools as authors or co-authors.

## Architecture

```
Harness native model (OUTER / final author)
        │ /council or /council-session → mandos(prompt, thread_id?, prior_answer?, …)
        ▼
orchestrator/  (MCP stdio; stateless one-shot, local store for sessions)
  mcp_server.py (mandos / mandos_status / mandos_clear_sessions)
        → sessions.py (session mode: local store, per-member messages[], compaction)
        → panel.py  (concurrent fan-out + overall deadline; partial results)
            → providers/ (OpenAI-compatible: vLLM / OpenRouter / DeepSeek / …)
            → aggregate panel result (answers, latency, tokens, errors)
        → transcript.py (optional: the conversation a harness hook captured)
        → judge/    (hybrid → matrix → llm; one JudgeOutcome whichever ran)
            → extract.py  (hybrid/probe: an analyst proposes claims and gaps)
            → jev/        (noul / choice / score over {state, model, questions})
            → llm.py      (the generative analyst: shape "llm", and the fallback)
        → model_catalog.py + budget.py (context-window resolve + advisory budget)
        → analysis + calibration + all raw answers + Markdown rendering + meta
```

`mcp_server.py` is a thin surface over the `panel.run_deliberation` pipeline. One
transport: **stdio** (each harness spawns `mandos-mcp`).

## Commands

Python 3.13 (`.python-version` targets the `3.13` minor series so Codex cloud
`3.13.13` and newer local patch releases work). From the repo root:

```bash
./.venv/bin/python -m pytest                  # tests
./.venv/bin/ruff check orchestrator           # lint
./.venv/bin/ruff format --check orchestrator  # style gate (CI enforces)
./.venv/bin/python -m orchestrator.mcp_server # run the stdio server (manual; blocks)
MANDOS_SOURCE="$(pwd)" ./scripts/install.sh # install/test from this checkout
```

On Windows, use `.\scripts\install.ps1 -Source .`. The installer is the supported
install workflow for both users and local testing; raw `uv tool install .`
bypasses the stale-tool-env safeguards and is only for debugging uv behavior.

## How to work here

- Treat `docs/architecture/` as the live contract and keep it aligned with code
  changes that affect behavior, configuration, security, deployment, or dependencies.
- Keep provider/panel/judge behind `Protocol` interfaces so they stay testable with
  deterministic fakes (`fakes.py`: `FakeChatProvider`, `FakeJevClient`). Mock HTTP
  with `respx`; the generative judge runs at `temperature: 0`; snapshot the schema,
  not the wording.
- **No test calls Jev.** `FakeJevClient` answers every question in the shape it was
  asked, identically every run. Test the judge's reasoning from probabilities (a claim
  two models back and one denies becomes a contradiction), not Jev's accuracy - measure that against the live API under `local/`.
- When a test's assertion depends on the *negatives*, script the whole Jev reply. The
  fake's simulated yes at 0.75 would otherwise make every claim look supported.

## Workspace context

One sub-project of the **Tengwar / Fëanor's Code** workspace; see the parent
`../CLAUDE.md` for cross-project conventions. Naming follows the legendarium theme
(TENGWAR, Thorondor, Mirrormere). Honour the workspace constraint: **never commit,
stage, push, or deploy without asking.** Build, lint, and test when needed for
verification.
