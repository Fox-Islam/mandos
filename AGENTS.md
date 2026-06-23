# Repository Guidelines

Imladris is a Python + FastMCP project: a cross-harness multi-model deliberation
(Fusion) MCP server, run as an MCP **stdio** subprocess. The deliberation engine
fans a prompt out to a configurable panel of **OpenAI-compatible** LLM providers
(1–8), runs one API-side analysis **judge**, and lets the harness's own native model
author the final answer from the judge's `analysis` plus all raw panel answers. (The
curator and anonymization passes were removed.) The design and current contracts
live in `docs/architecture/`. **No FastAPI/REST, no HTTP server, no Docker** —
those were removed.

## Project Structure & Module Organization

- `orchestrator/` — the service package: `mcp_server.py` (FastMCP `imladris` +
  `imladris_status` + `imladris_clear_sessions` tools, the `/council` and
  `/council-session` prompts, `main()`), `panel.py` (fan-out + degradation + session
  orchestration), `judge.py` (the analysis judge), `sessions.py` (local session store
  + per-member `messages[]` + window-aware compaction), `model_catalog.py` (models.dev
  fetch/cache/seed + context-window resolver), `budget.py` (advisory context-budget
  estimator), `providers/` (the single OpenAI-compatible kind + factory), `settings.py`
  (JSON/YAML + env config; roles panel/judge), `models.py`, `interfaces.py`,
  `costing.py`, `json_utils.py`, `fakes.py`, `data/` (bundled model-catalog seed),
  `cli/` (the `imladris` configurator TUI), `tests/`.
- `config/` — `imladris.example.yaml` (illustrative provider list, presets, pricing).
- `scripts/` — `install.sh`, `install.ps1`, and stdio smoke helpers (Bash + PowerShell).
- `docs/architecture/` — live architecture, pipeline, configuration, security,
  deployment, and dependency references.

The project slug is `imladris`: the MCP **tool id** and server id
(`FastMCP("imladris")`), the console scripts (`imladris`, `imladris-mcp`), and the
local skill name. The user-facing prompts are `/council` (one-shot) and
`/council-session` (stateful). Keep `imladris` stable.

## Build, Test, and Development Commands

Run from the repository root (Python 3.13; `.python-version` targets the `3.13`
minor series so Codex cloud `3.13.13` and newer local patch releases work):

- For Codex cloud tasks, the setup script `scripts/codex-cloud-setup.sh` builds a
  3.13 venv and installs the dev requirements before starting implementation.
- `./.venv/bin/python -m pytest` — offline test suite.
- `./.venv/bin/ruff check orchestrator` — lint.
- `./.venv/bin/ruff format --check orchestrator` — style gate (CI enforces).
- `./.venv/bin/python -m orchestrator.mcp_server` — run the MCP stdio server (manual; blocks).
- `./scripts/install.sh` or `.\scripts\install.ps1 -Source .` — install/test the
  `imladris` + `imladris-mcp` console scripts through the supported installer.

## Coding Style & Naming Conventions

Python 3.13, 4-space indentation, `snake_case` functions/modules, `PascalCase`
classes, typed Pydantic request/response models. Keep provider, panel, and judge
dependencies behind small `Protocol` interfaces so each stage stays testable with
deterministic fakes. Match existing module style; avoid speculative abstractions.

## Testing Guidelines

Use focused `pytest` coverage. Cover pure policy logic with unit tests, the MCP
tool surface via an in-memory FastMCP `Client`, and external providers with
deterministic fakes (`fakes.py`) / `respx`. Keep degraded cases covered: provider
timeout, partial panel failure, `ok==0` short-circuit, malformed judge JSON (record
`meta.judge_error`; raw answers still returned), and session store-failure
degradation. The judge runs at `temperature: 0`; snapshot the schema, not the wording.

## Commit & Pull Request Guidelines

Use short, imperative subjects (`Harden judge JSON parsing`). PRs should
summarize scope, list commands run, call out unimplemented assumptions, and note
license or configuration impact. Do not commit, amend, or push unless the user
explicitly asks. Never add AI tools as authors or co-authors.

## Security & Configuration Tips

Secrets are env-only, referenced by name (`api_key_env`) from config, and stored
in `~/.imladris/.env` (0600); they are never logged or returned by
`imladris_status`. The MCP server runs over stdio — no exposed port, no bearer
token. Panel prompts leave your network for any non-local provider; the
`local-only` preset keeps every token on-prem. Cap `max_tokens` and panel size
(1–8); treat panel/judge output as untrusted external text. Never commit secrets
or real config. See `SECURITY.md`.
