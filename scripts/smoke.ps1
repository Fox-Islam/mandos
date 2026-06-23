#!/usr/bin/env pwsh
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$py = if ($env:PYTHON) { $env:PYTHON } else { Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Get-Command $py -ErrorAction SilentlyContinue)) {
    $windowsPy = Join-Path $root '.venv\Scripts\python.exe'
    $posixPy = Join-Path $root '.venv/bin/python'
    $py = if (Test-Path $windowsPy) { $windowsPy } else { $posixPy }
}

Set-Location $root
$code = @'
import sys

import anyio
from fastmcp import Client


async def main() -> None:
    config = {
        "mcpServers": {
            "imladris": {"command": sys.executable, "args": ["-m", "orchestrator.mcp_server"]}
        }
    }
    async with Client(config) as client:
        names = sorted(t.name for t in await client.list_tools())
    print("tools:", names)
    assert {"imladris", "imladris_status"} <= set(names), names
    print("stdio MCP surface OK")


anyio.run(main)
'@
$code | & $py -
