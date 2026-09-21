# Repository Guidelines

Mandos is a Python + FastMCP project: a cross-harness multi-model deliberation MCP
server, run as an MCP **stdio** subprocess, **whose judge is Jev** — TypeSafe's System
One decision model. The engine fans a prompt out to a configurable panel of
**OpenAI-compatible** LLM providers (1–8), then puts named questions about their
answers to Jev and derives the analysis from the calibrated probabilities that come
back. The harness's own native model authors the final answer from that `analysis`
plus all raw panel answers.

It started as a port of OpenRouter's *Fusion* and keeps its panel stage and analysis
schema; the generative analyst survives as `judge.shape: "llm"` and as the fallback.
(The curator and anonymization passes were removed.) The design and current contracts
live in `docs/architecture/`. **No FastAPI/REST, no HTTP server, no Docker** — those
were removed.

## Project Structure & Module Organization

- `orchestrator/` — the service package: `mcp_server.py` (FastMCP `mandos` +
  `mandos_status` + `mandos_clear_sessions` tools, the `/council` and
  `/council-session` prompts, `main()`), `panel.py` (fan-out + degradation + session
  orchestration + Markdown rendering), `jev/` (the Jev client: wire format, the
  `noul`/`choice`/`score` primitives, provider switch, factory), `judge/` (the four
  shapes behind one dispatcher — `hybrid.py`, `matrix.py`, `verify.py`, `llm.py`, plus
  `extract.py` and the shared `outcome.py`), `sessions.py` (local session store +
  per-member `messages[]` + window-aware compaction), `model_catalog.py` (models.dev
  fetch/cache/seed + context-window resolver), `budget.py` (advisory context-budget
  estimator), `providers/` (the single OpenAI-compatible kind + factory), `settings.py`
  (JSON/YAML + env config; the `judge` block; roles panel/judge), `models.py`,
  `interfaces.py`, `costing.py`, `json_utils.py`, `fakes.py`, `data/` (bundled
  model-catalog seed), `cli/` (the `mandos` configurator TUI), `tests/`.
- `config/` — `mandos.example.yaml` (illustrative providers, judge, presets, pricing).
- `scripts/` — `install.sh`, `install.ps1`, and stdio smoke helpers (Bash + PowerShell).
- `docs/architecture/` — live architecture, pipeline, judge shapes, configuration,
  security, deployment, and dependency references.

The project slug is `mandos`: the MCP **tool id** and server id
(`FastMCP("mandos")`), the console scripts (`mandos`, `mandos-mcp`), and the
local skill name. The user-facing prompts are `/council` (one-shot) and
`/council-session` (stateful). Keep `mandos` stable.

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
  `mandos` + `mandos-mcp` console scripts through the supported installer.

## Coding Style & Naming Conventions

Python 3.13, 4-space indentation, `snake_case` functions/modules, `PascalCase`
classes, typed Pydantic request/response models. Keep provider, panel, and judge
dependencies behind small `Protocol` interfaces so each stage stays testable with
deterministic fakes. Match existing module style; avoid speculative abstractions.

Nothing may assume it is the only caller. A harness can have several councils in
flight, so new code takes the pooled client from `orchestrator.http.shared_client()`
rather than building its own, and any process-level state it adds needs the same
treatment the session lock map got: reference-counted, not accumulated.

Judge code has one extra rule: a finding must be **derived from a measurement**. If
you add a judgement, add the Jev question that settles it and the named threshold that
reads its answer (see `SUPPORT_HIGH`, `CONTESTED`, `BLIND_SPOT` in
`orchestrator/judge/hybrid.py`). Never let a generative model write the verdict.

## Testing Guidelines

Use focused `pytest` coverage. Cover pure policy logic with unit tests, the MCP
tool surface via an in-memory FastMCP `Client`, and external services with
deterministic fakes (`fakes.py`: `FakeChatProvider`, `FakeJevClient`) / `respx`.

**No test calls Jev.** `FakeJevClient` answers every question in the shape it was
asked, identically every run, so a judge test measures the judge's reasoning from
probabilities rather than Jev's accuracy. When an assertion depends on the negatives,
script the whole reply — the fake's simulated yes at 0.75 would otherwise make every
claim look supported.

Keep degraded cases covered: provider timeout, partial panel failure, `ok==0`
short-circuit, a failed extraction (falls to `matrix`, not to `llm`), Jev unreachable
(falls all the way to `llm`, `meta.judge_fallback_from` set), Jev unreachable with no
analyst (`analysis: null`), a partially failed Jev batch, malformed judge JSON,
a missing or stale conversation capture, and session store-failure degradation.

`pytest` is offline. `pytest -m live` runs `test_jev_live.py` against the real API
and needs a key; it pins thresholds that were measured rather than chosen, so change
them only with a live run to back it up. The generative judge runs
at `temperature: 0`; snapshot the schema, not the wording.

## Commit & Pull Request Guidelines

Use short, imperative subjects (`Harden judge JSON parsing`). PRs should
summarize scope, list commands run, call out unimplemented assumptions, and note
license or configuration impact. Do not commit, amend, or push unless the user
explicitly asks. Never add AI tools as authors or co-authors.

## Security & Configuration Tips

Secrets are env-only, referenced by name (`api_key_env`) from config, and stored
in `~/.mandos/.env` (0600); they are never logged or returned by `mandos_status`. That
includes the Jev key, whose variable name follows `judge.provider` unless set. The MCP
server runs over stdio — no exposed port, no bearer token.

Panel prompts leave your network for any non-local provider, **and the Jev judge is
itself an egress destination**: a hosted Jev shape sends the whole deliberation
off-prem even behind an all-local panel. Use `judge.base_url` for a self-hosted
endpoint or `judge.shape: "llm"` with a local analyst; `mandos doctor` labels the
judge endpoint on-prem/off-prem. Cap `max_tokens` and panel size (1–8); treat
panel/judge output as untrusted external text. Never commit secrets or real config.
See `SECURITY.md`.
