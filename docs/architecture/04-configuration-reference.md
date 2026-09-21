# Configuration Reference

> This page is the source of truth for the live config contract. The `judge` block
> is part of it; catalog, budget, and session fields are too.

## Inputs

Mandos uses two inputs:

- `~/.mandos/config.json` or another JSON/YAML file selected by
  `MANDOS_CONFIG` or an explicit path.
- Environment variables containing secret values, for providers **and for the Jev
  judge**. The config stores only the env-var name in `api_key_env`; values usually
  live in `~/.mandos/.env` with mode `0600`.

Config lookup order is:

1. explicit `load_config(path)`
2. `MANDOS_CONFIG`
3. `./mandos.json`, `./mandos.yaml`, `./mandos.yml`
4. `~/.mandos/config.json`, `~/.mandos/config.yaml`,
   `~/.mandos/config.yml`

## Judge

```yaml
judge:
  shape: hybrid          # hybrid | matrix | verify | llm
  provider: typesafe     # typesafe | openrouter
  model: jev-latest
  base_url: null         # override the provider's own host
  api_key_env: null      # default: follows `provider`
  timeout_s: 30
  max_retries: 2
  questions_per_call: 60
```

| Field | Description |
|---|---|
| `shape` | Which judge runs. See [`03-judge-shapes.md`](03-judge-shapes.md). |
| `provider` | `typesafe` (`api.typesafe.ai/v1/systemone`) or `openrouter` (`openrouter.ai/api/alpha/decisions`). Same request and answer bodies either way. |
| `model` | Jev model name. `jev-latest` resolves to the same build on both providers. |
| `base_url` | Host override for a self-hosted or proxied endpoint. |
| `api_key_env` | Env-var name holding the Jev key. Unset means it follows `provider` — `TYPESAFE_API_KEY` or `OPENROUTER_API_KEY` — so switching provider switches which key is read. Set it and yours is kept across a provider switch. |
| `timeout_s` | Per **attempt**, not per call (see providers). The overall deadline caps the total. |
| `max_retries` | Extra attempts on 408/409/425/429, 5xx and transport errors. |
| `questions_per_call` | Batch bound. Jev answers a batch in parallel, so this bounds one request body rather than cost. |

`shape: "llm"` needs no Jev key at all. `shape: "matrix"` needs no
`defaults.analysis_model`. Everything else needs both — and says so as a warning, not
an error, because the fallback exists precisely for the half-configured case.

## Defaults

```yaml
defaults:
  preset: quality
  analysis_model: local
  timeout_s: 90
  max_depth: 1
  max_tokens: 4096
  analysis_max_tokens: 8192
  temperature: 0.2
  budget_warning_ratio: 0.75
  budget_error_ratio: 0.90
  session_max_turns: 12
```

- `analysis_model` must reference an enabled provider with the `judge` role. It is the
  generative analyst: it proposes claims for `hybrid`, writes the analysis for
  `verify` and `llm`, and is the fallback whenever Jev cannot deliver.
- `timeout_s` is the overall call deadline, shared by panel, analyst and Jev. This is
  the only true bound: per-provider `timeout_s` applies to each *attempt*, so a
  provider retrying twice can spend `3 x timeout_s` plus backoff on its own.
- `max_depth` is the recursion guard threshold.
- `max_tokens` and `temperature` are sent to panel providers unless overridden.
- `analysis_max_tokens` is the output ceiling for the analyst, which summarises the
  whole panel and so needs more room than any single panellist.
- `budget_warning_ratio` and `budget_error_ratio` drive advisory context-budget
  green/amber/red status.
- `session_max_turns` is the maximum stored history turns sent to session panel
  members before older turns are compacted.
- `session_max_age_days` prunes sessions untouched for that long, opportunistically
  on each session write. `0` disables it and they accumulate until cleared by hand.

Removed legacy defaults `curation_model`, `anonymize`, and `include_raw` are
ignored on load with a warning so old config files keep working.

## Providers

```yaml
providers:
  - id: local
    kind: openai
    base_url: http://localhost:8000/v1
    model: local-model
    context_window: 131072
    resolved_context_window: 131072
    context_window_source: endpoint
    roles: [panel, judge]
    timeout_s: 60
    max_retries: 1
```

Provider fields:

| Field | Description |
|---|---|
| `id` | Unique id used by panels, presets, pricing, and `analysis_model`. |
| `kind` | `openai` or `openrouter`; both use the OpenAI-compatible provider. |
| `base_url` | API base. The OpenAI SDK appends `/chat/completions`. |
| `model` | Model id sent in the request body. |
| `catalog_key` | Optional provider/catalog key used for model metadata lookup. |
| `context_window` | Optional manual context-window override. This is user truth and wins. |
| `resolved_context_window` | Optional endpoint/models.dev cached context-window metadata. |
| `context_window_source` | `override`, `endpoint`, `modelsdev`, or `unknown`. |
| `roles` | Any combination of `panel` and `judge`; default is `[panel]`. |
| `enabled` | Disabled providers are excluded from runtime resolution. |
| `api_key_env` | Env-var name holding the provider key; value is never stored here. |
| `headers` | Extra request headers. Overrides the OpenRouter attribution headers below. |
| `tools` | Provider-executed tools, passed through verbatim (e.g. `[{"type": "openrouter:web_search"}]`). Client-executed `function` tools are refused at load. |
| `max_tool_calls` | Cap on provider-side tool iterations, when the endpoint honours one. |
| `timeout_s` | Per **attempt**, not per call: the real bound is `timeout_s x (max_retries + 1)` plus backoff. The overall deadline is what actually caps a deliberation. |
| `max_retries` | Additional retries for 429, 5xx, and transport errors. |

The legacy `curator` role is stripped on load.

## Presets

```yaml
presets:
  quality:
    panel: [local, provider-b]
    analysis: local
```

Each preset must have a non-empty `panel` of 1-8 unique enabled panel providers.
`analysis`, when present, must reference an enabled judge provider. The legacy
`curation` key is ignored on load.

## Tool Arguments

`mandos(prompt, context, panel, preset, analysis_model, judge_shape, max_tokens,
temperature, reasoning_effort, timeout_s, thread_id, prior_answer,
use_conversation, depth)` builds a `DeliberateRequest`.

Resolution precedence is:

- `panel`: request `panel` -> preset `panel` -> all enabled panel providers
- `analysis_model`: request `analysis_model` -> preset `analysis` ->
  `defaults.analysis_model`. This selects the *generative analyst*.
- `judge_shape`: request `judge_shape` -> `judge.shape`. The right shape depends on
  the question rather than the installation — `probe` when the answer turns on a fact
  nobody has, `hybrid` when the models will genuinely differ — so a caller that knows
  which it is facing can say so. A caller may select `llm` and bypass Jev entirely,
  which means `judge.shape` is a default rather than a guarantee.
- execution knobs: request value -> matching default
- session mode: when `thread_id` is present, Mandos loads
  `~/.mandos/sessions/<thread_id>.json`, treats `prior_answer` as the previous
  host-authored assistant turn, and sends reconstructed `messages[]` to the panel.

The response always includes all successful `raw_answers`; there is no
`include_raw` gate. Session responses also echo `thread_id` and set `compacted`
when older history turns were omitted from the provider request.

## Validation

Config validation fails fast when:

- provider ids are duplicated
- no provider is enabled
- an enabled provider names an `api_key_env` whose env var is unset
- `defaults.analysis_model` or preset `analysis` is not an enabled judge provider
- the `judge` block itself is malformed (unknown shape or provider)
- a preset panel is empty, oversized, duplicated, unknown, or names a non-panel
  provider

A judge that cannot run as configured **warns** instead: a missing Jev key, or a
shape with no analyst behind it, loads fine and degrades at runtime with
`meta.judge_error`. Refusing to load would take the whole deliberation down to save
the analysis, which is the wrong trade.

Provider runtime failures are different again: they are recorded per panel answer and
do not crash the batch.

## Panel Tools

A provider may declare tools its **endpoint** executes:

```yaml
providers:
  - id: researcher
    kind: openrouter
    base_url: https://openrouter.ai/api/v1
    model: google/gemini-3-flash-preview
    tools:
      - { type: "openrouter:web_search" }
      - { type: "openrouter:web_fetch" }
    max_tool_calls: 4
```

They are passed through verbatim and the provider runs them, so there is no tool loop
in Mandos and no extra round trip: the panel member returns a finished answer that
already used the tool.

A client-executed `{"type": "function"}` tool is **refused at config load**. The model
would reply with `tool_calls` waiting for a result Mandos cannot supply, and running
such tools here would be worse than useless — the harness already has filesystem,
shell and database access behind a permission model that asks before it reads or runs
anything, and an MCP subprocess quietly executing tools to feed a third-party panel is
what that model exists to prevent. When a question depends on the user's files or data,
the **calling model** gathers it and passes it as `context`.

## Conversation Context

```yaml
context:
  from_transcript: true
  max_turns: 12
  max_chars: 24000
  max_age_s: 3600
```

An MCP server sees only its tool arguments, so the panel is normally briefed on
whatever the calling model retyped into `prompt` and `context`. A harness hook
(`scripts/hooks/capture_transcript.py`) closes that gap: on each user prompt it writes
the recent turns to `~/.mandos/context/<key>.json` and exits — no API call, nothing
blocking, no classifier deciding whether the turn "needs" a council. When the model
later chooses to convene one, the server reads that file and puts the conversation in
front of the question.

Install it in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command",
                  "command": "python3 ~/.mandos/hooks/capture_transcript.py"}]}
    ]
  }
}
```

Without the hook there is nothing to read and the setting does nothing. Per call,
`use_conversation: false` keeps one panel blind to it.

| Field | Description |
|---|---|
| `from_transcript` | Whether a capture is used at all. |
| `max_turns` / `max_chars` | Bounds; the budget is spent from the newest turn backwards. |
| `max_age_s` | A capture older than this belongs to a different task and is ignored. |

Credentials matching common key, token and JWT shapes are stripped **before** anything
is written, so a secret never reaches the file either. See `05-security.md` for what
this changes about egress.

## App Attribution

Calls to an OpenRouter endpoint carry `X-Title: Mandos` and an `HTTP-Referer`, so
OpenRouter's activity page attributes the spend instead of showing "Unknown". A
council call fans out to several models at once, so an unattributed panel reads as
unexplained spend from nowhere.

Only OpenRouter endpoints get them — a provider is treated as OpenRouter when its
`kind` is `openrouter` or its host is `openrouter.ai`. Sending a referer identifying
the caller to every configured host would leak which tool is calling to endpoints that
never asked for it.

Two ways to change it, neither of which needs a config change:

| Override | Effect |
|---|---|
| `MANDOS_APP_TITLE` / `MANDOS_APP_URL` | Rename globally. An empty value suppresses that one header. |
| `X-Title` in a provider's `headers` | Rename for that provider; wins over the environment. |

The Jev judge follows the same rule: labelled on `openrouter`, not on `typesafe`.

## Safe Status

`mandos_status()` returns defaults, the judge block, the context block, providers, presets, pricing,
`requires_secret` flags, advisory budget thresholds, and per-provider green/amber/red
budget estimates for the configured default output allowance. It never returns secret
values or `api_key_env` names — including the judge's, which is reported only as
`requires_secret` plus an `on-prem`/`off-prem` egress flag for the resolved Jev
endpoint.
