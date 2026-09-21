"""Write secret tokens to ``~/.mandos/.env`` at 0600.

Merge-not-clobber: existing keys are preserved, provided keys updated. Token values
are never echoed or logged; the config stores only ``api_key_env`` names.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

DEFAULT_ENV_PATH = "~/.mandos/.env"
EXAMPLE_RESOURCE = "env.example"


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


def read_example() -> str:
    """The packaged example env file, shipped as data so an install carries it."""
    return files("orchestrator.data").joinpath(EXAMPLE_RESOURCE).read_text(encoding="utf-8")


def example_env_names() -> list[str]:
    """Variable names the example offers, in the order it lists them."""
    names: list[str] = []
    for raw in read_example().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.partition("=")[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def env_file_names(path: str | Path = DEFAULT_ENV_PATH) -> list[str]:
    """Names the env file itself declares, empty ones included.

    Not ``loaded_env_values``: that overlays the whole process environment, so its
    keys would put every unrelated variable on screen.
    """
    return sorted(_read_existing(expand_user_path(path)))


def _write_pairs(target: Path, pairs: dict[str, str]) -> Path:
    """Write every pair, empty values included, and lock the file down.

    ``write_env`` drops empty values because it exists to record secrets. Seeding a
    name with no value is the opposite job: an empty value reads as unset everywhere,
    so the name can sit in the file as a prompt without pretending to be configured.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in pairs.items()]
    target.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def ensure_env_file(path: str | Path = DEFAULT_ENV_PATH) -> tuple[Path, bool]:
    """Create the env file from the example when it is missing.

    Returns the path and whether it had to be created, so a caller can say so.
    """
    target = expand_user_path(path)
    if target.exists():
        return target, False
    _write_pairs(target, {name: "" for name in example_env_names()})
    return target, True


def sync_env_with_example(path: str | Path = DEFAULT_ENV_PATH) -> list[str]:
    """Add any example name the file lacks. Nothing is renamed, changed or removed."""
    target = expand_user_path(path)
    existing = _read_existing(target)
    added = [name for name in example_env_names() if name not in existing]
    if added:
        _write_pairs(target, {**existing, **{name: "" for name in added}})
    return added


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
