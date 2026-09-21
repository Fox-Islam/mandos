"""Wire the ``mandos`` MCP entry into each harness (plan §11, §12.2).

Every writer is idempotent, backs up an existing file first, and never clobbers
other MCP servers. Each launches ``mandos-mcp`` over stdio with ``MANDOS_CONFIG``
pointed at ``~/.mandos/config.json``.
"""

from __future__ import annotations

from orchestrator.cli.harness.claude_code import wire_claude_code
from orchestrator.cli.harness.codex import (
    codex_tool_output_token_limit,
    set_codex_tool_output_token_limit,
    wire_codex,
)
from orchestrator.cli.harness.common import MANDOS_CONFIG_VALUE, MCP_COMMAND
from orchestrator.cli.harness.opencode import wire_opencode

__all__ = [
    "MANDOS_CONFIG_VALUE",
    "MCP_COMMAND",
    "codex_tool_output_token_limit",
    "set_codex_tool_output_token_limit",
    "wire_claude_code",
    "wire_codex",
    "wire_opencode",
]
