"""Wire the ``imladris`` MCP entry into each harness (plan §11, §12.2).

Every writer is idempotent, backs up an existing file first, and never clobbers
other MCP servers. Each launches ``imladris-mcp`` over stdio with ``IMLADRIS_CONFIG``
pointed at ``~/.imladris/config.json``.
"""

from __future__ import annotations

from orchestrator.cli.harness.claude_code import wire_claude_code
from orchestrator.cli.harness.codex import (
    codex_tool_output_token_limit,
    set_codex_tool_output_token_limit,
    wire_codex,
)
from orchestrator.cli.harness.common import IMLADRIS_CONFIG_VALUE, MCP_COMMAND
from orchestrator.cli.harness.opencode import wire_opencode

__all__ = [
    "IMLADRIS_CONFIG_VALUE",
    "MCP_COMMAND",
    "codex_tool_output_token_limit",
    "set_codex_tool_output_token_limit",
    "wire_claude_code",
    "wire_codex",
    "wire_opencode",
]
