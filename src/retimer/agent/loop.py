"""The agent: a model that settles what the routine steps left open.

The routine steps run first and need no model. When their draft stands on its
own, the model is not asked at all. Otherwise it gets a dossier with the open
questions and a handful of tools, and works until it submits or its budget is
spent. Whatever it does, the result is a plan built and checked by the same
code as the fixed pipeline's, and never one that puts fewer certain lines in
place than the draft it started from.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field

from retimer.agent.prompt import LAST_STEP, OPENING, REMIND_SUBMIT, SYSTEM, TOOLS
from retimer.agent.workbench import ToolError, Workbench
from retimer.cache import Cache
from retimer.classify import LineLabel
from retimer.model import Subtitle
from retimer.pipeline import Proposal, Settings, SyncResult, prepare, with_matched_reference
from retimer.providers import OPENROUTER_KEY_ENV, OpenRouter
from retimer.views import Options

# Where the model is called. OpenRouter is the default, so that one key covers
# the transcripts, the embeddings and the agent. It speaks the Messages API of
# Anthropic at an address of its own, which is why one client serves both.
PROVIDER_ENV = "RETIMER_AGENT_PROVIDER"
Provider = Literal["openrouter", "anthropic"]
DEFAULT_PROVIDER: Provider = "openrouter"
OPENROUTER_MESSAGES_URL = "https://openrouter.ai/api"
# OpenRouter has several upstreams for a Claude model. The benchmark ran on
# Anthropic's own, so that is the only one a request may go to.
OPENROUTER_UPSTREAM = {"only": ["anthropic"], "allow_fallbacks": False}
ANTHROPIC_KEY_ENV = "ANTHROPIC_RETIMER_API_KEY"
ANTHROPIC_URL_ENV = "RETIMER_ANTHROPIC_URL"
ANTHROPIC_URL = "https://api.anthropic.com"

MODELS = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}
# The smallest model is the default: the questions are narrow, and on the
# benchmark it settles nearly all that the true answers would.
DEFAULT_MODEL = MODELS["haiku"]
# What OpenRouter calls them. A name that is not here is sent as it is.
OPENROUTER_MODELS = {
    "claude-opus-5-5": "anthropic/claude-opus-5.5",
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
    "claude-haiku-5-5": "anthropic/claude-haiku-5.5",
}
# USD per million tokens: input, output, cache write, cache read. List prices of
# 2026-11-03; the cost in a report is computed from these and the token counts.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
_OWN_NAMES = {theirs: ours for ours, theirs in OPENROUTER_MODELS.items()}
# Models for which the provider can rerun a declined request on another model.
_SERVER_FALLBACK = {"claude-opus-5-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Finished = Literal["not_needed", "submitted", "no_submit", "budget", "declined", "error"]


def agent_provider() -> Provider:
    """Where the model is called, from the environment. An unknown name is an error."""
    name = (os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()
    if name not in ("openrouter", "anthropic"):
        raise ValueError(f"{PROVIDER_ENV} is {name!r}. It takes openrouter or anthropic.")
    return name  # type: ignore[return-value]


def agent_key_env(provider: Provider | None = None) -> str:
    """The name of the key the agent needs, for the provider or the one in the environment."""
    provider = provider or agent_provider()
    return OPENROUTER_KEY_ENV if provider == "openrouter" else ANTHROPIC_KEY_ENV


def agent_available() -> bool:
    """Whether the key for the model is there. Without it jobs run the routine steps alone."""
    try:
        return bool(os.environ.get(agent_key_env()))
    except ValueError:
        return False


class AgentSettings(BaseModel):
    """How the agent runs. The defaults are what the benchmark ran with."""

    model: str = DEFAULT_MODEL
    provider: Provider = Field(default_factory=agent_provider)
    effort: Effort = "medium"
    # auto asks the model only when the draft leaves something open.
    consult: Literal["auto", "always"] = "auto"
    # Model calls per job, and what they may cost together. When either runs
    # out, the best plan so far is returned as partial.
    max_turns: int = 8
    max_cost_usd: float = 1.0
    max_tokens: int = 16_000


class ToolCall(BaseModel):
    name: str
    input: dict
    error: bool = False


class AgentTrace(BaseModel):
    """What the agent did on one job, for the report."""

    model: str
    effort: str
    provider: str = DEFAULT_PROVIDER
    consulted: bool = False
    questions: list[str] = Field(default_factory=list)
    finished: Finished = "not_needed"
    turns: int = 0
    calls: list[ToolCall] = Field(default_factory=list)
    verdict: str | None = None
    note: str | None = None
    pairs: dict[int, int] = Field(default_factory=dict)
    removed: list[int] = Field(default_factory=list)
    kept: list[int] = Field(default_factory=list)
    fit: str | None = None
    # Whether the plan is the agent's, or the draft it started from.
    changed: bool = False
    accepted: bool = True
    served_by: list[str] = Field(default_factory=list)
    tokens: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    seconds: float = 0.0
    error: str | None = None


def resolve_model(name: str) -> str:
    return MODELS.get(name, name)


def make_client(provider: Provider | None = None) -> anthropic.Anthropic:
    """A client for the project's own key. The address is fixed on purpose.

    The SDK would otherwise take the address and the key from the environment,
    which on a development machine can point at something else than the API.
    """
    provider = provider or agent_provider()
    name = agent_key_env(provider)
    key = os.environ.get(name)
    if not key:
        raise RuntimeError(f"{name} is not set")
    if provider == "openrouter":
        address = OPENROUTER_MESSAGES_URL
    else:
        address = os.environ.get(ANTHROPIC_URL_ENV) or ANTHROPIC_URL
    return anthropic.Anthropic(api_key=key, base_url=address, max_retries=4, timeout=180.0)


def _cost(model: str, usage: dict[str, int]) -> float:
    prices = PRICES.get(model)
    if prices is None:
        return 0.0
    counts = (
        usage.get("input", 0),
        usage.get("output", 0),
        usage.get("cache_write", 0),
        usage.get("cache_read", 0),
    )
    return sum(count * price for count, price in zip(counts, prices, strict=True)) / 1_000_000


def _ask(client: Any, settings: AgentSettings, messages: list[dict]) -> Any:
    params: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        # The instructions and tools are the same for every job: one cache entry
        # serves them all, and the automatic one follows the growing conversation.
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "tools": TOOLS,
        "messages": messages,
        "cache_control": {"type": "ephemeral"},
        "output_config": {"effort": settings.effort},
    }
    if settings.provider == "openrouter":
        params["model"] = OPENROUTER_MODELS.get(settings.model, settings.model)
        params["extra_body"] = {"provider": OPENROUTER_UPSTREAM}
        # OpenRouter does not pass the rerun of a declined request on.
        return client.messages.create(**params)
    if settings.model in _SERVER_FALLBACK:
        # A request the provider's safety filters decline is rerun on the model
        # it recommends, so a false alarm does not fail the job.
        return client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **params)
    return client.messages.create(**params)


def _dispatch(bench: Workbench, name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "pair_lines":
            return bench.pair_lines(args["pairs"]), False
        if name == "unpair_lines":
            return bench.unpair_lines(args["lines"]), False
        if name == "set_edge_lines":
            return bench.set_edge_lines(args.get("remove", []), args.get("keep", [])), False
        if name == "find_counterparts":
            return bench.find_counterparts(args["lines"]), False
        if name == "read_lines":
            return bench.read_lines(args["first_line"], args["last_line"]), False
        if name == "read_reference":
            return bench.read_reference(args["first_place"], args["last_place"]), False
        if name == "choose_fit":
            return bench.choose_fit(args["kind"]), False
        return f"There is no tool named {name}.", True
    except ToolError as error:
        return str(error), True
    except (KeyError, TypeError, ValueError) as error:
        return f"The request could not be read ({error!r}). Nothing was changed.", True


def _converse(bench: Workbench, settings: AgentSettings, client: Any, trace: AgentTrace) -> None:
    """Let the model work on the bench until it submits or the budget is spent."""
    messages: list[dict] = [{"role": "user", "content": OPENING.format(dossier=bench.dossier())}]
    usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    reminded = False
    trace.finished = "budget"
    limit = settings.max_turns
    turn = 0
    while turn < limit:
        response = _ask(client, settings, messages)
        turn += 1
        trace.turns = turn
        counted = response.usage
        usage["input"] += getattr(counted, "input_tokens", 0) or 0
        usage["output"] += getattr(counted, "output_tokens", 0) or 0
        usage["cache_write"] += getattr(counted, "cache_creation_input_tokens", 0) or 0
        usage["cache_read"] += getattr(counted, "cache_read_input_tokens", 0) or 0
        trace.tokens = dict(usage)
        trace.cost_usd = round(_cost(settings.model, usage), 6)
        served = getattr(response, "model", None) or settings.model
        served = _OWN_NAMES.get(served, served)
        if served not in trace.served_by:
            trace.served_by.append(served)

        if response.stop_reason == "refusal":
            trace.finished = "declined"
            return
        messages.append({"role": "assistant", "content": response.content})
        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            if reminded or response.stop_reason == "max_tokens":
                trace.finished = "no_submit"
                return
            reminded = True
            messages.append({"role": "user", "content": REMIND_SUBMIT})
            continue

        results: list[dict] = []
        for call in calls:
            args = call.input if isinstance(call.input, dict) else {}
            if call.name == "submit":
                trace.calls.append(ToolCall(name="submit", input=args))
                trace.verdict = args.get("verdict")
                trace.note = args.get("note")
                trace.finished = "submitted"
                return
            text, failed = _dispatch(bench, call.name, args)
            trace.calls.append(ToolCall(name=call.name, input=args, error=failed))
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": text,
            }
            if failed:
                result["is_error"] = True
            results.append(result)
        if trace.cost_usd >= settings.max_cost_usd:
            # One more call, for the submit, and no further.
            limit = min(limit, turn + 1)
        if turn == limit - 1:
            results.append({"type": "text", "text": LAST_STEP})
        messages.append({"role": "user", "content": results})


def _conclude(bench: Workbench, trace: AgentTrace) -> SyncResult:
    """Turn the bench into the job's result, and say in the report what happened."""
    notes: list[str] = []
    if trace.finished == "submitted":
        # The verdict of the routine steps is overruled only where they were in
        # doubt. Many lines that found their counterpart are not undone by a
        # reader who thinks otherwise, and neither is their absence.
        in_doubt = bench.verdict_doubt() is not None
        was_aligned = bench.baseline.alignment.verdict == "aligned"
        if trace.verdict == "other_content" and (in_doubt or not was_aligned):
            bench.same_content = False
            bench.refresh()
        elif trace.verdict == "other_content":
            notes.append(
                "The agent took the reference for other content. The lines that found "
                "their counterpart say otherwise, so the plan was kept."
            )
        elif not was_aligned and in_doubt:
            bench.same_content = True
            bench.refresh()

    draft, baseline = bench.draft, bench.baseline
    use: Proposal = draft
    trace.changed = bench.changed
    if bench.changed and draft.result.plan.outcome != "rejected_no_match":
        slack = max(1, round(0.1 * baseline.in_place))
        if draft.in_place < baseline.in_place - slack:
            use = baseline
            trace.accepted = False
            notes.append(
                "The agent's changes put fewer certain lines in place than the plan it "
                "started from, so that plan was kept."
            )
    trace.pairs = dict(bench.pairs)
    trace.removed = sorted(bench.trim)
    trace.kept = sorted(bench.keep)
    trace.fit = bench.fit_kind

    result = use.result
    report = result.report.model_copy(deep=True)
    plan = result.plan
    unfinished = {
        "budget": "The agent's budget ran out before it finished.",
        "no_submit": "The agent stopped without finishing.",
        "declined": "The model declined the request.",
        "error": "The model could not be reached.",
    }.get(trace.finished)
    if unfinished:
        notes.append(unfinished + " The plan may leave parts of the file unsettled.")
        if trace.finished in ("budget", "no_submit") and plan.outcome in (
            "synced",
            "already_in_sync",
        ):
            plan = plan.model_copy(update={"outcome": "partial"})
            report.outcome = "partial"
    if trace.note:
        notes.append(f"Agent: {trace.note}")
    report.warnings = [*report.warnings, *notes]
    report.agent = trace.model_dump(mode="json")
    if trace.consulted:
        report.cost_usd["agent"] = trace.cost_usd
        report.seconds["agent"] = trace.seconds
    return SyncResult(plan=plan, subtitle=result.subtitle, report=report)


def run_agent(
    bench: Workbench, settings: AgentSettings | None = None, client: Any | None = None
) -> SyncResult:
    """Decide a prepared job: with the model where something is open, without it otherwise."""
    settings = settings or AgentSettings()
    settings = settings.model_copy(update={"model": resolve_model(settings.model)})
    trace = AgentTrace(model=settings.model, effort=settings.effort, provider=settings.provider)
    trace.questions = bench.open_questions()
    if not trace.questions and settings.consult == "auto":
        return _conclude(bench, trace)

    trace.consulted = True
    started = time.monotonic()
    try:
        _converse(bench, settings, client or make_client(settings.provider), trace)
    except anthropic.APIStatusError as error:
        trace.finished = "error"
        trace.error = f"{error.status_code}: {error.message}"[:300]
    except (anthropic.APIConnectionError, RuntimeError) as error:
        trace.finished = "error"
        trace.error = str(error)[:300]
    trace.seconds = round(time.monotonic() - started, 2)
    return _conclude(bench, trace)


def sync_with_agent(
    target: Subtitle,
    *,
    audio: Path | None = None,
    reference: Subtitle | None = None,
    options: Options | None = None,
    audio_language: str | None = None,
    reference_language: str | None = None,
    settings: Settings | None = None,
    agent: AgentSettings | None = None,
    client: OpenRouter | None = None,
    cache: Cache | None = None,
    labels: list[LineLabel] | None = None,
    model_client: Any | None = None,
    matched_reference: bool = False,
) -> SyncResult:
    """Synchronize `target` like `retimer.pipeline.sync`, with the agent deciding what is open."""
    prepared = prepare(
        target,
        audio=audio,
        reference=reference,
        options=options,
        audio_language=audio_language,
        reference_language=reference_language,
        settings=settings,
        client=client,
        cache=cache,
        labels=labels,
    )
    result = run_agent(Workbench(prepared), agent, model_client)
    return with_matched_reference(prepared, result) if matched_reference else result
