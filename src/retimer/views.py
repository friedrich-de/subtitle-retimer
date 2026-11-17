"""The two views of a target subtitle.

The output view is what the request options ask for, expressed as content
operations for the plan. The match view is the working copy used to find
anchors: dialogue only, reduced to the spoken words. They are independent on
purpose, because what helps matching is not always what the user wants removed.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from retimer import clean
from retimer.classify import LineLabel, classify_lines
from retimer.model import Subtitle
from retimer.plan import ContentOp, RemoveLines, StripText
from retimer.text import is_drawing, to_plain


class Options(BaseModel):
    """Request options that decide what the delivered subtitle contains."""

    remove_hearing_impaired: bool = False
    remove_karaoke: bool = False
    strip_formatting: bool = False
    remove_symbols: bool = False
    # Trim leading or trailing lines that the reference confirms are absent. This one
    # is decided while syncing, so it yields no content operation here.
    drop_unmatched: bool = True


class MatchLine(BaseModel):
    id: int
    start_ms: int
    end_ms: int
    text: str


class MatchView(BaseModel):
    lines: list[MatchLine]
    excluded: dict[str, int]


class Views(BaseModel):
    content_ops: list[ContentOp]
    match_view: MatchView


def build_content_ops(
    subtitle: Subtitle, options: Options, labels: list[LineLabel]
) -> list[ContentOp]:
    """Translate the request options into content operations on the original file."""
    label_by_id = {label.id: label.label for label in labels}
    remove: dict[str, list[int]] = {}
    strip: dict[str, list[int]] = {}

    steps = []
    if options.remove_hearing_impaired:
        steps.append(("hearing_impaired", clean.remove_hearing_impaired))
    if options.remove_symbols:
        steps.append(("symbols", clean.remove_symbols))
    if options.strip_formatting:
        steps.append(("formatting", clean.strip_formatting))

    for line in subtitle.lines:
        label = label_by_id[line.id]
        if label == "comment":
            continue
        if options.remove_karaoke and label == "song":
            remove.setdefault("karaoke", []).append(line.id)
            continue
        if options.remove_hearing_impaired and label == "sound_description":
            remove.setdefault("hearing_impaired", []).append(line.id)
            continue
        if options.strip_formatting and is_drawing(line.text):
            remove.setdefault("drawing", []).append(line.id)
            continue

        text = line.text
        changed_by = []
        for what, strip_step in steps:
            new_text = strip_step(text)
            if new_text == text:
                continue
            if not to_plain(new_text):
                # Nothing readable is left, so the line goes instead of being edited.
                remove.setdefault(what, []).append(line.id)
                changed_by = []
                break
            changed_by.append(what)
            text = new_text
        for what in changed_by:
            strip.setdefault(what, []).append(line.id)

    ops: list[ContentOp] = [RemoveLines(reason=reason, ids=ids) for reason, ids in remove.items()]
    ops += [StripText(what=what, ids=strip[what]) for what, _ in steps if what in strip]
    return ops


# Lines with the same words that follow each other this closely are one line
# on screen: the layers of a line with a border or a glow, or a line that an
# effect draws again for every frame.
_SAME_LINE_GAP_MS = 150


def _join_repeats(lines: list[MatchLine]) -> tuple[list[MatchLine], int]:
    """One line for every run of lines that show the same words at the same time.

    The first line of a run stands for it, with the time span of the whole
    run. Returns the lines in the order they came in, and how many were joined
    into another.
    """
    joined: dict[int, MatchLine] = {}
    latest: dict[str, int] = {}
    for line in sorted(lines, key=lambda line: (line.start_ms, line.id)):
        first = joined.get(latest.get(line.text, -1))
        if first is not None and line.start_ms <= first.end_ms + _SAME_LINE_GAP_MS:
            if line.end_ms > first.end_ms:
                joined[first.id] = first.model_copy(update={"end_ms": line.end_ms})
            continue
        joined[line.id] = line
        latest[line.text] = line.id
    kept = [joined[line.id] for line in lines if line.id in joined]
    return kept, len(lines) - len(kept)


def build_match_view(subtitle: Subtitle, labels: list[LineLabel]) -> MatchView:
    """Dialogue lines reduced to their spoken words. Each keeps the id of its original line."""
    label_by_id = {label.id: label.label for label in labels}
    lines = []
    excluded: Counter[str] = Counter()
    for line in subtitle.lines:
        label = label_by_id[line.id]
        if label != "dialogue":
            excluded[label] += 1
            continue
        text = clean.normalize_for_match(line.text)
        if not text:
            excluded["empty"] += 1
            continue
        lines.append(MatchLine(id=line.id, start_ms=line.start_ms, end_ms=line.end_ms, text=text))
    lines, repeats = _join_repeats(lines)
    if repeats:
        excluded["repeat"] += repeats
    return MatchView(lines=lines, excluded=dict(excluded))


def build_views(
    subtitle: Subtitle, options: Options, labels: list[LineLabel] | None = None
) -> Views:
    labels = labels if labels is not None else classify_lines(subtitle)
    return Views(
        content_ops=build_content_ops(subtitle, options, labels),
        match_view=build_match_view(subtitle, labels),
    )
