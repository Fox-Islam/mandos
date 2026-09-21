"""Wire OpenCode: the ``mandos`` server in ``~/.config/opencode/opencode.json``
(stdio / ``type: local``) — plan §11.3. This writer is authoritative on key names."""

from __future__ import annotations

from pathlib import Path

from orchestrator.cli.harness.common import MANDOS_CONFIG_VALUE, MCP_COMMAND, merge_json


def opencode_path(home: Path) -> Path:
    return Path(home).expanduser() / ".config" / "opencode" / "opencode.json"


def wire_opencode(home: Path) -> Path:
    path = opencode_path(home)

    def mutate(data: dict) -> None:
        servers = data.setdefault("mcp", {})
        servers["mandos"] = {
            "type": "local",
            "command": [MCP_COMMAND],
            "environment": {"MANDOS_CONFIG": MANDOS_CONFIG_VALUE},
        }

    return merge_json(path, mutate)
