"""Delete-member confirmation screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static

from orchestrator.cli.config_ops import delete_member, persist, referenced_env_keys
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin


class DeleteMemberScreen(ArrowNavigationMixin, Screen[None]):
    """Confirm deletion of a selected provider member."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(self, *, member_id: str, dashboard: object | None = None) -> None:
        super().__init__()
        self.member_id = member_id
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield Static(f"Delete member: {self.member_id}", id="delete-title", classes="brand")
        with Vertical(id="delete-member"):
            yield Static("", id="delete-warning", classes="status")
            with Horizontal(classes="form-row"):
                yield Button("Delete", variant="error", id="confirm-delete")
                yield Button("Cancel", id="cancel-delete")
            yield Static("", id="delete-status", classes="status")

    def on_mount(self) -> None:
        self._render_warning()
        self.query_one("#confirm-delete", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-delete":
            self.action_confirm_delete()
        elif event.button.id == "cancel-delete":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_confirm_delete(self) -> None:
        draft = self.app.draft
        if draft is None:
            self._set_status("config draft is not loaded")
            return
        try:
            before_keys = {
                provider.api_key_env for provider in draft.providers if provider.api_key_env
            }
            after = delete_member(draft, self.member_id)
            prune_keys = tuple(sorted(before_keys - referenced_env_keys(after)))
            persist(after, {}, draft.source_path, prune_keys, env_path=draft.env_path)
        except Exception as exc:
            self._set_status(str(exc))
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _render_warning(self) -> None:
        draft = self.app.draft
        provider = None
        if draft is not None:
            provider = next((item for item in draft.providers if item.id == self.member_id), None)
        if provider is None:
            self.query_one("#delete-warning", Static).update("Unknown member.")
            return

        notes = [f"Delete {provider.id} ({provider.model})."]
        if draft is not None and draft.defaults.analysis_model == provider.id:
            notes.append("This member is the judge; deleting it clears Judge.")
        self.query_one("#delete-warning", Static).update("\n".join(notes))

    def _set_status(self, message: str) -> None:
        self.query_one("#delete-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
