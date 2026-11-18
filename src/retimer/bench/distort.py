"""Turns an in-sync subtitle into a mistimed one with exact ground truth.

A distortion describes how the target's timeline differs from the reference:

- `breaks` model two cuts of the same film. A positive `delta_ms` means the
  target was timed for a version with an extra segment at that point, so every
  later line sits later. A negative one means the target's version lacks a
  segment the reference has: the lines inside it do not exist in the target, and
  every later line sits earlier.
- `offset_ms` moves the whole file.
- `rate` stretches it, as a frame rate conversion does.
- `noise` adds lines that are not dialogue: sound descriptions, credits, a song
  block, and trailing lines the reference has no counterpart for.

A caller can also hand in lines of its own, `extras`: the effect lines of a
karaoke, signs redrawn for every frame, the other episodes of a pack. They are
placed in the in-sync file first and then distorted with everything else.

They compose in that order: `target = (true + breaks_before(true) + offset) * rate`.
The inverse is expressible as a plan (one scale, one shift per block), and the
generator returns that ideal plan alongside the truth.
"""

from __future__ import annotations

import copy
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import pysubs2
from pydantic import BaseModel, Field

from retimer.classify import classify_lines
from retimer.model import Subtitle
from retimer.plan import Plan, RemoveLines, Scale, Shift

LineKind = Literal[
    "base", "sound_description", "credit", "karaoke", "trailing", "sign", "other_content"
]

_SONG_STYLE = "OP-Romaji"
_MIN_GAP_FOR_SOUND_MS = 1_500
_SOUND_MS = 1_200
_CREDIT_MS = 3_000
_SONG_LINE_MS = 3_000
_TRAILING_LINE_MS = 3_000
_TRAILING_GAP_MS = 1_000
_TRAILING_LEAD_MS = 5_000

_SOUNDS = {
    "ja": ["（ため息）", "［ドアの音］", "（笑い声）", "［音楽］", "（足音）", "［電話の音］"],
    "": ["[door slams]", "(sighs)", "[phone rings]", "(LAUGHS)", "[footsteps]", "[music playing]"],
}
_CREDITS = [
    "Subtitles by the Example Group",
    "Synced and corrected by example_user",
    "Downloaded from www.example-subs.org",
    "Translation by the Example Team",
]
_SONG_SYLLABLES = ["ha", "shi", "re", "to", "o", "ku", "ma", "de", "yu", "me", "no", "sa", "ki"]
_TRAILING = [
    "Next time on the Example Show...",
    "Support us and become a VIP member",
    "Advertise your product or brand here",
    "Subtitles by the Example Group",
    "Watch more episodes at www.example-subs.org",
    "Thanks for watching!",
]


class Break(BaseModel):
    """A point where the two versions of the film differ. Times are in the true timeline."""

    at_ms: int = Field(ge=0)
    delta_ms: int


class Noise(BaseModel):
    sound_descriptions: int = Field(default=0, ge=0)
    credits: int = Field(default=0, ge=0)
    karaoke_lines: int = Field(default=0, ge=0)
    trailing_lines: int = Field(default=0, ge=0)

    def any(self) -> bool:
        return bool(
            self.sound_descriptions or self.credits or self.karaoke_lines or self.trailing_lines
        )


class Distortion(BaseModel):
    offset_ms: int = 0
    rate: float = Field(default=1.0, gt=0)
    breaks: list[Break] = Field(default_factory=list)
    noise: Noise = Field(default_factory=Noise)

    def is_identity(self) -> bool:
        return self.offset_ms == 0 and self.rate == 1.0 and not self.breaks and not self.noise.any()


class TruthLine(BaseModel):
    """What is known about one line of the distorted target.

    `id` is the line's position in the target file. `true_start_ms` is where the
    line belongs in the reference timeline, or None when the reference has no
    place for it (trailing lines). `label` is the rule-based label of a base line,
    and the noise kind for an added one.
    """

    id: int
    kind: LineKind
    label: str
    base_id: int | None = None
    true_start_ms: int | None = None
    true_end_ms: int | None = None


@dataclass(frozen=True)
class Extra:
    """A line a caller adds to the in-sync file before it is distorted.

    Times are in the true timeline, like those of the base lines. `timed` is
    False for a line the reference has no place for, such as a line of another
    episode: it moves with the rest and is expected to be removed.
    """

    start_ms: int
    end_ms: int
    text: str
    kind: LineKind
    style: str = "Default"
    effect: str = ""
    layer: int = 0
    comment: bool = False
    timed: bool = True


@dataclass
class Distorted:
    subtitle: Subtitle
    truth: list[TruthLine]
    ideal_plan: Plan
    dropped_base_ids: list[int]


@dataclass
class _Record:
    start: int
    end: int
    text: str
    style: str
    kind: LineKind
    label: str
    base_id: int | None = None
    event: pysubs2.SSAEvent | None = None
    timed: bool = True


class DistortionError(ValueError):
    """The distortion cannot be applied to this subtitle."""


def _gaps(records: list[_Record], minimum_ms: int, floor_ms: int) -> list[tuple[int, int]]:
    """Stretches with no line on screen, from `floor_ms` on.

    The lead-in before the first line counts as a gap. The floor keeps added
    lines late enough that an early shift cannot push them before zero.
    """
    spans = sorted((r.start, r.end) for r in records)
    gaps = []
    cursor = floor_ms
    for start, end in spans:
        if start - cursor >= minimum_ms:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    return gaps


def _add_sounds(
    records: list[_Record], count: int, language: str, rng: random.Random, floor_ms: int
) -> None:
    pool = _SOUNDS.get(language, _SOUNDS[""])
    gaps = _gaps(records, _MIN_GAP_FOR_SOUND_MS, floor_ms)
    rng.shuffle(gaps)
    for index, (gap_start, gap_end) in enumerate(gaps[:count]):
        duration = min(_SOUND_MS, gap_end - gap_start - 300)
        start = gap_start + (gap_end - gap_start - duration) // 2
        records.append(
            _Record(
                start, start + duration, pool[index % len(pool)], "Default", "sound_description", ""
            )
        )


def _add_credits(records: list[_Record], count: int, floor_ms: int) -> None:
    gaps = sorted(_gaps(records, _CREDIT_MS + 1_000, floor_ms), key=lambda gap: gap[0])
    for index, (gap_start, _) in enumerate(gaps[:count]):
        start = gap_start + 500
        records.append(
            _Record(
                start, start + _CREDIT_MS, _CREDITS[index % len(_CREDITS)], "Default", "credit", ""
            )
        )


def _song_text(index: int, fmt: str, rng: random.Random) -> str:
    syllables = [rng.choice(_SONG_SYLLABLES) for _ in range(6)]
    if fmt == "ass":
        return "".join(f"{{\\k{rng.randint(20, 60)}}}{syllable}" for syllable in syllables)
    return "♪ " + " ".join(syllables) + " ♪"


def _add_song(
    records: list[_Record], count: int, fmt: str, rng: random.Random, floor_ms: int
) -> None:
    gaps = _gaps(records, _SONG_LINE_MS + 1_000, floor_ms)
    if not gaps:
        return
    gap_start, gap_end = max(gaps, key=lambda gap: gap[1] - gap[0])
    room = (gap_end - gap_start - 1_000) // _SONG_LINE_MS
    style = _SONG_STYLE if fmt == "ass" else "Default"
    for index in range(min(count, room)):
        start = gap_start + 500 + index * _SONG_LINE_MS
        records.append(
            _Record(
                start,
                start + _SONG_LINE_MS - 100,
                _song_text(index, fmt, rng),
                style,
                "karaoke",
                "",
            )
        )


def _add_trailing(records: list[_Record], count: int) -> None:
    cursor = max(r.end for r in records) + _TRAILING_LEAD_MS
    for index in range(count):
        records.append(
            _Record(
                cursor,
                cursor + _TRAILING_LINE_MS,
                _TRAILING[index % len(_TRAILING)],
                "Default",
                "trailing",
                "",
                timed=False,
            )
        )
        cursor += _TRAILING_LINE_MS + _TRAILING_GAP_MS


def _check_breaks(breaks: list[Break]) -> list[Break]:
    ordered = sorted(breaks, key=lambda b: b.at_ms)
    cursor = 0
    for item in ordered:
        if item.delta_ms == 0:
            raise DistortionError("a break needs a delta other than zero")
        if item.at_ms < cursor:
            raise DistortionError("breaks overlap: a cut reaches past the next break")
        cursor = item.at_ms + max(0, -item.delta_ms)
    return ordered


def _block_of(start: int, breaks: list[Break]) -> tuple[int, int] | None:
    """Block index and cumulative delta for a line start, or None inside a cut."""
    block = 0
    delta = 0
    for index, item in enumerate(breaks, start=1):
        if start < item.at_ms:
            break
        if item.delta_ms < 0 and start < item.at_ms - item.delta_ms:
            return None
        block = index
        delta += item.delta_ms
    return block, delta


def _as_a_script_keeps(ms: int) -> int:
    """A time rounded to hundredths of a second, as the script writer rounds it."""
    return (ms + 5) - (ms + 5) % 10


def distort(
    base: Subtitle,
    distortion: Distortion,
    *,
    fmt: str | None = None,
    language: str = "",
    seed: int = 0,
    extras: Sequence[Extra] = (),
    styles: Mapping[str, pysubs2.SSAStyle] | None = None,
) -> Distorted:
    """Apply `distortion` to an in-sync subtitle and return the target with its truth.

    `extras` are added to the in-sync file first, and `styles` to its styles.
    """
    fmt = fmt or base.format
    rng = random.Random(seed)
    breaks = _check_breaks(distortion.breaks)
    labels = {label.id: label.label for label in classify_lines(base)}

    records = [
        _Record(
            line.start_ms,
            line.end_ms,
            line.text,
            line.style,
            "base",
            labels[line.id],
            base_id=line.id,
            event=event,
        )
        for line, event in zip(base.lines, base.ssa.events, strict=True)
    ]
    records += [
        _Record(
            extra.start_ms,
            extra.end_ms,
            extra.text,
            extra.style,
            extra.kind,
            "",
            event=pysubs2.SSAEvent(
                layer=extra.layer,
                effect=extra.effect,
                type="Comment" if extra.comment else "Dialogue",
            ),
            timed=extra.timed,
        )
        for extra in extras
    ]
    noise = distortion.noise
    floor_ms = max(0, -distortion.offset_ms)
    if noise.karaoke_lines:
        _add_song(records, noise.karaoke_lines, fmt, rng, floor_ms)
    if noise.credits:
        _add_credits(records, noise.credits, floor_ms)
    if noise.sound_descriptions:
        _add_sounds(records, noise.sound_descriptions, language, rng, floor_ms)
    if noise.trailing_lines:
        _add_trailing(records, noise.trailing_lines)

    placed: list[tuple[int, int, int, _Record]] = []
    dropped: list[int] = []
    for record in records:
        where = _block_of(record.start, breaks)
        if where is None:
            if record.base_id is not None:
                dropped.append(record.base_id)
            continue
        block, delta = where
        start = round((record.start + delta + distortion.offset_ms) * distortion.rate)
        end = round((record.end + delta + distortion.offset_ms) * distortion.rate)
        placed.append((start, end, block, record))
    if not placed:
        raise DistortionError("the distortion leaves no lines")
    if min(start for start, _, _, _ in placed) < 0:
        raise DistortionError("the distortion moves lines before zero")
    placed.sort(key=lambda item: item[0])
    if fmt == "ass":
        # A script keeps hundredths of a second. The plan that undoes the
        # distortion is cut where the file has its lines, not where they were meant.
        placed = [
            (_as_a_script_keeps(start), _as_a_script_keeps(end), block, record)
            for start, end, block, record in placed
        ]

    ssa = copy.deepcopy(base.ssa)
    for name, style in (styles or {}).items():
        ssa.styles.setdefault(name, style)
    if any(record.style == _SONG_STYLE for _, _, _, record in placed):
        ssa.styles.setdefault(_SONG_STYLE, pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER))
    events = []
    truth = []
    for line_id, (start, end, _, record) in enumerate(placed):
        event = copy.deepcopy(record.event) if record.event is not None else pysubs2.SSAEvent()
        event.start, event.end, event.text, event.style = start, end, record.text, record.style
        events.append(event)
        truth.append(
            TruthLine(
                id=line_id,
                kind=record.kind,
                label=record.label or record.kind,
                base_id=record.base_id,
                true_start_ms=record.start if record.timed else None,
                true_end_ms=record.end if record.timed else None,
            )
        )
    ssa.events = events
    subtitle = Subtitle(ssa, fmt)
    return Distorted(
        subtitle=subtitle,
        truth=truth,
        ideal_plan=_ideal_plan(placed, breaks, distortion),
        dropped_base_ids=sorted(dropped),
    )


def _ideal_plan(
    placed: list[tuple[int, int, int, _Record]], breaks: list[Break], distortion: Distortion
) -> Plan:
    """The plan that undoes the distortion, in the engine's own vocabulary."""
    deltas = [0]
    for item in breaks:
        deltas.append(deltas[-1] + item.delta_ms)

    first_start: dict[int, int] = {}
    for start, _, block, _ in placed:
        first_start.setdefault(block, start)
    blocks = sorted(first_start)

    timing: list[Scale | Shift] = []
    if distortion.rate != 1.0:
        timing.append(Scale(factor=1 / distortion.rate, reason="undo the rate change"))
    for position, block in enumerate(blocks):
        ms = -(deltas[block] + distortion.offset_ms)
        if ms == 0:
            continue
        timing.append(
            Shift.model_validate(
                {
                    "from": first_start[block] if position > 0 else None,
                    "to": first_start[blocks[position + 1]] if position + 1 < len(blocks) else None,
                    "ms": ms,
                }
            )
        )

    absent = [line_id for line_id, item in enumerate(placed) if not item[3].timed]
    content = [RemoveLines(reason="absent_from_reference", ids=absent)] if absent else []
    return Plan(
        outcome="synced" if timing else "already_in_sync",
        content_ops=content,
        timing_ops=timing,
    )
