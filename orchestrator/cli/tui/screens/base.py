"""Shared commit behaviour for the configurator's editing screens.

Every screen that changes the config follows the same protocol: refuse to act without a
loaded draft, report any failure as screen status instead of crashing the TUI, and on
success refresh both the dashboard state and its widget before closing. Five screens
had their own copy, so a change to the protocol meant five edits and a screen could
drift without a test noticing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class DraftCommitMixin:
    """Commit an edit to the config draft, then close the screen."""

    def commit(self, draft: Any | None, apply: Callable[[], None]) -> bool:
        """Run ``apply`` under the draft guard and the status-reporting contract.

        ``apply`` does the screen-specific work and persists it; it is called only when
        ``draft`` is loaded. Returns whether the screen closed, so a caller that needs
        to stay open on failure can tell.
        """
        if draft is None:
            self._set_status("config draft is not loaded")
            return False
        try:
            apply()
        except Exception as exc:  # noqa: BLE001
            self._set_status(str(exc))
            return False
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()
        return True
