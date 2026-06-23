"""Execution defaults screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Select, Static

from orchestrator.cli.config_ops import persist
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

NONE_VALUE = "__none__"
SEL_PRESET_SELECT = "#preset-select"


class DefaultsScreen(ArrowNavigationMixin, Screen[None]):
    """Edit persisted defaults used when a tool call does not override them."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(self, *, dashboard: object | None = None) -> None:
        super().__init__()
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield Static("Run defaults", id="defaults-title", classes="brand")
        with Vertical(id="defaults-form"):
            yield Static("Default preset", classes="field-label")
            yield Select([("(none)", NONE_VALUE)], allow_blank=False, id="preset-select")
            yield Static("Max output tokens", classes="field-label")
            yield Input(id="max-tokens", type="integer")
            yield Static("Timeout seconds", classes="field-label")
            yield Input(id="timeout-s", type="number")
            yield Static("Temperature", classes="field-label")
            yield Input(id="temperature", type="number")
            yield Static("Max recursion depth", classes="field-label")
            yield Input(id="max-depth", type="integer")
            with Horizontal(classes="form-row"):
                yield Button("Apply", variant="primary", id="apply-defaults")
                yield Button("Cancel", id="cancel-defaults")
            yield Static("", id="defaults-status", classes="status")

    def on_mount(self) -> None:
        self._load_defaults()
        self.query_one(SEL_PRESET_SELECT, Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-defaults":
            self.action_apply_defaults()
        elif event.button.id == "cancel-defaults":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_apply_defaults(self) -> None:
        draft = self.app.draft
        if draft is None:
            self._set_status("config draft is not loaded")
            return
        try:
            config = draft.to_config()
            defaults = config.defaults
            defaults.preset = self._selected_preset()
            defaults.max_tokens = self._optional_int("#max-tokens", "max output tokens")
            defaults.timeout_s = self._positive_float("#timeout-s", "timeout seconds")
            defaults.temperature = self._temperature()
            defaults.max_depth = self._positive_int("#max-depth", "max recursion depth")
            persist(config, {}, draft.source_path, env_path=draft.env_path)
        except Exception as exc:
            self._set_status(str(exc))
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _load_defaults(self) -> None:
        draft = self.app.draft
        if draft is None:
            self.app.refresh_dashboard_state()
            draft = self.app.draft
        if draft is None:
            return

        options = [("(none)", NONE_VALUE), *[(name, name) for name in draft.presets]]
        preset = self.query_one(SEL_PRESET_SELECT, Select)
        preset.set_options(options)
        preset.value = (
            draft.defaults.preset if draft.defaults.preset in draft.presets else NONE_VALUE
        )

        defaults = draft.defaults
        self.query_one("#max-tokens", Input).value = (
            "" if defaults.max_tokens is None else str(defaults.max_tokens)
        )
        self.query_one("#timeout-s", Input).value = str(defaults.timeout_s)
        self.query_one("#temperature", Input).value = str(defaults.temperature)
        self.query_one("#max-depth", Input).value = str(defaults.max_depth)

    def _selected_preset(self) -> str | None:
        value = self.query_one(SEL_PRESET_SELECT, Select).value
        if value == NONE_VALUE or value is Select.NULL:
            return None
        return str(value)

    def _optional_int(self, selector: str, label: str) -> int | None:
        value = self._input(selector)
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if parsed < 1:
            raise ValueError(f"{label} must be a positive integer")
        return parsed

    def _positive_int(self, selector: str, label: str) -> int:
        parsed = self._optional_int(selector, label)
        if parsed is None:
            raise ValueError(f"{label} is required")
        return parsed

    def _positive_float(self, selector: str, label: str) -> float:
        value = self._input(selector)
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number") from exc
        if parsed <= 0:
            raise ValueError(f"{label} must be greater than zero")
        return parsed

    def _temperature(self) -> float:
        value = self._input("#temperature")
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError("temperature must be a number") from exc
        if parsed < 0 or parsed > 2:
            raise ValueError("temperature must be between 0 and 2")
        return parsed

    def _input(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _set_status(self, message: str) -> None:
        self.query_one("#defaults-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
