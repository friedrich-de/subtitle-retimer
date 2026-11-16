"""The verification score: how well a retimed file agrees with the reference."""

from __future__ import annotations

import statistics

from pydantic import BaseModel

from retimer.match import Anchor

# An anchored line counts as in place when it starts this close to its counterpart.
SCORE_TOLERANCE_MS = 500


class BlockScore(BaseModel):
    from_ms: int | None
    to_ms: int | None
    anchors: int
    within_tolerance: float | None
    median_error_ms: float | None
    confidence: float


class Score(BaseModel):
    """Agreement between the anchored lines, at their new times, and the reference."""

    anchors: int
    within_tolerance: float | None
    median_error_ms: float | None
    p90_error_ms: float | None
    blocks: list[BlockScore]
    tolerance_ms: int = SCORE_TOLERANCE_MS


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def block_confidence(support: float, within: float | None) -> float:
    """Trust in one block: enough lines found their counterpart, and the anchors are in place.

    `support` is what the lines of the block add up to in the fit. From about
    six on, more adds little. `within` is the share of the block's anchors that
    are in place. The anchors were found from the text alone, so they are an
    independent check; a block without a single anchor is trusted half.
    """
    if support <= 0:
        return 0.0
    checked = 0.5 if within is None else within
    return round(checked * (0.4 + 0.6 * min(1.0, support / 6)), 2)


def score_alignment(
    anchors: list[Anchor],
    new_starts: dict[int, int],
    ranges: list[tuple[int | None, int | None]],
    supports: list[float],
    tolerance_ms: int = SCORE_TOLERANCE_MS,
    slack_ms: dict[int, int] | None = None,
) -> Score:
    """Score the anchors against the new start times of their lines.

    `new_starts` maps line ids to start times after the plan; lines the plan
    removed are absent. `ranges` are the blocks of the plan, in original target
    time, with what supports each, and every block gets its own figures.
    `slack_ms` names lines whose counterpart is timed less exactly than the
    tolerance: such a line is in place within its own slack.
    """
    slack = slack_ms or {}
    placed = [
        (anchor, abs(anchor.ref_start_ms - new_starts[anchor.target_id]))
        for anchor in anchors
        if anchor.target_id in new_starts
    ]
    errors = [error for _, error in placed]
    # What each line may be off by, next to how far off it is.
    allowed = [max(tolerance_ms, slack.get(anchor.target_id, 0)) for anchor, _ in placed]

    def within(members: list[int]) -> float | None:
        if not members:
            return None
        inside = sum(1 for index in members if errors[index] <= allowed[index])
        return round(inside / len(members), 3)

    blocks = []
    for (from_ms, to_ms), support in zip(ranges, supports, strict=True):
        inside = [
            index
            for index, (anchor, _) in enumerate(placed)
            if (from_ms is None or anchor.target_start_ms >= from_ms)
            and (to_ms is None or anchor.target_start_ms < to_ms)
        ]
        share = within(inside)
        blocks.append(
            BlockScore(
                from_ms=from_ms,
                to_ms=to_ms,
                anchors=len(inside),
                within_tolerance=share,
                median_error_ms=statistics.median(errors[index] for index in inside)
                if inside
                else None,
                confidence=block_confidence(support, share),
            )
        )
    return Score(
        anchors=len(placed),
        within_tolerance=within(list(range(len(placed)))),
        median_error_ms=statistics.median(errors) if errors else None,
        p90_error_ms=round(_percentile(errors, 0.9), 1) if errors else None,
        blocks=blocks,
        tolerance_ms=tolerance_ms,
    )
