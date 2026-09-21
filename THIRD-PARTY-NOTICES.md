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
| httpx | async HTTP client underlying the openai SDK | BSD-3-Clause |
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
