# Security Architecture

Mandos runs as a local stdio subprocess spawned by the harness. It has no
network listener. Its only network traffic is outbound HTTPS to configured
OpenAI-compatible provider `base_url`s and to the configured Jev endpoint.

## Secrets

Provider **and Jev** credentials are env-only:

- Config stores `api_key_env`, the name of the environment variable. For the judge,
  leaving it unset resolves to the provider's own variable (`TYPESAFE_API_KEY` or
  `OPENROUTER_API_KEY`) — still a name, never a value.
- Secret values live in `~/.mandos/.env` or the existing process environment.
- `load_env_file()` sets unset variables only.
- `mandos_status()` strips `api_key_env` from providers and from the judge, returning
  only `requires_secret`.
- Jev error bodies are truncated to 200 characters before they reach a log or a
  result, because an error body can echo the state that was sent.

Never put key values in `config.json`, docs, logs, or tests.

## Data Egress

The egress boundary is the configured provider roster:

- Every non-local panel provider receives the full prompt and optional `CONTEXT:`
  block.
- In session mode, panel providers receive reconstructed prior user/assistant turns
  from `~/.mandos/sessions/<thread_id>.json`, so a session sends more off-prem each
  turn than a one-shot. The session file stays local; only an all-local panel keeps the
  history fully on-prem.
- The judge receives the question, the `CONTEXT:` block, and every successful raw
  panel answer. This is true of the generative analyst and of Jev alike: a Jev call's
  `state` carries the question, the context and every answer keyed by provider id.
- The response returns all successful raw panel answers to the harness.

**The Jev judge is an egress destination.** `api.typesafe.ai` and `openrouter.ai` are
off-prem, so a Jev shape sends the whole deliberation off-prem even when every panel
member is local. For a deliberation that must stay on the network, either set
`judge.base_url` to a self-hosted Jev endpoint or set `judge.shape: "llm"` with a
local analyst. `mandos_status()` and `mandos doctor` both report the judge's resolved
endpoint as `on-prem` or `off-prem` so this is checkable rather than assumed.

For sensitive prompts, use an all-local panel, an on-prem or local judge, and verify
the resolved config with `mandos_status`.

## App Attribution

OpenRouter endpoints receive `X-Title: Mandos` and an `HTTP-Referer` pointing at the
project, so spend is attributed rather than showing as "Unknown". No other host
receives either header, and neither carries anything about the user, the prompt or the
configuration. `MANDOS_APP_TITLE=""` and `MANDOS_APP_URL=""` suppress them.

## Conversation Capture

With the capture hook installed and `context.from_transcript` on (the default), panel
providers receive the recent conversation, not only what the calling model retyped.

This is the same data they already received, filtered differently: previously the
calling model decided what mattered, now the last N turns go whole. What that adds is
completeness **without judgement** — a key pasted twenty turns ago, an unrelated
tangent, a file read for another task. Three things bound it:

- Credentials matching common key, token, JWT and `SECRET=`/`Bearer` shapes are
  stripped when the capture is *written*, so a secret never reaches disk either.
- `max_turns` and `max_chars` cap what is kept, newest first.
- A capture older than `max_age_s` (default one hour) is ignored as belonging to a
  different task.

The capture lives at `~/.mandos/context/<key>.json`, mode `0600`, keyed by harness
session or working directory so two projects never read each other's conversation. Set
`context.from_transcript: false` to disable, or pass `use_conversation: false` for one
call. `mandos_status` and `mandos doctor` both report whether it is on.

**If the panel is off-prem, the conversation is off-prem.** For an all-local panel it
never leaves the network; for anything else, treat it as you would the prompt.

## Panel Tools

A provider may declare tools its own endpoint executes (`openrouter:web_search` and
similar). Mandos passes them through and never runs one itself: a client-executed
`function` tool is refused at config load.

That refusal is deliberate. The harness holds filesystem, shell and database access
behind a permission model that asks before it reads or runs anything. An MCP subprocess
executing tools on its own, to feed answers to a third-party panel, would route around
that model entirely. Local data reaches the panel only by the calling model gathering
it and passing it as `context`, where the harness's own permission prompts still apply.

## Session Files

Council sessions are stored locally under `~/.mandos/sessions/` using validated
`thread_id` filenames (`^[A-Za-z0-9_-]{1,64}$`, path-contained), atomic writes, a
per-thread lock, and restrictive `0600` permissions. The files are kept until
explicitly cleared with `mandos_clear_sessions`, or until they go untouched for
`defaults.session_max_age_days` (default 30) and are pruned on the next session
write. Set it to `0` to keep them indefinitely.

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
| `use_conversation` | `true`, `false`, or unset to follow config |
| `depth` | `>= 0` |

Provider failures are data, not thrown batch errors. If all panel members fail,
the response carries `meta.failure = all_panels_failed`.

## Recursion Guard

`run_deliberation()` checks `request.depth >= defaults.max_depth` and returns
`fusion_invocation_capped` before fan-out. `depth` is a tool argument, so a caller
chaining one deliberation into another passes `depth + 1` and the chain is capped. An
ordinary call leaves it at 0 and the guard never fires — panellists are plain chat
completions and cannot re-enter on their own.

## Operator Responsibilities

- Keep `.env` mode restrictive and out of version control.
- Restrict outbound connectivity at the host or firewall layer when required.
- Treat provider outputs as untrusted external text. This includes anything the judge
  derives from them: a claim string in `analysis` originated in a provider's answer.
- Prefer local-only presets and an on-prem judge for confidential prompts.
