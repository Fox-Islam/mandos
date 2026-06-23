"""Textual app shell for the Imladris configurator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual.app import App

from orchestrator.cli.config_ops import Draft, Issue, compute_issues, default_env_path, load_draft
from orchestrator.cli.secrets import expand_user_path
from orchestrator.cli.tui.screens.dashboard import DashboardScreen

HarnessStatus = dict[str, bool]
DraftLoader = Callable[..., Draft]


class ImladrisApp(App[int]):
    """Full-screen configurator app.

    Phase 2 owns the app shell and dashboard. Later phases add mutating screens.
    """

    CSS_PATH = "app.tcss"
    TITLE = "imladris"
    SUB_TITLE = "Council Configurator"

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        home: str | Path | None = None,
        harness_status: HarnessStatus | None = None,
        draft_loader: DraftLoader = load_draft,
    ) -> None:
        super().__init__()
        self.config_path = config_path
        self.home = expand_user_path(home) if home is not None else Path.home()
        self.env_path = default_env_path(self.home)
        self._harness_status = harness_status
        self._draft_loader = draft_loader
        self.draft: Draft | None = None
        self.issues: list[Issue] = []
        self.harness_status: HarnessStatus = {
            "claude-code": False,
            "codex": False,
            "opencode": False,
        }

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())

    def refresh_dashboard_state(self) -> None:
        self.draft = self._draft_loader(self.config_path, env_path=self.env_path)
        self.harness_status = self._detect_harnesses()
        self.issues = compute_issues(self.draft, self.harness_status)

    def _detect_harnesses(self) -> HarnessStatus:
        if self._harness_status is not None:
            return dict(self._harness_status)

        from orchestrator.cli.app import detect_wired_harnesses

        return detect_wired_harnesses(self.home)
