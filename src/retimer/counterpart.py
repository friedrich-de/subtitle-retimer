"""The matched reference: the reference subtitle, reduced to what the target says too.

A job that was given a reference subtitle can hand it back as a second file.
It holds the dialogue lines that have a counterpart in the delivered target,
on the timeline the target was moved onto, with the request's content options
applied. Signs, songs and sound descriptions are left out, and so is every
line the target has nothing for. Shown together the two files are a dual
subtitle, and line by line the one is a translation of the other.

Which lines belong together is read off the clock. Once the target is in
place, a line of the reference and a line of the target that say the same are
on screen at the same time, however differently two languages cut their
sentences. The text is not asked a second time. It was asked where it decides
something, in placing the target, and a name or a short answer reads too
differently across languages to be told apart by meaning: "Kariya." and
"雁夜" stand out from each other no more than two unrelated lines do.
"""

from __future__ import annotations

from bisect import bisect_left
from itertools import accumulate

from pydantic import BaseModel, Field

from retimer.classify import LineLabel
from retimer.engine import apply_plan, moved_span, validate_plan
from retimer.model import Subtitle
from retimer.plan import ContentOp, Plan, RemoveLines
from retimer.reference import ReferenceTimeline, RefEvent
from retimer.text import is_drawing, to_plain
from retimer.views import Options, build_content_ops

# Two lines belong together when they share this much of the time the shorter
# one is on screen. Measured on a fansub script against the broadcast captions
# of the same episodes: a line shares nearly all of its time with a line of
# the other file or less than a fifth of it, as a gasp does that the next
# sentence runs into. Hardly anything lies in between, so the bar sits there.
MIN_SHARED = 0.5


class PairedLine(BaseModel):
    """One line of a delivered file, as a pair names it.

    `index` is the line's number in the file it was delivered in, counted
    from 1 over the lines a player shows as text: comments and drawings are
    not counted. In an SRT file that is the cue number. `text` is what a
    viewer reads, without tags and with real line breaks.
    """

    index: int
    start_ms: int
    end_ms: int
    text: str


class LinePair(BaseModel):
    """Lines of the two delivered files that are on screen together and say the same.

    Mostly one line on each side. Two languages cut their sentences
    differently, so a pair can hold several lines on either side: everything
    that hangs together stays in one pair. The span runs from the first of
    its lines to the last.
    """

    start_ms: int
    end_ms: int
    target: list[PairedLine]
    reference: list[PairedLine]


class MatchedReferenceReport(BaseModel):
    """What became of the lines of the reference subtitle.

    `left_out` counts the lines that are not in the file, by reason: the kind
    of line for what is not dialogue, `no_counterpart` for dialogue the
    target has nothing for, and the content option that emptied a line.
    """

    delivered: bool = False
    # Why there is no file, when there is none.
    note: str | None = None
    reference_lines: int = 0
    dialogue_lines: int = 0
    lines: int = 0
    left_out: dict[str, int] = Field(default_factory=dict)
    # Dialogue lines of the delivered target, and how many of them have a
    # counterpart in the file.
    target_dialogue_lines: int = 0
    target_paired_lines: int = 0
    # Dialogue lines of the target in blocks that could not be placed with
    # confidence. Nothing is paired with them.
    target_unplaced_lines: int = 0
    # The reference disagreed with the audio and was moved onto it.
    moved_onto_audio: bool = False
    # Which lines of the two delivered files belong together, in the order
    # they are shown. Every line of the matched reference is in one pair.
    pairs: list[LinePair] = Field(default_factory=list)


def link_by_time(
    events: list[RefEvent], spans: list[tuple[int, int]], min_shared: float = MIN_SHARED
) -> list[tuple[int, int]]:
    """Every event and span that are counterparts, as (event, span) positions.

    An event and a span are counterparts when they overlap for at least
    `min_shared` of the shorter of the two. One event can have several, and
    so can one span: a long sentence in one language is often two lines in
    the other.
    """
    order = sorted(range(len(spans)), key=lambda index: spans[index])
    starts = [spans[index][0] for index in order]
    # The latest end among the spans up to each one. Looking back from an
    # event stops where nothing before can reach it any more.
    reach = list(accumulate((spans[index][1] for index in order), max))
    links = []
    for column, event in enumerate(events):
        position = bisect_left(starts, event.end_ms) - 1
        while position >= 0 and reach[position] > event.start_ms:
            start, end = spans[order[position]]
            shared = min(end, event.end_ms) - max(start, event.start_ms)
            shorter = min(end - start, event.end_ms - event.start_ms)
            if shorter > 0 and shared >= min_shared * shorter:
                links.append((column, order[position]))
            position -= 1
    return sorted(links)


def pair_by_time(
    events: list[RefEvent], spans: list[tuple[int, int]], min_shared: float = MIN_SHARED
) -> tuple[list[bool], list[bool]]:
    """Which events and which spans have a counterpart on the other side."""
    links = link_by_time(events, spans, min_shared)
    with_span = {event for event, _ in links}
    with_event = {span for _, span in links}
    return (
        [index in with_span for index in range(len(events))],
        [index in with_event for index in range(len(spans))],
    )


def _groups(links: list[tuple[int, int]]) -> list[tuple[list[int], list[int]]]:
    """Events and spans that hang together through their links, group by group."""
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def root(node: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for event, span in links:
        parent[root(("event", event))] = root(("span", span))
    members: dict[tuple[str, int], tuple[set[int], set[int]]] = {}
    for event, span in links:
        events, spans = members.setdefault(root(("span", span)), (set(), set()))
        events.add(event)
        spans.add(span)
    return [(sorted(events), sorted(spans)) for events, spans in members.values()]


def _shown(subtitle: Subtitle) -> dict[int, PairedLine]:
    """The lines of a delivered file that a player shows as text, by line id."""
    shown = {}
    for line in subtitle.lines:
        if line.is_comment or is_drawing(line.text):
            continue
        shown[line.id] = PairedLine(
            index=len(shown) + 1,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            text=to_plain(line.text),
        )
    return shown


def pair_lines(
    links: list[tuple[int, int]],
    events: list[RefEvent],
    span_ids: list[list[int]],
    reference: Subtitle,
    target: Subtitle,
) -> list[LinePair]:
    """The pairs of lines of the two delivered files, from the links between them.

    `span_ids` names, for every span that was linked, the lines of the target
    it stands for. A line that is in neither delivered file is left out, and
    a pair that has lost one of its sides is no pair.
    """
    in_reference, in_target = _shown(reference), _shown(target)
    pairs = []
    for group_events, group_spans in _groups(links):
        ours = {line_id for index in group_events for line_id in events[index].line_ids}
        theirs = {line_id for index in group_spans for line_id in span_ids[index]}
        ref_lines = [in_reference[line_id] for line_id in ours if line_id in in_reference]
        target_lines = [in_target[line_id] for line_id in theirs if line_id in in_target]
        if not ref_lines or not target_lines:
            continue
        ref_lines.sort(key=lambda line: line.index)
        target_lines.sort(key=lambda line: line.index)
        both = ref_lines + target_lines
        pairs.append(
            LinePair(
                start_ms=min(line.start_ms for line in both),
                end_ms=max(line.end_ms for line in both),
                target=target_lines,
                reference=ref_lines,
            )
        )
    pairs.sort(key=lambda pair: (pair.start_ms, pair.reference[0].index))
    return pairs


def match_reference(
    reference: Subtitle,
    labels: list[LineLabel],
    timeline: ReferenceTimeline,
    target_spans: list[tuple[int, int]],
    options: Options,
    target: Subtitle | None = None,
    target_ids: list[list[int]] | None = None,
) -> tuple[Subtitle | None, MatchedReferenceReport]:
    """Reduce the reference subtitle to the lines the target has a counterpart for.

    `timeline` is the one the target was matched against, built from this
    reference. `target_spans` are the dialogue lines of the delivered target,
    at the times the plan gave them. The file comes back with its lines where
    the timeline has them: at their own times, or moved onto the audio when
    the two disagreed. Without a single counterpart there is no file.

    With `target`, the delivered target, and `target_ids`, the lines of it
    that each span stands for, the report also lists which lines of the two
    files belong together.
    """
    label_of = {label.id: label.label for label in labels}
    report = MatchedReferenceReport(
        reference_lines=len(reference),
        dialogue_lines=sum(1 for label in labels if label.label == "dialogue"),
        target_dialogue_lines=len(target_spans),
        moved_onto_audio=bool(timeline.moves),
    )
    events = [event for event in timeline.events if event.line_ids]
    links = link_by_time(events, target_spans)
    with_span = {event for event, _ in links}
    event_paired = [index in with_span for index in range(len(events))]
    report.target_paired_lines = len({span for _, span in links})
    spoken = {line_id for event in events for line_id in event.line_ids}
    paired = {
        line_id
        for event, has_one in zip(events, event_paired, strict=True)
        if has_one
        for line_id in event.line_ids
    }
    if not paired:
        report.note = "No line of the reference subtitle has a counterpart in the target."
        return None, report

    moves = Plan(timing_ops=timeline.moves)
    remove: dict[str, list[int]] = {}
    for line in reference.lines:
        label = label_of[line.id]
        if label != "dialogue":
            reason = label
        elif line.id not in spoken:
            # Dialogue by its style, with nothing in it that is spoken.
            reason = "empty"
        elif line.id not in paired:
            reason = "no_counterpart"
        else:
            start, end = moved_span(moves, line.start_ms, line.end_ms)
            if not (line.duration_ms > 0 and start < 0 and end <= 0):
                continue
            # Moved onto the audio, the line would be over before it begins.
            reason = "before_start"
        remove.setdefault(reason, []).append(line.id)

    content: list[ContentOp] = [
        RemoveLines(reason=reason, ids=ids) for reason, ids in remove.items()
    ]
    content += build_content_ops(reference, options, labels)
    plan = Plan(content_ops=content, timing_ops=timeline.moves)
    if validate_plan(reference, plan):
        report.note = (
            "The reference subtitle was moved onto the audio in blocks that run into each "
            "other, so its lines cannot be written out in order."
        )
        return None, report
    applied = apply_plan(reference, plan)
    summary = applied.summary
    report.left_out = dict(summary.removed)
    if summary.emptied_by_strip:
        report.left_out["emptied"] = summary.emptied_by_strip
    report.lines = summary.lines_out
    if not summary.lines_out:
        report.note = "The content options left nothing of the lines that have a counterpart."
        return None, report
    report.delivered = True
    if target is not None and target_ids is not None:
        report.pairs = pair_lines(links, events, target_ids, applied.subtitle, target)
    return applied.subtitle, report
