# Security Policy

Mandos is intended for self-hosted use. It runs as a single MCP **stdio**
subprocess spawned by the harness — there is **no HTTP server, no exposed port,
and no bearer token**. The only network activity is outbound HTTPS to the
provider `base_url`s in your config. This posture is simpler and stronger than a
network-listening service.

## Supported Versions

Security fixes target the default branch until tagged releases exist.

## Reporting a Vulnerability

Open a private security advisory in the hosting platform if available. If not,
contact the maintainer privately before opening a public issue. Include:

- affected tool or component
- reproduction steps
- expected impact
- whether the issue requires non-default configuration

Do not include live secrets, private URLs, or third-party data in reports.

## Known Security Boundaries

- **Secrets** are read from environment variables referenced by name
  (`api_key_env`) in config, stored in `~/.mandos/.env` (0600) and loaded at
  startup; they are redacted in logs and never returned by `mandos_status`.
  Never commit real config or `.env`.
- **Panel prompts leave your network** for any non-local provider. The `local-only`
  preset keeps every token on-prem; `mandos_status` reports which providers are
  off-prem. Document exactly which providers receive prompt data.
- **Egress:** the server only contacts the `base_url`s in config. Lock these down
  at the firewall; the server does not sandbox provider endpoints.
- **Panel and judge answers are untrusted external text.** A compromised
  or adversarial provider can return prompt-injection payloads; the authoring
  (native) model must treat returned content as untrusted data.
- **Council sessions persist history locally.** Session mode writes
  `~/.mandos/sessions/<thread_id>.json` (0600); the `thread_id` is charset-validated
  and path-contained under the sessions directory. The file stays on-prem, but its
  contents are re-sent to the configured panel providers each turn — only an all-local
  panel keeps session history fully on-prem. Clear sessions with
  `mandos clear-sessions [thread_id]` (or the `mandos_clear_sessions` tool); the
  engine never auto-prunes.
- **Input bounds.** There is no built-in per-client rate limiting. Cap input
  sizes, `max_tokens`, and panel size (1–8) at the configuration boundary.
- A recursion depth guard (`max_depth`) prevents an `mandos` call from
  re-entering itself when a panellist is itself an Mandos instance.
