# Security Architecture

Imladris runs as a local stdio subprocess spawned by the harness. It has no
network listener. Its only network traffic is outbound HTTPS to configured
OpenAI-compatible provider `base_url`s.

## Secrets

Provider credentials are env-only:

- Config stores `api_key_env`, the name of the environment variable.
- Secret values live in `~/.imladris/.env` or the existing process environment.
- `load_env_file()` sets unset variables only.
- `imladris_status()` strips `api_key_env` and returns only `requires_secret`.

Never put provider key values in `config.json`, docs, logs, or tests.

## Data Egress

The egress boundary is the configured provider roster:

- Every non-local panel provider receives the full prompt and optional `CONTEXT:`
  block.
- In session mode, panel providers receive reconstructed prior user/assistant turns
  from `~/.imladris/sessions/<thread_id>.json`, so a session sends more off-prem each
  turn than a one-shot. The session file stays local; only an all-local panel keeps the
  history fully on-prem.
- The analysis judge receives the question plus every successful raw panel answer.
- The response returns all successful raw panel answers to the harness.

For sensitive prompts, use an all-local panel and local analysis judge, and verify
the resolved config with `imladris_status`.

## Session Files

Council sessions are stored locally under `~/.imladris/sessions/` using validated
`thread_id` filenames (`^[A-Za-z0-9_-]{1,64}$`, path-contained), atomic writes, a
per-thread lock, and restrictive `0600` permissions. The files are kept until
explicitly cleared with `imladris_clear_sessions`; there is no auto-prune.

Store failures degrade rather than crash: an unreadable or corrupt session starts from
empty history (turn 1) and the response still returns; an unwritable session returns
the response with `meta.session_warning` but does not persist the turn (continuity is
not durable).

## Input Bounds

`DeliberateRequest` bounds caller inputs:

| Field | Constraint |
|---|---|
| `prompt` | 1 to 200,000 chars |
| `context` | 0 to 200,000 chars when supplied |
| `prior_answer` | 0 to 100,000 chars when supplied |
| `panel` | 1 to 8 members |
| `max_tokens` | `>= 1` when supplied |
| `temperature` | 0 to 2 |
| `timeout_s` | `> 0` when supplied |
| `reasoning_effort` | `low`, `medium`, or `high` |
| `thread_id` | `^[A-Za-z0-9_-]{1,64}$` when supplied |

Provider failures are data, not thrown batch errors. If all panel members fail,
the response carries `meta.failure = all_panels_failed`.

## Recursion Guard

`run_deliberation()` checks `request.depth >= defaults.max_depth` and returns
`fusion_invocation_capped` before fan-out. In normal stdio use the tool does not
receive inbound depth, so this mainly protects Imladris-as-panel-member setups.

## Operator Responsibilities

- Keep `.env` mode restrictive and out of version control.
- Restrict outbound connectivity at the host or firewall layer when required.
- Treat provider outputs as untrusted external text.
- Prefer local-only presets for confidential prompts.
