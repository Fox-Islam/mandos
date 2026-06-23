# Dependencies

Imladris is a Python 3.13 FastMCP stdio server with one outbound provider client:
the maintained OpenAI async SDK configured against OpenAI-compatible endpoints.

## Runtime Packages

| Package | Purpose |
|---|---|
| `fastmcp` | MCP server, tool, and prompt surface over stdio. |
| `openai` | Async OpenAI-compatible chat client for panel and judge calls. |
| `httpx` | Shared async transport underneath the OpenAI SDK. |
| `pydantic` | Request/response models and config validation. |
| `PyYAML` | YAML config parsing. |
| `structlog` | Structured logging, with secret redaction. |
| `rich` | CLI rendering support. |
| `textual` | Full-screen configurator TUI. |

No third-party dependency was added for the provider-catalog/budget/session
work: `orchestrator/model_catalog.py` (models.dev metadata fetch — data only,
never invoking npm — JSON cache, and an offline seed), `orchestrator/budget.py`
(advisory context-budget estimates), and `orchestrator/sessions.py` (local
council-session store) all build on the existing packages, reusing `httpx` for
the catalog fetch. The bundled seed `orchestrator/data/model_catalog_seed.json`
ships via `[tool.setuptools.package-data]` (`"orchestrator.data" = ["*.json"]`).

Retired dependencies include FastAPI, Uvicorn, Anthropic SDKs, cloud-vendor SDKs,
Docker/image tooling, and the curator/anonymization implementation.

## Runtime Calls

| Destination | When | Data |
|---|---|---|
| Panel providers | Concurrently, once per selected panel member | System prompt, user prompt, optional context, model knobs. |
| Analysis judge | After at least one panel member succeeds | Question plus all successful raw panel answers. |

All calls use configured `base_url`s and are outbound only. Provider failures are
recorded in the response.

## Dev/Test Packages

| Package | Purpose |
|---|---|
| `pytest` / `pytest-asyncio` | Unit and async tests. |
| `anyio` | Async I/O primitives required by `pytest-asyncio`. |
| `respx` | HTTP mocking for provider-client tests. |
| `coverage` | Coverage measurement. |
| `ruff` | Lint and format gate. |

The normal verification commands are:

```bash
ruff check orchestrator
ruff format --check orchestrator
pytest
```
