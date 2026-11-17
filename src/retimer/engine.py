"""Validates a plan and applies it to the original file."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from retimer import clean
from retimer.model import Subtitle
from retimer.plan import Plan, RemoveLines, Scale, Shift, StripText
from retimer.text import to_plain

# Real frame rate conversions lie between 25/29.97 and 29.97/25.
MIN_SCALE = 0.8
MAX_SCALE = 1.25
_MAX_IDS_IN_ISSUE = 20

_STRIPPERS: dict[str, Callable[[str], str]] = {
    "hearing_impaired": clean.remove_hearing_impaired,
    "symbols": clean.remove_symbols,
    "formatting": clean.strip_formatting,
}


class PlanIssue(BaseModel):
    code: str
    message: str
    ids: list[int] = []


class PlanError(ValueError):
    """The plan cannot be applied. `issues` says why, in a form the agent can act on."""

    def __init__(self, issues: list[PlanIssue]) -> None:
        super().__init__("; ".join(issue.message for issue in issues))
        self.issues = issues


@dataclass
class ApplySummary:
    removed: dict[str, int] = field(default_factory=dict)
    stripped: dict[str, int] = field(default_factory=dict)
    emptied_by_strip: int = 0
    moved: int = 0
    lines_in: int = 0
    lines_out: int = 0


@dataclass
class ApplyResult:
    subtitle: Subtitle
    summary: ApplySummary


def _removed_ids(plan: Plan) -> set[int]:
    return {line_id for op in plan.content_ops if isinstance(op, RemoveLines) for line_id in op.ids}


def _new_times(subtitle: Subtitle, plan: Plan) -> dict[int, tuple[int, int]]:
    """New start and end for every line, from its original times."""
    scales = [op for op in plan.timing_ops if isinstance(op, Scale)]
    shifts = [op for op in plan.timing_ops if isinstance(op, Shift)]
    factor = scales[0].factor if scales else 1.0
    times = {}
    for line in subtitle.lines:
        start = round(line.start_ms * factor)
        end = round(line.end_ms * factor)
        delta = next((op.ms for op in shifts if op.contains(line.start_ms)), 0)
        times[line.id] = (start + delta, end + delta)
    return times


def _ranges_overlap(a: Shift, b: Shift) -> bool:
    a_from = a.from_ms if a.from_ms is not None else float("-inf")
    a_to = a.to_ms if a.to_ms is not None else float("inf")
    b_from = b.from_ms if b.from_ms is not None else float("-inf")
    b_to = b.to_ms if b.to_ms is not None else float("inf")
    return a_from < b_to and b_from < a_to


def validate_plan(subtitle: Subtitle, plan: Plan) -> list[PlanIssue]:
    """Return every reason the plan cannot run on this file. An empty list means it can."""
    issues: list[PlanIssue] = []
    known = set(subtitle.ids)

    unknown = sorted(
        {line_id for op in plan.content_ops for line_id in (op.ids or []) if line_id not in known}
    )
    if unknown:
        issues.append(
            PlanIssue(
                code="unknown_ids",
                message="content operations name lines that are not in the file",
                ids=unknown[:_MAX_IDS_IN_ISSUE],
            )
        )

    scales = [op for op in plan.timing_ops if isinstance(op, Scale)]
    shifts = [op for op in plan.timing_ops if isinstance(op, Shift)]
    if len(scales) > 1:
        issues.append(
            PlanIssue(code="multiple_scales", message="a plan may hold one scale operation")
        )
    for op in scales:
        if not MIN_SCALE <= op.factor <= MAX_SCALE:
            issues.append(
                PlanIssue(
                    code="scale_out_of_range",
                    message=f"scale factor {op.factor} is outside {MIN_SCALE} to {MAX_SCALE}",
                )
            )
    for op in shifts:
        if op.from_ms is not None and op.to_ms is not None and op.from_ms >= op.to_ms:
            issues.append(
                PlanIssue(
                    code="empty_range",
                    message=f"shift range {op.from_ms} to {op.to_ms} is empty",
                )
            )
    for index, first in enumerate(shifts):
        for second in shifts[index + 1 :]:
            if _ranges_overlap(first, second):
                issues.append(
                    PlanIssue(
                        code="overlapping_ranges",
                        message=(
                            f"shift ranges {first.from_ms} to {first.to_ms} and "
                            f"{second.from_ms} to {second.to_ms} overlap"
                        ),
                    )
                )
    if issues:
        return issues

    removed = _removed_ids(plan)
    times = _new_times(subtitle, plan)
    kept = [line for line in subtitle.lines if line.id not in removed]

    # A comment is a note for editors and is shown to nobody. One at the very
    # start of a script, where editors keep them, must not stand in the way of
    # moving the file earlier. It stops at zero instead.
    #
    # A line that would begin before zero and still be on screen after it is
    # shown from zero on. Only a line that would be over before the film
    # starts has no place.
    # The same goes for a line without a duration, which nobody sees either.
    negative = [
        line.id
        for line in kept
        if not line.is_comment
        and line.duration_ms > 0
        and times[line.id][0] < 0
        and times[line.id][1] <= 0
    ]
    if negative:
        issues.append(
            PlanIssue(
                code="negative_time",
                message="lines would end before zero; remove them or change the shift",
                ids=negative[:_MAX_IDS_IN_ISSUE],
            )
        )

    reordered = []
    latest_start = None
    events = sorted(
        (line for line in kept if not line.is_comment),
        key=lambda line: (line.start_ms, line.id),
    )
    for line in events:
        start = times[line.id][0]
        if latest_start is not None and start < latest_start:
            reordered.append(line.id)
        else:
            latest_start = start
    if reordered:
        issues.append(
            PlanIssue(
                code="reordered",
                message=(
                    "lines would move in front of lines that precede them; "
                    "the blocks collide, so remove the extra lines or adjust the ranges"
                ),
                ids=reordered[:_MAX_IDS_IN_ISSUE],
            )
        )
    return issues


def apply_plan(subtitle: Subtitle, plan: Plan) -> ApplyResult:
    """Run the plan on the original file and return a new subtitle. The input is untouched."""
    issues = validate_plan(subtitle, plan)
    if issues:
        raise PlanError(issues)

    summary = ApplySummary(lines_in=len(subtitle))
    result = subtitle.copy()
    events = dict(zip(result.ids, result.ssa.events, strict=True))
    times = _new_times(subtitle, plan)

    removed: set[int] = set()
    for op in plan.content_ops:
        if isinstance(op, RemoveLines):
            fresh = set(op.ids) - removed
            removed |= fresh
            if fresh:
                summary.removed[op.reason] = summary.removed.get(op.reason, 0) + len(fresh)

    stripped: Counter[str] = Counter()
    for op in plan.content_ops:
        if not isinstance(op, StripText):
            continue
        strip = _STRIPPERS[op.what]
        targets = op.ids if op.ids is not None else result.ids
        for line_id in targets:
            if line_id in removed:
                continue
            event = events[line_id]
            if event.is_comment:
                # Comment events are notes for editors and never reach a viewer.
                continue
            new_text = strip(event.text)
            if new_text == event.text:
                continue
            if not to_plain(new_text):
                # Stripping left nothing readable, so the line is dropped.
                removed.add(line_id)
                summary.emptied_by_strip += 1
                continue
            event.text = new_text
            stripped[op.what] += 1
    summary.stripped = dict(stripped)

    kept_ids = [line_id for line_id in result.ids if line_id not in removed]
    for line_id in kept_ids:
        event = events[line_id]
        start, end = times[line_id]
        if start < 0:
            start, end = 0, max(0, end)
        if (start, end) != (event.start, event.end):
            summary.moved += 1
        event.start, event.end = start, end

    result.ssa.events = [events[line_id] for line_id in kept_ids]
    result.ids = kept_ids
    summary.lines_out = len(kept_ids)
    return ApplyResult(subtitle=result, summary=summary)
