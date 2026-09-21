"""The ``mandos`` console script: configurator TUI + ``doctor``.

``main`` dispatches ``mandos`` (full-screen configurator), ``mandos doctor``,
``mandos refresh-catalog``, ``mandos clear-sessions``, and ``--help``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from orchestrator.cli.config_ops import compute_issues, load_draft
from orchestrator.cli.harness.claude_code import claude_code_path
from orchestrator.cli.harness.codex import codex_path
from orchestrator.cli.harness.opencode import opencode_path
from orchestrator.model_catalog import refresh_cache_if_stale, resolve_model_metadata
from orchestrator.sessions import clear_sessions
from orchestrator.settings import (
    MandosConfig,
    ProviderDescriptor,
    is_local_base_url,
    load_config,
    load_env_file,
)

HELP = """mandos - configure the multi-model deliberation council.

Usage:
  mandos            Run the full-screen configurator TUI.
  mandos doctor     Show the resolved roster, roles, and wired harnesses (no secrets).
  mandos refresh-catalog
                    Fetch models.dev metadata into the local catalog cache.
  mandos clear-sessions [thread_id]
                    Clear all local council sessions, or one thread when supplied.
  mandos --help     Show this help.

The configurator writes ~/.mandos/config.json and ~/.mandos/.env (0600), then wires
the harnesses you select (Claude Code / Codex / OpenCode) to launch `mandos-mcp` over
stdio.
"""


def detect_wired_harnesses(home: Path) -> dict[str, bool]:
    """Best-effort detection of which harnesses already carry a mandos entry."""
    home = Path(home).expanduser()
    wired = {}
    claude = claude_code_path(home, "global")
    wired["claude-code"] = claude.exists() and "mandos" in claude.read_text(encoding="utf-8")
    codex = codex_path(home)
    wired["codex"] = codex.exists() and "mandos" in codex.read_text(encoding="utf-8")
    opencode = opencode_path(home)
    wired["opencode"] = opencode.exists() and "mandos" in opencode.read_text(encoding="utf-8")
    return wired


def _doctor_member_line(p: ProviderDescriptor) -> str:
    where = "off-prem" if p.api_key_env is not None else "local"
    metadata = resolve_model_metadata(p.catalog_key or p.kind, p.model)
    context_window = (
        p.context_window
        or p.resolved_context_window
        or (metadata.context_window if metadata else None)
    )
    ctx = f" ctx={context_window}" if context_window else " ctx=?"
    return f"  - {p.id}: roles={','.join(p.roles)} model={p.model}{ctx} [{where}]"


def _doctor_judge_lines(config: MandosConfig) -> list[str]:
    """Report the judge that decides, and its key state by name-free
    presence only (the variable name is a secret-adjacent detail ``safe_status`` also
    withholds)."""
    judge = config.judge
    analyst = config.defaults.analysis_model or "(none)"
    if not judge.uses_jev:
        return [f"Judge: llm - {analyst}"]
    where = "on-prem" if is_local_base_url(judge.resolved_base_url) else "off-prem"
    key = "set" if judge.api_key else "MISSING"
    lines = [f"Judge: {judge.shape} - Jev {judge.model} via {judge.provider} [{where}, key {key}]"]
    if judge.needs_analysis_model:
        lines.append(f"  analyst behind it: {analyst}")
    return lines


def doctor_report(
    config: MandosConfig,
    wired: dict[str, bool],
    issues: list[str] | None = None,
) -> str:
    """Render a secret-free health report: roster, roles, off-prem flag, harnesses."""
    lines = ["Mandos configuration", "=" * 22, "", "Council members:"]
    for p in config.providers:
        if p.enabled:
            lines.append(_doctor_member_line(p))
    lines.append("")
    lines.extend(_doctor_judge_lines(config))
    lines.append(f"Default preset: {config.defaults.preset or '(none)'}")
    lines.append(
        "Budget thresholds: "
        f"{int(config.defaults.budget_warning_ratio * 100)}%"
        f"/{int(config.defaults.budget_error_ratio * 100)}%"
    )
    if config.presets:
        lines.append(f"Presets: {', '.join(config.presets)}")
    lines.append("")
    lines.append("Wired harnesses:")
    for name, ok in wired.items():
        lines.append(f"  - {name}: {'yes' if ok else 'no'}")
    if issues:
        lines.extend(["", "Configuration issues:"])
        lines.extend(f"  - {issue}" for issue in issues)
    return "\n".join(lines)


def doctor() -> int:
    load_env_file()
    wired = detect_wired_harnesses(Path.home())
    try:
        config = load_config()
    except Exception as exc:
        print(f"No usable Mandos config found: {exc}")
        draft = load_draft()
        issues = compute_issues(draft, wired)
        for issue in issues:
            print(f"- {issue.label} -> {issue.action}")
        print("Run `mandos` to create or repair one.")
        return 1
    draft = load_draft()
    issues = [issue.label for issue in compute_issues(draft, wired)]
    print(doctor_report(config, wired, issues))
    return 0


def clear_sessions_command(thread_id: str | None = None) -> int:
    cleared = clear_sessions(thread_id=thread_id)
    target = f"session {thread_id}" if thread_id else "sessions"
    print(f"Cleared {cleared} {target}.")
    return 0


def refresh_catalog_command() -> int:
    result = refresh_cache_if_stale(force=True)
    if result.refreshed:
        print(f"Refreshed model catalog: {len(result.models)} models cached.")
        return 0
    if result.error:
        print(
            f"Model catalog refresh skipped: {result.error}; "
            f"{len(result.models)} cached/seed models available."
        )
        return 1
    print(f"Model catalog already fresh: {len(result.models)} cached/seed models available.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if args and args[0] == "doctor":
        return doctor()
    if args and args[0] == "refresh-catalog":
        return refresh_catalog_command()
    if args and args[0] == "clear-sessions":
        return clear_sessions_command(args[1] if len(args) > 1 else None)
    from orchestrator.cli.tui import MandosApp

    result = MandosApp().run()
    return 0 if result is None else int(result)


if __name__ == "__main__":
    raise SystemExit(main())
