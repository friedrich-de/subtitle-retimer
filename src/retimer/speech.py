"""Text-free alignment: where do line starts best coincide with speech starting?

This is what classic sync tools rely on, so it serves two purposes. It is the
fallback when too few lines can be matched by meaning, and it sharpens an
offset that the anchors only know to within a few hundred milliseconds.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

BIN_MS = 10
# How closely a line start and a speech onset have to agree to count as coinciding.
SMOOTH_MS = 60


class Correlation(BaseModel):
    """The offset at which most line starts meet a speech onset.

    `matches` is roughly how many lines coincide at that offset, `expected` how
    many would by chance. `strength` is their ratio.
    """

    offset_ms: int
    matches: float
    expected: float
    strength: float
    lines: int


def correlate_speech(
    line_starts: list[int] | np.ndarray,
    onsets: list[int] | np.ndarray,
    *,
    center_ms: int = 0,
    window_ms: int = 60_000,
    rate: float = 1.0,
) -> Correlation | None:
    """Find the offset near `center_ms` that puts the most line starts on speech onsets.

    Line starts are scaled by `rate` first. Returns None when there is nothing
    to compare.
    """
    starts = np.asarray(line_starts, dtype=np.float64) * rate
    marks = np.asarray(onsets, dtype=np.float64)
    if len(starts) == 0 or len(marks) == 0:
        return None
    differences = (marks[None, :] - starts[:, None]).ravel() - center_ms
    differences = differences[np.abs(differences) <= window_ms]
    bins = 2 * window_ms // BIN_MS + 1
    if len(differences) == 0:
        return Correlation(
            offset_ms=center_ms, matches=0, expected=0, strength=0, lines=len(starts)
        )
    index = np.round((differences + window_ms) / BIN_MS).astype(np.int64)
    counts = np.bincount(index, minlength=bins).astype(np.float64)

    reach = int(3 * SMOOTH_MS / BIN_MS)
    kernel = np.exp(-0.5 * (np.arange(-reach, reach + 1) * BIN_MS / SMOOTH_MS) ** 2)
    smooth = np.convolve(counts, kernel, mode="same")
    peak = int(np.argmax(smooth))
    expected = float(len(differences) / bins * kernel.sum())
    matches = float(smooth[peak])
    return Correlation(
        offset_ms=center_ms + peak * BIN_MS - window_ms,
        matches=round(matches, 2),
        expected=round(expected, 3),
        strength=round(matches / expected, 2) if expected else 0.0,
        lines=len(starts),
    )
