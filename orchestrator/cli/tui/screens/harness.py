"""Harness wiring screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Static

from orchestrator.cli.harness import (
    codex_tool_output_token_limit,
    set_codex_tool_output_token_limit,
    wire_claude_code,
    wire_codex,
    wire_opencode,
)
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

SEL_HARNESS_CLAUDE_CODE = "#harness-claude-code"


class HarnessScreen(ArrowNavigationMixin, Screen[None]):
    """Wire Imladris into supported MCP harnesses."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(self, *, dashboard: object | None = None) -> None:
        super().__init__()
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield Static("Wire harnesses", id="harness-title", classes="brand")
        with Vertical(id="harness-form"):
            yield Checkbox("claude-code", id="harness-claude-code")
            yield Checkbox("codex", id="harness-codex")
            yield Static("Codex tool output token limit", classes="field-label")
            yield Input(id="codex-output-limit", type="integer")
            yield Checkbox("opencode", id="harness-opencode")
            with Horizontal(classes="form-row"):
                yield Button("Apply", variant="primary", id="apply-harnesses")
                yield Button("Cancel", id="cancel-harnesses")
            yield Static("", id="harness-status", classes="status")

    def on_mount(self) -> None:
        status = self.app._detect_harnesses()
        self.query_one(SEL_HARNESS_CLAUDE_CODE, Checkbox).value = status.get("claude-code", False)
        self.query_one("#harness-codex", Checkbox).value = status.get("codex", False)
        self.query_one("#harness-opencode", Checkbox).value = status.get("opencode", False)
        limit = codex_tool_output_token_limit(self.app.home)
        self.query_one("#codex-output-limit", Input).value = "" if limit is None else str(limit)
        self.query_one(SEL_HARNESS_CLAUDE_CODE, Checkbox).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-harnesses":
            self.action_apply()
        elif event.button.id == "cancel-harnesses":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_apply(self) -> None:
        try:
            codex_limit = self._codex_output_limit()
            if self.query_one(SEL_HARNESS_CLAUDE_CODE, Checkbox).value:
                wire_claude_code(self.app.home, "global")
            if self.query_one("#harness-codex", Checkbox).value:
                wire_codex(self.app.home)
                if codex_limit is not None:
                    set_codex_tool_output_token_limit(self.app.home, codex_limit)
            if self.query_one("#harness-opencode", Checkbox).value:
                wire_opencode(self.app.home)
        except Exception as exc:
            self.query_one("#harness-status", Static).update(str(exc))
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()

    def _codex_output_limit(self) -> int | None:
        value = self.query_one("#codex-output-limit", Input).value.strip()
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError("Codex tool output token limit must be an integer") from exc
        if parsed < 1:
            raise ValueError("Codex tool output token limit must be a positive integer")
        return parsed
