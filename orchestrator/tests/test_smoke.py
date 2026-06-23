"""Smoke tests: package import + the MCP tool surface over an in-memory Client.

There is no REST app; the only transport is stdio (plan §6). We assert the tool
surface through FastMCP's in-memory ``Client`` rather than spinning up a process.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

import orchestrator
from orchestrator.mcp_server import mcp


def test_package_imports():
    assert orchestrator.__version__


@pytest.mark.asyncio
async def test_mcp_tool_surface():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert {"imladris", "imladris_status"} <= names
