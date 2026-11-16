"""Scores one synced file against its ground truth, and summarizes many results."""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence

from pydantic import BaseModel

from retimer.bench.distort import TruthLine
from retimer.model import Subtitle
from retimer.text import to_plain

TOLERANCES_MS = (100, 250, 500)
# A file counts as synced when this share of its dialogue lines is within the tolerance.
SUCCESS_TOLERANCE_MS = 250
SUCCESS_SHARE = 0.95
# The same share within this wider tolerance is reported next to it. Hand-timed
# subtitles lead or trail the voice by a quarter of a second as a matter of
# style, so against audio the strict figure also measures that style.
LOOSE_TOLERANCE_MS = 500
# A system that reports no outcome is taken to have left the file alone when no
# line moved further than the tightest tolerance.
UNMOVED_MS = TOLERANCES_MS[0]

_SPACE = re.compile(r"\s+")


class FileMetrics(BaseModel):
    """How one output file compares to the truth. Errors are on line start times."""

    outcome: str
    expected_outcome: str
    lines_scored: int = 0
    lines_missing: int = 0
    median_error_ms: float | None = None
    p90_error_ms: float | None = None
    max_error_ms: float | None = None
    median_signed_error_ms: float | None = None
    within: dict[str, float] = {}
    within_all_lines: dict[str, float] = {}
    success: bool = False
    absent_lines: int = 0
    absent_removed: int = 0
    content_ok: bool | None = None
    decision_correct: bool | None = None


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between the two nearest ranks. `q` is between 0 and 1."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _key(text: str) -> str:
    return _SPACE.sub(" ", to_plain(text)).strip()


def match_output_ids(target: Subtitle, output: Subtitle) -> list[int | None]:
    """For every output line, the id of the target line it came from.

    Tools that only retime keep every line, but they may sort the file or drop
    lines. Lines are therefore paired by their text, and lines with the same
    text are paired in time order.
    """
    by_text: dict[str, list[int]] = defaultdict(list)
    for line in sorted(target.lines, key=lambda line: (line.start_ms, line.id)):
        by_text[_key(line.text)].append(line.id)
    taken: dict[str, int] = defaultdict(int)
    ids: list[int | None] = [None] * len(output)
    order = sorted(range(len(output)), key=lambda i: (output.lines[i].start_ms, i))
    lines = output.lines
    for index in order:
        key = _key(lines[index].text)
        position = taken[key]
        if position < len(by_text.get(key, ())):
            ids[index] = by_text[key][position]
            taken[key] = position + 1
    return ids


def infer_outcome(target: Subtitle, output: Subtitle, output_ids: Sequence[int | None]) -> str:
    """The outcome of a tool that does not report one: did it move anything?"""
    original = {line.id: line.start_ms for line in target.lines}
    moved = [
        abs(line.start_ms - original[line_id])
        for line, line_id in zip(output.lines, output_ids, strict=True)
        if line_id is not None
    ]
    if moved and max(moved) <= UNMOVED_MS:
        return "already_in_sync"
    return "synced"


def _shares(errors: Sequence[float], total: int) -> dict[str, float]:
    if not total:
        return {}
    return {
        str(tolerance): sum(1 for error in errors if error <= tolerance) / total
        for tolerance in TOLERANCES_MS
    }


def evaluate(
    truth: Sequence[TruthLine],
    target: Subtitle,
    output: Subtitle | None,
    *,
    outcome: str | None,
    expected_outcome: str,
    output_ids: Sequence[int | None] | None = None,
    remove_kinds: Iterable[str] = (),
    drop_unmatched: bool = True,
) -> FileMetrics:
    """Score an output file.

    `output_ids` names the target line behind every output line. Systems that
    know it pass it; for the others it is recovered from the text. `remove_kinds`
    are the noise kinds the request options ask to remove.
    """
    if output is not None and output_ids is None:
        output_ids = match_output_ids(target, output)
    if outcome is None:
        outcome = "failed" if output is None else infer_outcome(target, output, output_ids or [])

    if expected_outcome == "rejected_no_match":
        correct = outcome == "rejected_no_match"
        return FileMetrics(
            outcome=outcome,
            expected_outcome=expected_outcome,
            success=correct,
            decision_correct=correct,
        )

    starts: dict[int, int] = {}
    if output is not None and outcome not in ("rejected_no_match", "failed"):
        for line, line_id in zip(output.lines, output_ids or [], strict=True):
            if line_id is not None:
                starts.setdefault(line_id, line.start_ms)

    timed = [line for line in truth if line.true_start_ms is not None]
    dialogue = [line for line in timed if line.kind == "base" and line.label == "dialogue"]
    absent = [line for line in truth if line.true_start_ms is None]
    remove_kinds = set(remove_kinds)

    signed = [starts[line.id] - line.true_start_ms for line in dialogue if line.id in starts]
    errors = [abs(value) for value in signed]
    missing = len(dialogue) - len(errors)
    within = _shares(errors, len(dialogue))

    # Lines the options ask to remove are not expected in the output, so they are
    # left out of the all-lines figure.
    expected_timed = [line for line in timed if line.kind not in remove_kinds]
    all_errors = [
        abs(starts[line.id] - line.true_start_ms) for line in expected_timed if line.id in starts
    ]

    absent_removed = sum(1 for line in absent if line.id not in starts)
    unwanted = [line for line in truth if line.kind in remove_kinds]
    content_ok = None
    if output is not None and (unwanted or absent):
        unwanted_gone = all(line.id not in starts for line in unwanted)
        absent_gone = absent_removed == len(absent) or not drop_unmatched
        content_ok = unwanted_gone and absent_gone and missing == 0

    decision: bool | None = None
    if expected_outcome == "already_in_sync":
        decision = outcome == "already_in_sync"
    elif absent and drop_unmatched:
        decision = absent_removed == len(absent) and missing == 0

    success = bool(dialogue) and within.get(str(SUCCESS_TOLERANCE_MS), 0.0) >= SUCCESS_SHARE
    return FileMetrics(
        outcome=outcome,
        expected_outcome=expected_outcome,
        lines_scored=len(dialogue),
        lines_missing=missing,
        median_error_ms=statistics.median(errors) if errors else None,
        p90_error_ms=percentile(errors, 0.9) if errors else None,
        max_error_ms=max(errors) if errors else None,
        median_signed_error_ms=statistics.median(signed) if signed else None,
        within=within,
        within_all_lines=_shares(all_errors, len(expected_timed)),
        success=success,
        absent_lines=len(absent),
        absent_removed=absent_removed,
        content_ok=content_ok,
        decision_correct=decision,
    )


class Summary(BaseModel):
    """Results of one system over a group of cases."""

    system: str
    group: str
    runs: int
    failed: int
    file_success: float | None
    file_success_loose: float | None = None
    median_error_ms: float | None
    p90_error_ms: float | None
    within: dict[str, float]
    decision_cases: int
    decision_accuracy: float | None
    mean_seconds: float | None
    total_cost_usd: float
    mean_cost_usd: float | None


def summarize(results: Sequence[dict], system: str, group: str) -> Summary:
    """Aggregate result records, as written by the harness, into one row.

    The error figures are medians over files, so one long file does not outweigh
    the rest. The tolerance shares are pooled over all dialogue lines.
    """
    metrics = [FileMetrics.model_validate(result["metrics"]) for result in results]
    timing = [m for m in metrics if m.expected_outcome != "rejected_no_match"]
    medians = [m.median_error_ms for m in timing if m.median_error_ms is not None]
    p90s = [m.p90_error_ms for m in timing if m.p90_error_ms is not None]
    lines = sum(m.lines_scored for m in timing)
    within = {
        str(tolerance): sum(m.within.get(str(tolerance), 0.0) * m.lines_scored for m in timing)
        / lines
        for tolerance in TOLERANCES_MS
        if lines
    }
    decisions = [m.decision_correct for m in metrics if m.decision_correct is not None]
    seconds = [result["seconds"] for result in results if result.get("seconds") is not None]
    costs = [result.get("cost_usd") or 0.0 for result in results]
    return Summary(
        system=system,
        group=group,
        runs=len(results),
        failed=sum(1 for m in metrics if m.outcome == "failed"),
        file_success=sum(1 for m in timing if m.success) / len(timing) if timing else None,
        file_success_loose=(
            sum(1 for m in timing if m.within.get(str(LOOSE_TOLERANCE_MS), 0.0) >= SUCCESS_SHARE)
            / len(timing)
            if timing
            else None
        ),
        median_error_ms=statistics.median(medians) if medians else None,
        p90_error_ms=statistics.median(p90s) if p90s else None,
        within=within,
        decision_cases=len(decisions),
        decision_accuracy=sum(1 for d in decisions if d) / len(decisions) if decisions else None,
        mean_seconds=statistics.fmean(seconds) if seconds else None,
        total_cost_usd=sum(costs),
        mean_cost_usd=statistics.fmean(costs) if costs else None,
    )


class Stability(BaseModel):
    """How much repeated runs of the same case disagree, for one system."""

    system: str
    cases_repeated: int
    outcome_agreement: float | None
    success_agreement: float | None
    mean_spread_ms: float | None
    max_spread_ms: float | None


def stability(results: Sequence[dict], system: str) -> Stability:
    by_case: dict[str, list[FileMetrics]] = defaultdict(list)
    for result in results:
        by_case[result["case"]].append(FileMetrics.model_validate(result["metrics"]))
    repeated = [runs for runs in by_case.values() if len(runs) > 1]
    spreads = []
    for runs in repeated:
        medians = [run.median_error_ms for run in runs if run.median_error_ms is not None]
        if len(medians) > 1:
            spreads.append(max(medians) - min(medians))
    return Stability(
        system=system,
        cases_repeated=len(repeated),
        outcome_agreement=(
            sum(1 for runs in repeated if len({run.outcome for run in runs}) == 1) / len(repeated)
            if repeated
            else None
        ),
        success_agreement=(
            sum(1 for runs in repeated if len({run.success for run in runs}) == 1) / len(repeated)
            if repeated
            else None
        ),
        mean_spread_ms=statistics.fmean(spreads) if spreads else None,
        max_spread_ms=max(spreads) if spreads else None,
    )
