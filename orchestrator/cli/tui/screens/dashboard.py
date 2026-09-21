"""Main dashboard screen for the Mandos configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, OptionList, Static
from textual.worker import Worker, WorkerState

from orchestrator.budget import BudgetEstimate, BudgetState, estimate_provider_budget
from orchestrator.cli.config_ops import Draft, Issue
from orchestrator.model_catalog import (
    CatalogRefreshResult,
    cache_status,
    refresh_cache_if_stale,
    resolve_model_metadata,
)

BRAND_TITLE = "mandos"
BRAND_SUBTITLE = "Council Configurator"

NODE = "◆"
ACCENT = "✦"

WORDMARK: tuple[str, ...] = (
    "                              __          ",
    "   ____ ___  ____ _____  ____/ /___  _____",
    "  / __ `__ \\/ __ `/ __ \\/ __  / __ \\/ ___/",
    " / / / / / / /_/ / / / / /_/ / /_/ (__  ) ",
    "/_/ /_/ /_/\\__,_/_/ /_/\\__,_/\\____/____/  ",
)

DashboardAction = tuple[str, str]
CATALOG_WORKER = "dashboard-catalog-refresh"

SEL_HARNESS = "#harness"
SEL_ROSTER = "#roster"
SEL_ACTION_MENU = "#action-menu"
WIRE_HARNESSES_LABEL = "Wire harnesses"


class DashboardScreen(Screen[None]):
    """Read-only dashboard for the current config draft."""

    BINDINGS = [
        Binding("left", "focus_actions", show=False, priority=True),
        Binding("right", "focus_roster", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._action_menu_actions: list[DashboardAction] = []
        self._catalog_status = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard"):
            yield Static("", id="brand", classes="brand", markup=False)
            yield Static("", id="paths", classes="meta")
            with Horizontal(id="dashboard-body"):
                with Vertical(id="dashboard-nav"):
                    yield OptionList(id="action-menu", markup=False, compact=True)
                    yield Static("", id="harness", classes="meta")
                with Vertical(id="dashboard-main"):
                    yield DataTable(id="roster")
                    yield Static("", id="roles", classes="status")
                    yield Static("", id="issues", classes="status")

    def on_mount(self) -> None:
        self._decorate_frames()
        self.refresh_dashboard()
        self._refresh_stale_catalog()
        self._focus_default_widget()

    def on_screen_resume(self) -> None:
        self.refresh_dashboard()
        self._focus_default_widget()

    def _decorate_frames(self) -> None:
        outer = self.query_one("#dashboard", Vertical)
        outer.border_title = f"{ACCENT}  mandos  {ACCENT}"
        outer.border_subtitle = "↑↓ navigate · enter select"
        self.query_one("#dashboard-nav", Vertical).border_title = f"{NODE} Actions {NODE}"

    def refresh_dashboard(self) -> None:
        self.app.refresh_dashboard_state()
        draft = self.app.draft
        if draft is None:
            return

        issues = self.app.issues
        width = self.size.width or 80
        self.query_one("#brand", Static).update(self._brand(width))
        self.query_one("#paths", Static).update(self._paths(draft))
        self.query_one(
            "#dashboard-main", Vertical
        ).border_title = f"{NODE} {self._roster_title(draft)} {NODE}"
        self._populate_roster(draft, width)
        self.query_one("#roles", Static).update(self._roles_summary(draft, width))
        self.query_one("#issues", Static).update(self._issues_summary(issues))
        self.query_one(SEL_HARNESS, Static).update(self._harness_summary())
        self._populate_action_menu(draft)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == CATALOG_WORKER:
            self._handle_catalog_worker(event.worker, event.state)

    def _focus_default_widget(self) -> None:
        table = self.query_one(SEL_ROSTER, DataTable)
        draft = self.app.draft
        if draft is not None and not draft.parse_errors and table.row_count > 0:
            table.focus()
        else:
            self.query_one(SEL_ACTION_MENU, OptionList).focus()

    def action_focus_actions(self) -> None:
        self.query_one(SEL_ACTION_MENU, OptionList).focus()

    def action_focus_roster(self) -> None:
        draft = self.app.draft
        table = self.query_one(SEL_ROSTER, DataTable)
        if draft is not None and not draft.parse_errors and table.row_count > 0:
            table.focus()

    def _brand(self, width: int) -> str:
        inner = max(20, width - 6)
        word_width = max(len(line) for line in WORDMARK)
        if inner < word_width + 2:
            return self._compact_brand(inner)

        offset = max(0, (inner - word_width) // 2)
        lines = [(" " * offset) + line for line in WORDMARK]
        lines.append(f"{ACCENT} {BRAND_SUBTITLE} {ACCENT}".center(inner))
        lines.append(self._divider(inner))
        return "\n".join(lines)

    def _compact_brand(self, inner: int) -> str:
        return "\n".join(
            [
                BRAND_TITLE.center(inner),
                f"{ACCENT} {BRAND_SUBTITLE} {ACCENT}".center(inner),
                self._divider(inner),
            ]
        )

    def _divider(self, inner: int) -> str:
        span = max(0, inner - 3)
        left = span // 2
        right = span - left
        return f"{ACCENT}{'─' * left}{NODE}{'─' * right}{ACCENT}"

    def _paths(self, draft: Draft) -> str:
        return (
            f"config: {self._display_path(draft.source_path)}   "
            f"env: {self._display_path(draft.env_path)}"
        )

    def _roster_title(self, draft: Draft) -> str:
        if not draft.providers:
            return "Council (0) - none yet"
        return f"Council ({len(draft.providers)})"

    def _populate_roster(self, draft: Draft, width: int) -> None:
        table = self.query_one(SEL_ROSTER, DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("id", "kind", "model", "ctx", "budget", "env-var", "tok", "roles", "on")
        model_limit = 24 if width < 100 else 42
        value_limit = 18 if width < 100 else 28
        for provider in draft.providers:
            env_var = provider.api_key_env or "- (keyless)"
            token = (
                "-"
                if provider.api_key_env is None
                else self._token_status(draft, provider.api_key_env)
            )
            table.add_row(
                self._truncate(provider.id, value_limit),
                provider.kind,
                self._truncate(provider.model, model_limit),
                self._context_window(provider),
                self._budget_label(self._budget_estimate(draft, provider)),
                self._truncate(env_var, value_limit),
                token,
                self._roles(provider.roles),
                "yes" if provider.enabled else "no",
                key=provider.id,
            )

    def _populate_action_menu(self, draft: Draft) -> None:
        actions = self._available_actions(draft)
        self._action_menu_actions = actions
        menu = self.query_one(SEL_ACTION_MENU, OptionList)
        menu.clear_options()
        menu.add_options(label for _, label in actions)
        menu.disabled = not actions
        if actions:
            menu.highlighted = 0

    def _available_actions(self, draft: Draft) -> list[DashboardAction]:
        if draft.parse_errors:
            return [("wire", WIRE_HARNESSES_LABEL), ("quit", "Quit")]

        actions: list[DashboardAction] = [("add", "Add member")]
        if draft.providers:
            actions.extend(
                [
                    ("edit", "Edit selected member"),
                    ("delete", "Delete selected member"),
                    ("roles", "Reassign roles"),
                    ("judge", "Edit judge"),
                    ("defaults", "Run defaults"),
                ]
            )
        else:
            actions.append(("wire", WIRE_HARNESSES_LABEL))
            actions.append(("judge", "Edit judge"))
            actions.append(("defaults", "Run defaults"))
            actions.append(("refresh", "Refresh model catalog"))
            actions.append(("quit", "Quit"))
            return actions
        actions.extend(
            [
                ("wire", WIRE_HARNESSES_LABEL),
                ("refresh", "Refresh model catalog"),
                ("quit", "Quit"),
            ]
        )
        return actions

    def _token_status(self, draft: Draft, key: str) -> str:
        return "set" if draft.token_present.get(key, False) else "missing"

    def _roles_summary(self, draft: Draft, width: int) -> str:
        return (
            f"{NODE} Roles    Judge = {self._judge_summary(draft, width)}"
            f"    Budget = {self._budget_summary(draft)}"
        )

    def _judge_summary(self, draft: Draft, width: int) -> str:
        """Name the judge that decides.

        For a Jev shape the analyst is a supporting act (or absent), so leading with
        the chat provider would misreport what is doing the judging.
        """
        analyst = self._member_label(draft, draft.defaults.analysis_model, width)
        if not draft.judge.uses_jev:
            return f"{analyst} (llm)"
        # No square brackets: this Static renders Rich markup, which would eat
        # "[set]" as a style tag and drop the key state from the summary entirely.
        key_state = self._token_status(draft, draft.judge.resolved_api_key_env)
        jev = f"{draft.judge.model} via {draft.judge.provider}, key {key_state}"
        if draft.judge.shape == "matrix":
            return f"{jev} (matrix)"
        return f"{jev} ({draft.judge.shape}, analyst {analyst})"

    def _member_label(self, draft: Draft, member_id: str | None, width: int) -> str:
        if member_id is None:
            return "(none)"
        provider = next((p for p in draft.providers if p.id == member_id), None)
        if provider is None:
            return f"{member_id} (missing)"
        limit = 16 if width < 100 else 28
        return f"{provider.id} ({self._truncate(provider.model, limit)})"

    def _issues_summary(self, issues: list[Issue]) -> str:
        if not issues:
            draft = self.app.draft
            count = len(draft.providers) if draft is not None else 0
            return f"{NODE} Status   Fully configured - {count} members"
        ordered = sorted(issues, key=lambda issue: issue.action != "Manual config repair")
        lines = [f"{NODE} Status   {len(issues)} issue{'s' if len(issues) != 1 else ''}"]
        lines.extend(f"  - {issue.label} -> {issue.action}" for issue in ordered[:4])
        if len(ordered) > 4:
            lines.append(f"  - +{len(issues) - 4} more")
        return "\n".join(lines)

    def _harness_summary(self) -> str:
        status = self.app.harness_status
        summary = (
            f"{NODE} Harness  claude-code {self._yes_no(status.get('claude-code', False))}    "
            f"codex {self._yes_no(status.get('codex', False))}    "
            f"opencode {self._yes_no(status.get('opencode', False))}"
        )
        return f"{summary}\n{self._catalog_status}" if self._catalog_status else summary

    def _display_path(self, path: Path) -> str:
        home = self.app.home
        try:
            relative = path.expanduser().relative_to(home)
        except ValueError:
            return str(path)
        if str(relative) == ".":
            return "~"
        return f"~/{relative.as_posix()}"

    def _roles(self, roles: list[str]) -> str:
        return " | ".join(roles) if roles else "-"

    def _yes_no(self, value: bool) -> str:
        return "yes" if value else "no"

    def _context_window(self, provider) -> str:
        if provider.context_window:
            return str(provider.context_window)
        if provider.resolved_context_window:
            return str(provider.resolved_context_window)
        metadata = resolve_model_metadata(provider.catalog_key or provider.kind, provider.model)
        if metadata and metadata.context_window:
            return str(metadata.context_window)
        return "?"

    def _budget_estimate(self, draft: Draft, provider) -> BudgetEstimate:
        return estimate_provider_budget(
            provider,
            prompt="",
            context=None,
            expected_output_tokens=draft.defaults.max_tokens or 0,
            warning_ratio=draft.defaults.budget_warning_ratio,
            error_ratio=draft.defaults.budget_error_ratio,
        )

    def _budget_label(self, estimate: BudgetEstimate) -> str:
        if estimate.ratio is None:
            return "unknown"
        return f"{estimate.state} {int(estimate.ratio * 100)}%"

    def _budget_summary(self, draft: Draft) -> str:
        estimates = [self._budget_estimate(draft, provider) for provider in draft.providers]
        state = self._worst_budget_state([estimate.state for estimate in estimates])
        thresholds = (
            f"{int(draft.defaults.budget_warning_ratio * 100)}%"
            f"/{int(draft.defaults.budget_error_ratio * 100)}%"
        )
        output = draft.defaults.max_tokens or 0
        return f"{state} (out {output}, warn/error {thresholds})"

    def _worst_budget_state(self, states: list[BudgetState]) -> BudgetState:
        for state in ("red", "amber", "green"):
            if state in states:
                return state
        return "unknown"

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[: max(0, limit - 3)]}..."

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "roster":
            self.action_edit_member()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "action-menu":
            return
        try:
            action_id = self._action_menu_actions[event.option_index][0]
        except IndexError:
            return
        self._run_action_menu_item(action_id)

    def _run_action_menu_item(self, action_id: str) -> None:
        if action_id == "add":
            self.action_add_member()
        elif action_id == "edit":
            self.action_edit_member()
        elif action_id == "delete":
            self.action_delete_member()
        elif action_id == "roles":
            self.action_reassign_roles()
        elif action_id == "judge":
            self.action_edit_judge()
        elif action_id == "defaults":
            self.action_run_defaults()
        elif action_id == "refresh":
            self.action_refresh_catalog()
        elif action_id == "wire":
            self.action_wire_harnesses()
        elif action_id == "quit":
            self.app.exit(0)

    def action_add_member(self) -> None:
        draft = self.app.draft
        if draft is None or draft.parse_errors:
            return
        from orchestrator.cli.tui.screens.member_form import MemberFormScreen

        self.app.push_screen(MemberFormScreen(mode="add", dashboard=self))

    def action_edit_member(self) -> None:
        draft = self.app.draft
        if draft is None or draft.parse_errors:
            return
        member_id = self._selected_member_id()
        if member_id is None:
            return
        from orchestrator.cli.tui.screens.member_form import MemberFormScreen

        self.app.push_screen(MemberFormScreen(mode="edit", member_id=member_id, dashboard=self))

    def action_delete_member(self) -> None:
        draft = self.app.draft
        if draft is None or draft.parse_errors:
            return
        member_id = self._selected_member_id()
        if member_id is None:
            return
        from orchestrator.cli.tui.screens.delete_member import DeleteMemberScreen

        self.app.push_screen(DeleteMemberScreen(member_id=member_id, dashboard=self))

    def action_reassign_roles(self) -> None:
        draft = self.app.draft
        if draft is None or draft.parse_errors or not draft.providers:
            return
        from orchestrator.cli.tui.screens.roles import RolesScreen

        self.app.push_screen(RolesScreen(dashboard=self))

    def action_edit_judge(self) -> None:
        draft = self.app.draft
        if draft is None or draft.parse_errors:
            return
        from orchestrator.cli.tui.screens.judge import JudgeScreen

        self.app.push_screen(JudgeScreen(dashboard=self))

    def action_run_defaults(self) -> None:
        draft = self.app.draft
        if draft is None or draft.parse_errors:
            return
        from orchestrator.cli.tui.screens.defaults import DefaultsScreen

        self.app.push_screen(DefaultsScreen(dashboard=self))

    def action_wire_harnesses(self) -> None:
        from orchestrator.cli.tui.screens.harness import HarnessScreen

        self.app.push_screen(HarnessScreen(dashboard=self))

    def action_refresh_catalog(self) -> None:
        self._catalog_status = f"{NODE} Catalog  refreshing..."
        self.query_one(SEL_HARNESS, Static).update(self._harness_summary())
        self.run_worker(
            lambda: refresh_cache_if_stale(cache_path=self._catalog_cache_path(), force=True),
            name=CATALOG_WORKER,
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    def _refresh_stale_catalog(self) -> None:
        if not cache_status(self._catalog_cache_path()).stale:
            return
        self._catalog_status = f"{NODE} Catalog  stale; refreshing..."
        self.query_one(SEL_HARNESS, Static).update(self._harness_summary())
        self.run_worker(
            lambda: refresh_cache_if_stale(cache_path=self._catalog_cache_path()),
            name=CATALOG_WORKER,
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    def _catalog_cache_path(self) -> Path:
        return self.app.home / ".mandos" / "model-catalog-cache.json"

    def _handle_catalog_worker(self, worker: Worker, state: WorkerState) -> None:
        if state == WorkerState.SUCCESS and isinstance(worker.result, CatalogRefreshResult):
            result = worker.result
            if result.refreshed:
                self._catalog_status = f"{NODE} Catalog  refreshed {len(result.models)} models"
            elif result.error:
                self._catalog_status = (
                    f"{NODE} Catalog  refresh unavailable; {len(result.models)} cached/seed models"
                )
            else:
                self._catalog_status = f"{NODE} Catalog  current; {len(result.models)} models"
        elif state == WorkerState.ERROR:
            self._catalog_status = f"{NODE} Catalog  refresh unavailable"
        else:
            return
        self.query_one(SEL_HARNESS, Static).update(self._harness_summary())
        self.refresh_dashboard()

    def _selected_member_id(self) -> str | None:
        table = self.query_one(SEL_ROSTER, DataTable)
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        row = table.ordered_rows[table.cursor_row]
        return row.key.value
