"""Shared constants + helpers for harness writers."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

MCP_COMMAND = "imladris-mcp"
IMLADRIS_CONFIG_VALUE = "~/.imladris/config.json"


def backup(path: Path) -> Path | None:
    """Copy ``path`` to ``path.bak`` if it exists; return the backup path or None."""
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        return bak
    return None


def merge_json(path: Path, mutate: Callable[[dict], None]) -> Path:
    """Load JSON (or {}), apply ``mutate`` in place, back up, write pretty JSON.

    Preserves all unrelated keys; safe to re-run (idempotent when ``mutate`` is).
    """
    data: dict = {}
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
        backup(path)
    mutate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
