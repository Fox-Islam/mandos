from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field, ValidationError

from orchestrator.models import DeliberateRequest, ReasoningEffort
from orchestrator.observability import configure_logging
from orchestrator.panel import failure_response, run_deliberation
from orchestrator.sessions import clear_sessions
from orchestrator.settings import load_config, load_env_file

mcp = FastMCP("imladris")


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
        return "no Imladris configuration found"
    return "failed to load Imladris configuration"


@mcp.tool()
async def imladris(
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
            description="Background appended to the prompt for every panellist and the judge.",
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
            description="Override the judge; must be an enabled provider with the 'judge' role.",
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
) -> dict:
    """Convene a panel of independent AI models to deliberate on a hard question, then
    return a structured analysis (consensus, disagreements, gaps, unique insights,
    blind spots) plus all raw panel answers. Use for research, expert critique,
    "compare and contrast", architecture/design trade-offs, or any task where being
    wrong is costly. You (the calling model) remain the final author: read the
    analysis + raw answers and write the answer.
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
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            timeout_s=timeout_s,
        )
        config = load_config()
    except (ValueError, FileNotFoundError) as exc:
        return failure_response(
            prompt, failure="unexpected_error", error=_safe_error(exc)
        ).model_dump()
    return (await run_deliberation(req, config)).model_dump()


@mcp.tool()
async def imladris_clear_sessions(thread_id: str | None = None) -> dict:
    """Clear local Imladris council-session history.

    Pass a thread_id to clear one session, or omit it to clear all local session
    JSON files under ~/.imladris/sessions.
    """
    return {"cleared": clear_sessions(thread_id=thread_id)}


@mcp.tool()
async def imladris_status() -> dict:
    """Return resolved non-secret config: presets, provider roles, per-provider egress
    (on-prem/off-prem), and advisory context-budget state. Performs no liveness probe.

    Never returns secret values or ``api_key_env`` names (plan §6.2).
    """
    try:
        return load_config().safe_status()
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": _safe_error(exc)}


@mcp.prompt(name="council")
def council_prompt(question: str) -> str:
    return (
        "Convene the council. Call the `imladris` tool with this question, then YOU "
        "author the final answer from what it returns. Rules for authoring:\n"
        "- Read the structured analysis and the raw panel answers; the panel members "
        "are advisors, you are the author.\n"
        "- Preserve genuine contradictions and caveats — do not smooth them away.\n"
        "- Give extra weight to consensus; flag where the panel was uncertain or "
        "left blind spots unaddressed.\n"
        "- Attribute non-obvious or contested claims to their source where it helps "
        "the reader judge reliability.\n\n"
        f"Question: {question}"
    )


@mcp.prompt(name="council-session")
def council_session_prompt(thread_id: str, question: str) -> str:
    return (
        "Convene a stateful council session. Call the `imladris` tool with "
        f"`thread_id={thread_id!r}` and this question. Use a stable thread id matching "
        "`[A-Za-z0-9_-]{1,64}`. If you have authored a prior answer in this same "
        "thread, pass it as `prior_answer` so Imladris can store it as the assistant "
        "turn. After the tool returns, YOU author the final answer from the structured "
        "analysis and raw panel answers.\n\n"
        f"Question: {question}"
    )


def main() -> None:
    """Console-script entry point (``imladris-mcp``): configure stderr logging, load
    secrets, then serve stdio."""
    configure_logging()
    load_env_file()
    mcp.run()


if __name__ == "__main__":
    main()
