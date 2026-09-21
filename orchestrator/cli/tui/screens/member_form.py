"""Add/edit member form for the Mandos configurator."""

from __future__ import annotations

import json
from typing import Any, Literal

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Select, Static
from textual.worker import Worker, WorkerState

from orchestrator.cli import probe
from orchestrator.cli.catalog import catalog_options, get_entry
from orchestrator.cli.config_ops import (
    Draft,
    add_member,
    loaded_env_values,
    persist,
    plan_token_update,
    update_member,
)
from orchestrator.cli.tui.screens.base import DraftCommitMixin
from orchestrator.cli.tui.screens.navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin
from orchestrator.model_catalog import ModelMetadata, resolve_model_metadata
from orchestrator.settings import ContextWindowSource, ProviderDescriptor

Mode = Literal["add", "edit"]
MODEL_WORKER = "member-form-models"
PROBE_WORKER = "member-form-probe"

SEL_PRESET = "#preset"
SEL_MEMBER_ID = "#member-id"
SEL_KIND = "#kind"
SEL_BASE_URL = "#base-url"
SEL_API_KEY_ENV = "#api-key-env"
SEL_MODEL_INPUT = "#model-input"
SEL_MODEL_SELECT = "#model-select"
SEL_CONTEXT_WINDOW = "#context-window"


class MemberFormScreen(DraftCommitMixin, ArrowNavigationMixin, Screen[None]):
    """Single-screen form for creating or editing a provider member."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        mode: Mode,
        member_id: str | None = None,
        dashboard: object | None = None,
    ) -> None:
        super().__init__()
        self.mode = mode
        self.member_id = member_id
        self._dashboard = dashboard
        self._catalog_prefill: dict[str, str] = {}
        self._model_contexts: dict[str, int | None] = {}
        self._model_context_sources: dict[str, ContextWindowSource] = {}
        self._resolved_context_window: int | None = None
        self._context_window_source: ContextWindowSource = "unknown"

    def compose(self) -> ComposeResult:
        title = "Add member" if self.mode == "add" else f"Edit member: {self.member_id}"
        yield Static(title, id="member-form-title", classes="screen-title")
        with Vertical(id="member-form"):
            yield Static("Provider preset", classes="field-label")
            with Horizontal(classes="form-row"):
                yield Select(
                    catalog_options(),
                    value="custom",
                    allow_blank=False,
                    id="preset",
                )
                yield Input(placeholder="search all presets", id="preset-search")
            yield Static("id", classes="field-label")
            yield Input(id="member-id")
            yield Static("kind", classes="field-label")
            yield Select(
                [("openai", "openai"), ("openrouter", "openrouter")],
                prompt="Select kind",
                allow_blank=True,
                id="kind",
            )
            yield Static("base_url", classes="field-label")
            yield Input(id="base-url")
            yield Static("api_key_env", classes="field-label")
            yield Input(id="api-key-env")
            yield Static("token", classes="field-label")
            yield Input(password=True, id="token")
            yield Static("model", classes="field-label")
            with Horizontal(classes="form-row"):
                yield Select(
                    [],
                    prompt="Fetch models or type one",
                    id="model-select",
                    disabled=True,
                )
                yield Input(id="model-input")
                yield Button("Fetch models", id="fetch-models")
            yield Static("headers", classes="field-label")
            yield Input(placeholder='{"Header-Name":"value"}', id="headers")
            with Horizontal(classes="form-row"):
                yield Static("context_window", classes="field-label")
                yield Input(placeholder="optional", id="context-window", type="integer")
                yield Static("timeout_s", classes="field-label")
                yield Input("60", id="timeout-s", type="number")
                yield Static("max_retries", classes="field-label")
                yield Input("1", id="max-retries", type="integer")
                yield Checkbox("enabled", value=True, id="enabled")
            with Horizontal(classes="form-row"):
                yield Button("Test connection", id="test-connection")
                yield Button("Save", variant="primary", id="save-member")
                yield Button("Cancel", id="cancel")
            yield Static("", id="form-status", classes="status")

    def on_mount(self) -> None:
        if self.mode == "edit":
            self._load_member()
            self.query_one(SEL_MEMBER_ID, Input).focus()
        else:
            self.query_one(SEL_PRESET, Select).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "model-select":
            if event.value is not Select.NULL:
                model = str(event.value)
                self.query_one(SEL_MODEL_INPUT, Input).value = model
                self._apply_resolved_context(model)
            return
        if event.select.id != "preset" or self.mode != "add" or event.value is Select.NULL:
            return
        entry = get_entry(str(event.value))
        if entry is None:
            return
        self._prefill_catalog_value(SEL_BASE_URL, entry.base_url)
        self._prefill_catalog_value(SEL_API_KEY_ENV, entry.default_api_key_env or "")
        kind = self.query_one(SEL_KIND, Select)
        current_kind = "" if kind.value is Select.NULL else str(kind.value)
        if not current_kind or current_kind == self._catalog_prefill.get(SEL_KIND):
            kind.value = entry.kind
            self._catalog_prefill[SEL_KIND] = entry.kind
        self._set_resolved_context_window(entry.default_context_window, "modelsdev")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "preset-search" and self.mode == "add":
            self._set_preset_options(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fetch-models":
            self.action_fetch_models()
        elif event.button.id == "test-connection":
            self.action_test_connection()
        elif event.button.id == "save-member":
            self.action_save()
        elif event.button.id == "cancel":
            self.action_cancel()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == MODEL_WORKER:
            self._handle_model_worker(event.worker, event.state)
        elif event.worker.name == PROBE_WORKER:
            self._handle_probe_worker(event.worker, event.state)

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_fetch_models(self) -> None:
        base_url = self._input(SEL_BASE_URL)
        if not base_url:
            self._set_status("base_url is required before fetching models")
            return
        try:
            headers = self._headers()
        except ValueError as exc:
            self._set_status(str(exc))
            return
        token = self._probe_token()
        self._set_status("Fetching models...")
        self.run_worker(
            lambda: probe.list_model_metadata(
                base_url,
                token,
                headers,
                provider_key=self._catalog_key(),
            ),
            name=MODEL_WORKER,
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    def action_test_connection(self) -> None:
        base_url = self._input(SEL_BASE_URL)
        model = self._model_value()
        if not base_url:
            self._set_status("base_url is required before testing")
            return
        if not model:
            self._set_status("model is required before testing")
            return
        try:
            headers = self._headers()
        except ValueError as exc:
            self._set_status(str(exc))
            return
        token = self._probe_token()
        self._set_status("Testing connection...")
        self.run_worker(
            lambda: probe.check_member(base_url, token, model, headers),
            name=PROBE_WORKER,
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    def action_save(self) -> None:
        draft = self._draft()

        def apply() -> None:
            member_data = self._member_data()
            token_changes, prune_env_keys = self._token_plan(draft, member_data)
            if self.mode == "add":
                updated = add_member(draft, member_data, token_changes=token_changes)
            else:
                if self.member_id is None:
                    raise ValueError("member id is required for edit")
                updated = update_member(
                    draft,
                    self.member_id,
                    token_changes=token_changes,
                    **member_data,
                )
            persist(
                updated,
                token_changes,
                draft.source_path,
                prune_env_keys,
                env_path=draft.env_path,
            )

        self.commit(draft, apply)

    def _load_member(self) -> None:
        draft = self._draft()
        provider = None
        if draft is not None:
            provider = next((item for item in draft.providers if item.id == self.member_id), None)
        if provider is None:
            self._set_status(f"unknown provider: {self.member_id}")
            return

        self.query_one(SEL_MEMBER_ID, Input).value = provider.id
        self.query_one(SEL_KIND, Select).value = provider.kind
        self.query_one(SEL_BASE_URL, Input).value = provider.base_url
        self.query_one(SEL_API_KEY_ENV, Input).value = provider.api_key_env or ""
        self.query_one("#headers", Input).value = (
            json.dumps(provider.headers) if provider.headers else ""
        )
        self.query_one("#timeout-s", Input).value = str(provider.timeout_s)
        self.query_one("#max-retries", Input).value = str(provider.max_retries)
        self.query_one("#enabled", Checkbox).value = provider.enabled
        self._set_model_choices([provider.model], provider.model)
        self._set_manual_context_window(provider.context_window)
        self._set_resolved_context_window(
            provider.resolved_context_window,
            provider.context_window_source,
        )

    def _draft(self) -> Draft | None:
        if self.app.draft is None:
            self.app.refresh_dashboard_state()
        return self.app.draft

    def _member_data(self) -> dict[str, Any]:
        model = self._model_value()
        if not model:
            raise ValueError("model is required")
        manual_context_window = self._optional_int(SEL_CONTEXT_WINDOW)
        context_window_source = self._context_window_source
        if manual_context_window is not None and self._resolved_context_window is None:
            context_window_source = "override"
        data = {
            "id": self._input(SEL_MEMBER_ID),
            "kind": self._select_value(SEL_KIND),
            "catalog_key": self._catalog_key(),
            "base_url": self._input(SEL_BASE_URL),
            "model": model,
            "context_window": manual_context_window,
            "resolved_context_window": self._resolved_context_window,
            "context_window_source": context_window_source,
            "roles": self._existing_roles(),
            "enabled": self.query_one("#enabled", Checkbox).value,
            "api_key_env": self._input(SEL_API_KEY_ENV) or None,
            "headers": self._headers(),
            "timeout_s": self._float("#timeout-s"),
            "max_retries": self._int("#max-retries"),
        }
        try:
            ProviderDescriptor.model_validate(data)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        return data

    def _token_plan(
        self, draft: Draft, member_data: dict[str, Any]
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        token = self._input("#token")
        api_key_env = member_data.get("api_key_env")
        enabled = bool(member_data.get("enabled", True))
        env = loaded_env_values(draft.env_path)

        if self.mode == "edit":
            return self._token_plan_edit(draft, api_key_env, token, enabled, env)
        return self._token_plan_add(api_key_env, token, enabled, env)

    def _token_plan_edit(
        self,
        draft: Draft,
        api_key_env: str | None,
        token: str,
        enabled: bool,
        env: dict[str, str],
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        if self.member_id is None:
            raise ValueError("member id is required for edit")
        plan = plan_token_update(
            draft,
            self.member_id,
            api_key_env=api_key_env,
            token=token,
            loaded_env=env,
        )
        changes = dict(plan.changes)
        if not token and api_key_env and api_key_env not in changes:
            if env.get(str(api_key_env)):
                changes[str(api_key_env)] = env[str(api_key_env)]
            elif enabled:
                raise ValueError(f"token required for {api_key_env}")
        return changes, plan.prune_env_keys

    def _token_plan_add(
        self,
        api_key_env: str | None,
        token: str,
        enabled: bool,
        env: dict[str, str],
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        if token:
            if not api_key_env:
                raise ValueError("api_key_env is required when a token is provided")
            return {str(api_key_env): token}, ()
        if api_key_env and env.get(str(api_key_env)):
            return {str(api_key_env): env[str(api_key_env)]}, ()
        if enabled and api_key_env:
            raise ValueError(f"token required for {api_key_env}")
        return {}, ()

    def _existing_roles(self) -> list[str]:
        if self.mode == "add":
            return ["panel"]
        draft = self._draft()
        if draft is None:
            return ["panel"]
        provider = next((item for item in draft.providers if item.id == self.member_id), None)
        return list(provider.roles) if provider is not None else ["panel"]

    def _handle_model_worker(self, worker: Worker, state: WorkerState) -> None:
        if state == WorkerState.SUCCESS:
            models = worker.result
            if isinstance(models, list) and models:
                metadata = [item for item in models if isinstance(item, ModelMetadata)]
                if metadata:
                    self._model_contexts = {model.id: model.context_window for model in metadata}
                    self._model_context_sources = {
                        model.id: self._source_from_metadata(model) for model in metadata
                    }
                    self._set_model_choices([model.id for model in metadata], metadata[0].id)
                else:
                    self._model_contexts = {}
                    self._model_context_sources = {}
                    self._set_model_choices([str(model) for model in models], str(models[0]))
                self._set_status(f"Loaded {len(models)} models{self._context_status_suffix()}")
            else:
                self._use_free_text_model()
                self._set_status("Model list unavailable; type the model id")
        elif state == WorkerState.ERROR:
            self._use_free_text_model()
            self._set_status("Model list unavailable; type the model id")

    def _handle_probe_worker(self, worker: Worker, state: WorkerState) -> None:
        if state == WorkerState.SUCCESS:
            result = worker.result
            detail = f" ({result.detail})" if getattr(result, "detail", "") else ""
            self._set_status(f"Connection: {result.status}{detail}")
        elif state == WorkerState.ERROR:
            self._set_status("Connection: other")

    def _set_model_choices(self, models: list[str], selected: str | None = None) -> None:
        select = self.query_one(SEL_MODEL_SELECT, Select)
        select.disabled = False
        select.set_options([(model, model) for model in models])
        select.value = selected or models[0]
        model_input = self.query_one(SEL_MODEL_INPUT, Input)
        model_input.value = selected or models[0]
        model_input.disabled = True
        selected_model = selected or models[0]
        self._apply_resolved_context(selected_model)

    def _apply_resolved_context(self, selected_model: str) -> None:
        if selected_model in self._model_contexts:
            self._set_resolved_context_window(
                self._model_contexts[selected_model],
                self._model_context_sources.get(selected_model, "endpoint"),
            )
        else:
            metadata = resolve_model_metadata(self._catalog_key(), selected_model)
            self._set_resolved_context_window(
                metadata.context_window if metadata else None,
                self._source_from_metadata(metadata) if metadata is not None else "unknown",
            )

    def _use_free_text_model(self) -> None:
        select = self.query_one(SEL_MODEL_SELECT, Select)
        select.set_options([])
        select.value = Select.NULL
        select.disabled = True
        self.query_one(SEL_MODEL_INPUT, Input).disabled = False
        self._set_resolved_context_window(None, "unknown")

    def _model_value(self) -> str:
        select = self.query_one(SEL_MODEL_SELECT, Select)
        if not select.disabled and select.value is not Select.NULL:
            return str(select.value).strip()
        return self._input(SEL_MODEL_INPUT)

    def _probe_token(self) -> str | None:
        token = self._input("#token")
        if token:
            return token
        api_key_env = self._input(SEL_API_KEY_ENV)
        draft = self._draft()
        env_path = draft.env_path if draft is not None else self.app.env_path
        return loaded_env_values(env_path).get(api_key_env) if api_key_env else None

    def _headers(self) -> dict[str, str]:
        raw = self._input("#headers")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"headers must be JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise ValueError("headers must be a JSON object of string values")
        return parsed

    def _input(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _select_value(self, selector: str) -> str:
        value = self.query_one(selector, Select).value
        return "" if value is Select.NULL else str(value)

    def _float(self, selector: str) -> float:
        value = self._input(selector)
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{selector.removeprefix('#')} must be a number") from exc

    def _int(self, selector: str) -> int:
        value = self._input(selector)
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{selector.removeprefix('#')} must be an integer") from exc

    def _optional_int(self, selector: str) -> int | None:
        value = self._input(selector)
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{selector.removeprefix('#')} must be an integer") from exc
        if parsed <= 0:
            raise ValueError(f"{selector.removeprefix('#')} must be positive")
        return parsed

    def _prefill_catalog_value(self, selector: str, value: str) -> None:
        field = self.query_one(selector, Input)
        current = field.value.strip()
        if not current or current == self._catalog_prefill.get(selector, ""):
            field.value = value
            self._catalog_prefill[selector] = value

    def _set_preset_options(self, query: str) -> None:
        preset = self.query_one(SEL_PRESET, Select)
        current = None if preset.value is Select.NULL else str(preset.value)
        options = catalog_options(query)
        values = {value for _, value in options}
        preset.set_options(options)
        if current in values:
            preset.value = current
        elif "custom" in values:
            preset.value = "custom"
        elif options:
            preset.value = options[0][1]

    def _catalog_key(self) -> str:
        value = self.query_one(SEL_PRESET, Select).value
        if value is not Select.NULL and (self.mode == "add" or str(value) != "custom"):
            return str(value)
        if self.mode == "edit":
            draft = self._draft()
            if draft is not None:
                provider = next(
                    (item for item in draft.providers if item.id == self.member_id),
                    None,
                )
                if provider is not None:
                    return provider.catalog_key or provider.kind
        return self._select_value(SEL_KIND) or "custom"

    def _set_manual_context_window(self, value: int | None) -> None:
        if value is not None:
            self.query_one(SEL_CONTEXT_WINDOW, Input).value = str(value)

    def _set_resolved_context_window(
        self,
        value: int | None,
        source: ContextWindowSource,
    ) -> None:
        self._resolved_context_window = value if value and value > 0 else None
        self._context_window_source = source if self._resolved_context_window else "unknown"
        field = self.query_one(SEL_CONTEXT_WINDOW, Input)
        field.placeholder = (
            f"override {self._resolved_context_window} ({self._context_window_source})"
            if self._resolved_context_window
            else "optional"
        )

    def _source_from_metadata(self, metadata: ModelMetadata | None) -> ContextWindowSource:
        if metadata is None:
            return "unknown"
        if metadata.source == "live":
            return "endpoint"
        if metadata.source in {"seed", "cache", "modelsdev"}:
            return "modelsdev"
        return "unknown"

    def _context_status_suffix(self) -> str:
        if self._resolved_context_window is None:
            return ""
        return f"; context {self._resolved_context_window} ({self._context_window_source})"

    def _set_status(self, message: str) -> None:
        self.query_one("#form-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
