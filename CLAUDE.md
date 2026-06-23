# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The design and current contracts live in **`docs/architecture/`** (overview,
pipeline-workflow, configuration-reference, security, deployment, dependencies). 

> **Stack: Python, stdio-only, like thorondor.** Python 3.13 + `fastmcp`, run as an
> MCP **stdio** subprocess.

> **Name: Imladris.** The slug `imladris` is the MCP **tool id** and server id
> (`FastMCP("imladris")`), the console scripts (`imladris`, `imladris-mcp`), and the
> skill name. The user-facing command/prompt is `/council`. Treat `imladris` as a
> single token; keep it stable.

## What this is

A **Python MCP server** replicating OpenRouter's *Fusion*: fan a prompt out to a
configurable panel of **OpenAI-compatible** LLM providers, run an **API-side analysis
judge** (consensus / contradictions / partial coverage / unique insights / blind
spots), then let the **harness's own native model** author the final answer from the
analysis plus all raw panel answers. Portable across
Claude Code, Codex, OpenCode, and any MCP-aware harness. Installed via the
one-liner installer and configured via the full-screen `imladris` TUI.

## Golden rules (these shape the whole design — internalise them)

- **The harness's native model is always the FINAL AUTHOR.** An MCP server cannot
  reliably call back into it (MCP `sampling` is unimplemented in CC/Codex). So the
  analysis judge runs **API-side and finishes before the tool returns**; the native
  model authors only after, in the outer loop.
- **The judge analyses; it never merges or authors.** Two API-side roles (panel →
  analysis judge), then the native author writes from `analysis` + all raw answers.
  (The old curator pass was removed — D13; anonymization too — D14.)
- **The engine is stateless for one-shot `/council`** (derived from `prompt` +
  `context`) apart from a per-request depth guard. **Sessions are the one stateful
  component:** `/council-session` owns a local on-prem store (D15).
- **Secrets come from env vars referenced by name** (`api_key_env`), stored in
  `~/.imladris/.env` (0600) and loaded at startup. Never hardcode keys, never put
  them in config files, never log them, never return them from `imladris_status`.
- **Every provider is OpenAI-compatible** (`/v1/chat/completions`) — OpenRouter,
  DeepSeek, MiniMax, local vLLM, OpenAI, Groq, Together, … There is **no special
  case**. (The old "Anthropic is the only special case" rule is retired; the only
  Anthropic model in the loop is the harness's native Claude, the final author.)
- **Partial panel results are normal.** A provider failure is a *recorded error in
  the result*, never a raised exception that fails the batch. If `ok == 0`,
  short-circuit to a clean error so the host can fall back.
- **Judge JSON parsing must degrade gracefully** — strip fences/preamble, and on
  parse/schema failure record `meta.judge_error` and return `analysis: null`; raw
  panel answers are returned unconditionally regardless, so the host can still author.
  Never crash.
- **stdio only** — no Docker, no REST, no exposed port, no bearer token.
- **Commit policy:** commit/stage only when the user explicitly asks; never add AI
  tools as authors or co-authors.

## Architecture

```
Harness native model (OUTER / final author)
        │ /council or /council-session → imladris(prompt, thread_id?, prior_answer?, …)
        ▼
orchestrator/  (MCP stdio; stateless one-shot, local store for sessions)
  mcp_server.py (imladris / imladris_status / imladris_clear_sessions)
        → sessions.py (session mode: local store, per-member messages[], compaction)
        → panel.py  (concurrent fan-out + overall deadline; partial results)
            → providers/ (OpenAI-compatible: vLLM / OpenRouter / DeepSeek / …)
            → aggregate panel result (answers, latency, tokens, errors)
        → judge.py  → analysis judge → structured analysis JSON (real provider ids)
        → model_catalog.py + budget.py (context-window resolve + advisory budget)
        → analysis + all raw answers + Markdown rendering + meta
```

`mcp_server.py` is a thin surface over the `panel.run_deliberation` pipeline. One
transport: **stdio** (each harness spawns `imladris-mcp`).

## Commands

Python 3.13 (`.python-version` targets the `3.13` minor series so Codex cloud
`3.13.13` and newer local patch releases work). From the repo root:

```bash
./.venv/bin/python -m pytest                  # tests
./.venv/bin/ruff check orchestrator           # lint
./.venv/bin/ruff format --check orchestrator  # style gate (CI enforces)
./.venv/bin/python -m orchestrator.mcp_server # run the stdio server (manual; blocks)
IMLADRIS_SOURCE="$(pwd)" ./scripts/install.sh # install/test from this checkout
```

On Windows, use `.\scripts\install.ps1 -Source .`. The installer is the supported
install workflow for both users and local testing; raw `uv tool install .`
bypasses the stale-tool-env safeguards and is only for debugging uv behavior.

## How to work here

- Treat `docs/architecture/` as the live contract and keep it aligned with code
  changes that affect behavior, configuration, security, deployment, or dependencies.
- Keep provider/panel/judge behind `Protocol` interfaces so they stay testable with
  deterministic fakes (`fakes.py`). Mock HTTP with `respx`; the judge runs at
  `temperature: 0`; snapshot the schema, not the wording.

## Workspace context

One sub-project of the **Tengwar / Fëanor's Code** workspace; see the parent
`../CLAUDE.md` for cross-project conventions. Naming follows the legendarium theme
(TENGWAR, Thorondor, Mirrormere). Honour the workspace constraint: **never commit,
stage, push, or deploy without asking.** Build, lint, and test when needed for
verification.
