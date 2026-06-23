# Deployment Guide

Imladris is a Python FastMCP stdio server distributed as a `uv` tool. Each
harness launches `imladris-mcp` as a child process. There is no HTTP server, no
exposed port, no Docker image, and no bearer-token surface.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/FeanorsCodeSL/imladris/main/scripts/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/FeanorsCodeSL/imladris/main/scripts/install.ps1 | iex
```

The installer bootstraps `uv`, resolves Python 3.13, refreshes the `imladris`
tool environment, and verifies `imladris --help`.

For local source testing, use the same installer:

```powershell
.\scripts\install.ps1 -Source .
```

## Configure

Run `imladris`. The Textual configurator:

1. Adds or edits 1-8 OpenAI-compatible providers.
2. Captures live/catalog model metadata and optional manual context-window overrides.
3. Assigns panel membership and one analysis judge.
4. Writes `~/.imladris/config.json`.
5. Writes token values only to `~/.imladris/.env` with restrictive permissions.
6. Wires Claude Code, Codex, or OpenCode to launch `imladris-mcp`.

`imladris doctor` prints the resolved roster, role assignments, off-prem providers,
context windows, budget thresholds, and harness wiring without secret values or
env-var names. `imladris refresh-catalog` fetches models.dev metadata into the local
cache; normal deliberation does not fetch catalog data.

## MCP Surface

- `imladris(prompt, context, panel, preset, analysis_model, max_tokens,
  temperature, reasoning_effort, timeout_s, thread_id, prior_answer)` returns
  structured `analysis`, unconditional `raw_answers`, `panel`, `meta`, and Markdown
  `text`. `thread_id` enables local session mode.
- `imladris_status()` returns non-secret configuration status.
- `imladris_clear_sessions(thread_id?)` clears local session files.
- `imladris clear-sessions [thread_id]` is the CLI equivalent.
- `imladris refresh-catalog` refreshes the local model metadata cache.
- `/council` instructs the host to call `imladris` and author from the returned
  analysis and raw answers.
- `/council-session` instructs the host to keep reusing a `thread_id` and pass its
  prior authored answer back as `prior_answer`.

## Local-Only

For sensitive data, use a preset whose panel and analysis judge are local
providers:

```yaml
presets:
  local-only:
    panel: [local]
    analysis: local
```

Any non-local panel provider receives the full prompt and optional context. In
session mode, panel providers also receive the reconstructed local session history.
Any non-local analysis judge also receives the successful panel answers.

## Operations

- Keep real configs and `.env` files out of version control.
- Use firewall or host egress rules when provider access must be constrained.
- Verify the active provider list with `imladris_status` before sending sensitive
  prompts.
- Clear local session history with `imladris clear-sessions [thread_id]` or
  `imladris_clear_sessions` when a thread should no longer be retained.
- Run `ruff check orchestrator`, `ruff format --check orchestrator`, and `pytest`
  before shipping code changes.
