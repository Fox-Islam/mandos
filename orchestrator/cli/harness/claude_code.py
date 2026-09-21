"""Wire Claude Code: ``mcpServers.mandos`` in ``~/.claude.json`` (global) or a
project ``.mcp.json`` (workspace)"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from orchestrator.cli.harness.common import MANDOS_CONFIG_VALUE, MCP_COMMAND, merge_json

Scope = Literal["global", "workspace"]


def claude_code_path(base: Path, scope: Scope) -> Path:
    base = Path(base).expanduser()
    return base / ".claude.json" if scope == "global" else base / ".mcp.json"


def wire_claude_code(base: Path, scope: Scope = "global") -> Path:
    """Merge the mandos MCP entry into Claude Code config.

    ``base`` is the home directory for ``global`` scope, or the project root for
    ``workspace`` scope.
    """
    path = claude_code_path(base, scope)

    def mutate(data: dict) -> None:
        servers = data.setdefault("mcpServers", {})
        servers["mandos"] = {
            "command": MCP_COMMAND,
            "env": {"MANDOS_CONFIG": MANDOS_CONFIG_VALUE},
        }

    return merge_json(path, mutate)
