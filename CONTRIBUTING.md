# Contributing

Imladris is a Python + FastMCP project: a single `orchestrator/` package holding
the deliberation engine and the FastMCP stdio server (`mcp_server.py`). See
`docs/architecture/` for the design and current contracts.

## Setup

Use **Python 3.13** (`.python-version` targets the `3.13` minor series so Codex
cloud `3.13.13` and newer local patch releases work). From the repo root:

```bash
python -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r orchestrator/requirements-dev.txt
```

## Checks

Run these before opening a change:

```bash
./.venv/bin/ruff check orchestrator           # lint
./.venv/bin/ruff format --check orchestrator  # style gate (CI enforces this)
./.venv/bin/python -m pytest                  # offline test suite
```

## Coding Style & Naming

Python 3.13 style, 4-space indent, `snake_case` functions/modules, `PascalCase`
classes, typed Pydantic request/response models. Keep provider, panel, and judge
dependencies behind small `Protocol` interfaces (`interfaces.py`) so each stage stays
testable with deterministic fakes. Match existing module style; avoid speculative
abstractions.

## Scope

Keep changes surgical. Do not vendor third-party source or model weights.
**Secrets never live in config or the repo** — providers reference an env var by
name (`api_key_env`); keep token values only in `~/.imladris/.env` (0600), never
in the config file.

Keep changes covered by focused tests and update the architecture docs when a public
contract changes.

## Pull Requests

Use short, imperative subjects such as `Harden judge JSON parsing` or
`Add council sessions`. Summaries should state the user-facing behavior
change, the commands run, any unimplemented assumptions, and any license or
config impact.

Do not commit, amend, or push unless the user explicitly asks. **Never add AI
tools as authors or co-authors.**
