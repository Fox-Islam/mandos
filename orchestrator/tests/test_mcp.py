"""Phase 6 — MCP surface: tool surface, secret-safe status, clean short-circuit."""

from __future__ import annotations

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from structlog.testing import capture_logs

from orchestrator.mcp_server import mcp


def _write_config(tmp_path, monkeypatch, providers, **extra):
    cfg = {"providers": providers, **extra}
    path = tmp_path / "imladris.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("IMLADRIS_CONFIG", str(path))
    return path


@pytest.mark.asyncio
async def test_tool_surface_and_council_prompt():
    async with Client(mcp) as client:
        tools = {t.name for t in await client.list_tools()}
        prompts = {p.name for p in await client.list_prompts()}
    assert {"imladris", "imladris_status", "imladris_clear_sessions"} <= tools
    assert "council" in prompts
    assert "council-session" in prompts


@pytest.mark.asyncio
async def test_status_leaks_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_SECRET_TOKEN", "topsecretvalue")
    _write_config(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "p",
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "roles": ["panel"],
                "api_key_env": "MY_SECRET_TOKEN",
            }
        ],
    )
    async with Client(mcp) as client:
        result = await client.call_tool("imladris_status", {})
    blob = json.dumps(result.data)
    assert "MY_SECRET_TOKEN" not in blob
    assert "topsecretvalue" not in blob
    assert result.data["providers"][0]["requires_secret"] is True


@pytest.mark.asyncio
async def test_imladris_returns_clean_short_circuit_when_unreachable(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "p",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "m",
                "roles": ["panel"],
                "max_retries": 0,
                "timeout_s": 5,
            }
        ],
    )
    async with Client(mcp) as client:
        result = await client.call_tool("imladris", {"prompt": "anything", "timeout_s": 5})
    data = result.data
    assert data["meta"]["ok"] == 0
    assert data["meta"]["failure"] == "all_panels_failed"
    assert data["meta"]["contract_version"] == "1"
    assert data["analysis"] is None
    assert isinstance(data["text"], str) and data["text"]


@pytest.mark.asyncio
async def test_input_schema_advertises_bounds_enums_descriptions():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    schema = tools["imladris"].inputSchema
    blob = json.dumps(schema)
    assert '"maxLength": 200000' in blob
    assert '"maxItems": 8' in blob
    assert '"maximum": 2' in blob
    assert all(level in blob for level in ('"low"', '"medium"', '"high"'))
    assert schema["properties"]["prompt"]["description"]
    assert schema["properties"]["analysis_model"]["description"]


@pytest.mark.asyncio
async def test_out_of_range_arg_rejected_at_protocol_boundary():
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("imladris", {"prompt": "x", "temperature": 9})


@pytest.mark.asyncio
async def test_status_discloses_egress_without_leaking_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_TOKEN", "supersecret")
    _write_config(
        tmp_path,
        monkeypatch,
        [
            {"id": "local", "base_url": "http://127.0.0.1/v1", "model": "m", "roles": ["panel"]},
            {
                "id": "cloud",
                "base_url": "https://api.vendor.com/v1",
                "model": "m",
                "roles": ["panel"],
                "api_key_env": "CLOUD_TOKEN",
            },
        ],
    )
    async with Client(mcp) as client:
        result = await client.call_tool("imladris_status", {})
    egress = {p["id"]: p["egress"] for p in result.data["providers"]}
    assert egress == {"local": "on-prem", "cloud": "off-prem"}
    blob = json.dumps(result.data)
    assert "CLOUD_TOKEN" not in blob and "supersecret" not in blob


@pytest.mark.asyncio
async def test_invalid_thread_id_returns_typed_envelope_not_toolerror(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        [{"id": "p", "base_url": "http://127.0.0.1:9/v1", "model": "m", "roles": ["panel"]}],
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "imladris", {"prompt": "anything", "thread_id": "bad/thread"}
        )
    data = result.data
    assert data["meta"]["failure"] == "unexpected_error"
    assert data["meta"]["contract_version"] == "1"
    assert isinstance(data["text"], str) and data["text"]


@pytest.mark.asyncio
async def test_config_load_error_does_not_leak_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("UNSET_PROVIDER_SECRET", raising=False)
    _write_config(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "p",
                "base_url": "https://api.vendor.com/v1",
                "model": "m",
                "roles": ["panel"],
                "api_key_env": "UNSET_PROVIDER_SECRET",
                "headers": {"Authorization": "Bearer sk-LEAKED-HEADER-VALUE"},
            }
        ],
    )
    with capture_logs() as logs:
        async with Client(mcp) as client:
            result = await client.call_tool("imladris", {"prompt": "anything"})
    data = result.data
    assert data["meta"]["failure"] == "unexpected_error"
    assert isinstance(data["text"], str) and data["text"]
    leaks = ("UNSET_PROVIDER_SECRET", "sk-LEAKED-HEADER-VALUE")
    response_blob = json.dumps(data)
    log_blob = json.dumps(logs, default=str)
    for secret in leaks:
        assert secret not in response_blob
        assert secret not in log_blob


@pytest.mark.asyncio
async def test_status_config_load_error_does_not_leak_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("UNSET_PROVIDER_SECRET", raising=False)
    _write_config(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "p",
                "base_url": "https://api.vendor.com/v1",
                "model": "m",
                "roles": ["panel"],
                "api_key_env": "UNSET_PROVIDER_SECRET",
                "headers": {"Authorization": "Bearer sk-LEAKED-HEADER-VALUE"},
            }
        ],
    )
    async with Client(mcp) as client:
        result = await client.call_tool("imladris_status", {})

    data = result.data
    assert data["ok"] is False
    blob = json.dumps(data)
    for secret in ("UNSET_PROVIDER_SECRET", "sk-LEAKED-HEADER-VALUE"):
        assert secret not in blob


@pytest.mark.asyncio
async def test_council_session_prompt_states_thread_id_format():
    async with Client(mcp) as client:
        result = await client.get_prompt("council-session", {"thread_id": "t1", "question": "q"})
    assert "[A-Za-z0-9_-]{1,64}" in result.messages[0].content.text
