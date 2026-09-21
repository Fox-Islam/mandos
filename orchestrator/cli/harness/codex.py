"""Wire Codex: ``[mcp_servers.mandos]`` in ``~/.codex/config.toml``

No stdlib TOML *writer* exists, and a general re-serialiser would risk mangling
unrelated Codex settings. So this splices only our own table textually: every other
byte of the file is preserved verbatim.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from orchestrator.cli.harness.common import MANDOS_CONFIG_VALUE, MCP_COMMAND, backup

_HEADER = "[mcp_servers.mandos]"
_TOOL_OUTPUT_TOKEN_LIMIT = "tool_output_token_limit"


def codex_path(home: Path) -> Path:
    return Path(home).expanduser() / ".codex" / "config.toml"


def codex_tool_output_token_limit(home: Path) -> int | None:
    path = codex_path(home)
    if not path.exists():
        return None
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8")).get(_TOOL_OUTPUT_TOKEN_LIMIT)
    except tomllib.TOMLDecodeError:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def set_codex_tool_output_token_limit(home: Path, limit: int) -> Path:
    if limit < 1:
        raise ValueError("tool_output_token_limit must be a positive integer")

    path = codex_path(home)
    if path.exists():
        backup(path)
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []

    rendered = f"{_TOOL_OUTPUT_TOKEN_LIMIT} = {limit}"
    first_table = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
    )
    for index in range(first_table):
        if lines[index].partition("=")[0].strip() == _TOOL_OUTPUT_TOKEN_LIMIT:
            lines[index] = rendered
            break
    else:
        lines.insert(first_table, rendered)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _block() -> list[str]:
    return [
        _HEADER,
        f'command = "{MCP_COMMAND}"',
        f'env = {{ MANDOS_CONFIG = "{MANDOS_CONFIG_VALUE}" }}',
    ]


def wire_codex(home: Path) -> Path:
    path = codex_path(home)
    block = _block()

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(block) + "\n", encoding="utf-8")
        return path

    backup(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == _HEADER), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
    else:
        end = start + 1
        while end < len(lines) and not lines[end].lstrip().startswith("["):
            end += 1
        lines[start:end] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
