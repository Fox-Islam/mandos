#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PY="${PYTHON:-${ROOT_DIR}/.venv/bin/python}"
if ! command -v "$PY" >/dev/null 2>&1; then
    if [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
        PY="${ROOT_DIR}/.venv/bin/python"
    else
        PY="${ROOT_DIR}/.venv/Scripts/python.exe"
    fi
fi

cd "$ROOT_DIR"
"$PY" - <<'PYEOF'
import sys

import anyio
from fastmcp import Client


async def main() -> None:
    config = {
        "mcpServers": {
            "mandos": {"command": sys.executable, "args": ["-m", "orchestrator.mcp_server"]}
        }
    }
    async with Client(config) as client:
        names = sorted(t.name for t in await client.list_tools())
    print("tools:", names)
    assert {"mandos", "mandos_status"} <= set(names), names
    print("stdio MCP surface OK")


anyio.run(main)
PYEOF
