"""Write secret tokens to ``~/.mandos/.env`` at 0600 (plan §12.2 token rule).

Merge-not-clobber: existing keys are preserved, provided keys updated. Token values
are never echoed or logged; the config stores only ``api_key_env`` names.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_PATH = "~/.mandos/.env"


def expand_user_path(path: str | Path) -> Path:
    """Expand ``~`` with an explicit HOME override when one is present.

    Windows' ``Path.expanduser()`` ignores a monkeypatched ``HOME`` when
    ``USERPROFILE`` is set. The CLI tests use ``HOME`` to isolate user-level
    Mandos files, and real harnesses may also provide it explicitly.
    """
    raw = os.fspath(path)
    if raw == "~":
        return Path(os.environ.get("HOME") or Path.home())
    if raw.startswith("~/") or raw.startswith("~\\"):
        home = os.environ.get("HOME")
        if home:
            return Path(home) / raw[2:]
    return Path(path).expanduser()


def _needs_quoting(value: str) -> bool:
    return value == "" or any(c in value for c in " \t#\"'")


def _read_existing(path: Path) -> dict[str, str]:
    existing: dict[str, str] = {}
    if not path.exists():
        return existing
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        existing[key.strip()] = value.strip()
    return existing


def write_env(
    values: dict[str, str],
    path: str | Path = DEFAULT_ENV_PATH,
    *,
    prune_keys: list[str] | tuple[str, ...] | set[str] = (),
) -> Path:
    """Merge ``values`` into the env file, set 0600, and return the path.

    Only non-empty values are written. Existing unrelated keys are kept unless
    explicitly pruned.
    """
    target = expand_user_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    merged = _read_existing(target)
    for key in prune_keys:
        if key not in values:
            merged.pop(key, None)
    for key, value in values.items():
        if value:
            merged[key] = f'"{value}"' if _needs_quoting(value) else value

    lines = [f"{key}={value}" for key, value in merged.items()]
    target.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target
