"""The arithmetic of judging: errors and shifts per window of a few minutes.

A real pair has no known truth for its lines. What can be said is how far the
lines of an output sit from reference lines that mean the same, how far a
system moved the lines, and where two systems moved them differently. All
three are taken per window, because a file can be right in one part and wrong
in another, and a median over a window is not thrown by a single odd pair.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Literal

# A window of the timeline. Errors and shifts are taken per window.
WINDOW_MS = 180_000
# A window whose median error is larger than this is off.
OFF_MS = 350
# A window needs this many pairs to be judged at all.
MIN_PAIRS = 3
# Two systems disagree in a window when their shifts differ by more than this.
DISAGREE_MS = 300
# A file whose lines all moved less than this was left alone.
UNMOVED_MS = 100
# The sound check tells whether lines are within about this much of the voices.
SOUND_OFF_MS = 500

Verdict = Literal["verified", "off", "unjudged"]
SoundVerdict = Literal["confirmed", "off", "unclear"]


def window_errors(
    evidence: Iterable[tuple[int, int]], starts: Mapping[int, int]
) -> list[tuple[int, int, int]]:
    """How far the lines of an output start from where the reference says, per window.

    `evidence` holds pairs of a line id and the start of the reference line
    that means the same. `starts` is where each line starts in the output.
    A line that is not in the output gives no error. Returns, per window of
    the reference's timeline: its index, the number of pairs in it and their
    median error in milliseconds, positive when the output is late.
    """
    errors: dict[int, list[int]] = defaultdict(list)
    for line_id, reference_ms in evidence:
        if line_id in starts:
            errors[reference_ms // WINDOW_MS].append(starts[line_id] - reference_ms)
    return [
        (index, len(found), round(statistics.median(found)))
        for index, found in sorted(errors.items())
    ]


def judge_windows(windows: Iterable[tuple[int, int, int]]) -> tuple[Verdict, int, int]:
    """The verdict on a file, with how many windows could be judged and how many are off.

    A file is verified when every window that has enough pairs is within the
    tolerance. One window that is off makes the file off. A file without a
    single window to judge is neither.
    """
    judged = [median for _, pairs, median in windows if pairs >= MIN_PAIRS]
    off = sum(1 for median in judged if abs(median) > OFF_MS)
    if not judged:
        return "unjudged", 0, 0
    return ("off" if off else "verified"), len(judged), off


def line_shifts(original: Mapping[int, int], moved: Mapping[int, int]) -> dict[int, int]:
    """How far each line was moved, for the lines that are in both."""
    return {
        line_id: moved[line_id] - start for line_id, start in original.items() if line_id in moved
    }


def window_shifts(original: Mapping[int, int], moved: Mapping[int, int]) -> dict[int, int]:
    """The median shift of the lines of each window of the original file's timeline."""
    found: dict[int, list[int]] = defaultdict(list)
    for line_id, shift in line_shifts(original, moved).items():
        found[original[line_id] // WINDOW_MS].append(shift)
    return {index: round(statistics.median(shifts)) for index, shifts in sorted(found.items())}


def segments(
    original: Mapping[int, int], moved: Mapping[int, int], tolerance_ms: int = 30
) -> list[tuple[int, int, int, int]]:
    """The stretches of a file that were moved by the same amount.

    Each as the original start of its first and last line, the shift, and
    the number of lines. A file that was stretched has no such stretches and
    comes out as many short ones.
    """
    shifts = line_shifts(original, moved)
    ordered = sorted(shifts, key=lambda line_id: (original[line_id], line_id))
    found: list[list[int]] = []
    for line_id in ordered:
        start, shift = original[line_id], shifts[line_id]
        if found and abs(found[-1][2] - shift) <= tolerance_ms:
            found[-1][1] = start
            found[-1][3] += 1
        else:
            found.append([start, start, shift, 1])
    return [tuple(segment) for segment in found]


def disagreements(
    one: Mapping[int, int], other: Mapping[int, int], limit_ms: int = DISAGREE_MS
) -> list[tuple[int, int, int]]:
    """The windows in which two systems moved the lines differently.

    Each as the window's index and the two shifts. Windows only one of them
    has lines in are left out.
    """
    return [
        (index, one[index], other[index])
        for index in sorted(set(one) & set(other))
        if abs(one[index] - other[index]) > limit_ms
    ]


def judge_sound(reading: Mapping | None, off_ms: int = SOUND_OFF_MS) -> SoundVerdict | None:
    """What the sound check's reading of a file comes to. None when there was no audio.

    `confirmed`: the reading over the whole file is clear and within the
    tolerance, and so is every window that reads clear. `off`: the whole file
    or one of its windows reads clear and lies outside. `unclear`: the sound
    does not say, which is also what lines far from their place look like,
    because the check only searches a few seconds around them.
    """
    if reading is None:
        return None
    clear = [offset for _, _, offset, _, is_clear in reading["windows"] if is_clear]
    whole = reading["offset_ms"] if reading["clear"] else None
    if (whole is not None and abs(whole) > off_ms) or any(abs(o) > off_ms for o in clear):
        return "off"
    return "confirmed" if whole is not None else "unclear"
