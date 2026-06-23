"""Shared keyboard navigation helpers for TUI form screens."""

from __future__ import annotations

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import OptionList, Select

ARROW_NAV_BINDINGS = [
    Binding("up", "focus_previous_field", show=False, priority=True),
    Binding("down", "focus_next_field", show=False, priority=True),
]


class ArrowNavigationMixin:
    """Move focus between form controls with arrow keys."""

    def action_focus_next_field(self) -> None:
        if self._move_expanded_select("down"):
            return
        self._arrow_screen().focus_next()

    def action_focus_previous_field(self) -> None:
        if self._move_expanded_select("up"):
            return
        self._arrow_screen().focus_previous()

    def focus_first_field(self) -> None:
        self._arrow_screen().focus_next()

    def _move_expanded_select(self, direction: str) -> bool:
        focused = self._arrow_screen().app.focused
        if not isinstance(focused, OptionList):
            return False
        parent = focused.parent
        if not isinstance(parent, Select) or not parent.expanded:
            return False
        if direction == "up":
            focused.action_cursor_up()
        else:
            focused.action_cursor_down()
        return True

    def _arrow_screen(self) -> Screen:
        if not isinstance(self, Screen):
            raise TypeError("ArrowNavigationMixin must be used with textual.screen.Screen")
        return self
