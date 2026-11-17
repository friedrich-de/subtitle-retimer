"""Fits the offsets: one shift, a rate change, block shifts, or rate plus blocks.

Every pair of target line and reference event says how far the line would have
to move to meet the event. For the pairs that mean the same, these offsets form
a shape over the file, and each fit is one explanation of that shape. A fit is
`reference = target * rate + offset`, with one offset per block.

The work is done in two steps. First the offsets that many pairs agree on are
collected; each is a candidate for a block. Then every line is given the
candidate that explains it best, and every change of candidate has a price, so
a block only appears where several lines in a row ask for it.

The fits are robust on purpose. Some pairs are wrong, and speech timestamps
scatter by a few hundred milliseconds, so a pair only counts near a candidate,
and it counts for less the further away it is.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from retimer.match import Anchor, Matching

FILM = 24000 / 1001
# Frame rate conversions that occur in practice, as reference time over target time.
KNOWN_RATES: dict[str, float] = {
    "25 fps to 23.976 fps": 25 / FILM,
    "23.976 fps to 25 fps": FILM / 25,
    "25 fps to 24 fps": 25 / 24,
    "24 fps to 25 fps": 24 / 25,
    "24 fps to 23.976 fps": 24 / FILM,
    "23.976 fps to 24 fps": FILM / 24,
}

# Pairs within this distance of a block's offset belong to it. The actual
# tolerance adapts to how tightly the anchors agree, within these bounds.
MIN_TOLERANCE_MS = 150
MAX_TOLERANCE_MS = 700
# A pair this far from its block's offset would count for nothing.
SUPPORT_SCALE_MS = 1_000
# With fewer anchors than this there is nothing to fit.
MIN_ANCHORS = 3
MAX_CANDIDATES = 12
# What the pairs behind a candidate offset have to add up to.
MIN_CANDIDATE_WEIGHT = 1.5
# A line that merely lands where something is said counts this much.
ONSET_STRENGTH = 0.1
# From this strength on a line counts as having found its counterpart.
MATCHED_STRENGTH = 0.3
# What a fit pays for its complexity, in lines it has to explain on top. A rate
# has to win over a tenth of the lines: on a short file a slight rate change
# and scattered timestamps look alike, and then no rate is the safer reading.
RATE_PENALTY = 1.5
RATE_PENALTY_SHARE = 0.1
# How much further the pairs may scatter under a rate than without one for the
# rate to be considered at all.
RATE_TOLERANCE_SLACK = 1.25
# A break has to be paid for by the lines after it. Where the pairs agree to a
# fraction of a second, as two subtitles do, a chance agreement is unlikely and
# two lines are enough. Against speech timestamps it takes more.
MIN_BREAK_COST = 1.2
MAX_BREAK_COST = 2.0
# Fits of one kind whose scores are this close explain the lines equally well.
CLOSE_SCORE_SHARE = 0.05
CLOSE_SCORE = 1.0
# A break is a little cheaper at a long pause, which decides between positions
# that explain the lines equally well.
PAUSE_BONUS = 0.3
PAUSE_FULL_MS = 10_000
# What a pair counts that a reader asserted. It outweighs any pair found by
# similarity and pays for a break on its own, so one asserted line can open a
# block at the end of a file or between two blocks.
PIN_STRENGTH = 3.0
# In working out the offset of a block an asserted pair weighs no more than a
# pair found by matching. One whose place is timed loosely weighs nothing where
# the block has a line that was measured: the reader only read.
PIN_WEIGHT = 1.0
# A line the routine steps placed keeps the counterpart it was placed on. With
# asserted pairs in play, it counts only for reference events this close to it,
# and leaving the block that puts it there costs as much as breaking a pair.
HOLD_WINDOW_MS = 1_500
HOLD_STRENGTH = 3.0

FitKind = Literal["constant", "rate", "blocks", "rate_blocks"]


class Pin(BaseModel):
    """A line and the moment in the reference where it is said, asserted by a reader.

    The reader says which line and which place in the reference belong
    together. The times are looked up, never written by the reader.

    `slack_ms` is how well the time of the place is known. A line of a
    reference subtitle starts when it says. A clause inside a transcribed
    utterance starts about when its first word was heard, and a subtitle line
    leads or trails that by more than the lines of one block differ. So the
    pair says that its line belongs to any block that puts it this close to
    the place, without saying when exactly the block starts.
    """

    line_id: int
    line_start_ms: int
    ref_ms: int
    slack_ms: int = 0

    @property
    def anchor(self) -> Anchor:
        return Anchor(
            target_id=self.line_id,
            target_start_ms=self.line_start_ms,
            ref_index=-1,
            ref_start_ms=self.ref_ms,
            offset_ms=self.ref_ms - self.line_start_ms,
            score=PIN_STRENGTH,
        )


@dataclass
class Evidence:
    """What the fits are made from.

    `strength` says, for every line and every reference event, how much the
    pair counts as the same line, from 0 to 1. Lines and events are in time
    order. `anchors` are the pairs that are certain from the text alone. A
    pinned line counts for its pin alone, and for more than any other pair.
    `pinned` gives, for each pin, the row of its line, the time of its place
    and how well that time is known.

    `hold` names lines with the reference time they were placed at before any
    pair was asserted. Such a line keeps that counterpart: an asserted pair
    may open a block for lines nobody placed, but it does not pull placed
    lines over to it.
    """

    line_ids: list[int]
    line_starts: np.ndarray
    ref_starts: np.ndarray
    strength: np.ndarray
    anchors: list[Anchor]
    pins: list[Pin] = field(default_factory=list)
    pinned: list[tuple[int, float, int]] = field(default_factory=list)
    held: list[tuple[int, float]] = field(default_factory=list)

    @classmethod
    def from_matching(
        cls,
        matching: Matching,
        pins: list[Pin] | None = None,
        hold: dict[int, int] | None = None,
    ) -> Evidence:
        pins = list(pins or [])
        pinned = {pin.line_id for pin in pins}
        lines = [(line.start_ms, line.id) for line in matching.lines]
        known = {line.id for line in matching.lines}
        # A pinned line that matching left out, a sign that turned out to be spoken, joins.
        extra = sorted({(pin.line_start_ms, pin.line_id) for pin in pins} - set(lines))
        extra = [item for item in extra if item[1] not in known]
        strength = matching.strength().astype(np.float64)
        if extra:
            strength = np.vstack([strength, np.zeros((len(extra), strength.shape[1]))])
            lines += extra
        ref_starts = np.array([event.start_ms for event in matching.events], dtype=np.float64)
        homes: dict[int, float] = {}
        for row, (_, line_id) in enumerate(lines):
            home = (hold or {}).get(line_id)
            if home is not None and line_id not in pinned and row < len(matching.lines):
                strength[row, np.abs(ref_starts - home) > HOLD_WINDOW_MS] = 0.0
                homes[line_id] = float(home)
        if pins:
            row_of = {line_id: row for row, (_, line_id) in enumerate(lines)}
            strength = np.hstack([strength, np.zeros((len(lines), len(pins)))])
            for column, pin in enumerate(pins):
                row = row_of[pin.line_id]
                strength[row, :] = 0.0
                strength[row, len(matching.events) + column] = PIN_STRENGTH
            ref_starts = np.concatenate([ref_starts, [float(pin.ref_ms) for pin in pins]])
        rows = sorted(range(len(lines)), key=lambda row: lines[row])
        order = np.argsort(ref_starts, kind="stable")
        new_row = {lines[row][1]: position for position, row in enumerate(rows)}
        return cls(
            line_ids=[lines[row][1] for row in rows],
            line_starts=np.array([lines[row][0] for row in rows], dtype=np.float64),
            ref_starts=ref_starts[order],
            strength=strength[rows][:, order],
            anchors=[anchor for anchor in matching.anchors if anchor.target_id not in pinned]
            + [pin.anchor for pin in pins],
            pins=pins,
            pinned=[(new_row[pin.line_id], float(pin.ref_ms), pin.slack_ms) for pin in pins],
            held=[(new_row[line_id], home) for line_id, home in homes.items()],
        )


class Block(BaseModel):
    """A stretch of the target that moves by one offset.

    `from_ms` and `to_ms` are start times in the original target. The first
    block has no start and the last no end. `matched` counts the lines that
    found their counterpart at this offset, `support` adds up how well.
    """

    from_ms: int | None
    to_ms: int | None
    offset_ms: int
    lines: int
    matched: int
    support: float
    spread_ms: float


class Fit(BaseModel):
    kind: FitKind
    rate: float = 1.0
    rate_name: str | None = None
    blocks: list[Block]
    lines: int
    matched: int
    matched_share: float
    support: float
    median_error_ms: float | None
    p90_error_ms: float | None
    tolerance_ms: int
    score: float
    matched_ids: list[int] = Field(default_factory=list, exclude=True)

    def block_at(self, start_ms: int) -> Block:
        """The block that a line starting at `start_ms`, in the original target, is in."""
        for block in self.blocks:
            if block.to_ms is None or start_ms < block.to_ms:
                return block
        return self.blocks[-1]

    def residual(self, anchor: Anchor) -> float:
        """How far the anchor's line would land from its counterpart."""
        block = self.block_at(anchor.target_start_ms)
        return anchor.ref_start_ms - anchor.target_start_ms * self.rate - block.offset_ms

    def explained(self, anchors: list[Anchor]) -> set[int]:
        """Target line ids of the anchors that this fit puts in place."""
        return {
            anchor.target_id
            for anchor in anchors
            if abs(self.residual(anchor)) <= self.tolerance_ms
        }


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    total = np.cumsum(weights[order])
    return float(values[order][int(np.searchsorted(total, total[-1] / 2))])


def _tolerance(offsets: np.ndarray) -> int:
    """How far pairs of one block scatter, judged by the tightest large cluster of anchors."""
    if len(offsets) < MIN_ANCHORS:
        return MAX_TOLERANCE_MS
    ordered = np.sort(offsets)
    ends = np.searchsorted(ordered, ordered + 2 * MAX_TOLERANCE_MS, side="right")
    start = int(np.argmax(ends - np.arange(len(ordered))))
    members = ordered[start : ends[start]]
    spread = float(np.median(np.abs(members - np.median(members))))
    return int(min(MAX_TOLERANCE_MS, max(MIN_TOLERANCE_MS, 3 * 1.4826 * spread)))


def _candidates(offsets: np.ndarray, weights: np.ndarray, tolerance: int) -> list[float]:
    """Offsets that many pairs agree on, the best supported first.

    Wrong pairs spread their offsets over the whole length of the file, so a
    candidate has to gather clearly more weight than such pairs would by chance.
    """
    if len(offsets) == 0:
        return []
    order = np.argsort(offsets, kind="stable")
    values = offsets[order]
    left = weights[order].astype(np.float64)
    width = 2 * tolerance
    span = max(float(values[-1] - values[0]), float(width))
    ends = np.searchsorted(values, values + width, side="right")
    centers: list[float] = []
    while len(centers) < MAX_CANDIDATES:
        total = np.concatenate([[0.0], np.cumsum(left)])
        sums = total[ends] - total[:-1]
        start = int(np.argmax(sums))
        # What a stretch of this width would gather from the pairs outside it.
        chance = float(total[-1] - sums[start]) * width / span
        if sums[start] < max(MIN_CANDIDATE_WEIGHT, chance + 3 * math.sqrt(chance)):
            break
        members = slice(start, int(ends[start]))
        centers.append(_weighted_median(values[members], left[members]))
        left[members] = 0.0
    return centers


def _table(
    evidence: Evidence, rate: float, centers: list[float], tolerance: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every line and candidate, the best counterpart near where the line would land.

    Returns how much it counts, how alike the two texts are, and how far the
    counterpart is from where the line lands.
    """
    predicted = evidence.line_starts * rate
    count = len(predicted)
    events = len(evidence.ref_starts)
    rows = np.arange(count)
    value = np.zeros((count, len(centers)))
    text = np.zeros((count, len(centers)))
    residual = np.zeros((count, len(centers)))
    for index, center in enumerate(centers):
        landing = predicted + center
        low = np.searchsorted(evidence.ref_starts, landing - tolerance, side="left")
        high = np.searchsorted(evidence.ref_starts, landing + tolerance, side="right")
        for step in range(int((high - low).max(initial=0))):
            column = np.minimum(low + step, events - 1)
            inside = low + step < high
            delta = evidence.ref_starts[column] - landing
            alike = evidence.strength[rows, column]
            worth = np.where(
                inside,
                np.maximum(alike, ONSET_STRENGTH) * (1.0 - (delta / SUPPORT_SCALE_MS) ** 2),
                0.0,
            )
            better = worth > value[:, index]
            value[:, index] = np.where(better, worth, value[:, index])
            text[:, index] = np.where(better, alike, text[:, index])
            residual[:, index] = np.where(better, delta, residual[:, index])
    offsets = np.asarray(centers, dtype=np.float64)
    for row, ref_ms, slack in evidence.pinned:
        delta = ref_ms - (predicted[row] + offsets)
        inside = np.abs(delta) <= max(tolerance, slack)
        value[row] = np.where(inside, PIN_STRENGTH, 0.0)
        text[row] = np.where(inside, PIN_WEIGHT, 0.0)
        residual[row] = np.where(inside, delta, 0.0)
    return value, text, residual


def _break_cost(tolerance: int) -> float:
    share = (tolerance - MIN_TOLERANCE_MS) / (MAX_TOLERANCE_MS - MIN_TOLERANCE_MS)
    return MIN_BREAK_COST + (MAX_BREAK_COST - MIN_BREAK_COST) * min(1.0, max(0.0, share))


def _assign(
    value: np.ndarray,
    landing: np.ndarray,
    centers: list[float],
    break_cost: float,
    slack: float,
) -> np.ndarray:
    """Give every line a candidate, so that the lines are explained well and breaks are few.

    A break is only allowed where it keeps the lines in order: the lines after
    it may not move in front of the lines before it. The offsets are only known
    to within `slack`, so that much of a crossing is let through here and
    straightened out when the plan is built.
    """
    count, states = value.shape
    offsets = np.asarray(centers)
    same = np.eye(states, dtype=bool)
    cost = 1.0 - value
    total = cost[0].copy()
    back = np.zeros((count, states), dtype=np.int32)
    for index in range(1, count):
        gap = landing[index] - landing[index - 1]
        price = break_cost - PAUSE_BONUS * min(1.0, gap / PAUSE_FULL_MS)
        in_order = offsets[None, :] >= offsets[:, None] - gap - slack
        move = total[:, None] + np.where(same, 0.0, np.where(in_order, price, np.inf))
        back[index] = move.argmin(axis=0)
        total = move.min(axis=0) + cost[index]
    labels = np.zeros(count, dtype=np.int32)
    labels[-1] = int(np.argmin(total))
    for index in range(count - 1, 0, -1):
        labels[index - 1] = back[index, labels[index]]
    return labels


def _middle(residual: np.ndarray, weight: np.ndarray, loose: np.ndarray) -> float:
    """The offset the lines of a block agree on.

    `loose` marks asserted pairs whose place is timed loosely, such as the
    start of a clause in a transcript. Where the block has anything better,
    that alone says when it starts, and a loose pair only says that its line
    belongs to the block. A block that rests on loose pairs alone has few of
    them, often two, and then the plain middle is a better estimate than
    either of the two.
    """
    exact = ~loose
    if exact.any():
        return _weighted_median(residual[exact], weight[exact])
    return float(np.median(residual)) if len(residual) else 0.0


def _refined(
    centers: list[float],
    labels: np.ndarray,
    text: np.ndarray,
    residual: np.ndarray,
    loose: np.ndarray,
) -> list[float]:
    """Move every candidate in use to the middle of the lines that chose it."""
    refined = list(centers)
    rows = np.arange(len(labels))
    for state in set(labels.tolist()):
        chosen = rows[(labels == state) & (text[rows, labels] >= MATCHED_STRENGTH)]
        if len(chosen):
            refined[state] += _middle(residual[chosen, state], text[chosen, state], loose[chosen])
    return refined


def _merge_alike(blocks: list[Block], tolerance: int) -> list[Block]:
    """Join neighbours that ended up with nearly the same offset."""
    merged: list[Block] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if previous is None or abs(previous.offset_ms - block.offset_ms) > tolerance // 2:
            merged.append(block)
            continue
        weights = (max(previous.matched, 1), max(block.matched, 1))
        merged[-1] = Block(
            from_ms=previous.from_ms,
            to_ms=block.to_ms,
            offset_ms=round(
                (previous.offset_ms * weights[0] + block.offset_ms * weights[1]) / sum(weights)
            ),
            lines=previous.lines + block.lines,
            matched=previous.matched + block.matched,
            support=round(previous.support + block.support, 2),
            spread_ms=max(previous.spread_ms, block.spread_ms),
        )
    return merged


def _fit(
    evidence: Evidence,
    rate: float,
    rate_name: str | None,
    centers: list[float],
    tolerance: int,
    with_blocks: bool,
) -> Fit | None:
    landing = evidence.line_starts * rate
    count = len(landing)
    rows = np.arange(count)
    asserted = np.zeros(count, dtype=bool)
    asserted[[row for row, _, _ in evidence.pinned]] = True
    loose = np.zeros(count, dtype=bool)
    loose[[row for row, _, slack in evidence.pinned if slack > 0]] = True

    def assign(table: np.ndarray, offsets: list[float]) -> np.ndarray:
        if with_blocks:
            # A held line is worth more to the candidate that leaves it where it
            # was placed. That only steers the choice, it is not counted as support.
            grip = np.zeros_like(table)
            for row, home in evidence.held:
                near = np.abs(landing[row] + np.asarray(offsets) - home) <= tolerance
                grip[row] = np.where(near, HOLD_STRENGTH, 0.0)
            return _assign(table + grip, landing, offsets, _break_cost(tolerance), tolerance)
        return np.full(count, int(np.argmax(table.sum(axis=0))), dtype=np.int32)

    # Twice: the first round shows which lines belong to a candidate, and their
    # middle is a better offset for it than the first guess.
    value, text, residual = _table(evidence, rate, centers, tolerance)
    labels = assign(value, centers)
    centers = _refined(centers, labels, text, residual, loose)
    value, text, residual = _table(evidence, rate, centers, tolerance)
    labels = assign(value, centers)

    blocks = []
    errors: list[float] = []
    matched_ids: list[int] = []
    start = 0
    for index in range(1, count + 1):
        if index < count and labels[index] == labels[start]:
            continue
        state = int(labels[start])
        inside = rows[start:index]
        found = inside[text[inside, state] >= MATCHED_STRENGTH]
        shift = 0.0
        spread = 0.0
        if len(found):
            shift = _middle(residual[found, state], text[found, state], loose[found])
            # How tightly a fit holds is judged by the lines matching found. An
            # asserted pair is not a measurement of time, unless it is all there is.
            own = found[~asserted[found]]
            measured = own if len(own) else found
            away = np.abs(residual[measured, state] - shift)
            spread = float(np.median(away))
            errors += [float(error) for error in away]
            matched_ids += [evidence.line_ids[row] for row in found]
        blocks.append(
            Block(
                from_ms=None if start == 0 else int(evidence.line_starts[start]),
                to_ms=None if index == count else int(evidence.line_starts[index]),
                offset_ms=round(centers[state] + shift),
                lines=index - start,
                matched=len(found),
                support=round(float(value[inside, state].sum()), 2),
                spread_ms=round(spread, 1),
            )
        )
        start = index
    blocks = _merge_alike(blocks, tolerance)
    if not matched_ids:
        return None

    support = float(value[rows, labels].sum())
    is_rate = rate != 1.0
    kind: FitKind = (
        ("rate_blocks" if is_rate else "blocks")
        if len(blocks) > 1
        else ("rate" if is_rate else "constant")
    )
    rate_penalty = max(RATE_PENALTY, RATE_PENALTY_SHARE * support) if is_rate else 0.0
    score = support - rate_penalty - _break_cost(tolerance) * (len(blocks) - 1)
    return Fit(
        kind=kind,
        rate=rate,
        rate_name=rate_name,
        blocks=blocks,
        lines=count,
        matched=len(matched_ids),
        matched_share=len(matched_ids) / count,
        support=round(support, 2),
        median_error_ms=round(statistics.median(errors), 1),
        p90_error_ms=round(float(np.percentile(errors, 90)), 1),
        tolerance_ms=tolerance,
        score=round(score, 2),
        matched_ids=matched_ids,
    )


def fit_offsets(evidence: Evidence) -> list[Fit]:
    """The best fit of each kind that the evidence supports, best score first.

    Only frame rate conversions that occur in practice are tried as rates, which
    keeps a handful of wrong pairs from being explained as a strange rate.
    """
    if len(evidence.anchors) < MIN_ANCHORS or len(evidence.line_starts) == 0:
        return []
    # How tightly the pairs of a block agree is judged by the pairs matching
    # found. Asserted pairs join only when there are too few of those.
    measured = [anchor for anchor in evidence.anchors if anchor.ref_index >= 0]
    if len(measured) < MIN_ANCHORS:
        measured = evidence.anchors
    anchor_starts = np.array([anchor.target_start_ms for anchor in measured], dtype=float)
    anchor_refs = np.array([anchor.ref_start_ms for anchor in measured], dtype=float)
    lines, events = np.nonzero(evidence.strength > 0)
    weights = evidence.strength[lines, events]

    found: dict[str, list[Fit]] = {}
    rates: list[tuple[float, str | None]] = [(1.0, None), *((r, n) for n, r in KNOWN_RATES.items())]
    unscaled = _tolerance(anchor_refs - anchor_starts)
    for rate, name in rates:
        tolerance = _tolerance(anchor_refs - anchor_starts * rate)
        # The right rate makes the pairs agree more closely, never less. A rate
        # under which they scatter further than without it is the wrong one,
        # and the wider tolerance it would be given lets it gather lines that
        # only land near something. With blocks that follow the drift it then
        # outscores the truth and leaves every line up to half a second off.
        if rate != 1.0 and tolerance > RATE_TOLERANCE_SLACK * unscaled:
            continue
        centers = _candidates(
            evidence.ref_starts[events] - evidence.line_starts[lines] * rate, weights, tolerance
        )
        # An asserted pair that no candidate explains is a candidate of its own,
        # however little else speaks for its offset.
        for pin in evidence.pins:
            offset = pin.ref_ms - pin.line_start_ms * rate
            reach = max(tolerance / 2, pin.slack_ms)
            if all(abs(offset - center) > reach for center in centers):
                centers.append(float(offset))
        if not centers:
            continue
        for with_blocks in (False, True):
            fit = _fit(evidence, rate, name, centers, tolerance, with_blocks)
            if fit is not None:
                found.setdefault(fit.kind, []).append(fit)
    best = [_closest(fits) for fits in found.values()]
    return sorted(best, key=lambda fit: (-fit.score, len(fit.blocks), fit.rate != 1.0))


def _closest(fits: list[Fit]) -> Fit:
    """Of the fits of one kind, the best scoring, or among near equals the tightest.

    Two frame rates a tenth of a percent apart explain the same lines of a
    short film. What tells them apart is how closely: under the wrong rate the
    lines drift away from their counterparts along the file.
    """
    top = max(fit.score for fit in fits)
    close = [
        fit for fit in fits if fit.score >= top - max(CLOSE_SCORE, CLOSE_SCORE_SHARE * abs(top))
    ]
    return min(close, key=lambda fit: (fit.median_error_ms, -fit.score))


def choose_fit(fits: list[Fit]) -> Fit | None:
    """The fit a fixed pipeline goes with: the best score, and the simpler one on a tie."""
    return fits[0] if fits else None
