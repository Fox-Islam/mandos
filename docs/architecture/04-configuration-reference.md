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
| `timeout_s` | Per-attempt timeout, bounded by the overall deadline. |
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
- `timeout_s` is the overall call deadline, shared by panel, analyst and Jev.
- `max_depth` is the recursion guard threshold.
- `max_tokens` and `temperature` are sent to panel providers unless overridden.
- `analysis_max_tokens` is the output ceiling for the analyst, which summarises the
  whole panel and so needs more room than any single panellist.
- `budget_warning_ratio` and `budget_error_ratio` drive advisory context-budget
  green/amber/red status.
- `session_max_turns` is the maximum stored history turns sent to session panel
  members before older turns are compacted.

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
| `headers` | Extra request headers. |
| `timeout_s` | Per-provider timeout, bounded by the overall deadline. |
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

`mandos(prompt, context, panel, preset, analysis_model, max_tokens,
temperature, reasoning_effort, timeout_s, thread_id, prior_answer)` builds a
`DeliberateRequest`.

Resolution precedence is:

- `panel`: request `panel` -> preset `panel` -> all enabled panel providers
- `analysis_model`: request `analysis_model` -> preset `analysis` ->
  `defaults.analysis_model`. This selects the *generative analyst*; the judge shape
  itself is config-only.
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

## Safe Status

`mandos_status()` returns defaults, the judge block, providers, presets, pricing,
`requires_secret` flags, advisory budget thresholds, and per-provider green/amber/red
budget estimates for the configured default output allowance. It never returns secret
values or `api_key_env` names — including the judge's, which is reported only as
`requires_secret` plus an `on-prem`/`off-prem` egress flag for the resolved Jev
endpoint.
