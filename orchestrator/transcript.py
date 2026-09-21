"""Conversation capture: what the panel is told about the discussion so far.

An MCP server sees only its tool arguments. Everything the panel knows about the
conversation is whatever the calling model chose to retype into ``prompt`` and
``context`` - a summary, written under time pressure, of a conversation the harness is
already holding in full.

A harness hook can close that gap without the server ever reaching into the harness. A
``UserPromptSubmit`` hook writes the recent turns here and exits; when the model later
chooses to convene a council, the server reads the file. Nothing blocks, nothing is
classified, no API call happens on an ordinary turn, and control stays where it was -
the model still decides when to deliberate.

**What this changes.** The panel already receives the conversation, filtered through
the calling model's judgement about what matters. This sends it unfiltered, so it also
sends what the model would have left out: a key pasted twenty turns ago, an unrelated
tangent, a file read for a different task. That is why capture is bounded by turn count
and size, why obvious credentials are stripped before anything is stored, and why
``mandos_status`` and ``mandos doctor`` both report whether it is on.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

DEFAULT_CONTEXT_DIR = "~/.mandos/context"

# Beyond this the capture is stale enough that it probably belongs to a different task.
DEFAULT_MAX_AGE_S = 3600.0

# Patterns that look like credentials wherever they appear. Broad: a false
# positive costs the panel one opaque string, a false negative ships a live key to a
# third party.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "[redacted-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "[redacted-token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-key]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "[redacted-jwt]",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)"
            r"\s*[:=]\s*[\"']?([^\s\"']{6,})"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]{12,}"), r"\1 [redacted]"),
)


def redact(text: str) -> str:
    """Strip anything that looks like a credential.

    Applied when the transcript is captured, not when it is read, so a secret never
    reaches the file on disk either.
    """
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def context_root(root: str | Path | None = None) -> Path:
    return Path(root or DEFAULT_CONTEXT_DIR).expanduser()


def capture_key(cwd: str | None = None, session_id: str | None = None) -> str:
    """A stable filename for one harness session.

    Keyed by session when the harness supplies one, else by working directory, so two
    projects open at once never read each other's conversation.
    """
    raw = session_id or os.path.abspath(cwd or os.getcwd())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def capture_path(key: str, *, root: str | Path | None = None) -> Path:
    return context_root(root) / f"{key}.json"


def write_capture(
    turns: list[dict[str, str]],
    *,
    key: str,
    root: str | Path | None = None,
    max_turns: int = 12,
    max_chars: int = 24_000,
) -> Path:
    """Store the most recent turns for this session, redacted and bounded.

    Written 0600: it is a verbatim copy of a conversation.
    """
    kept = _bound(turns[-max_turns:] if max_turns > 0 else turns, max_chars)
    target = capture_path(key, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"captured": time.time(), "turns": kept}
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def read_capture(
    *,
    key: str,
    root: str | Path | None = None,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    now: float | None = None,
) -> list[dict[str, str]]:
    """The stored turns, or ``[]`` when there are none, they are stale, or the file is
    unreadable. Never raises: a missing capture must degrade to today's behaviour."""
    try:
        target = capture_path(key, root=root)
        if not target.exists():
            return []
        data: Any = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        captured = float(data.get("captured") or 0)
        if max_age_s > 0 and (now or time.time()) - captured > max_age_s:
            return []
        turns = data.get("turns")
        if not isinstance(turns, list):
            return []
        return [
            {"role": str(t.get("role", "user")), "content": str(t.get("content", ""))}
            for t in turns
            if isinstance(t, dict) and t.get("content")
        ]
    except Exception:  # noqa: BLE001
        return []


def render(turns: list[dict[str, str]]) -> str:
    """The conversation as the panel sees it."""
    if not turns:
        return ""
    lines = ["CONVERSATION SO FAR:"]
    for turn in turns:
        lines.append(f"\n[{turn['role']}]\n{turn['content']}")
    return "\n".join(lines)


def _bound(turns: list[dict[str, str]], max_chars: int) -> list[dict[str, str]]:
    """Keep the most recent turns that fit, redacted. The newest matter most, so the
    budget is spent from the end backwards."""
    kept: list[dict[str, str]] = []
    budget = max_chars
    for turn in reversed(turns):
        content = redact(str(turn.get("content", "")))
        if not content:
            continue
        if len(content) > budget:
            # Truncate from the front: the end of a turn is the part still being
            # discussed. Stop afterwards - everything older is out of budget.
            if budget > 0:
                kept.append(
                    {
                        "role": str(turn.get("role", "user")),
                        "content": "..." + content[-budget:],
                    }
                )
            break
        budget -= len(content)
        kept.append({"role": str(turn.get("role", "user")), "content": content})
    return list(reversed(kept))
