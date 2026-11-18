"""The sound check: do the lines start where the sound says somebody starts to speak?

A yardstick for files nobody has timed by hand. It asks neither a transcript
nor a reference subtitle nor the retimer itself, only the signal: where a
voice rises out of the sound around it. The lines of a subtitle that is in
sync begin at such rises far more often than chance allows, and the lines of
one that is not do so at some other offset, or at none.

The answer comes for the whole file and for every stretch of a few minutes,
because a file can be right up to a break and wrong after it. It is an offset
and a strength. The offset is how far the lines would have to move to sit on
the sound. The strength compares the best offset near zero with the best that
turns up where nothing can fit, a minute and more away: above 1 the lines beat
every chance offset, and from `CLEAR_STRENGTH` on the offset can be believed.

No speech model is used. A voice is told from music and noise by two classic
signs: it is loud in the band a telephone carries, and it has a pitch. That is
rough, and one line proves nothing. A few dozen lines together do.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from retimer.audio import INPUT_GUARD_ARGS, TOOL_TIMEOUT_S, AudioError, _require
from retimer.classify import classify_lines
from retimer.model import Subtitle

SAMPLE_RATE = 8_000
STEP_MS = 10
_HOP = SAMPLE_RATE * STEP_MS // 1000
# Loudness is read from 30 ms of sound, pitch from 64 ms: two periods of a low voice.
_LEVEL_FRAME = 240
_PITCH_FRAME = 512
_VOICE_BAND_HZ = (250.0, 3_600.0)
_PITCH_HZ = (80.0, 400.0)
# Loudness counts from the quietest moments of the file up to this far above them.
_LEVEL_RANGE_DB = 45.0
# A voice stands out against what the last and the next seconds sound like.
_BED_MS = 3_000
_FRAMES_AT_ONCE = 20_000

# A line begins where the sound after its start is more voice than the sound before.
HEAD_MS = 1_200
LEAD_MS = 800
# No single line weighs more than this many of its own usual swings, so that
# one shot or one scream does not speak for a whole stretch.
_LINE_CAP = 3.0
# Lines this close together are one start, as the layers of a styled line are.
_SAME_START_MS = 100

WINDOW_MS = 180_000
REACH_MS = 5_000
# Chance is measured this far from where the lines could fit, on either side,
# in stretches as wide as the search itself.
_CHANCE_GAP_MS = 2_000
_CHANCE_STRETCHES = 8
# Fewer lines than this say too little to tell an offset from chance.
MIN_LINES = 8
# From here on an offset is believed. On the two seed films every stretch that
# is in sync comes out at 1.45 or more, and of 48 stretches whose lines were
# moved elsewhere the highest is 1.18. On generated sound about one stretch of
# unrelated lines in 70 gets over the bar, so one clear stretch alone is a
# strong hint and no proof.
CLEAR_STRENGTH = 1.4


class SoundCheckError(RuntimeError):
    pass


class WindowCheck(BaseModel):
    """What the sound says about the lines that start in one stretch of the file.

    `offset_ms` is how far the lines would have to move to sit on the sound:
    positive when they come too early. It is None when the stretch has too
    few lines to tell. `clear` says whether the offset can be believed.
    """

    from_ms: int
    to_ms: int
    lines: int
    offset_ms: int | None = None
    strength: float | None = None
    clear: bool = False


class SoundCheck(BaseModel):
    """The check of one subtitle against one audio file, as a whole and stretch by stretch."""

    audio_ms: int
    lines: int
    offset_ms: int | None = None
    strength: float | None = None
    clear: bool = False
    window_ms: int = WINDOW_MS
    reach_ms: int = REACH_MS
    windows: list[WindowCheck]


def decode_audio(path: Path) -> np.ndarray:
    """The sound of a file as mono samples between -1 and 1, at `SAMPLE_RATE`."""
    try:
        done = subprocess.run(
            [
                _require("ffmpeg"),
                "-v",
                "error",
                *INPUT_GUARD_ARGS,
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=TOOL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise AudioError(f"ffmpeg did not finish in {TOOL_TIMEOUT_S:.0f} s") from error
    if done.returncode != 0:
        raise AudioError(f"ffmpeg failed: {done.stderr.decode(errors='replace').strip()[-400:]}")
    return np.frombuffer(done.stdout, dtype=np.int16).astype(np.float32) / 32_768.0


def _moving_mean(values: np.ndarray, width: int) -> np.ndarray:
    return np.convolve(values, np.ones(width) / width, mode="same")


def _frames(samples: np.ndarray, size: int, first: int, last: int) -> np.ndarray:
    """Frames `first` to `last`, each centred on its own step of the file."""
    padded_from = first * _HOP
    index = np.arange(size)[None, :] + (_HOP * np.arange(last - first))[:, None]
    return samples[padded_from + index]


def voice_presence(samples: np.ndarray) -> np.ndarray:
    """How much a voice stands out of the sound around it, for every step of the file.

    The value is high where the voice band is louder than in the seconds
    around and the sound has a pitch, and zero where it is not louder. It has
    no unit: only its rises and falls are used.
    """
    count = len(samples) // _HOP
    if count == 0:
        return np.zeros(0)
    level = np.empty(count)
    pitched = np.empty(count)
    frequencies = np.fft.rfftfreq(_LEVEL_FRAME, 1 / SAMPLE_RATE)
    in_band = (frequencies >= _VOICE_BAND_HZ[0]) & (frequencies < _VOICE_BAND_HZ[1])
    shortest = int(SAMPLE_RATE / _PITCH_HZ[1])
    longest = int(SAMPLE_RATE / _PITCH_HZ[0])
    level_window = np.hanning(_LEVEL_FRAME).astype(np.float32)
    pitch_window = np.hanning(_PITCH_FRAME).astype(np.float32)
    # Half a frame of silence at either end puts every frame around its own step.
    for_level = np.pad(samples, (_LEVEL_FRAME // 2, _LEVEL_FRAME))
    for_pitch = np.pad(samples, (_PITCH_FRAME // 2, _PITCH_FRAME))
    for first in range(0, count, _FRAMES_AT_ONCE):
        last = min(count, first + _FRAMES_AT_ONCE)
        spectrum = np.fft.rfft(_frames(for_level, _LEVEL_FRAME, first, last) * level_window)
        level[first:last] = (np.abs(spectrum[:, in_band]) ** 2).sum(axis=1)
        # A sound with a pitch repeats itself after one period: its
        # autocorrelation has a peak there that noise does not have.
        power = np.abs(
            np.fft.rfft(
                _frames(for_pitch, _PITCH_FRAME, first, last) * pitch_window,
                n=2 * _PITCH_FRAME,
            )
        )
        repeats = np.fft.irfft(power**2)[:, : longest + 1]
        pitched[first:last] = repeats[:, shortest:].max(axis=1) / (repeats[:, 0] + 1e-9)

    decibels = 10 * np.log10(level + 1e-10)
    floor = np.percentile(decibels, 5)
    loudness = np.clip(decibels, floor, floor + _LEVEL_RANGE_DB) - floor
    stands_out = np.maximum(loudness - _moving_mean(loudness, _BED_MS // STEP_MS), 0)
    return stands_out * _moving_mean(pitched, 5)


def line_spans(subtitle: Subtitle) -> list[tuple[int, int]]:
    """The dialogue lines of a file as (start, end), with lines that start together as one."""
    labels = classify_lines(subtitle)
    spans = sorted(
        (line.start_ms, line.end_ms)
        for line, label in zip(subtitle.lines, labels, strict=True)
        if label.label == "dialogue" and line.end_ms > line.start_ms
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start - merged[-1][0] <= _SAME_START_MS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _span_steps(reach_ms: int) -> int:
    """How far the lines are moved either way, in steps: the search and the room for chance."""
    return (reach_ms + _CHANCE_GAP_MS + _CHANCE_STRETCHES * (2 * reach_ms + STEP_MS)) // STEP_MS


def line_responses(
    spans: list[tuple[int, int]], presence: np.ndarray, reach_ms: int = REACH_MS
) -> np.ndarray:
    """For every line, how much more voice follows its start than precedes it, at every offset.

    One row per line, one column per offset from far before to far after, in
    steps of `STEP_MS`. Every row is in units of its own usual swing and
    capped, so that the rows can be added up. An offset that would put the
    line outside the audio counts as nothing.
    """
    steps = _span_steps(reach_ms)
    offsets = np.arange(-steps, steps + 1)
    total = np.concatenate([[0.0], np.cumsum(presence, dtype=np.float64)])
    lead = LEAD_MS // STEP_MS
    rows = np.zeros((len(spans), len(offsets)))
    for row, (start_ms, end_ms) in enumerate(spans):
        head = max(200, min(HEAD_MS, end_ms - start_ms)) // STEP_MS
        at = round(start_ms / STEP_MS) + offsets
        inside = (at - lead >= 0) & (at + head <= len(presence))
        if inside.sum() < len(offsets) // 4:
            continue
        at = np.clip(at, lead, max(lead, len(presence) - head))
        rise = (total[at + head] - total[at]) / head - (total[at] - total[at - lead]) / lead
        usual = np.median(rise[inside])
        swing = 1.4826 * np.median(np.abs(rise[inside] - usual)) + 1e-9
        rows[row, inside] = np.clip((rise[inside] - usual) / swing, -_LINE_CAP, _LINE_CAP)
    return rows


def read_offset(curve: np.ndarray, reach_ms: int = REACH_MS) -> tuple[int, float]:
    """The best offset within reach of zero, and how far it stands above chance.

    `curve` is the sum of the lines' responses over all offsets. Chance is
    the best that turns up in stretches of the curve far from zero, where the
    lines lie on other people's sentences.
    """
    middle = len(curve) // 2
    smooth = _moving_mean(curve, 5)
    reach = reach_ms // STEP_MS
    peak = middle - reach + int(np.argmax(smooth[middle - reach : middle + reach + 1]))
    gap = (reach_ms + _CHANCE_GAP_MS) // STEP_MS
    far = np.concatenate([smooth[: middle - gap], smooth[middle + gap + 1 :]])
    usual = float(far.mean())
    width = 2 * reach + 1
    edges = [middle + gap + number * width for number in range(_CHANCE_STRETCHES)]
    edges += [middle - gap - (number + 1) * width + 1 for number in range(_CHANCE_STRETCHES)]
    by_chance = max(float(smooth[edge : edge + width].max()) for edge in edges)
    if by_chance <= usual:
        return (peak - middle) * STEP_MS, 0.0
    strength = (float(smooth[peak]) - usual) / (by_chance - usual)
    return (peak - middle) * STEP_MS, round(max(strength, 0.0), 2)


def check_sound(
    samples: np.ndarray,
    subtitle: Subtitle,
    window_ms: int = WINDOW_MS,
    reach_ms: int = REACH_MS,
) -> SoundCheck:
    """Check the dialogue lines of `subtitle` against the sound in `samples`."""
    presence = voice_presence(samples)
    audio_ms = len(presence) * STEP_MS
    spans = [span for span in line_spans(subtitle) if 0 <= span[0] < audio_ms]
    whole = np.zeros(2 * _span_steps(reach_ms) + 1)
    windows = []
    for from_ms in range(0, max(audio_ms, 1), window_ms):
        to_ms = min(from_ms + window_ms, audio_ms)
        inside = [span for span in spans if from_ms <= span[0] < to_ms]
        found = WindowCheck(from_ms=from_ms, to_ms=to_ms, lines=len(inside))
        if inside:
            curve = line_responses(inside, presence, reach_ms).sum(axis=0)
            whole += curve
            if len(inside) >= MIN_LINES:
                found.offset_ms, found.strength = read_offset(curve, reach_ms)
                found.clear = found.strength >= CLEAR_STRENGTH
        windows.append(found)
    check = SoundCheck(
        audio_ms=audio_ms,
        lines=len(spans),
        window_ms=window_ms,
        reach_ms=reach_ms,
        windows=windows,
    )
    if len(spans) >= MIN_LINES:
        check.offset_ms, check.strength = read_offset(whole, reach_ms)
        check.clear = check.strength >= CLEAR_STRENGTH
    return check


def check_files(
    audio: Path, subtitle: Subtitle, window_ms: int = WINDOW_MS, reach_ms: int = REACH_MS
) -> SoundCheck:
    """Check a subtitle against an audio or video file."""
    samples = decode_audio(audio)
    if len(samples) < SAMPLE_RATE:
        raise SoundCheckError("the file has less than a second of sound")
    return check_sound(samples, subtitle, window_ms, reach_ms)


def _clock(ms: int) -> str:
    return f"{ms // 60_000}:{ms // 1000 % 60:02}"


def render_text(check: SoundCheck) -> str:
    """The check as a few lines for a person."""

    def row(name: str, found: SoundCheck | WindowCheck) -> str:
        if found.offset_ms is None:
            return f"{name:<13}{found.lines:>5} lines   too few to tell"
        told = "clear" if found.clear else "not clear"
        return (
            f"{name:<13}{found.lines:>5} lines   offset {found.offset_ms:>+6} ms"
            f"   strength {found.strength:>5.2f}   {told}"
        )

    rows = [row("whole file", check)]
    rows += [
        row(f"{_clock(window.from_ms)}-{_clock(window.to_ms)}", window) for window in check.windows
    ]
    return "\n".join(rows)
