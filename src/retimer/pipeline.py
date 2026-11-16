"""The fixed pipeline: the tools run in one set order, with no model deciding.

It turns a target and a reference into a plan, the retimed file and a report.
The agent will make the same calls and judge between the fits itself; this
pipeline always takes the best-scoring fit. It is the service's floor, and the
benchmark's measure of what the agent adds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from retimer.cache import Cache
from retimer.classify import LineLabel, classify_lines
from retimer.embed import Embedder
from retimer.engine import PlanError, PlanIssue, apply_plan, validate_plan
from retimer.fit import KNOWN_RATES, Evidence, Fit, FitKind, Pin, choose_fit, fit_offsets
from retimer.match import Anchor, Coverage, Matching, match_content, match_lines
from retimer.model import Line, Subtitle
from retimer.plan import ContentOp, Outcome, Plan, RemoveLines, Scale, Shift, TimingOp
from retimer.providers import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_SPEECH_MODEL,
    DEFAULT_SPEECH_PROVIDER,
    OpenRouter,
)
from retimer.reference import (
    ReferenceTimeline,
    RefEvent,
    timeline_from_subtitle,
    timeline_from_transcript,
)
from retimer.score import Score, score_alignment
from retimer.speech import Correlation, correlate_speech
from retimer.transcribe import Transcript, transcribe_audio
from retimer.views import MatchLine, Options, Views, build_views

# From this strength on, the text of a line occurs in the reference.
FOUND_STRENGTH = 0.5


class Settings(BaseModel):
    """What the pipeline can be tuned by. The defaults are what the benchmark ran with."""

    speech_model: str = DEFAULT_SPEECH_MODEL
    speech_provider: str | None = DEFAULT_SPEECH_PROVIDER
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # A file whose one offset is smaller than this is left alone. Against audio
    # the bar is higher: hand-timed subtitles lead or trail the voice by a
    # quarter of a second as a matter of style, and that is not being out of sync.
    in_sync_ms: int = 100
    in_sync_audio_ms: int = 350
    # Below these the anchors do not carry a verdict.
    min_anchors: int = 3
    min_matched_share: float = 0.15
    # With this many anchorable lines on both sides and still no match, the
    # reference is other content. With fewer, there was too little text to tell.
    min_text_for_verdict: int = 6
    # How far the text-free alignment may move an offset the fit found.
    refine_window_ms: int = 800
    refine: bool = True
    # The text-free fallback searches this far in both directions.
    fallback_window_ms: int = 120_000
    # A leading or trailing line with no speech this close to it has no counterpart.
    edge_margin_ms: int = 1_000
    # More unmatched lines than this at one end are not an outro, they are a
    # part of the film that was not placed. They are kept and flagged.
    max_trim_lines: int = 6
    max_trim_share: float = 0.05
    # A block needs this confidence, or the outcome is partial.
    min_block_confidence: float = 0.4


class ReferenceSummary(BaseModel):
    kind: str
    language: str | None
    events: int
    duration_ms: int | None
    dropped: dict[str, int]
    notes: list[str]


class BlockReport(BaseModel):
    """One block of the plan. Times are original target times."""

    from_ms: int | None
    to_ms: int | None
    shift_ms: int
    lines: int
    matched: int
    support: float
    spread_ms: float
    refined: bool = False
    confidence: float = 0.0


class SyncReport(BaseModel):
    outcome: str
    confidence: float
    reference: ReferenceSummary
    target_lines: int
    eligible_lines: int
    anchors: int
    coverage: Coverage | None = None
    fits: list[Fit] = Field(default_factory=list)
    chosen: str | None = None
    rate: float = 1.0
    rate_name: str | None = None
    blocks: list[BlockReport] = Field(default_factory=list)
    fallback: Correlation | None = None
    trimmed: list[int] = Field(default_factory=list)
    unmatched_kept: list[int] = Field(default_factory=list)
    score: Score | None = None
    warnings: list[str] = Field(default_factory=list)
    # What the agent did, when one was asked. None for the fixed pipeline.
    agent: dict | None = None
    cost_usd: dict[str, float] = Field(default_factory=dict)
    seconds: dict[str, float] = Field(default_factory=dict)

    @property
    def total_cost_usd(self) -> float:
        return sum(self.cost_usd.values())


class SyncResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    plan: Plan
    subtitle: Subtitle | None
    report: SyncReport


class Adjustments(BaseModel):
    """What a reader changes about the pipeline's own reading of a file.

    The fixed pipeline runs with none of them. The agent arrives at its plan by
    adding to them: it never writes a time, it says which fit, which line
    belongs to which place in the reference, and what happens to lines that
    have no counterpart. Everything else is computed as it always is.
    """

    fit: FitKind | None = None
    pins: list[Pin] = Field(default_factory=list)
    # Lines at the edges to remove although the rules would keep them, and the reverse.
    trim: list[int] = Field(default_factory=list)
    keep: list[int] = Field(default_factory=list)
    # The reader looked at the lines at the edges and decided about each.
    edges_reviewed: bool = False
    # The verdict on whether both sides are the same content, when the reader overrules it.
    same_content: bool | None = None
    # Lines the routine steps placed, with the reference time they were placed
    # at. They keep that counterpart while pairs are asserted around them.
    held: dict[int, int] = Field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.model_copy(update={"held": {}}) == Adjustments()


@dataclass
class Alignment:
    """Matching and fitting of a list of lines against a timeline.

    `anchors` are the pairs the fits were checked against: those found from the
    text, and those a reader asserted. `matched` are the lines that found their
    counterpart under the chosen fit.
    """

    matching: Matching
    anchors: list[Anchor]
    fits: list[Fit]
    chosen: Fit | None
    matched: set[int]
    coverage: Coverage
    verdict: Literal["aligned", "no_match", "too_little_text"]


def align(
    matching: Matching,
    settings: Settings,
    pins: list[Pin] | None = None,
    kind: FitKind | None = None,
    hold: dict[int, int] | None = None,
) -> Alignment:
    """Fit the offsets and say whether the two sides are the same content.

    `kind` asks for the fit of that kind where there is one. Without it, or
    when there is none, the best-scoring fit is taken.
    """
    evidence = Evidence.from_matching(matching, pins, hold if pins else None)
    fits = fit_offsets(evidence)
    chosen = next((fit for fit in fits if fit.kind == kind), None) or choose_fit(fits)
    # The verdict rests on the anchors alone. They were found from the text
    # without looking at the clock, so their agreeing on a fit is real evidence.
    in_place = chosen.explained(evidence.anchors) if chosen else set()
    matched = in_place | set(chosen.matched_ids) if chosen else set()
    result = matching.result
    comparable = min(result.eligible_lines, result.eligible_events)
    share = len(in_place) / comparable if comparable else 0.0
    verdict: Literal["aligned", "no_match", "too_little_text"]
    if len(in_place) >= settings.min_anchors and share >= settings.min_matched_share:
        verdict = "aligned"
    elif comparable >= settings.min_text_for_verdict:
        verdict = "no_match"
    else:
        verdict = "too_little_text"
    return Alignment(
        matching=matching,
        anchors=evidence.anchors,
        fits=fits,
        chosen=chosen,
        matched=matched,
        coverage=match_content(matching.lines, matched),
        verdict=verdict,
    )


def align_lines(
    lines: list[MatchLine], timeline: ReferenceTimeline, embedder: Embedder, settings: Settings
) -> Alignment:
    """Match the lines against the timeline, then fit the offsets."""
    return align(match_lines(lines, timeline, embedder), settings)


def _break_start(
    lines: list[Line], dialogue_ids: set[int], first_ms: int, room_ms: float, rate: float
) -> int:
    """Where a break falls, given the first dialogue line after it.

    Signs, songs and sound descriptions between the two dialogue lines around
    the break could belong to either side. The break goes to the longest pause
    among them that leaves `room_ms`: the block after the break moves that much
    closer to the block before it, and may not run into it.
    """
    after = next(
        (
            index
            for index, line in enumerate(lines)
            if line.id in dialogue_ids and line.start_ms >= first_ms
        ),
        None,
    )
    if after is None:
        return first_ms
    before = next(
        (index for index in range(after - 1, -1, -1) if lines[index].id in dialogue_ids), None
    )
    if before is None:
        return first_ms
    best = None
    latest_end = max(line.end_ms for line in lines[: before + 1])
    for index in range(before + 1, after + 1):
        gap = (lines[index].start_ms - lines[index - 1].start_ms) * rate
        key = (gap >= room_ms, lines[index].start_ms - latest_end)
        if best is None or key > best[0]:
            best = (key, lines[index].start_ms)
        latest_end = max(latest_end, lines[index].end_ms)
    return best[1]


def _shift_at(blocks: list[BlockReport], start_ms: int) -> int:
    for block in blocks:
        if block.to_ms is None or start_ms < block.to_ms:
            return block.shift_ms
    return blocks[-1].shift_ms if blocks else 0


def build_blocks(
    fit: Fit,
    lines: list[Line],
    dialogue_ids: set[int],
    timeline: ReferenceTimeline,
    settings: Settings,
) -> list[BlockReport]:
    """Turn a fit into the blocks of a plan: set the breaks, then sharpen each offset."""
    bounds: list[int | None] = [None]
    for left, right in zip(fit.blocks, fit.blocks[1:], strict=False):
        room = left.offset_ms - right.offset_ms
        bounds.append(_break_start(lines, dialogue_ids, right.from_ms, room, fit.rate))
    bounds.append(None)

    blocks = []
    for index, block in enumerate(fit.blocks):
        from_ms, to_ms = bounds[index], bounds[index + 1]
        shift = block.offset_ms
        refined = False
        if settings.refine:
            starts = [
                line.start_ms
                for line in lines
                if line.id in dialogue_ids
                and (from_ms is None or line.start_ms >= from_ms)
                and (to_ms is None or line.start_ms < to_ms)
            ]
            found = correlate_speech(
                starts,
                timeline.onsets,
                center_ms=block.offset_ms,
                window_ms=settings.refine_window_ms,
                rate=fit.rate,
            )
            # The sharper offset is taken only when clearly more lines meet speech
            # there than chance would give.
            if found and found.matches >= max(3.0, 0.3 * len(starts)) and found.strength >= 2.5:
                shift = found.offset_ms
                refined = True
        blocks.append(
            BlockReport(
                from_ms=from_ms,
                to_ms=to_ms,
                shift_ms=shift,
                lines=block.lines,
                matched=block.matched,
                support=block.support,
                spread_ms=block.spread_ms,
                refined=refined,
            )
        )
    return _settle(blocks, lines, fit.rate, fit.tolerance_ms)


def _settle(
    blocks: list[BlockReport], lines: list[Line], rate: float, slack_ms: int
) -> list[BlockReport]:
    """Straighten out blocks that cross by less than their offsets are known to.

    Two lines that start almost together on either side of a break can end up
    in the wrong order, because each block's offset is a little off. The later
    block is then moved just far enough to start with the line before it.
    """
    for index in range(1, len(blocks)):
        block = blocks[index]
        before = [line for line in lines if line.start_ms < block.from_ms]
        after = [line for line in lines if line.start_ms >= block.from_ms]
        if not before or not after:
            continue
        last = round(before[-1].start_ms * rate) + _shift_at(blocks, before[-1].start_ms)
        first = round(after[0].start_ms * rate) + block.shift_ms
        if 0 < last - first <= slack_ms:
            blocks[index] = block.model_copy(update={"shift_ms": block.shift_ms + last - first})
    return blocks


def _edge_lines(
    lines: list[Line],
    labels: dict[int, str],
    matched: set[int],
    new_times: dict[int, tuple[int, int]],
    timeline: ReferenceTimeline,
    found_elsewhere: set[int],
    settings: Settings,
) -> tuple[list[int], list[int], bool]:
    """Leading and trailing lines with no counterpart: those to trim, and those to keep.

    A line is trimmed only when the reference confirms it is absent. With audio
    that means it lands past the end of the audio or on a stretch where nobody
    speaks. A reference subtitle alone is weaker evidence: the line has to land
    outside everything the reference covers and also read like a credit or an
    advertisement.

    A line whose text does occur in the reference, only somewhere else, is not
    absent. It belongs to a part of the file that was not placed, and it stays.
    The third value says that an end had more silent lines than an outro has.
    Those are kept for the same reason.
    """
    ordered = [line for line in lines if not line.is_comment]
    positions = [index for index, line in enumerate(ordered) if line.id in matched]
    if not positions or not timeline.events:
        return [], [], False
    has_audio = timeline.kind in ("audio", "both")
    first_said = timeline.events[0].start_ms
    last_said = max(event.end_ms for event in timeline.events)
    margin = settings.edge_margin_ms
    judged = [line for line in ordered if labels.get(line.id, "dialogue") in ("dialogue", "credit")]
    limit = max(settings.max_trim_lines, int(settings.max_trim_share * len(judged)))

    def beyond(line: Line) -> bool:
        start, _ = new_times[line.id]
        return has_audio and (
            start < 0 or (timeline.duration_ms is not None and start >= timeline.duration_ms)
        )

    def silent(line: Line) -> bool:
        start, end = new_times[line.id]
        if has_audio:
            return not any(
                low < end + margin and high > start - margin for low, high in timeline.speech
            )
        return start > last_said + margin or end < first_said - margin

    def absent(line: Line) -> bool:
        if beyond(line):
            return True
        if line.id in found_elsewhere or not silent(line):
            return False
        return has_audio or labels.get(line.id) == "credit"

    trim: list[int] = []
    keep: list[int] = []
    too_many = False
    # Each end is walked from the outside in, and trimming stops at the first line that stays.
    for run in (ordered[positions[-1] + 1 :][::-1], ordered[: positions[0]]):
        run = [line for line in run if labels.get(line.id, "dialogue") in ("dialogue", "credit")]
        gone = []
        for line in run:
            if not absent(line):
                break
            gone.append(line)
        if sum(1 for line in gone if not beyond(line)) > limit:
            too_many = True
            gone = [line for line in gone if beyond(line)]
        trim += [line.id for line in gone]
        keep += [line.id for line in run if line not in gone]
    return sorted(trim), sorted(keep), too_many


def _new_times(
    lines: list[Line], rate: float, blocks: list[BlockReport]
) -> dict[int, tuple[int, int]]:
    times = {}
    for line in lines:
        shift = _shift_at(blocks, line.start_ms)
        times[line.id] = (round(line.start_ms * rate) + shift, round(line.end_ms * rate) + shift)
    return times


def _timing_ops(rate: float, rate_name: str | None, blocks: list[BlockReport]) -> list[TimingOp]:
    ops: list[TimingOp] = []
    if rate != 1.0:
        ops.append(Scale(factor=rate, reason=rate_name))
    for block in blocks:
        if block.shift_ms:
            ops.append(
                Shift.model_validate(
                    {"from": block.from_ms, "to": block.to_ms, "ms": block.shift_ms}
                )
            )
    return ops


def _in_sync(rate: float, blocks: list[BlockReport], bar_ms: int) -> bool:
    return rate == 1.0 and len(blocks) == 1 and abs(blocks[0].shift_ms) <= bar_ms


def _rest_on_synced(
    blocks: list[BlockReport], bar_ms: int, exact_ms: int, style: bool
) -> list[BlockReport]:
    """Leave alone the part of a file that is in sync already.

    A block whose offset is below the bar is in sync, as a whole file with that
    offset would be. With `style`, against audio, its small offset is the hand
    of whoever timed the subtitle: how far the lines lead or trail the voice.
    The other blocks were timed by the same hand, so they move by their offset
    less that one, and land the way the part in sync does.
    """
    synced = [block for block in blocks if abs(block.shift_ms) <= bar_ms]
    if len(blocks) < 2 or not synced:
        return blocks
    lead = max(synced, key=lambda block: (block.matched, block.support)).shift_ms if style else 0
    settled = []
    for block in blocks:
        shift = block.shift_ms - lead
        if abs(shift) <= (exact_ms if style else bar_ms):
            shift = 0
        settled.append(block.model_copy(update={"shift_ms": shift}))
    return settled


def _move_events(events: list[RefEvent], rate: float, blocks: list[BlockReport]) -> list[RefEvent]:
    moved = []
    for event in events:
        shift = _shift_at(blocks, event.start_ms)
        moved.append(
            RefEvent(
                start_ms=round(event.start_ms * rate) + shift,
                end_ms=round(event.end_ms * rate) + shift,
                text=event.text,
            )
        )
    return sorted(moved, key=lambda event: event.start_ms)


def combine_references(
    subtitle_timeline: ReferenceTimeline,
    audio_timeline: ReferenceTimeline,
    embedder: Embedder,
    settings: Settings,
) -> ReferenceTimeline:
    """One timeline from a reference subtitle and audio.

    The subtitle supplies the events, because its text is clean and its lines
    are cut like the target's. First it is checked against the audio like any
    other subtitle. When the two disagree the audio wins: the subtitle's events
    are moved to where the audio says they are, and the timeline says so.
    """
    notes = list(subtitle_timeline.notes)
    events = subtitle_timeline.events
    lines = [
        MatchLine(id=index, start_ms=event.start_ms, end_ms=event.end_ms, text=event.text)
        for index, event in enumerate(events)
    ]
    check = align_lines(lines, audio_timeline, embedder, settings)
    if check.verdict == "aligned" and check.chosen is not None:
        plain = [
            Line(
                id=line.id,
                start_ms=line.start_ms,
                end_ms=line.end_ms,
                text="",
                style="",
                is_comment=False,
            )
            for line in lines
        ]
        blocks = build_blocks(
            check.chosen, plain, {line.id for line in lines}, audio_timeline, settings
        )
        if _in_sync(check.chosen.rate, blocks, settings.in_sync_audio_ms):
            notes.append("The reference subtitle agrees with the audio.")
        else:
            events = _move_events(events, check.chosen.rate, blocks)
            shifts = ", ".join(f"{block.shift_ms} ms" for block in blocks)
            notes.append(
                "The reference subtitle disagrees with the audio "
                f"(rate {check.chosen.rate:.5f}, shifts {shifts}). The audio wins: "
                "the reference subtitle was moved onto the audio before matching."
            )
    elif check.verdict == "no_match":
        notes.append(
            "The reference subtitle and the audio do not seem to be the same content. "
            "The reference subtitle was used as it is."
        )
    else:
        notes.append("The reference subtitle could not be checked against the audio.")
    return ReferenceTimeline(
        kind="both",
        language=subtitle_timeline.language,
        events=events,
        speech=audio_timeline.speech,
        onsets=[event.start_ms for event in events],
        duration_ms=audio_timeline.duration_ms,
        dropped={**audio_timeline.dropped, **subtitle_timeline.dropped},
        notes=notes,
    )


def _fallback(
    lines: list[Line], dialogue_ids: set[int], timeline: ReferenceTimeline, settings: Settings
) -> tuple[Correlation | None, float, str | None]:
    """Text-free alignment over the whole file, at every frame rate that occurs in practice."""
    starts = [line.start_ms for line in lines if line.id in dialogue_ids]
    best: tuple[Correlation | None, float, str | None] = (None, 1.0, None)
    for name, rate in [(None, 1.0), *KNOWN_RATES.items()]:
        found = correlate_speech(
            starts, timeline.onsets, window_ms=settings.fallback_window_ms, rate=rate
        )
        if found and (best[0] is None or found.matches > best[0].matches + (1.0 if name else 0.0)):
            best = (found, rate, name)
    return best


def _merge_weakest(blocks: list[BlockReport]) -> list[BlockReport]:
    """Give the least supported block to its better supported neighbour."""
    weakest = min(range(len(blocks)), key=lambda index: blocks[index].support)
    sides = [index for index in (weakest - 1, weakest + 1) if 0 <= index < len(blocks)]
    heir = max(sides, key=lambda index: blocks[index].support)
    gone = blocks[weakest]
    grown = blocks[heir].model_copy(
        update={"to_ms": gone.to_ms} if heir < weakest else {"from_ms": gone.from_ms}
    )
    return [
        grown if index == heir else block for index, block in enumerate(blocks) if index != weakest
    ]


@dataclass
class Prepared:
    """What the routine steps find out about a job, before any plan is made.

    The reference is transcribed or read, the target's lines are labelled, and
    the two are matched. None of this depends on a decision, so it is done once
    and every plan for the job is built from it.
    """

    target: Subtitle
    options: Options
    settings: Settings
    label_of: dict[int, str]
    views: Views
    # The lines of the target that are not comments, in time order.
    lines: list[Line]
    dialogue_ids: set[int]
    timeline: ReferenceTimeline
    matching: Matching
    cost_usd: dict[str, float] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)


@dataclass
class Proposal:
    """A plan for a job, with what it was built from.

    `result` is what the caller gets. The rest lets a reader see why the plan
    looks the way it does: the fit behind it, the blocks, where every line
    lands, and which lines found a counterpart.
    """

    result: SyncResult
    adjustments: Adjustments
    alignment: Alignment
    blocks: list[BlockReport]
    rate: float
    new_times: dict[int, tuple[int, int]]
    matched: set[int]
    removed: set[int]
    in_place: int = 0


def prepare(
    target: Subtitle,
    *,
    audio: Path | None = None,
    reference: Subtitle | None = None,
    options: Options | None = None,
    audio_language: str | None = None,
    reference_language: str | None = None,
    settings: Settings | None = None,
    client: OpenRouter | None = None,
    cache: Cache | None = None,
    labels: list[LineLabel] | None = None,
) -> Prepared:
    """Run the steps that need no decision: the reference, the labels and the matching."""
    if audio is None and reference is None:
        raise ValueError("a reference is needed: audio, a subtitle, or both")
    options = options or Options()
    settings = settings or Settings()
    cache = cache if cache is not None else Cache()
    embedder = Embedder(client=client, cache=cache, model=settings.embedding_model)
    seconds: dict[str, float] = {}
    cost: dict[str, float] = {}

    labels = labels if labels is not None else classify_lines(target)
    views = build_views(target, options, labels)
    lines = sorted(
        (line for line in target.lines if not line.is_comment),
        key=lambda line: (line.start_ms, line.id),
    )

    started = time.monotonic()
    transcript: Transcript | None = None
    if audio is not None:
        transcript = transcribe_audio(
            audio,
            model=settings.speech_model,
            provider=settings.speech_provider,
            language=audio_language,
            client=client,
            cache=cache,
        )
        cost["transcription"] = transcript.cost_usd
    seconds["transcription"] = round(time.monotonic() - started, 2)

    started = time.monotonic()
    if transcript is not None and reference is not None:
        timeline = combine_references(
            timeline_from_subtitle(reference, reference_language),
            timeline_from_transcript(transcript),
            embedder,
            settings,
        )
    elif transcript is not None:
        timeline = timeline_from_transcript(transcript)
    else:
        timeline = timeline_from_subtitle(reference, reference_language)
    matching = match_lines(views.match_view.lines, timeline, embedder)
    seconds["matching"] = round(time.monotonic() - started, 2)
    cost["embeddings"] = embedder.cost_usd
    return Prepared(
        target=target,
        options=options,
        settings=settings,
        label_of={label.id: label.label for label in labels},
        views=views,
        lines=lines,
        dialogue_ids={line.id for line in views.match_view.lines},
        timeline=timeline,
        matching=matching,
        cost_usd=cost,
        seconds=seconds,
    )


def propose(prepared: Prepared, adjustments: Adjustments | None = None) -> Proposal:
    """Build the plan for a prepared job: fit, blocks, edge lines, score and outcome.

    Without adjustments this is the fixed pipeline. With them it is the same
    computation under what a reader decided.
    """
    adjust = adjustments or Adjustments()
    target, options, settings = prepared.target, prepared.options, prepared.settings
    timeline, views, lines = prepared.timeline, prepared.views, prepared.lines
    label_of = prepared.label_of
    dialogue_ids = prepared.dialogue_ids | {pin.line_id for pin in adjust.pins}

    alignment = align(prepared.matching, settings, adjust.pins, adjust.fit, adjust.held)
    verdict = alignment.verdict
    if adjust.same_content is False:
        verdict = "no_match"
    elif adjust.same_content and alignment.chosen is not None:
        verdict = "aligned"

    result = alignment.matching.result
    report = SyncReport(
        outcome="failed",
        confidence=0.0,
        reference=ReferenceSummary(
            kind=timeline.kind,
            language=timeline.language,
            events=len(timeline.events),
            duration_ms=timeline.duration_ms,
            dropped=timeline.dropped,
            notes=timeline.notes,
        ),
        target_lines=result.target_lines,
        eligible_lines=result.eligible_lines,
        anchors=len(result.anchors),
        coverage=alignment.coverage,
        fits=alignment.fits,
        warnings=[note for note in timeline.notes if "disagrees" in note or "not seem" in note],
        cost_usd=dict(prepared.cost_usd),
        seconds=dict(prepared.seconds),
    )
    warnings = report.warnings
    state = Proposal(
        result=None,  # type: ignore[arg-type]
        adjustments=adjust,
        alignment=alignment,
        blocks=[],
        rate=1.0,
        new_times={},
        matched=set(),
        removed=set(),
    )

    def finish(outcome: Outcome, content: list[ContentOp], timing: list[TimingOp]) -> Proposal:
        plan = Plan(
            outcome=outcome,
            content_ops=content,
            timing_ops=timing,
            confidence=round(min(1.0, max(0.0, report.confidence)), 2),
        )
        report.outcome = outcome
        subtitle = None if outcome == "rejected_no_match" else apply_plan(target, plan).subtitle
        state.result = SyncResult(plan=plan, subtitle=subtitle, report=report)
        return state

    def unchanged(reason: str) -> Proposal:
        warnings.append(reason + " Timing is unchanged.")
        report.blocks = []
        report.confidence = 0.0
        state.blocks = []
        state.rate = 1.0
        state.new_times = _new_times(lines, 1.0, [])
        try:
            return finish("partial", list(views.content_ops), [])
        except PlanError:
            return finish("partial", [], [])

    if verdict == "no_match":
        report.confidence = 1.0 - alignment.coverage.share
        if adjust.same_content is False:
            warnings.append("Read side by side, the reference is other content than the target.")
        else:
            warnings.append(
                f"Only {alignment.coverage.matched_lines} of "
                f"{alignment.coverage.eligible_lines} lines found a counterpart in the "
                "reference, so it is treated as other content."
            )
        return finish("rejected_no_match", [], [])

    if verdict == "too_little_text" or alignment.chosen is None:
        found, rate, rate_name = _fallback(lines, dialogue_ids, timeline, settings)
        report.fallback = found
        warnings.append(
            "Too little text could be matched by meaning, so the timing of speech alone was used."
        )
        if not (found and found.matches >= 4 and found.strength >= 3):
            return unchanged("The timing of speech gave no clear offset either.")
        blocks = [
            BlockReport(
                from_ms=None,
                to_ms=None,
                shift_ms=found.offset_ms,
                lines=len(dialogue_ids),
                matched=0,
                support=0.0,
                spread_ms=0.0,
            )
        ]
        matched: set[int] = set()
        in_sync = False
        report.rate, report.rate_name = rate, rate_name
    else:
        fit = alignment.chosen
        rate, rate_name = fit.rate, fit.rate_name
        matched = alignment.matched
        blocks = build_blocks(fit, lines, dialogue_ids, timeline, settings)
        report.chosen, report.rate, report.rate_name = fit.kind, rate, rate_name
        bar = settings.in_sync_audio_ms if timeline.kind == "audio" else settings.in_sync_ms
        in_sync = _in_sync(rate, blocks, bar)
        if in_sync:
            blocks = [block.model_copy(update={"shift_ms": 0}) for block in blocks]
        elif rate == 1.0:
            blocks = _rest_on_synced(blocks, bar, settings.in_sync_ms, timeline.kind == "audio")

    removed = {
        line_id for op in views.content_ops if isinstance(op, RemoveLines) for line_id in op.ids
    }
    # Lines whose text the reference holds somewhere, whether or not they were placed.
    strength = alignment.matching.strength()
    elsewhere = {
        line.id
        for line, best in zip(
            alignment.matching.lines, strength.max(axis=1, initial=0.0), strict=True
        )
        if best >= FOUND_STRENGTH
    }
    merged = False
    while True:
        new_times = _new_times(lines, rate, blocks)
        trim, keep, too_many = _edge_lines(
            lines, label_of, matched, new_times, timeline, elsewhere, settings
        )
        # What the reader decided about a line stands over what the rules say.
        trim = sorted((set(trim) | set(adjust.trim)) - set(adjust.keep) - matched)
        keep = sorted((set(keep) | set(adjust.keep)) - set(trim))
        if not options.drop_unmatched:
            keep, trim = sorted(set(keep) | set(trim)), []
        trim = sorted(set(trim) - removed)
        # A line that would start before zero has no place in the reference at all.
        before_zero = sorted(
            line.id
            for line in lines
            if new_times[line.id][0] < 0 and line.id not in removed and line.id not in trim
        )
        content = list(views.content_ops)
        if trim or before_zero:
            content.append(
                RemoveLines(reason="absent_from_reference", ids=sorted({*trim, *before_zero}))
            )
        timing = _timing_ops(rate, rate_name, blocks)
        issues = validate_plan(target, Plan(content_ops=content, timing_ops=timing))
        limit = max(settings.max_trim_lines, int(settings.max_trim_share * len(lines)))
        if len(before_zero) > limit:
            issues.append(
                PlanIssue(code="negative_time", message="too many lines would start before zero")
            )
        if not issues:
            break
        if len(blocks) <= 1:
            codes = "; ".join(issue.code for issue in issues)
            return unchanged(f"No valid timing could be built ({codes}).")
        # Blocks that run into each other cannot all be right. The least
        # supported one goes to its neighbour, until what is left fits together.
        blocks = _merge_weakest(blocks)
        merged = True

    too_many = too_many and not adjust.edges_reviewed
    if merged:
        warnings.append(
            "Some blocks ran into each other. The least supported were joined with "
            "their neighbours, so parts of the file may still be out of sync."
        )
    if too_many:
        warnings.append(
            "More lines at one end of the file land where nobody speaks than an outro has. "
            "They were kept: a part of the file may not have been placed."
        )
    if before_zero:
        warnings.append(
            f"{len(before_zero)} lines would start before the reference begins and were removed."
        )
    report.trimmed = trim
    report.unmatched_kept = keep

    gone = removed | set(trim) | set(before_zero)
    kept_starts = {
        line_id: start for line_id, (start, _) in new_times.items() if line_id not in gone
    }
    report.score = score_alignment(
        alignment.anchors,
        kept_starts,
        [(block.from_ms, block.to_ms) for block in blocks],
        [block.support for block in blocks],
        slack_ms={pin.line_id: pin.slack_ms for pin in adjust.pins},
    )
    for block, scored in zip(blocks, report.score.blocks, strict=True):
        block.confidence = scored.confidence
    report.blocks = blocks
    if verdict == "aligned":
        weights = [max(1, block.lines) for block in blocks]
        report.confidence = sum(
            block.confidence * weight for block, weight in zip(blocks, weights, strict=True)
        ) / sum(weights)
    else:
        report.confidence = 0.3

    state.blocks = blocks
    state.rate = rate
    state.new_times = new_times
    state.matched = set(matched)
    state.removed = gone
    state.in_place = round((report.score.within_tolerance or 0.0) * report.score.anchors)

    weak = [block for block in blocks if block.confidence < settings.min_block_confidence]
    if weak and verdict == "aligned":
        warnings.append(f"{len(weak)} of {len(blocks)} blocks could not be placed with confidence.")
    if weak or merged or too_many or verdict != "aligned":
        outcome: Outcome = "partial"
    elif in_sync or not timing:
        outcome = "already_in_sync"
    else:
        outcome = "synced"
    return finish(outcome, content, timing)


def sync(
    target: Subtitle,
    *,
    audio: Path | None = None,
    reference: Subtitle | None = None,
    options: Options | None = None,
    audio_language: str | None = None,
    reference_language: str | None = None,
    settings: Settings | None = None,
    client: OpenRouter | None = None,
    cache: Cache | None = None,
    labels: list[LineLabel] | None = None,
) -> SyncResult:
    """Synchronize `target` to the audio, the reference subtitle, or both.

    `audio_language` is a hint for the speech model; without it the language is
    detected. `reference_language` only labels the reference subtitle.
    """
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
    return propose(prepared).result
