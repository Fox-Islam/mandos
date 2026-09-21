# Dependencies

Mandos is a Python 3.13 FastMCP stdio server with two outbound clients: the
maintained OpenAI async SDK for chat providers, and a small in-tree client for Jev.

There is no Python SDK for Jev. `phox/typesafe-sdk-php` is the reference for the wire
format, not a dependency — `orchestrator/jev/` implements the one POST it needs over
the `httpx` client the pipeline already pools, which is also what lets the judge call
reuse a warm connection.

## Runtime Packages

| Package | Purpose |
|---|---|
| `fastmcp` | MCP server, tool, and prompt surface over stdio. |
| `openai` | Async OpenAI-compatible chat client for panel and judge calls. |
| `httpx` | Shared async transport, under the OpenAI SDK and the Jev client alike. |
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
| Generative analyst | For shapes `hybrid`, `verify`, `llm`, and on any Jev fallback | Question, context, and all successful raw panel answers. |
| Jev | For shapes `hybrid`, `matrix`, `verify` | A `state` of question + context + answers by provider id, and a batch of named questions. One call unless the batch exceeds `judge.questions_per_call`. |

All calls use configured `base_url`s and are outbound only. Provider failures are
recorded in the response.

## Dev/Test Packages

| Package | Purpose |
|---|---|
| `pytest` / `pytest-asyncio` | Unit and async tests. |
| `anyio` | Async I/O primitives required by `pytest-asyncio`. |
| `respx` | HTTP mocking for the provider and Jev client tests. |
| `coverage` | Coverage measurement. |
| `ruff` | Lint and format gate. |

No test reaches the network. `orchestrator/fakes.py` provides `FakeChatProvider` and
`FakeJevClient`; the latter answers every question in the shape that question asked
for, identically on every run, mirroring `FakeTypeSafe` in the PHP SDK.

The normal verification commands are:

```bash
ruff check orchestrator
ruff format --check orchestrator
pytest
```
