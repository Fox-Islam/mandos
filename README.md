# Imladris

Named for Imladris (Rivendell), seat of the Council of Elrond — where
representatives of every free people gathered to weigh a hard decision together.

![Imladris Council](docs/images/Imladris%20Council.gif)

## Design (source of truth)

[`docs/architecture/`](docs/architecture/) — overview, pipeline, MCP surface,
configuration reference, provider abstraction, judge, security, and deployment.

## How it works (one paragraph)

The harness's native model calls the `imladris` MCP tool (via the `/council`
or `/council-session` prompt). The server fans the prompt out to the configured panel (1–8
members) **concurrently** (each provider is a plain HTTPS call), collects answers
(with partial-result tolerance), then runs an **API-side analysis judge**
(consensus / contradictions / partial coverage / unique insights / blind spots).
The tool returns that analysis plus all raw panel answers. The native model reads
both and **authors the final answer**. A session call uses a local
`~/.imladris/sessions/<thread_id>.json` store to rebuild prior user/assistant turns for
the next panel pass. An MCP server can't call back into the
harness model, so the judge is API-side and the native model is always the outer
author.

## Install

Bootstrap [`uv`](https://docs.astral.sh/uv/) and install the `imladris` console
tool with a single command. You do not need a local checkout of this repository:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/FeanorsCodeSL/imladris/main/scripts/install.sh | sh
```

```powershell
# Windows
irm https://raw.githubusercontent.com/FeanorsCodeSL/imladris/main/scripts/install.ps1 | iex
```

Each script installs `uv` if absent, resolves or installs Python 3.13, removes any
previous `imladris` uv tool environment, reinstalls with the resolved Python
interpreter, and verifies `imladris --help` before finishing. This puts two
commands on your console PATH: `imladris` (the configurator TUI) and
`imladris-mcp` (the MCP stdio server harnesses spawn).
Until a PyPI release is uploaded, this git-URL install requires `git` on PATH;
the installer checks that up front.

Use the installer as the supported install workflow, including for local source
testing. Raw `uv tool install .` is only useful when deliberately debugging uv
itself because it bypasses the installer safeguards.

Then run the configurator:

```bash
imladris
```

![Imladris TUI dashboard](docs/images/tui-dashboard.svg)

The full-screen configurator loads the current config, shows the council roster,
lets you add/edit/delete members, assign the **judge** role, writes
`~/.imladris/config.json` + `~/.imladris/.env` (0600), and wires selected
harnesses (Claude Code / Codex / OpenCode) with the `imladris` MCP entry. After
that, continue in your harness and invoke `/council`, `/council-session`, or the
`imladris` tool when you want deliberation.

Run `imladris doctor` to print the resolved roster, roles, and wired harnesses
(no secrets). Use `imladris refresh-catalog` or the TUI refresh action to update
the local models.dev cache; runtime deliberation only reads cache/seed data.
Council sessions are local files under `~/.imladris/sessions/`. Clear one or all
of them with `imladris clear-sessions [thread_id]` or the `imladris_clear_sessions`
MCP tool.

## Repository layout

```
orchestrator/        the service package
  mcp_server.py      FastMCP server — tools: imladris, imladris_status, imladris_clear_sessions; council + council-session prompts; main()
  panel.py           panel fan-out orchestration (partial results + degradation)
  judge.py           API-side analysis judge
  model_catalog.py   offline-first model metadata, models.dev parser, cache helpers
  budget.py          advisory context-budget estimates
  sessions.py        local council-session store and message reconstruction
  providers/         single OpenAI-compatible chat provider kind + factory
  settings.py        JSON/YAML + env config loading/validation; roles panel/judge
  models.py          Pydantic request/response models
  interfaces.py      Protocol seams; fakes.py — deterministic test doubles
  costing.py         cost estimation; json_utils.py — tolerant judge JSON
  cli/               imladris configurator: TUI, catalog, config ops, secrets, probe, harness/
  tests/             pytest suite (respx for HTTP)
config/              imladris.example.yaml (illustrative providers, presets, pricing)
scripts/             install.sh, install.ps1, stdio smoke (Bash + PowerShell)
docs/architecture/   live architecture, configuration, security, deployment references
.github/workflows/   CI, sonar
.agents/skills/      imladris-deliberate skill
```

## Develop

Requires **Python 3.13**.

```bash
python -m venv .venv
./.venv/bin/python -m pip install -r orchestrator/requirements-dev.txt

./.venv/bin/ruff check orchestrator           # lint
./.venv/bin/ruff format --check orchestrator  # style gate (CI enforces)
./.venv/bin/python -m pytest                  # tests

./.venv/bin/python -m orchestrator.mcp_server # run the MCP server over stdio
IMLADRIS_SOURCE="$(pwd)" ./scripts/install.sh # install/test from this checkout
```

On Windows, use the same installer path:

```powershell
.\scripts\install.ps1 -Source .
```

## Configuration

Run `imladris` to write `~/.imladris/config.json` and `~/.imladris/.env` (0600).
The in-repo `config/imladris.example.yaml` is illustrative only. Both JSON and
YAML config files are supported by extension; the TUI writes JSON.
**Secrets live only in env vars referenced by name** (`api_key_env`) from the
config — never in the config file, never logged, never returned by
`imladris_status`.

Resolution: `--config <path>` → `IMLADRIS_CONFIG` → `./imladris.{json,yaml}` →
`~/.imladris/config.{json,yaml}`.

## Integration

Per-harness setup (Claude Code, Codex, OpenCode) is wired automatically by the
`imladris` configurator. Every entry launches `imladris-mcp` over stdio with
`IMLADRIS_CONFIG` pointed at `~/.imladris/config.json`. For the manual config
snippets, see [`docs/architecture/06-deployment.md`](docs/architecture/06-deployment.md).

## License

[MIT](LICENSE). See also `SECURITY.md`, `CONTRIBUTING.md`, `THIRD-PARTY-NOTICES.md`.
