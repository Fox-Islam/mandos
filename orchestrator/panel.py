from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from orchestrator.budget import BudgetEstimate, classify_budget, estimate_provider_budget
from orchestrator.costing import CostInput, estimate_cost
from orchestrator.http import shared_client
from orchestrator.jev import build_jev_client
from orchestrator.judge import JudgeOutcome, build_judge_user, run_judge
from orchestrator.model_catalog import metadata_from_provider_override, resolve_model_metadata
from orchestrator.models import (
    ChatRequest,
    ChatResult,
    DeliberateRequest,
    DeliberationResponse,
    PanelAnswer,
    RawAnswer,
)
from orchestrator.observability import get_logger
from orchestrator.providers.factory import build_provider
from orchestrator.sessions import (
    append_turn,
    apply_prior_answer,
    build_messages,
    estimate_messages_tokens,
    load_session,
    prune_sessions,
    session_lock,
    write_session,
)
from orchestrator.transcript import capture_key, read_capture
from orchestrator.transcript import render as render_turns

SYSTEM = (
    "Answer independently and thoroughly. Do not refer to other panelists. "
    "If answering well needs information you have not been given, say plainly what "
    "is missing and what you are assuming in its place, then answer under that "
    "assumption. Name the measurement that would settle it, not the topic."
)

CONTRACT_VERSION = "1"

log = get_logger("mandos.panel")

_FAILURE_PRIORITY = ("insufficient_credits", "rate_limited", "all_panels_failed")
_HTTP_CODE_RE = re.compile(r"\bHTTP (\d{3})\b")


def _classify_one(error: str) -> str:
    """Classify a single provider error, preferring the structured ``HTTP <code>``
    prefix the provider emits over loose substring matches."""
    text = error.lower()
    code_match = _HTTP_CODE_RE.search(error)
    code = code_match.group(1) if code_match else None
    if code == "402" or any(t in text for t in ("insufficient", "quota", "payment required")):
        return "insufficient_credits"
    if code == "429" or "rate limit" in text or "rate-limit" in text or "too many requests" in text:
        return "rate_limited"
    return "all_panels_failed"


def _classify_failure(errors: list[str]) -> str:
    """Aggregate per-provider classifications by fixed priority (order-independent).

    An all-timeout batch folds into ``all_panels_failed`` by design (timeouts are not
    a distinct ``FailureKind``); each provider's own ``error`` still carries the
    timeout/deadline string for the host."""
    seen = {_classify_one(e) for e in errors if e}
    return next((kind for kind in _FAILURE_PRIORITY if kind in seen), "all_panels_failed")


def _log_error_kind(error: str | None) -> str | None:
    if not error:
        return None
    code_match = _HTTP_CODE_RE.search(error)
    if code_match:
        return f"HTTP {code_match.group(1)}"
    text = error.lower()
    if "overall deadline exceeded" in text:
        return "deadline_exceeded"
    if "timeout" in text:
        return "timeout"
    if "connection" in text:
        return "connection_error"
    return "provider_error"


def failure_response(
    question: str,
    *,
    failure: str,
    error: str,
    depth: int = 0,
    thread_id: str | None = None,
    judge_shape: str | None = None,
) -> DeliberationResponse:
    """Build a contract-stable failure envelope.

    Every non-success return (depth cap, resolution/config error, ``ok == 0``)
    carries the same ``meta`` key set as a successful run so a host can read
    ``meta["panel_size"]`` / ``meta["cost_estimate_usd"]`` unconditionally, plus a
    typed ``failure`` and an ``error`` message. Renders ``text`` so the host always
    has something to author from."""
    basis = estimate_cost([], None)
    resp = DeliberationResponse(
        question=question,
        thread_id=thread_id,
        meta={
            "contract_version": CONTRACT_VERSION,
            "elapsed_ms": 0,
            "depth": depth,
            "panel_size": 0,
            "ok": 0,
            "failed": 0,
            "preset": None,
            "analysis_model": None,
            "judge_shape": judge_shape,
            "evidence_outstanding": 0,
            "passes_remaining": 0,
            "judge_provider": None,
            "judge_model": None,
            "judge_fallback_from": None,
            "jev_calls": 0,
            "jev_questions": 0,
            "budget": [],
            "compacted": False,
            "compacted_providers": [],
            "failure": failure,
            "error": error,
            "cost_estimate_usd": basis["usd"],
            "cost_basis": {**{k: v for k, v in basis.items() if k != "usd"}, "jev_usd": None},
        },
    )
    resp.text = _render(resp)
    log.warning("deliberation.failure", failure=failure, error=error[:200], depth=depth)
    return resp


def _resolve_panel(request: DeliberateRequest, config, preset) -> list[str]:
    if request.panel:
        ids = list(request.panel)
    elif preset:
        ids = list(preset.panel)
    else:
        ids = [p.id for p in config.providers if p.enabled and "panel" in p.roles]
    if not ids:
        raise ValueError("effective panel is empty")
    if len(ids) > 8:
        raise ValueError("effective panel exceeds 8 members")
    if len(ids) != len(set(ids)):
        raise ValueError("effective panel contains duplicate providers")
    provider_desc = config.provider_map()
    bad = [
        pid for pid in ids if pid not in provider_desc or "panel" not in provider_desc[pid].roles
    ]
    if bad:
        raise ValueError(f"unknown or non-panel providers: {bad}")
    return ids


def _provider_catalog_key(provider) -> str:
    return provider.catalog_key or provider.kind


def _catalog_price(provider_desc, provider_id: str) -> tuple[float, float] | None:
    """Per-million-token prices from the catalog for one provider, if it knows them."""
    provider = provider_desc.get(provider_id)
    if provider is None:
        return None
    metadata = resolve_model_metadata(_provider_catalog_key(provider), provider.model)
    if metadata is None or metadata.input_cost is None:
        return None
    return metadata.input_cost, metadata.output_cost or 0.0


def _context_window_for(provider) -> int | None:
    if provider.context_window:
        return provider.context_window
    if provider.resolved_context_window:
        return provider.resolved_context_window
    metadata = resolve_model_metadata(_provider_catalog_key(provider), provider.model)
    if metadata is not None:
        return metadata.context_window
    override = metadata_from_provider_override(provider)
    return override.context_window if override else None


def _budget_meta(
    request: DeliberateRequest,
    config,
    panel_ids: list[str],
    *,
    session_estimates: dict[str, int] | None = None,
) -> list[dict]:
    expected_output = request.max_tokens or config.defaults.max_tokens or 0
    provider_desc = config.provider_map()
    budgets = []
    for pid in panel_ids:
        provider = provider_desc[pid]
        context_window = _context_window_for(provider)
        if session_estimates is not None and pid in session_estimates:
            state, ratio = classify_budget(
                session_estimates[pid],
                context_window,
                warning_ratio=config.defaults.budget_warning_ratio,
                error_ratio=config.defaults.budget_error_ratio,
            )
            budgets.append(
                BudgetEstimate(
                    provider_id=provider.id,
                    model=provider.model,
                    context_window=context_window,
                    estimated_tokens=session_estimates[pid],
                    state=state,
                    ratio=ratio,
                ).as_dict()
            )
            continue
        budgets.append(
            estimate_provider_budget(
                provider,
                prompt=request.prompt,
                context=request.context,
                context_window=context_window,
                expected_output_tokens=expected_output,
                warning_ratio=config.defaults.budget_warning_ratio,
                error_ratio=config.defaults.budget_error_ratio,
            ).as_dict()
        )
    return budgets


def _claim_index(analysis) -> dict[tuple[str, int], object]:
    """Findings keyed by the narrative slot they belong to, so the renderer can put
    each number next to the sentence it is about."""
    if analysis.calibration is None:
        return {}
    return {(c.role, c.index): c for c in analysis.calibration.claims}


def _numbers(claim) -> str:
    """The calibrated readings for one finding, rendered inline.

    This is the part the authoring model is meant to act on: "consensus" with a
    support floor of 0.51 is a very different instruction from the same sentence at
    0.94, and a generative judge cannot tell you which one you have.
    """
    if claim is None:
        return ""
    bits = []
    if claim.holds is not None:
        bits.append(f"holds {claim.holds:.2f}")
    if claim.support:
        low = min(s.support for s in claim.support)
        high = max(s.support for s in claim.support)
        bits.append(f"support {low:.2f}-{high:.2f}")
    if claim.contested is not None:
        bits.append(f"contested {claim.contested:.2f}")
    if claim.standing is not None:
        bits.append(f"standing {claim.standing:.2f}/2")
    return f" _({'; '.join(bits)})_" if bits else ""


# A gap worth another pass: likely enough to change the answer that fetching it beats
# guessing. Below this, the caller is better off noting the assumption and moving on.
WORTH_FETCHING = 0.5


def _outstanding(analysis) -> int:
    """How many gaps are decisive enough to justify convening again."""
    if analysis is None:
        return 0
    return sum(1 for e in analysis.needs_evidence if (e.would_change or 0) >= WORTH_FETCHING)


def _render_evidence(item) -> str:
    """One thing the panel lacked. ``would_change`` leads the numbers because it is
    what decides whether fetching it is worth a second pass."""
    bits = []
    if item.would_change is not None:
        bits.append(f"would change the answer {item.would_change:.2f}")
    if item.lacked is not None:
        bits.append(f"lacked {item.lacked:.2f}")
    return f"- {item.item} _({'; '.join(bits)})_" if bits else f"- {item.item}"


def _render_contradictions(contradictions, index) -> list[str]:
    lines = ["\n**Contradictions**"]
    for i, c in enumerate(contradictions):
        lines.append(f"- *{c.topic}*{_numbers(index.get(('contradiction', i)))}")
        lines.extend(f"  - [{', '.join(p.ids)}] {p.claim}" for p in c.positions)
    return lines


def _render_matrix(cal) -> list[str]:
    """The matrix shape has no prose to attach numbers to, so it renders as tables."""
    lines: list[str] = []
    if cal.agreement:
        lines.append("\n**Pairwise agreement**")
        lines.extend(
            f"- {pair.ids[0]} vs {pair.ids[1]}: {pair.agreement:.2f}"
            for pair in sorted(cal.agreement, key=lambda p: p.agreement)
        )
    if cal.per_answer:
        lines.append("\n**Per answer** (hedging and scope are rubric levels out of 2)")
        for profile in cal.per_answer:
            readings = []
            if profile.scope is not None:
                readings.append(f"scope {profile.scope:.2f}")
            if profile.hedging is not None:
                readings.append(f"hedging {profile.hedging:.2f}")
            if profile.distinctive is not None:
                readings.append(f"distinctive {profile.distinctive:.2f}")
            lines.append(f"- **{profile.id}**: {', '.join(readings) or 'no readings'}")
    if cal.outlier and cal.outlier.id:
        confidence = (
            f" at {cal.outlier.confidence:.2f}" if cal.outlier.confidence is not None else ""
        )
        lines.append(f"\n**Least like the others:** {cal.outlier.id}{confidence}")
    return lines


def _render_analysis(an) -> list[str]:
    shape = an.calibration.shape if an.calibration else "llm"
    lines = [f"\n## Analysis\n\n*Judge: {shape}.*"]
    index = _claim_index(an)
    if an.consensus:
        lines.append("\n**Consensus**")
        lines.extend(
            f"- {c}{_numbers(index.get(('consensus', i)))}" for i, c in enumerate(an.consensus)
        )
    if an.contradictions:
        lines.extend(_render_contradictions(an.contradictions, index))
    if an.partial_coverage:
        lines.append("\n**Partial coverage**")
        lines.extend(
            f"- [{', '.join(pc.ids)}] {pc.point}{_numbers(index.get(('partial_coverage', i)))}"
            for i, pc in enumerate(an.partial_coverage)
        )
    if an.unique_insights:
        lines.append("\n**Unique insights**")
        lines.extend(
            f"- [{u.id}] {u.insight}{_numbers(index.get(('unique_insight', i)))}"
            for i, u in enumerate(an.unique_insights)
        )
    if an.blind_spots:
        lines.append("\n**Blind spots**")
        lines.extend(
            f"- {b}{_numbers(index.get(('blind_spot', i)))}" for i, b in enumerate(an.blind_spots)
        )
    if an.needs_evidence:
        lines.append("\n**The panel was missing** (fetch and re-run if it matters)")
        lines.extend(_render_evidence(e) for e in an.needs_evidence)
    if an.calibration:
        lines.extend(_render_matrix(an.calibration))
    if an.confidence_notes:
        lines.append(f"\n**Confidence:** {an.confidence_notes}")
    return lines


def _render(resp: DeliberationResponse) -> str:
    lines = [f"# Mandos deliberation\n\n**Question:** {resp.question}\n"]
    lines.append("## Panel")
    for a in resp.panel:
        suffix = f" — {a.error}" if a.error else ""
        lines.append(f"- **{a.id}** ({a.status}, {a.latency_ms} ms){suffix}")
    if resp.analysis:
        lines.extend(_render_analysis(resp.analysis))
    if resp.raw_answers:
        lines.append("\n## Raw answers")
        for r in resp.raw_answers:
            lines.append(f"\n### {r.id}\n{r.answer}")
    return "\n".join(lines)


def _resolve_analysis_id(request: DeliberateRequest, preset, config) -> str | None:
    return request.analysis_model or (
        (preset.analysis if preset else None) or config.defaults.analysis_model
    )


def _resolve_analysis_provider(analysis_id: str | None, provider_desc, client):
    """Resolve the judge provider for ``analysis_id``, enforcing enablement **and**
    the ``judge`` role before building it.

    Config-time validation only covers ``defaults.analysis_model`` and
    ``preset.analysis``; a per-request ``analysis_model`` override bypasses it, so it
    must be re-checked here. Returns ``(provider, judge_error)``: on any mismatch the
    provider is ``None`` and a ``judge_error`` string is returned so the pipeline
    degrades gracefully - the offending provider never receives the
    ``ANALYSIS_SYSTEM`` prompt and raw answers are still returned."""
    if not analysis_id:
        return None, None
    descriptor = provider_desc.get(analysis_id)
    if descriptor is None:
        return None, f"analysis_model {analysis_id!r} is not an enabled provider"
    if "judge" not in descriptor.roles:
        return None, f"analysis_model {analysis_id!r} is not a judge-role provider"
    return build_provider(descriptor, client), None


def _compose_user(prompt: str, context: str | None, conversation: str = "") -> str:
    """The panel's user message.

    The conversation leads, because it is the setting the question was asked in; the
    question and the caller's explicit context follow, because they are what is being
    asked. Empty parts vanish, so a one-shot call with no capture composes exactly as
    it always did.
    """
    parts = [conversation] if conversation else []
    parts.append(prompt)
    if context:
        parts.append(f"CONTEXT:\n{context}")
    return "\n\n".join(parts)


def _conversation(request: DeliberateRequest, config) -> str:
    """Whatever a harness hook captured for this session, or "" when there is none.

    Never raises and never blocks: no capture, a stale one, or an unreadable one all
    mean the panel is told what it would have been told before this feature existed.
    """
    wanted = request.use_conversation
    if wanted is False or (wanted is None and not config.context.from_transcript):
        return ""
    turns = read_capture(
        key=capture_key(),
        max_age_s=config.context.max_age_s,
    )
    if not turns:
        return ""
    return render_turns(turns[-config.context.max_turns :])


def _sent_input_text(chat_request: ChatRequest, fallback: str) -> str:
    """The text sent to a provider, for the missing-usage cost fallback: the
    full reconstructed history in session mode, else the composed one-shot prompt."""
    if chat_request.messages is not None:
        return "\n".join(message.content for message in chat_request.messages)
    return fallback


def _apply_jev_cost(meta: dict, outcome: JudgeOutcome) -> None:
    """Fold Jev's own reported charge into the advisory total.

    Only OpenRouter prices a decision call; TypeSafe does not report one. ``None`` and
    ``0.0`` are different answers, so an unpriced judge leaves ``jev_usd`` null rather
    than claiming the judging was free.
    """
    meta["cost_basis"]["jev_usd"] = (
        round(outcome.jev_cost, 6) if outcome.jev_cost is not None else None
    )
    if outcome.jev_cost is not None:
        meta["cost_estimate_usd"] = round(meta["cost_estimate_usd"] + outcome.jev_cost, 6)


def _apply_cost(meta: dict, calls: list[CostInput], pricing) -> None:
    """Set the advisory ``cost_estimate_usd`` float plus the ``cost_basis`` detail
    (coverage + per-provider breakdown) on ``meta`` from one estimate."""
    basis = estimate_cost(calls, pricing)
    meta["cost_estimate_usd"] = basis["usd"]
    meta["cost_basis"] = {k: v for k, v in basis.items() if k != "usd"}


def _build_chat_requests(
    request: DeliberateRequest,
    config,
    providers,
    provider_desc,
    session: dict | None,
) -> tuple[dict[str, ChatRequest], dict[str, bool], dict[str, int]]:
    user = _compose_user(request.prompt, request.context, _conversation(request, config))
    max_tokens = request.max_tokens or config.defaults.max_tokens
    expected_output = max_tokens or 0
    temperature = (
        request.temperature if request.temperature is not None else config.defaults.temperature
    )
    compacted_by_provider: dict[str, bool] = {}
    session_estimates: dict[str, int] = {}
    chat_requests: dict[str, ChatRequest] = {}
    for provider in providers:
        provider_config = provider_desc[provider.id]
        messages = None
        if session is not None:
            messages, provider_compacted = build_messages(
                session,
                system=SYSTEM,
                prompt=request.prompt,
                context=request.context,
                max_history_turns=config.defaults.session_max_turns,
                context_window=_context_window_for(provider_config),
                expected_output_tokens=expected_output,
                warning_ratio=config.defaults.budget_warning_ratio,
            )
            compacted_by_provider[provider.id] = provider_compacted
            session_estimates[provider.id] = estimate_messages_tokens(
                messages,
                expected_output_tokens=expected_output,
            )
        chat_requests[provider.id] = ChatRequest(
            system=SYSTEM,
            user=user,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=request.reasoning_effort,
        )
    return chat_requests, compacted_by_provider, session_estimates


ProgressFn = Callable[[float, float, str], Awaitable[None]]


async def _report(on_progress: ProgressFn | None, done: float, total: float, message: str) -> None:
    """Best-effort progress. A host that cannot receive it must not fail the run."""
    if on_progress is None:
        return
    try:
        await on_progress(done, total, message)
    except Exception:  # noqa: BLE001
        pass


async def _call_provider(provider, chat_request: ChatRequest, deadline: float) -> ChatResult:
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("overall deadline exceeded")
        return await asyncio.wait_for(
            provider.complete(chat_request, deadline=deadline),
            timeout=remaining,
        )
    except Exception as exc:  # noqa: BLE001
        return ChatResult(
            provider_id=provider.id,
            model=getattr(provider, "model", ""),
            status="error",
            error=str(exc) or type(exc).__name__,
        )


async def run_deliberation(
    request: DeliberateRequest,
    config,
    *,
    on_progress: ProgressFn | None = None,
) -> DeliberationResponse:
    """Run one deliberation.

    ``on_progress`` is called as each panel member lands and once the judge finishes.
    A deliberation is a single blocking tool call that can take a minute and a half;
    without this the host shows nothing at all while several models think.
    """
    if request.thread_id:
        async with session_lock(request.thread_id):
            session_warning = None
            try:
                session = load_session(request.thread_id)
            except Exception as exc:  # noqa: BLE001
                session = {"thread_id": request.thread_id, "turns": []}
                session_warning = f"session load failed: {type(exc).__name__}"
            apply_prior_answer(session, request.prior_answer)
            response = await _run_deliberation(
                request, config, session=session, on_progress=on_progress
            )
            if session_warning:
                response.meta["session_warning"] = session_warning
            append_turn(
                session,
                prompt=request.prompt,
                context=request.context,
                raw_answers=response.raw_answers,
                analysis=response.analysis,
                compacted=response.compacted,
            )
            try:
                write_session(session)
            except Exception as exc:  # noqa: BLE001
                response.meta["session_warning"] = (
                    f"{response.meta.get('session_warning')}; " if session_warning else ""
                ) + f"session write failed: {type(exc).__name__}"
            # Opportunistic: an abandoned thread ages out without anyone running a
            # command. Failure here must never cost the caller their answer.
            try:
                pruned = prune_sessions(max_age_days=config.defaults.session_max_age_days)
                if pruned:
                    log.info("sessions.pruned", count=len(pruned))
            except Exception:  # noqa: BLE001
                pass
            return response
    return await _run_deliberation(request, config, on_progress=on_progress)


async def _run_deliberation(
    request: DeliberateRequest,
    config,
    *,
    session: dict | None = None,
    on_progress: ProgressFn | None = None,
) -> DeliberationResponse:
    if request.depth >= config.defaults.max_depth:
        return failure_response(
            request.prompt,
            failure="fusion_invocation_capped",
            error="max recursion depth exceeded",
            depth=request.depth,
            thread_id=request.thread_id,
        )

    started = time.perf_counter()
    deadline = time.monotonic() + (request.timeout_s or config.defaults.timeout_s)
    provider_desc = config.provider_map()
    try:
        preset = config.presets.get(request.preset or config.defaults.preset or "")
        panel_ids = _resolve_panel(request, config, preset)
        analysis_id = _resolve_analysis_id(request, preset, config)
    except ValueError as exc:
        return failure_response(
            request.prompt,
            failure="unexpected_error",
            error=str(exc),
            depth=request.depth,
            thread_id=request.thread_id,
        )

    log.info(
        "deliberation.start",
        panel=panel_ids,
        judge_shape=request.judge_shape or config.judge.shape,
        analysis_model=analysis_id,
        preset=request.preset or config.defaults.preset,
        depth=request.depth,
        session=bool(request.thread_id),
        prompt_chars=len(request.prompt),
    )

    # One pooled client for the process, so concurrent deliberations reuse
    # connections instead of each paying for its own handshakes.
    client = shared_client()
    providers = [build_provider(provider_desc[pid], client) for pid in panel_ids]
    chat_requests, compacted_by_provider, session_estimates = _build_chat_requests(
        request, config, providers, provider_desc, session
    )
    compacted = any(compacted_by_provider.values())

    total = len(providers) + 1  # panel members, then the judge
    landed = 0

    async def _tracked(provider) -> ChatResult:
        nonlocal landed
        result = await _call_provider(provider, chat_requests[provider.id], deadline)
        landed += 1
        await _report(on_progress, landed, total, f"{provider.id} answered ({result.status})")
        return result

    await _report(on_progress, 0, total, f"asking {len(providers)} panel members")
    results = await asyncio.gather(*(_tracked(p) for p in providers))

    panel = [
        PanelAnswer(
            id=r.provider_id,
            model=r.model,
            status=r.status,
            error=r.error,
            latency_ms=r.latency_ms,
            tokens=r.usage,
            finish_reason=r.finish_reason,
        )
        for r in results
    ]
    for r in results:
        log.info(
            "provider.result",
            provider=r.provider_id,
            status=r.status,
            attempts=r.attempts,
            latency_ms=r.latency_ms,
            error_kind=_log_error_kind(r.error),
        )
    ok_results = [r for r in results if r.status == "ok"]
    raw = [RawAnswer(id=r.provider_id, model=r.model, answer=r.text) for r in ok_results]

    meta = {
        "contract_version": CONTRACT_VERSION,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "depth": request.depth,
        "panel_size": len(panel),
        "ok": len(ok_results),
        "failed": len(panel) - len(ok_results),
        "preset": request.preset or config.defaults.preset,
        "analysis_model": analysis_id,
        "judge_shape": request.judge_shape or config.judge.shape,
        "judge_provider": config.judge.provider if config.judge.uses_jev else None,
        "judge_model": config.judge.model if config.judge.uses_jev else None,
        "budget": _budget_meta(
            request,
            config,
            panel_ids,
            session_estimates=session_estimates if session is not None else None,
        ),
        "compacted": compacted,
        "compacted_providers": sorted(
            provider_id
            for provider_id, was_compacted in compacted_by_provider.items()
            if was_compacted
        ),
    }

    if not ok_results:
        meta["judge_fallback_from"] = None
        meta["jev_calls"] = 0
        meta["jev_questions"] = 0
        meta["failure"] = _classify_failure([r.error or "" for r in results])
        meta["error"] = "all panel providers failed"
        _apply_cost(meta, [CostInput(r.provider_id, r.usage) for r in results], config.pricing)
        # No judge ran, so nothing was spent on one - but the key must be present.
        meta["cost_basis"]["jev_usd"] = None
        resp = DeliberationResponse(
            question=request.prompt,
            thread_id=request.thread_id,
            compacted=compacted,
            panel=panel,
            meta=meta,
        )
        resp.text = _render(resp)
        log.warning(
            "deliberation.failed",
            failure=meta["failure"],
            failed=meta["failed"],
            elapsed_ms=meta["elapsed_ms"],
        )
        return resp

    analysis_provider, judge_role_error = _resolve_analysis_provider(
        analysis_id, provider_desc, client
    )
    # The right shape depends on the question instead of the installation, so a caller
    # that knows which it is facing may say so; config supplies the default.
    shape = request.judge_shape or config.judge.shape
    await _report(on_progress, len(providers), total, f"judging ({shape})")
    outcome = await run_judge(
        request.prompt,
        raw,
        shape=shape,
        deadline=deadline,
        context=request.context,
        jev_client=build_jev_client(config.judge, client),
        analysis_provider=analysis_provider,
        max_tokens=(
            request.max_tokens or config.defaults.analysis_max_tokens or config.defaults.max_tokens
        ),
        batch_size=config.judge.questions_per_call,
    )
    await _report(on_progress, total, total, f"judge finished ({outcome.shape})")
    meta["judge_shape"] = outcome.shape
    meta["judge_fallback_from"] = outcome.fallback_from
    meta["jev_calls"] = outcome.jev_calls
    meta["jev_questions"] = outcome.jev_questions
    # What a caller needs to decide whether to go round again: how many decisive gaps
    # are still open, and how many more passes the guard will allow.
    meta["evidence_outstanding"] = _outstanding(outcome.analysis)
    meta["passes_remaining"] = max(0, config.defaults.max_depth - request.depth - 1)

    if outcome.analysis_error or judge_role_error:
        # A misconfigured analyst is part of why the judge came up short, so it
        # belongs in the same field instead of a second one the host must know to
        # look for.
        meta["judge_error"] = "; ".join(
            part for part in (judge_role_error, outcome.analysis_error) if part
        )

    panel_input = _compose_user(request.prompt, request.context, _conversation(request, config))
    judge_input = build_judge_user(request.prompt, raw, request.context)
    cost_calls = [
        CostInput(
            r.provider_id,
            r.usage,
            input_text=_sent_input_text(chat_requests[r.provider_id], panel_input),
            output_text=r.text,
            catalog_price=_catalog_price(provider_desc, r.provider_id),
        )
        for r in ok_results
    ] + [
        CostInput(
            pid,
            usage,
            input_text=judge_input,
            output_text=outcome.analysis_text,
            catalog_price=_catalog_price(provider_desc, pid),
        )
        for pid, usage in outcome.usages
    ]
    _apply_cost(meta, cost_calls, config.pricing)
    _apply_jev_cost(meta, outcome)

    log.info(
        "judge.result",
        shape=outcome.shape,
        fallback_from=outcome.fallback_from,
        analysis=outcome.analysis is not None,
        jev_calls=outcome.jev_calls,
        jev_questions=outcome.jev_questions,
        judge_error=outcome.analysis_error,
    )

    log.info(
        "deliberation.done",
        ok=meta["ok"],
        failed=meta["failed"],
        elapsed_ms=meta["elapsed_ms"],
        cost_estimate_usd=meta["cost_estimate_usd"],
        analysis=outcome.analysis is not None,
    )
    resp = DeliberationResponse(
        question=request.prompt,
        thread_id=request.thread_id,
        compacted=compacted,
        panel=panel,
        analysis=outcome.analysis,
        raw_answers=raw,
        meta=meta,
    )
    resp.text = _render(resp)
    return resp
