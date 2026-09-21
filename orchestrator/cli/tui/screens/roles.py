"""Role reassignment screen."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Select, Static

from orchestrator.cli.config_ops import persist, set_judge, set_panel
from orchestrator.cli.tui.screens.base import DraftCommitMixin
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

NONE_VALUE = "__none__"
VALID_ID_FRAGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
SEL_JUDGE_SELECT = "#judge-select"


class RolesScreen(DraftCommitMixin, ArrowNavigationMixin, Screen[None]):
    """Assign judge and panel membership independently."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(self, *, dashboard: object | None = None) -> None:
        super().__init__()
        self._dashboard = dashboard
        self._panel_checkbox_ids: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="roles-form") as form:
            form.border_title = "Reassign roles"
            yield Static("Judge", classes="field-label")
            yield Select([("(none)", NONE_VALUE)], allow_blank=False, id="judge-select")
            yield Static("Panel membership", classes="field-label")
            yield Vertical(id="panel-members")
            with Horizontal(classes="form-actions"):
                yield Button("Save", variant="primary", id="apply-roles")
                yield Button("Cancel", id="cancel-roles")
            yield Static("", id="roles-status", classes="status")

    def on_mount(self) -> None:
        self._load_roles()
        self.query_one(SEL_JUDGE_SELECT, Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-roles":
            self.action_apply_roles()
        elif event.button.id == "cancel-roles":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_apply_roles(self) -> None:
        draft = self.app.draft

        def apply() -> None:
            config = draft.to_config()
            config = set_judge(config, self._selected_role(SEL_JUDGE_SELECT))
            for provider in draft.providers:
                checkbox_id = self._panel_checkbox_ids[provider.id]
                checkbox = self.query_one(f"#{checkbox_id}", Checkbox)
                config = set_panel(config, provider.id, checkbox.value)
            persist(config, {}, draft.source_path, env_path=draft.env_path)

        self.commit(draft, apply)

    def _load_roles(self) -> None:
        draft = self.app.draft
        if draft is None:
            self.app.refresh_dashboard_state()
            draft = self.app.draft
        if draft is None:
            return
        options = [
            ("(none)", NONE_VALUE),
            *[(provider.id, provider.id) for provider in draft.providers],
        ]
        judge = self.query_one(SEL_JUDGE_SELECT, Select)
        judge.set_options(options)
        judge.value = draft.defaults.analysis_model or NONE_VALUE

        panel_members = self.query_one("#panel-members", Vertical)
        panel_members.remove_children()
        self._panel_checkbox_ids = {}
        for index, provider in enumerate(draft.providers):
            checkbox_id = self._panel_checkbox_id(index, provider.id)
            self._panel_checkbox_ids[provider.id] = checkbox_id
            panel_members.mount(
                Checkbox(
                    f"{provider.id} ({provider.model})",
                    value="panel" in provider.roles,
                    id=checkbox_id,
                )
            )

    def _panel_checkbox_id(self, index: int, provider_id: str) -> str:
        if VALID_ID_FRAGMENT.fullmatch(provider_id):
            return f"panel-{provider_id}"
        return f"panel-member-{index}"

    def _selected_role(self, selector: str) -> str | None:
        value = self.query_one(selector, Select).value
        if value == NONE_VALUE or value is Select.NULL:
            return None
        return str(value)

    def _set_status(self, message: str) -> None:
        self.query_one("#roles-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
