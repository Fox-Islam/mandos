# Mandos

Named for Mandos, the Doomsman of the Valar — the one who pronounces judgement.

A multi-model deliberation MCP server whose **judge does not write prose**.

A panel of independent models answers your question in parallel. Then
[**Jev**](https://github.com/Fox-Islam/typesafe-sdk-php) — TypeSafe's System One
decision model — is asked, claim by claim and model by model, what the panel
actually established: *does this answer support this claim? do these two reach the
same conclusion? is this disagreement real, or two models phrasing one position
differently?* Every answer comes back as a calibrated probability. Your harness's own
model then writes the final answer from the panel's evidence and Jev's numbers.

The difference from a generative judge is the difference between being told there is
consensus and being shown that the weakest supporter scored 0.51.

## Why a decision model judges better than a chat model

An LLM-as-judge writes `"consensus": ["all three agree X is safe"]` and you have no
way to check it. It is one more generation, with the same failure modes as the
answers it is grading: it smooths disagreement into agreement, it is confidently
wrong at the same rate, and it reports its certainty as an adjective.

Jev cannot write that sentence. It answers named questions in fixed shapes — a
probability for a yes/no, a label with a distribution, a level on a rubric — so the
judge stage produces measurements rather than assertions. Three consequences:

- **The analysis is checkable.** Every finding carries the support floor, the
  contested probability and the confidence it was derived from.
- **There is no JSON to salvage.** No fenced code blocks, no truncated objects, no
  `judge_error` from a model that decided to preamble.
- **It is fast and it batches.** Jev answers a whole batch in parallel, so question
  count is nearly free: measured here, a 6-question judgement and a 44-question one
  both land near 420ms, because a call costs its round trip and almost nothing per
  question. A whole panel is adjudicated in one call for about $0.0002.

## The four judge shapes

Set `judge.shape` in config, or pass `analysis_model` per call.

| shape | what runs | what you get |
|---|---|---|
| **`hybrid`** *(default)* | an analyst proposes claims, Jev decides them | the full narrative schema, every finding carrying its numbers |
| **`matrix`** | Jev alone | pairwise agreement, per-answer rubrics, the outlier — no prose, no generative model anywhere |
| **`verify`** | the analyst writes, Jev grades what it wrote | each finding keeps its place and gains a probability that it holds |
| **`llm`** | the analyst alone | uncalibrated, Fusion-style; the fallback |

Only `matrix` needs no chat model for the judge role. Every Jev shape falls back to
`llm` when Jev cannot deliver, recording both the failure and `meta.judge_fallback_from`.

All three Jev shapes score every finding correctly and stably against the fixture in
[`docs/architecture/03-judge-shapes.md#measured`](docs/architecture/03-judge-shapes.md),
which is also where the default comes from.

## Relationship to OpenRouter Fusion

Mandos began as a port of [OpenRouter's Fusion
router](https://openrouter.ai/docs/guides/routing/routers/fusion-router) and keeps its
shape: a panel of 1–8 models in parallel, an analysis stage, and your own model as the
final author. The analysis schema is deliberately the same — consensus,
contradictions, partial coverage, unique insights, blind spots — so the two are
comparable.

What differs:

| | Fusion | Mandos |
|---|---|---|
| Judge | your outer model, generative | Jev, a calibrated decision model |
| Analysis | asserted | measured, with the numbers returned |
| Panel tools | `web_search` + `web_fetch` per panellist | none — panellists answer from what they know |
| Panel input | your actual conversation | the `prompt`/`context` your model passes |
| Hosting | OpenRouter, one key | your own OpenAI-compatible providers, on-prem capable |

The missing retrieval is the real gap and it is honest to say so: for a question that
turns on current facts, Fusion's panel is grounded and this one is not.

## Install

Bootstrap [`uv`](https://docs.astral.sh/uv/) and install the `mandos` console tool
with one command. No checkout needed:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Fox-Islam/mandos/main/scripts/install.sh | sh
```

```powershell
# Windows
irm https://raw.githubusercontent.com/Fox-Islam/mandos/main/scripts/install.ps1 | iex
```

Each script installs `uv` if absent, resolves or installs Python 3.13, removes any
previous `mandos` uv tool environment, reinstalls with the resolved interpreter, and
verifies `mandos --help` before finishing. That puts two commands on your PATH:
`mandos` (the configurator TUI) and `mandos-mcp` (the MCP stdio server your harness
spawns). Until a PyPI release exists this git-URL install needs `git` on PATH; the
installer checks up front.

Then configure:

```bash
mandos
```

![Mandos TUI dashboard](docs/images/tui-dashboard.svg)

The configurator loads the current config, shows the council roster, lets you
add/edit/delete members, sets the **judge** (shape, Jev provider, model, key), writes
`~/.mandos/config.json` + `~/.mandos/.env` (0600), and wires the harnesses you pick
(Claude Code / Codex / OpenCode). After that, invoke `/council`, `/council-session`,
or the `mandos` tool from your harness.

`mandos doctor` prints the resolved roster, the judge, and the wired harnesses — no
secrets. `mandos refresh-catalog` updates the local models.dev cache. Sessions live
in `~/.mandos/sessions/`; clear them with `mandos clear-sessions [thread_id]` or the
`mandos_clear_sessions` tool.

## Getting a Jev key

Jev is served by TypeSafe directly and through OpenRouter's decisions endpoint. The
request and answer bodies are identical; the provider only decides the host, the path
and which variable holds the key.

| | TypeSafe | OpenRouter |
|---|---|---|
| Host / path | `api.typesafe.ai` `/v1/systemone` | `openrouter.ai` `/api/alpha/decisions` |
| Key | `TYPESAFE_API_KEY` | `OPENROUTER_API_KEY` |
| Per-call cost reported | no | yes, folded into `meta.cost_basis.jev_usd` |

Set `judge.provider` and leave `judge.api_key_env` unset to let the key follow the
provider. `judge.base_url` overrides the host for a self-hosted or proxied endpoint.

## Repository layout

```
orchestrator/        the service package
  mcp_server.py      FastMCP server — tools: mandos, mandos_status, mandos_clear_sessions; council prompts
  panel.py           panel fan-out (partial results + degradation) and Markdown rendering
  jev/               the Jev client: wire format, question primitives, provider switch
  judge/             the four shapes — hybrid, matrix, verify, llm — and the dispatcher
  model_catalog.py   offline-first model metadata, models.dev parser, cache helpers
  budget.py          advisory context-budget estimates
  sessions.py        local council-session store and message reconstruction
  providers/         single OpenAI-compatible chat provider kind + factory
  settings.py        JSON/YAML + env config; roles panel/judge; the judge block
  models.py          Pydantic request/response models, including Calibration
  interfaces.py      Protocol seams; fakes.py — deterministic test doubles
  costing.py         cost estimation; json_utils.py — tolerant JSON for the llm judge
  cli/               the mandos configurator: TUI, catalog, config ops, secrets, probe, harness/
  tests/             pytest suite (respx for HTTP, deterministic Jev fake)
config/              mandos.example.yaml (illustrative providers, judge, presets, pricing)
scripts/             install.sh, install.ps1, stdio smoke (Bash + PowerShell)
docs/architecture/   live architecture, judge shapes, configuration, security, deployment
.agents/skills/      mandos-deliberate skill
```

## Design (source of truth)

[`docs/architecture/`](docs/architecture/) — overview, pipeline, judge shapes,
configuration reference, security, deployment, dependencies.

## Develop

Requires **Python 3.13**.

```bash
python -m venv .venv
./.venv/bin/python -m pip install -r orchestrator/requirements-dev.txt

./.venv/bin/ruff check orchestrator           # lint
./.venv/bin/ruff format --check orchestrator  # style gate (CI enforces)
./.venv/bin/python -m pytest                  # tests

./.venv/bin/python -m orchestrator.mcp_server # run the MCP server over stdio
MANDOS_SOURCE="$(pwd)" ./scripts/install.sh   # install/test from this checkout
```

On Windows: `.\scripts\install.ps1 -Source .`

No test calls Jev. `orchestrator/fakes.py` ships a deterministic fake that answers
every question in the shape it was asked, so the suite measures the judge's reasoning
from probabilities rather than Jev's accuracy.

## Configuration

Run `mandos` to write `~/.mandos/config.json` and `~/.mandos/.env` (0600). The in-repo
`config/mandos.example.yaml` is illustrative. Both JSON and YAML are supported by
extension; the TUI writes JSON. **Secrets live only in env vars referenced by name**
(`api_key_env`) — never in the config file, never logged, never returned by
`mandos_status`.

Resolution: `--config <path>` → `MANDOS_CONFIG` → `./mandos.{json,yaml}` →
`~/.mandos/config.{json,yaml}`.

## Integration

Per-harness setup (Claude Code, Codex, OpenCode) is wired by the configurator. Every
entry launches `mandos-mcp` over stdio with `MANDOS_CONFIG` pointed at
`~/.mandos/config.json`. For manual snippets see
[`docs/architecture/06-deployment.md`](docs/architecture/06-deployment.md).

## License

[MIT](LICENSE). See also `SECURITY.md`, `CONTRIBUTING.md`, `THIRD-PARTY-NOTICES.md`.
