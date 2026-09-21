"""Local on-prem council-session storage."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from orchestrator.budget import estimate_text_tokens
from orchestrator.models import THREAD_ID_PATTERN, Analysis, ChatMessage, RawAnswer

THREAD_ID_RE = re.compile(THREAD_ID_PATTERN)
DEFAULT_SESSIONS_DIR = "~/.mandos/sessions"
PERSISTED_TURNS_CAP = 200
_LOCKS: dict[str, asyncio.Lock] = {}


def validate_thread_id(thread_id: str) -> str:
    if not THREAD_ID_RE.fullmatch(thread_id):
        raise ValueError("thread_id must match ^[A-Za-z0-9_-]{1,64}$")
    return thread_id


def sessions_root(root: str | Path | None = None) -> Path:
    return Path(root or DEFAULT_SESSIONS_DIR).expanduser()


def session_path(thread_id: str, *, root: str | Path | None = None) -> Path:
    validate_thread_id(thread_id)
    base = sessions_root(root)
    target = base / f"{thread_id}.json"
    resolved_base = base.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if resolved_base != resolved_target.parent:
        raise ValueError("thread_id resolves outside the sessions directory")
    return target


def load_session(thread_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    path = session_path(thread_id, root=root)
    if not path.exists():
        return {"thread_id": thread_id, "created": time.time(), "turns": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("thread_id") != thread_id:
        raise ValueError("session file is malformed")
    turns = data.get("turns")
    if not isinstance(turns, list):
        raise ValueError("session file is malformed")
    data.setdefault("created", time.time())
    return data


def write_session(data: dict[str, Any], *, root: str | Path | None = None) -> Path:
    thread_id = str(data.get("thread_id") or "")
    path = session_path(thread_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data.setdefault("created", time.time())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        pass
    return path


@asynccontextmanager
async def session_lock(thread_id: str, *, root: str | Path | None = None):
    path = session_path(thread_id, root=root)
    key = str(path.resolve(strict=False))
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        yield


def _analysis_summary(analysis: dict[str, Any] | None) -> str | None:
    if not analysis:
        return None
    parsed = Analysis.model_validate(analysis)
    parts: list[str] = []
    if parsed.consensus:
        parts.append("Consensus: " + "; ".join(parsed.consensus[:3]))
    if parsed.confidence_notes:
        parts.append("Confidence: " + parsed.confidence_notes)
    if parsed.blind_spots:
        parts.append("Blind spots: " + "; ".join(parsed.blind_spots[:3]))
    return "\n".join(parts) if parts else None


def assistant_content_for_turn(turn: dict[str, Any]) -> str:
    assistant = turn.get("assistant", turn.get("prior_answer"))
    if isinstance(assistant, str) and assistant.strip():
        return assistant.strip()
    try:
        summary = _analysis_summary(turn.get("analysis"))
    except Exception:
        summary = None
    if summary:
        return summary
    return "Previous council turn completed, but no final answer or analysis was stored."


def apply_prior_answer(session: dict[str, Any], prior_answer: str | None) -> None:
    if not prior_answer:
        return
    turns = session.setdefault("turns", [])
    if turns:
        turns[-1]["assistant"] = prior_answer


def _user_content(prompt: str, context: str | None = None) -> str:
    return prompt if not context else f"{prompt}\n\nCONTEXT:\n{context}"


def estimate_messages_tokens(
    messages: list[ChatMessage],
    *,
    expected_output_tokens: int = 0,
) -> int:
    message_tokens = sum(estimate_text_tokens(message.content) for message in messages)
    return message_tokens + expected_output_tokens


def _assembled_messages(
    *,
    system: str,
    turns: list[dict[str, Any]],
    omitted_turns: int,
    prompt: str,
    context: str | None,
) -> list[ChatMessage]:
    messages = [ChatMessage(role="system", content=system)]
    if omitted_turns:
        messages.append(
            ChatMessage(
                role="assistant",
                content=(
                    f"Earlier council history was compacted; {omitted_turns} turns were omitted."
                ),
            )
        )
    for turn in turns:
        old_prompt = str(turn.get("user") or turn.get("prompt") or "")
        old_context = turn.get("context")
        messages.append(ChatMessage(role="user", content=_user_content(old_prompt, old_context)))
        messages.append(ChatMessage(role="assistant", content=assistant_content_for_turn(turn)))
    messages.append(ChatMessage(role="user", content=_user_content(prompt, context)))
    return messages


def build_messages(
    session: dict[str, Any],
    *,
    system: str,
    prompt: str,
    context: str | None,
    max_history_turns: int,
    context_window: int | None = None,
    expected_output_tokens: int = 0,
    warning_ratio: float = 0.75,
) -> tuple[list[ChatMessage], bool]:
    turns = [turn for turn in session.get("turns", []) if isinstance(turn, dict)]
    kept = turns[-max_history_turns:] if max_history_turns > 0 else []
    omitted = len(turns) - len(kept)

    target_tokens = None
    if context_window is not None:
        target_tokens = max(1, int(context_window * warning_ratio))

    while True:
        messages = _assembled_messages(
            system=system,
            turns=kept,
            omitted_turns=omitted,
            prompt=prompt,
            context=context,
        )
        if target_tokens is None:
            break
        estimated = estimate_messages_tokens(
            messages,
            expected_output_tokens=expected_output_tokens,
        )
        if estimated <= target_tokens or not kept:
            break
        kept = kept[1:]
        omitted += 1

    return messages, omitted > 0


def append_turn(
    session: dict[str, Any],
    *,
    prompt: str,
    context: str | None,
    raw_answers: list[RawAnswer],
    analysis: Analysis | None,
    compacted: bool,
) -> None:
    turns = session.setdefault("turns", [])
    turns.append(
        {
            "user": prompt,
            "context": context,
            "assistant": None,
            "panel_raw": [answer.model_dump() for answer in raw_answers],
            "analysis": analysis.model_dump() if analysis else None,
            "compacted": compacted,
        }
    )
    if len(turns) > PERSISTED_TURNS_CAP:
        del turns[:-PERSISTED_TURNS_CAP]


def clear_sessions(*, thread_id: str | None = None, root: str | Path | None = None) -> int:
    if thread_id is not None:
        path = session_path(thread_id, root=root)
        if path.exists():
            path.unlink()
            return 1
        return 0
    base = sessions_root(root)
    if not base.exists():
        return 0
    count = 0
    for path in base.glob("*.json"):
        path.unlink()
        count += 1
    return count
