#!/usr/bin/env python3
"""UserPromptSubmit hook: leave the recent conversation where Mandos can find it.

An MCP server sees only its tool arguments, so a council convened mid-conversation is
briefed on whatever the calling model retyped into ``prompt`` and ``context``. This
hook closes that gap from the harness side.

It does the cheap half of the job and nothing else: read the transcript the harness
already wrote, strip anything that looks like a credential, keep the last few turns,
write them to ``~/.mandos/context/<key>.json`` and exit. No API call, no classifier
deciding whether the turn "needs" a council, no blocking. The model still decides when
to deliberate; when it does, the server reads this file.

Install (Claude Code, ``~/.claude/settings.json``)::

    {
      "hooks": {
        "UserPromptSubmit": [
          {"hooks": [{"type": "command",
                      "command": "python3 ~/.mandos/hooks/capture_transcript.py"}]}
        ]
      }
    }

Exits 0 whatever happens. A hook that fails must never cost someone their turn.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_TURNS = 12
MAX_CHARS = 24_000


def _load_orchestrator():
    """Prefer the installed package; fall back to a checkout next to this file."""
    try:
        from orchestrator import transcript  # noqa: PLC0415
    except ImportError:
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root))
        from orchestrator import transcript  # noqa: PLC0415
    return transcript


def _turns_from_jsonl(path: Path, limit: int) -> list[dict[str, str]]:
    """Read the tail of a harness transcript into plain role/content turns.

    Tolerant by design: transcript formats differ between harnesses and change between
    versions, so anything unrecognised is skipped rather than guessed at.
    """
    turns: list[dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines[-(limit * 8) :]:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _text_of(message.get("content"))
        if text:
            turns.append({"role": role, "content": text})
    return turns[-limit:]


def _text_of(content: object) -> str:
    """Flatten a message body to text, ignoring tool calls and images."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]
    return "\n".join(parts).strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(payload, dict):
        return 0

    transcript = _load_orchestrator()
    turns = []
    path = payload.get("transcript_path")
    if isinstance(path, str) and path:
        turns = _turns_from_jsonl(Path(path).expanduser(), MAX_TURNS)

    # The prompt being submitted is not in the transcript yet; it is the most relevant
    # turn there is.
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        turns.append({"role": "user", "content": prompt.strip()})
    if not turns:
        return 0

    try:
        transcript.write_capture(
            turns,
            key=transcript.capture_key(
                cwd=payload.get("cwd"), session_id=payload.get("session_id")
            ),
            max_turns=MAX_TURNS,
            max_chars=MAX_CHARS,
        )
    except Exception:  # noqa: BLE001
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
