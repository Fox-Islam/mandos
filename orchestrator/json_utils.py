from __future__ import annotations

import json
from typing import Any


def _fenced_blocks(text: str):
    cursor = 0
    while True:
        start = text.find("```", cursor)
        if start < 0:
            return

        content_start = start + 3
        json_label_end = content_start + 4
        if (
            text[content_start:json_label_end].lower() == "json"
            and json_label_end < len(text)
            and text[json_label_end].isspace()
        ):
            content_start = json_label_end

        while content_start < len(text) and text[content_start].isspace():
            content_start += 1

        end = text.find("```", content_start)
        if end < 0:
            return

        yield text[content_start:end]
        cursor = end + 3


def parse_lenient(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a single JSON object from model output.

    Strategies are *additive*, not exclusive: try each fenced ```` ``` ```` block,
    then the outer-brace span of the original string. A model that wraps reasoning in
    a fence but emits the real JSON elsewhere is still recovered."""
    s = text.strip()
    for fenced in _fenced_blocks(s):
        parsed = _loads_dict(fenced.strip())
        if parsed is not None:
            return parsed
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        return _loads_dict(s[start : end + 1])
    return None


def _loads_dict(candidate: str) -> dict[str, Any] | None:
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
