# Dependencies

Mandos is a Python 3.13 FastMCP stdio server with two outbound clients: the
maintained OpenAI async SDK for chat providers, and a small in-tree client for Jev.

TypeSafe ships an official Python SDK (`typesafe-sdk`). Mandos does not take it as a
dependency: the judge needs one POST of `{state, model, questions}`, and issuing it
over the `httpx` client the pipeline already pools is what lets the judge call reuse a
warm connection instead of opening its own. `orchestrator/jev/` is that client;
`phox/typesafe-sdk-php` was the reference for the wire format.

The trade-off is ours to maintain: a wire-format change reaches us as a bug instead of
as a version bump. Adopting the SDK would invert that, at the cost of a second
connection pool and its release cadence - it took a breaking serialization change in
0.7.0.

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
work: `orchestrator/model_catalog.py` (models.dev metadata fetch - data only,
never invoking npm - JSON cache, and an offline seed), `orchestrator/budget.py`
(advisory context-budget estimates), and `orchestrator/sessions.py` (local
council-session store) all build on the existing packages, reusing `httpx` for
the catalog fetch. The bundled seed `orchestrator/data/model_catalog_seed.json`
ships via `[tool.setuptools.package-data]` (`"orchestrator.data" = ["*.json"]`).

Retired dependencies include FastAPI, Uvicorn, Anthropic SDKs, cloud-vendor SDKs,
Docker/image tooling, and the curator/anonymization implementation.

## Runtime Calls

| Destination | When | Data |
|---|---|---|
| Panel providers | Concurrently, once per selected panel member | System prompt, the captured conversation when enabled, user prompt, optional context, model knobs, and any provider-executed tools declared. |
| Generative analyst | For shapes `hybrid`, `verify`, `probe`, `llm`, and on any Jev fallback | Question, context, and all successful raw panel answers. |
| Jev | For shapes `hybrid`, `matrix`, `verify`, `probe` | A `state` of question + context + answers by provider id, and a batch of named questions. One call unless the batch exceeds `judge.questions_per_call`. |

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

`pytest` is offline. The live fixture in `orchestrator/tests/test_jev_live.py` runs
only under `pytest -m live` and needs a Jev key; it pins the judge's reading of a
fixed panel, including the support threshold that a live measurement forced to
move. Nothing offline can catch that regressing, because the fake answers whatever
it is told to.
