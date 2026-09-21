"""Harness wiring (idempotent, backup-first, never-clobber)."""

from __future__ import annotations

import json
import tomllib

from orchestrator.cli.harness import (
    codex_tool_output_token_limit,
    set_codex_tool_output_token_limit,
    wire_claude_code,
    wire_codex,
    wire_opencode,
)


def test_wire_claude_code_global(tmp_path):
    path = wire_claude_code(tmp_path, "global")
    assert path == tmp_path / ".claude.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["mandos"]["command"] == "mandos-mcp"
    assert data["mcpServers"]["mandos"]["env"]["MANDOS_CONFIG"] == "~/.mandos/config.json"


def test_wire_claude_code_preserves_other_servers_and_backs_up(tmp_path):
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "numWindows": 3}), encoding="utf-8"
    )
    wire_claude_code(tmp_path, "global")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"]["command"] == "x"
    assert data["numWindows"] == 3
    assert "mandos" in data["mcpServers"]
    assert (tmp_path / ".claude.json.bak").exists()


def test_wire_claude_code_is_idempotent(tmp_path):
    wire_claude_code(tmp_path, "global")
    first = (tmp_path / ".claude.json").read_text(encoding="utf-8")
    wire_claude_code(tmp_path, "global")
    assert (tmp_path / ".claude.json").read_text(encoding="utf-8") == first


def test_wire_claude_code_workspace(tmp_path):
    path = wire_claude_code(tmp_path, "workspace")
    assert path == tmp_path / ".mcp.json"
    assert "mandos" in json.loads(path.read_text(encoding="utf-8"))["mcpServers"]


def test_wire_codex_creates_table(tmp_path):
    path = wire_codex(tmp_path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["mcp_servers"]["mandos"]["command"] == "mandos-mcp"
    assert data["mcp_servers"]["mandos"]["env"]["MANDOS_CONFIG"] == "~/.mandos/config.json"


def test_wire_codex_preserves_existing_and_is_idempotent(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other-mcp"\n', encoding="utf-8"
    )
    wire_codex(tmp_path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "gpt-5"
    assert data["mcp_servers"]["other"]["command"] == "other-mcp"
    assert data["mcp_servers"]["mandos"]["command"] == "mandos-mcp"
    after_first = path.read_text(encoding="utf-8")
    wire_codex(tmp_path)
    assert path.read_text(encoding="utf-8") == after_first


def test_set_codex_tool_output_token_limit_preserves_existing_tables(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other-mcp"\n',
        encoding="utf-8",
    )

    set_codex_tool_output_token_limit(tmp_path, 128000)

    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["tool_output_token_limit"] == 128000
    assert data["model"] == "gpt-5"
    assert data["mcp_servers"]["other"]["command"] == "other-mcp"
    assert text.index("tool_output_token_limit") < text.index("[mcp_servers.other]")
    assert codex_tool_output_token_limit(tmp_path) == 128000


def test_set_codex_tool_output_token_limit_updates_existing_value(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("tool_output_token_limit = 1600\n", encoding="utf-8")

    set_codex_tool_output_token_limit(tmp_path, 32000)

    assert tomllib.loads(path.read_text(encoding="utf-8"))["tool_output_token_limit"] == 32000


def test_wire_opencode(tmp_path):
    path = wire_opencode(tmp_path)
    assert path == tmp_path / ".config" / "opencode" / "opencode.json"
    entry = json.loads(path.read_text(encoding="utf-8"))["mcp"]["mandos"]
    assert entry["type"] == "local"
    assert entry["command"] == ["mandos-mcp"]
    assert entry["environment"]["MANDOS_CONFIG"] == "~/.mandos/config.json"
