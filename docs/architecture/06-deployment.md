# Deployment Guide

Mandos is a Python FastMCP stdio server distributed as a `uv` tool. Each
harness launches `mandos-mcp` as a child process. There is no HTTP server, no
exposed port, no Docker image, and no bearer-token surface.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Fox-Islam/mandos/main/scripts/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/Fox-Islam/mandos/main/scripts/install.ps1 | iex
```

The installer bootstraps `uv`, resolves Python 3.13, refreshes the `mandos`
tool environment, and verifies `mandos --help`.

For local source testing, use the same installer:

```powershell
.\scripts\install.ps1 -Source .
```

## Configure

Run `mandos`. The Textual configurator:

1. Adds or edits 1-8 OpenAI-compatible providers.
2. Captures live/catalog model metadata and optional manual context-window overrides.
3. Assigns panel membership and the generative analyst.
4. Sets the judge on its own screen: shape, Jev provider, model, endpoint override
   and key.
5. Writes `~/.mandos/config.json`.
6. Writes token values — providers' and Jev's — only to `~/.mandos/.env` with
   restrictive permissions.
7. Wires Claude Code, Codex, or OpenCode to launch `mandos-mcp`.

`mandos doctor` prints the resolved roster, the judge (shape, Jev provider and model,
whether its endpoint is on-prem, whether its key is set), role assignments, off-prem
providers, context windows, budget thresholds, and harness wiring — without secret
values or env-var names. `mandos refresh-catalog` fetches models.dev metadata into the local
cache; normal deliberation does not fetch catalog data.

## MCP Surface

- `mandos(prompt, context, panel, preset, analysis_model, max_tokens,
  temperature, reasoning_effort, timeout_s, thread_id, prior_answer)` returns
  structured `analysis` (with its `calibration` block), unconditional `raw_answers`,
  `panel`, `meta`, and Markdown `text`. `thread_id` enables local session mode. The
  judge shape is config-only; `analysis_model` selects the generative analyst.
- `mandos_status()` returns non-secret configuration status.
- `mandos_clear_sessions(thread_id?)` clears local session files.
- `mandos clear-sessions [thread_id]` is the CLI equivalent.
- `mandos refresh-catalog` refreshes the local model metadata cache.
- `/council` instructs the host to call `mandos` and author from the returned
  analysis and raw answers.
- `/council-session` instructs the host to keep reusing a `thread_id` and pass its
  prior authored answer back as `prior_answer`.

## Local-Only

For sensitive data, use a preset whose panel and analyst are local providers **and**
keep the judge on-prem:

```yaml
judge:
  shape: llm            # or a Jev shape with judge.base_url on your own network

presets:
  local-only:
    panel: [local]
    analysis: local
```

Any non-local panel provider receives the full prompt and optional context. In
session mode, panel providers also receive the reconstructed local session history.
Any non-local analyst receives the successful panel answers — and so does Jev, whose
hosted endpoints are off-prem. A local panel with a hosted Jev judge still sends the
whole deliberation off-prem; `mandos doctor` labels the judge endpoint so this is
visible before you send anything.

## Operations

- Keep real configs and `.env` files out of version control.
- Use firewall or host egress rules when provider access must be constrained.
- Verify the active provider list **and the judge's egress label** with
  `mandos_status` before sending sensitive prompts.
- Clear local session history with `mandos clear-sessions [thread_id]` or
  `mandos_clear_sessions` when a thread should no longer be retained.
- Run `ruff check orchestrator`, `ruff format --check orchestrator`, and `pytest`
  before shipping code changes.
