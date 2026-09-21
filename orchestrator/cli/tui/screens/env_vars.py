"""Screen for the secrets the config refers to by name.

The config never holds a token, only the name of the environment variable that does,
so the roster can tell you a key is missing but not let you supply it. Setting one
otherwise meant editing the member that happens to reference it, or writing
``~/.mandos/.env`` by hand.

Values are never rendered. Each field reports only whether the variable currently
resolves, and a blank field leaves whatever is stored alone.
"""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from orchestrator.cli.config_ops import loaded_env_values
from orchestrator.cli.secrets import (
    ensure_env_file,
    env_file_names,
    sync_env_with_example,
    write_env,
)
from orchestrator.cli.tui.screens.base import DraftCommitMixin
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

SEL_ROWS = "#env-rows"
NEW_NAME = "#env-new-name"
NEW_VALUE = "#env-new-value"
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def field_id(name: str) -> str:
    """A widget id for a variable name, which may contain characters ids may not."""
    return "env-" + re.sub(r"[^A-Za-z0-9]", "-", name).lower()


class EnvVarsScreen(DraftCommitMixin, ArrowNavigationMixin, Screen[None]):
    """Set or replace the API keys the config names."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(self, *, dashboard: object | None = None) -> None:
        super().__init__()
        self._dashboard = dashboard
        self._names: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="env-form") as form:
            form.border_title = "Environment variables"
            yield Static(
                "Secrets live in the env file at 0600, never in the config. "
                "Values are not shown; leave a field blank to keep the stored one.",
                classes="status",
            )
            yield Vertical(id="env-rows")
            yield Static("Add another variable", classes="field-label")
            with Horizontal(classes="form-row"):
                yield Input(placeholder="NAME, e.g. GROQ_API_KEY", id="env-new-name")
                yield Input(password=True, placeholder="value", id="env-new-value")
            with Horizontal(classes="form-actions"):
                yield Button("Sync from example", id="sync-env")
                yield Button("Save", variant="primary", id="save-env")
                yield Button("Cancel", id="cancel-env")
            yield Static("", id="env-status", classes="status")

    async def on_mount(self) -> None:
        draft = self.app.draft
        created = False
        if draft is not None:
            _, created = ensure_env_file(draft.env_path)
        await self._load_rows()
        if created:
            self._set_status("Created the env file with the names Mandos knows about.")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-env":
            self.action_save()
        elif event.button.id == "sync-env":
            await self.action_sync()
        elif event.button.id == "cancel-env":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_save(self) -> None:
        draft = self.app.draft

        def apply() -> None:
            changes = {
                name: value
                for name in self._names
                for value in [self.query_one(f"#{field_id(name)}", Input).value.strip()]
                if value
            }
            name = self.query_one(NEW_NAME, Input).value.strip()
            value = self.query_one(NEW_VALUE, Input).value.strip()
            if name and not _NAME_RE.match(name):
                raise ValueError(f"{name} is not a valid environment variable name")
            if name and not value:
                raise ValueError(f"no value given for {name}")
            if value and not name:
                raise ValueError("name the variable the value belongs to")
            if name:
                changes[name] = value
            if not changes:
                raise ValueError("nothing to save")
            # The env file only. Going through `persist` would validate the config
            # first, which raises for a missing key, and a missing key is the reason
            # to be on this screen.
            write_env(changes, draft.env_path)

        self.commit(draft, apply)

    async def action_sync(self) -> None:
        """Add any name the example offers that the file lacks. Removes nothing."""
        draft = self.app.draft
        if draft is None:
            return
        try:
            added = sync_env_with_example(draft.env_path)
        except Exception as exc:  # noqa: BLE001
            self._set_status(str(exc))
            return
        await self._load_rows()
        self._set_status(
            f"Added {len(added)}: {', '.join(added)}"
            if added
            else "Already has every name the example offers."
        )

    async def _load_rows(self) -> None:
        draft = self.app.draft
        if draft is None:
            self.app.refresh_dashboard_state()
            draft = self.app.draft
        if draft is None:
            return

        # Read off the draft, not a validated config: a config missing a key cannot be
        # built, and that is exactly when someone opens this screen.
        names = {provider.api_key_env for provider in draft.providers if provider.api_key_env}
        if draft.judge.uses_jev:
            names.add(draft.judge.resolved_api_key_env)
        names.update(env_file_names(draft.env_path))
        self._names = sorted(name for name in names if name)

        resolved = loaded_env_values(draft.env_path)
        rows = self.query_one(SEL_ROWS, Vertical)
        await rows.remove_children()
        if not self._names:
            rows.mount(Static("No provider names a key yet.", classes="status"))
            return
        for name in self._names:
            state = "set" if resolved.get(name) else "not set"
            rows.mount(Static(f"{name}  ({state})", classes="field-label"))
            rows.mount(
                Input(
                    password=True,
                    placeholder="blank keeps the stored value",
                    id=field_id(name),
                )
            )

    def _set_status(self, message: str) -> None:
        self.query_one("#env-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
