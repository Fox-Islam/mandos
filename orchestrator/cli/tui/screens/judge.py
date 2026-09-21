"""Judge screen: which judge decides, on which provider, with which key.

The judge has its own screen, not a row on the defaults form. Everything here except
the key is written to ``config.json``; the key itself only ever reaches ``.env`` at
0600, and the config records its variable name.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Select, Static

from orchestrator.cli.config_ops import persist
from orchestrator.cli.tui.screens.base import DraftCommitMixin
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin
from orchestrator.jev import PROVIDERS as JEV_PROVIDERS

SEL_SHAPE = "#judge-shape"
SEL_PROVIDER = "#judge-provider"

SHAPES = [
    ("hybrid - an analyst proposes claims, Jev decides them", "hybrid"),
    ("matrix - Jev alone, no generative model", "matrix"),
    ("verify - an analyst writes, Jev grades it", "verify"),
    ("llm - the generative analyst alone", "llm"),
]


class JudgeScreen(DraftCommitMixin, ArrowNavigationMixin, Screen[None]):
    """Edit the judge: shape, Jev provider, model, endpoint and key."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(self, *, dashboard: object | None = None) -> None:
        super().__init__()
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield Static("Judge", id="judge-title", classes="brand")
        with Vertical(id="judge-form"):
            yield Static("Shape", classes="field-label")
            yield Select(SHAPES, allow_blank=False, id="judge-shape")
            yield Static("Jev provider", classes="field-label")
            yield Select(
                [(spec["label"], name) for name, spec in JEV_PROVIDERS.items()],
                allow_blank=False,
                id="judge-provider",
            )
            yield Static("Jev model", classes="field-label")
            yield Input(id="judge-model")
            yield Static("Endpoint override (blank = the provider's own)", classes="field-label")
            yield Input(id="judge-base-url")
            yield Static("Jev API key (blank keeps the stored one)", classes="field-label")
            yield Input(password=True, id="judge-token")
            yield Static("Timeout seconds", classes="field-label")
            yield Input(id="judge-timeout", type="number")
            yield Static("Questions per call", classes="field-label")
            yield Input(id="judge-batch", type="integer")
            with Horizontal(classes="form-row"):
                yield Button("Apply", variant="primary", id="apply-judge")
                yield Button("Cancel", id="cancel-judge")
            yield Static("", id="judge-status", classes="status")

    def on_mount(self) -> None:
        self._load()
        self.query_one(SEL_SHAPE, Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-judge":
            self.action_apply_judge()
        elif event.button.id == "cancel-judge":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_apply_judge(self) -> None:
        draft = self.app.draft

        def apply() -> None:
            config = draft.to_config()
            judge = config.judge
            judge.shape = str(self.query_one(SEL_SHAPE, Select).value)
            judge.provider = str(self.query_one(SEL_PROVIDER, Select).value)
            judge.model = self._input("#judge-model") or judge.model
            judge.base_url = self._input("#judge-base-url") or None
            judge.timeout_s = self._positive_float("#judge-timeout", "timeout seconds")
            judge.questions_per_call = self._positive_int("#judge-batch", "questions per call")
            token = self._input("#judge-token")
            # A blank key keeps whatever is stored; the config never holds the value.
            changes = {judge.resolved_api_key_env: token} if token else {}
            persist(config, changes, draft.source_path, env_path=draft.env_path)

        self.commit(draft, apply)

    def _load(self) -> None:
        draft = self.app.draft
        if draft is None:
            self.app.refresh_dashboard_state()
            draft = self.app.draft
        if draft is None:
            return
        judge = draft.judge
        self.query_one(SEL_SHAPE, Select).value = judge.shape
        self.query_one(SEL_PROVIDER, Select).value = judge.provider
        self.query_one("#judge-model", Input).value = judge.model
        self.query_one("#judge-base-url", Input).value = judge.base_url or ""
        self.query_one("#judge-timeout", Input).value = str(judge.timeout_s)
        self.query_one("#judge-batch", Input).value = str(judge.questions_per_call)

    def _input(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _positive_float(self, selector: str, label: str) -> float:
        try:
            parsed = float(self._input(selector))
        except ValueError as exc:
            raise ValueError(f"{label} must be a number") from exc
        if parsed <= 0:
            raise ValueError(f"{label} must be greater than zero")
        return parsed

    def _positive_int(self, selector: str, label: str) -> int:
        try:
            parsed = int(self._input(selector))
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if parsed < 1:
            raise ValueError(f"{label} must be a positive integer")
        return parsed

    def _set_status(self, message: str) -> None:
        self.query_one("#judge-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
