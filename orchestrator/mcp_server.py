from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field, ValidationError

from orchestrator.models import DeliberateRequest, JudgeShape, ReasoningEffort
from orchestrator.observability import configure_logging
from orchestrator.panel import failure_response, run_deliberation
from orchestrator.sessions import clear_sessions
from orchestrator.settings import load_config, load_env_file

mcp = FastMCP("mandos")


def _progress_reporter():
    """Report progress to the host, when there is a host listening.

    Taken from the ambient request rather than a ``ctx: Context`` parameter: adding one
    makes FastMCP resolve this module's annotations eagerly, and with
    ``from __future__ import annotations`` that fails on the ``Annotated`` field specs
    the tool signature is built from. Fetching it here keeps the signature plain.
    """
    try:
        from fastmcp.server.dependencies import get_context

        ctx = get_context()
    except Exception:  # noqa: BLE001
        return None

    async def report(done: float, total: float, message: str) -> None:
        await ctx.report_progress(progress=done, total=total, message=message)

    return report


def _safe_error(exc: Exception) -> str:
    """Sanitize a request/config-load error before it crosses the tool boundary or is
    logged.

    A pydantic ``ValidationError`` renders its ``input_value`` — for a config-load
    failure that is the whole config dict, including ``headers`` (which can carry an
    ``Authorization`` secret) and ``api_key_env`` names; custom-validator ``msg`` text
    embeds env-var names too. So we surface only the structurally-safe parts of each
    error — its field location and type — never ``msg``, ``input``, or ``ctx`` (golden
    rule: never return or log secrets)."""
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in err.get('loc', ())) or '<root>'} "
            f"({err.get('type', 'error')})"
            for err in exc.errors()
        ]
        return "request or configuration is invalid: " + ("; ".join(parts) or "validation error")
    if isinstance(exc, FileNotFoundError):
        return "no Mandos configuration found"
    return "failed to load Mandos configuration"


@mcp.tool()
async def mandos(
    prompt: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200_000,
            description="The question or task for the panel to deliberate on.",
        ),
    ],
    context: Annotated[
        str | None,
        Field(
            default=None,
            max_length=200_000,
            description="Background sent to every panellist and to the judge.",
        ),
    ] = None,
    thread_id: Annotated[
        str | None,
        Field(
            default=None,
            description="Stable session id (^[A-Za-z0-9_-]{1,64}$); reuses local history.",
        ),
    ] = None,
    prior_answer: Annotated[
        str | None,
        Field(
            default=None,
            max_length=100_000,
            description="Your prior answer for this thread; stored as the assistant turn.",
        ),
    ] = None,
    panel: Annotated[
        list[str] | None,
        Field(
            default=None,
            min_length=1,
            max_length=8,
            description="1-8 enabled provider ids to use as the panel (else the configured one).",
        ),
    ] = None,
    preset: Annotated[
        str | None,
        Field(default=None, description="Named preset selecting a panel and judge."),
    ] = None,
    analysis_model: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Override the generative analyst (proposes claims, or writes the "
                "analysis, or is the fallback); must be an enabled 'judge'-role "
                "provider. Use `judge_shape` to change how it is judged."
            ),
        ),
    ] = None,
    max_tokens: Annotated[
        int | None,
        Field(default=None, ge=1, description="Max output tokens per provider call."),
    ] = None,
    temperature: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            le=2,
            description="Panel sampling temperature (the judge always runs at temperature 0).",
        ),
    ] = None,
    reasoning_effort: Annotated[
        ReasoningEffort | None,
        Field(default=None, description="Reasoning-effort hint for providers that support it."),
    ] = None,
    timeout_s: Annotated[
        float | None,
        Field(default=None, gt=0, description="Overall deadline in seconds for the deliberation."),
    ] = None,
    judge_shape: Annotated[
        JudgeShape | None,
        Field(
            default=None,
            description=(
                "Override the judge for this call. 'probe' reports only what the panel "
                "lacked and does not deliberate — use it when the answer turns on a "
                "fact nobody has. 'hybrid' adjudicates claims and attributes them. "
                "'matrix' measures agreement with no generative model in the loop. "
                "'verify' has an analyst write and Jev grade it. 'llm' skips Jev "
                "entirely. Omit to use the configured default."
            ),
        ),
    ] = None,
    use_conversation: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "Whether the panel is shown the captured conversation (needs the "
                "capture hook installed). None follows config; False keeps this one "
                "call's panel blind to it."
            ),
        ),
    ] = None,
    depth: Annotated[
        int,
        Field(
            default=0,
            ge=0,
            description=(
                "Recursion depth. Pass depth+1 when one deliberation convenes another, "
                "so the guard can cap the chain; leave at 0 for an ordinary call."
            ),
        ),
    ] = 0,
) -> dict:
    """Convene a panel of independent AI models to deliberate on a hard question, then
    return a structured analysis (consensus, disagreements, gaps, unique insights,
    blind spots) plus all raw panel answers. Use for research, expert critique,
    "compare and contrast", architecture/design trade-offs, or any task where being
    wrong is costly.

    The judging is done by a calibrated decision model, not a generative one, so every
    finding arrives with the probabilities it was derived from in
    `analysis.calibration` — joined to the prose by `role` and `index`. A consensus
    whose weakest supporter scored 0.61 is a different instruction from the same
    sentence at 0.94. Check `meta.judge_fallback_from`: if it is set, the calibrated
    judge did not run and the analysis is an ordinary generative one.

    The panel cannot reach your machine. Put facts it could not know in `context`, and
    check `analysis.needs_evidence` in the result: it lists what the panel found itself
    missing, so a second call with that evidence supplied is often worth more than
    guessing what to include up front.

    `judge_shape` picks how the panel is judged for this call: 'probe' when the answer
    hinges on information nobody was given, 'hybrid' when the models will genuinely
    differ and you want the disagreement attributed.

    You (the calling model) remain the final author: read the analysis, its numbers,
    and the raw answers, then write the answer.
    """
    try:
        req = DeliberateRequest(
            prompt=prompt,
            context=context,
            thread_id=thread_id,
            prior_answer=prior_answer,
            panel=panel,
            preset=preset,
            analysis_model=analysis_model,
            judge_shape=judge_shape,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            timeout_s=timeout_s,
            use_conversation=use_conversation,
            depth=depth,
        )
        config = load_config()
    except (ValueError, FileNotFoundError) as exc:
        return failure_response(
            prompt, failure="unexpected_error", error=_safe_error(exc)
        ).model_dump()

    return (await run_deliberation(req, config, on_progress=_progress_reporter())).model_dump()


@mcp.tool()
async def mandos_clear_sessions(thread_id: str | None = None) -> dict:
    """Clear local Mandos council-session history.

    Pass a thread_id to clear one session, or omit it to clear all local session
    JSON files under ~/.mandos/sessions.
    """
    return {"cleared": clear_sessions(thread_id=thread_id)}


@mcp.tool()
async def mandos_status() -> dict:
    """Return resolved non-secret config: the judge (shape, Jev provider and model, its
    endpoint's on-prem/off-prem egress), presets, provider roles, per-provider egress,
    and advisory context-budget state. Performs no liveness probe.

    Never returns secret values or ``api_key_env`` names (plan §6.2).
    """
    try:
        return load_config().safe_status()
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": _safe_error(exc)}


@mcp.prompt(name="council")
def council_prompt(question: str) -> str:
    return (
        "Convene the council. Call the `mandos` tool with this question, then YOU "
        "author the final answer from what it returns. Rules for authoring:\n"
        "- Read the structured analysis and the raw panel answers; the panel members "
        "are advisors, you are the author.\n"
        "- Read `analysis.calibration` alongside the prose. Each finding carries the "
        "probabilities it was derived from, joined by `role` and `index`. Weight a "
        "finding by its numbers, not by the confidence of its phrasing: a consensus "
        "whose weakest supporter scored near the threshold is a weak one, and saying "
        "so is more useful than repeating it flatly.\n"
        "- Numbers are evidence, not permission. A high score means the answers back "
        "the claim consistently; the panel can be consistently wrong.\n"
        "- If `meta.judge_fallback_from` is set, the calibrated judge did not run — "
        "treat the analysis as one model's opinion and say so if it matters.\n"
        "- `analysis.needs_evidence` lists what the panel lacked, with how likely "
        "having it would change the answer. `meta.evidence_outstanding` counts the "
        "ones decisive enough to be worth fetching; `meta.passes_remaining` says how "
        "many more times you may convene.\n"
        "- **Keep going while it is still asking.** If both are above zero, get what "
        "you can — read the file, run the query, check the version — add it to "
        "everything you already supplied, and call `mandos` again with `depth` one "
        "higher. Repeat. Answering one question often uncovers the next, so stopping "
        "after a single round is arbitrary.\n"
        "- Stop when it stops asking, when what remains is something you cannot "
        "obtain, or when you run out of passes. Then say plainly which assumptions "
        "were never settled, rather than letting them stand as fact.\n"
        "- Preserve genuine contradictions and caveats — do not smooth them away.\n"
        "- Flag where the panel was uncertain or left blind spots unaddressed.\n"
        "- Attribute non-obvious or contested claims to their source where it helps "
        "the reader judge reliability.\n\n"
        f"Question: {question}"
    )


@mcp.prompt(name="council-session")
def council_session_prompt(thread_id: str, question: str) -> str:
    return (
        "Convene a stateful council session. Call the `mandos` tool with "
        f"`thread_id={thread_id!r}` and this question. Use a stable thread id matching "
        "`[A-Za-z0-9_-]{1,64}`. If you have authored a prior answer in this same "
        "thread, pass it as `prior_answer` so Mandos can store it as the assistant "
        "turn. After the tool returns, YOU author the final answer from the structured "
        "analysis and raw panel answers.\n\n"
        f"Question: {question}"
    )


def main() -> None:
    """Console-script entry point (``mandos-mcp``): configure stderr logging, load
    secrets, then serve stdio."""
    configure_logging()
    load_env_file()
    mcp.run()


if __name__ == "__main__":
    main()
