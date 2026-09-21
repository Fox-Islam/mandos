# Third-Party Notices

This file records the third-party components intentionally referenced by the
Mandos source distribution. It is engineering inventory, not legal advice. The
repository does not vendor Python wheels or model weights; distributors should
attach their own generated SBOM for the exact artifacts they ship.

## Python Packages

| Package | Purpose | License |
|---|---|---|
| fastmcp | MCP server SDK (stdio) | Apache-2.0 |
| openai | OpenAI-compatible async chat client (panel providers + analysis judge) | Apache-2.0 |
| httpx | async HTTP client: the pooled client every stage shares, the Jev client, and the model-catalog fetch | BSD-3-Clause |
| pydantic | typed models + config validation | MIT |
| PyYAML | YAML provider-list parsing | MIT |
| structlog | structured logging | Apache-2.0 / MIT |
| rich | terminal formatting and Textual dependency | MIT |
| textual | full-screen configurator TUI | MIT |
| pytest / respx / coverage / ruff | tests + lint (dev) | MIT / BSD |

## Provider Endpoints

Mandos calls operator-configured HTTP endpoints (local vLLM, OpenRouter,
MiniMax, DeepSeek, …) over the OpenAI-compatible `/v1/chat/completions` API. It
does not vendor or redistribute those services or their models; operators are
responsible for honouring each provider's terms and each model's license.

## Decision Service

The judge is **Jev**, TypeSafe's System One decision model, reached at
`api.typesafe.ai/v1/systemone` or, via OpenRouter, `openrouter.ai/api/alpha/decisions`.
Mandos implements the wire format and ships no part of the service or its model; use is
governed by whichever provider's terms the operator keys.

## Model Metadata

`orchestrator/model_catalog.py` fetches context-window and pricing metadata from
[models.dev](https://models.dev) (`https://models.dev/api.json`) and caches it locally.
A seed snapshot is bundled at `orchestrator/data/model_catalog_seed.json` so runtime
deliberation never requires the fetch. Refresh is an explicit configurator action.
