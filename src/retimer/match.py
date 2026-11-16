"""Finds anchors: pairs of target line and reference event that mean the same.

Lines are compared by meaning with a multilingual embedding model, each on its
own and together with its neighbours. A pair becomes an anchor when it stands
out against everything else in its row and column, and when the anchors keep
the same order in both files.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel

from retimer.embed import Embedder
from retimer.reference import ReferenceTimeline, RefEvent
from retimer.views import MatchLine

# How far a pair has to stand out from its row and column, in standard deviations.
MIN_DISTINCTIVENESS = 2.0
# Below the first value a pair is no evidence at all, from the second on it is full
# evidence. In between it counts in part, and only together with agreeing timing.
SOFT_LOW = 1.0
SOFT_HIGH = 3.0
# A line too short to tell apart never counts for more than this.
GENERIC_STRENGTH = 0.5
# Neighbours further away than this are not part of a line's context.
CONTEXT_GAP_MS = 4_000
REGIONS = 6


class Anchor(BaseModel):
    """A target line and the reference event that says the same."""

    target_id: int
    target_start_ms: int
    ref_index: int
    ref_start_ms: int
    offset_ms: int
    score: float


class MatchResult(BaseModel):
    anchors: list[Anchor]
    target_lines: int
    eligible_lines: int
    reference_events: int
    eligible_events: int
    embedding_model: str


class RegionCoverage(BaseModel):
    from_ms: int
    to_ms: int
    eligible_lines: int
    matched_lines: int


class Coverage(BaseModel):
    """Share of anchorable target lines with a confident counterpart, per region."""

    eligible_lines: int
    matched_lines: int
    share: float
    regions: list[RegionCoverage]


def is_generic(text: str) -> bool:
    """Whether a line is too short to tell apart: "Yes.", "Thank you.", "え？"."""
    letters = [char for char in text if char.isalnum()]
    wide = sum(1 for char in letters if unicodedata.east_asian_width(char) in ("W", "F"))
    if letters and wide / len(letters) >= 0.5:
        return len(letters) < 5
    words = [word for word in text.split() if any(char.isalnum() for char in word)]
    return len(words) < 3 and len(letters) < 12


def context_texts(items: list[tuple[int, int, str]]) -> list[str]:
    """Each text joined with its direct neighbours, when they are close in time."""
    joined = []
    for index, (start, end, text) in enumerate(items):
        parts = [text]
        if index > 0 and start - items[index - 1][1] <= CONTEXT_GAP_MS:
            parts.insert(0, items[index - 1][2])
        if index + 1 < len(items) and items[index + 1][0] - end <= CONTEXT_GAP_MS:
            parts.append(items[index + 1][2])
        joined.append(" ".join(parts))
    return joined


def similarity(
    target: list[tuple[int, int, str]], reference: list[tuple[int, int, str]], embedder: Embedder
) -> np.ndarray:
    """Cosine similarity of every target line with every reference event.

    The mean of two views: the line alone, which ties the pair to this line's
    start time, and the line with its neighbours, which tells a lone "I know"
    in one scene from the same words in another.
    """
    alone = (
        embedder.embed([text for _, _, text in target])
        @ embedder.embed([text for _, _, text in reference]).T
    )
    together = embedder.embed(context_texts(target)) @ embedder.embed(context_texts(reference)).T
    return (alone + together) / 2


def distinctiveness(scores: np.ndarray) -> np.ndarray:
    """How far each pair stands out from its row and from its column.

    Raw similarities depend on the embedding model and on how alike all lines
    of a film are. Standard scores do not, so one threshold serves every model.
    """
    if scores.size == 0:
        return scores
    by_row = (scores - scores.mean(axis=1, keepdims=True)) / (
        scores.std(axis=1, keepdims=True) + 1e-6
    )
    by_column = (scores - scores.mean(axis=0, keepdims=True)) / (
        scores.std(axis=0, keepdims=True) + 1e-6
    )
    return np.minimum(by_row, by_column)


def best_chain(weights: np.ndarray) -> list[tuple[int, int]]:
    """The pairs with the highest total weight that keep the same order in both lists."""
    rows, columns = weights.shape
    if rows == 0 or columns == 0:
        return []
    table = np.zeros((rows + 1, columns + 1), dtype=np.float32)
    for row in range(1, rows + 1):
        step = np.maximum(table[row - 1, 1:], table[row - 1, :-1] + weights[row - 1])
        table[row, 1:] = np.maximum.accumulate(step)
    pairs = []
    row, column = rows, columns
    while row > 0 and column > 0:
        if table[row, column] == table[row - 1, column]:
            row -= 1
        elif table[row, column] == table[row, column - 1]:
            column -= 1
        else:
            pairs.append((row - 1, column - 1))
            row -= 1
            column -= 1
    return pairs[::-1]


@dataclass
class Matching:
    """Everything matching found out about a target and a reference.

    `standing` holds, for every line and every event, how far the pair stands
    out. `anchors` are the pairs that are certain from the text alone. The fits
    use both: the anchors to see which offsets occur at all, the full table to
    place every line.
    """

    lines: list[MatchLine]
    events: list[RefEvent]
    standing: np.ndarray
    line_ok: np.ndarray
    event_ok: np.ndarray
    anchors: list[Anchor]
    embedding_model: str

    @property
    def result(self) -> MatchResult:
        return MatchResult(
            anchors=self.anchors,
            target_lines=len(self.lines),
            eligible_lines=int(self.line_ok.sum()),
            reference_events=len(self.events),
            eligible_events=int(self.event_ok.sum()),
            embedding_model=self.embedding_model,
        )

    def strength(self) -> np.ndarray:
        """How much each pair counts as the same line, from 0 to 1."""
        if self.standing.size == 0:
            return np.zeros(self.standing.shape, dtype=np.float32)
        strength = np.clip((self.standing - SOFT_LOW) / (SOFT_HIGH - SOFT_LOW), 0.0, 1.0)
        generic = ~self.line_ok[:, None] | ~self.event_ok[None, :]
        return np.where(generic, np.minimum(strength, GENERIC_STRENGTH), strength).astype(
            np.float32
        )


def match_lines(
    lines: list[MatchLine],
    timeline: ReferenceTimeline,
    embedder: Embedder,
    min_distinctiveness: float = MIN_DISTINCTIVENESS,
) -> Matching:
    """Match the target's dialogue lines against the reference timeline."""
    lines = sorted(lines, key=lambda line: (line.start_ms, line.id))
    events = list(timeline.events)
    target = [(line.start_ms, line.end_ms, line.text) for line in lines]
    reference = [(event.start_ms, event.end_ms, event.text) for event in events]
    line_ok = np.array([not is_generic(text) for _, _, text in target], dtype=bool)
    event_ok = np.array([not is_generic(text) for _, _, text in reference], dtype=bool)

    anchors: list[Anchor] = []
    standing = np.zeros((len(target), len(reference)), dtype=np.float32)
    if target and reference:
        standing = distinctiveness(similarity(target, reference, embedder))
        weights = np.clip(standing - min_distinctiveness, 0.0, None)
        weights[~line_ok, :] = 0.0
        weights[:, ~event_ok] = 0.0
        for row, column in best_chain(weights):
            line = lines[row]
            event = events[column]
            anchors.append(
                Anchor(
                    target_id=line.id,
                    target_start_ms=line.start_ms,
                    ref_index=column,
                    ref_start_ms=event.start_ms,
                    offset_ms=event.start_ms - line.start_ms,
                    score=round(float(standing[row, column]), 3),
                )
            )
    return Matching(
        lines=lines,
        events=events,
        standing=standing,
        line_ok=line_ok,
        event_ok=event_ok,
        anchors=anchors,
        embedding_model=embedder.model,
    )


def find_anchors(
    lines: list[MatchLine],
    timeline: ReferenceTimeline,
    embedder: Embedder,
    min_distinctiveness: float = MIN_DISTINCTIVENESS,
) -> MatchResult:
    """The anchors alone, for callers that do not fit them."""
    return match_lines(lines, timeline, embedder, min_distinctiveness).result


def match_content(lines: list[MatchLine], matched_ids: set[int]) -> Coverage:
    """How much of the target found a counterpart, overall and along the file."""
    eligible = sorted(
        (line for line in lines if not is_generic(line.text)), key=lambda line: line.start_ms
    )
    regions = []
    size = max(1, -(-len(eligible) // REGIONS))
    for start in range(0, len(eligible), size):
        part = eligible[start : start + size]
        regions.append(
            RegionCoverage(
                from_ms=part[0].start_ms,
                to_ms=part[-1].end_ms,
                eligible_lines=len(part),
                matched_lines=sum(1 for line in part if line.id in matched_ids),
            )
        )
    matched = sum(1 for line in eligible if line.id in matched_ids)
    return Coverage(
        eligible_lines=len(eligible),
        matched_lines=matched,
        share=matched / len(eligible) if eligible else 0.0,
        regions=regions,
    )
