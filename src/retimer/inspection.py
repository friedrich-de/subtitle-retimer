"""The inspection report and line sampling: what the agent reads instead of the file."""

from __future__ import annotations

import re
import statistics
from collections import Counter

from pydantic import BaseModel

from retimer.classify import Label, LineLabel, classify_lines
from retimer.clean import remove_hearing_impaired
from retimer.model import Line, Subtitle
from retimer.text import (
    has_karaoke_tags,
    has_music_symbol,
    has_override_tags,
    is_drawing,
    is_positioned,
    to_plain,
)

_LONG_LINE_MS = 20_000
_SAMPLE_CHARS = 80


class StyleStat(BaseModel):
    name: str
    lines: int
    share: float
    labels: dict[str, int]
    sample: str


class TagStats(BaseModel):
    lines_with_override_tags: int
    karaoke_lines: int
    drawing_lines: int
    positioned_lines: int


class HearingImpairedStats(BaseModel):
    full_lines: int
    lines_with_removable_parts: int


class TimingStats(BaseModel):
    first_start_ms: int | None
    last_end_ms: int | None
    median_duration_ms: int | None
    overlapping_lines: int
    max_simultaneous: int
    zero_or_negative_duration: int
    out_of_order: int
    longer_than_20s: int


class InspectionReport(BaseModel):
    format: str
    encoding: str
    line_count: int
    comment_count: int
    scripts: dict[str, float]
    language_guess: str | None
    labels: dict[str, int]
    low_confidence_lines: int
    styles: list[StyleStat]
    tags: TagStats
    hearing_impaired: HearingImpairedStats
    lines_with_music_symbols: int
    consecutive_duplicates: int
    timing: TimingStats


class SampledLine(BaseModel):
    id: int
    start_ms: int
    end_ms: int
    style: str
    label: str
    text: str


def _script_of(char: str) -> str | None:
    code = ord(char)
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF or 0xFF66 <= code <= 0xFF9F:
        return "kana"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "han"
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
        return "hangul"
    if 0x0400 <= code <= 0x052F:
        return "cyrillic"
    if 0x0600 <= code <= 0x06FF:
        return "arabic"
    if 0x0590 <= code <= 0x05FF:
        return "hebrew"
    if 0x0370 <= code <= 0x03FF:
        return "greek"
    if 0x0E00 <= code <= 0x0E7F:
        return "thai"
    if char.isalpha() and code <= 0x024F:
        return "latin"
    return "other" if char.isalpha() else None


def _scripts(texts: list[str]) -> dict[str, float]:
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(script for script in map(_script_of, text) if script)
    total = sum(counts.values())
    if not total:
        return {}
    return {script: round(count / total, 3) for script, count in counts.most_common()}


def _language_guess(scripts: dict[str, float]) -> str | None:
    """Name a language only where the script settles it. Latin text stays undecided."""
    if scripts.get("kana", 0) >= 0.1:
        return "ja"
    if scripts.get("hangul", 0) >= 0.3:
        return "ko"
    if scripts.get("han", 0) >= 0.5:
        return "zh"
    return None


def _timing(lines: list[Line]) -> TimingStats:
    if not lines:
        return TimingStats(
            first_start_ms=None,
            last_end_ms=None,
            median_duration_ms=None,
            overlapping_lines=0,
            max_simultaneous=0,
            zero_or_negative_duration=0,
            out_of_order=0,
            longer_than_20s=0,
        )
    out_of_order = sum(
        1 for prev, cur in zip(lines, lines[1:], strict=False) if cur.start_ms < prev.start_ms
    )
    ordered = sorted(lines, key=lambda line: (line.start_ms, line.id))
    overlapping = sum(
        1 for prev, cur in zip(ordered, ordered[1:], strict=False) if prev.end_ms > cur.start_ms
    )
    edges = sorted(
        [(line.start_ms, 1) for line in lines if line.duration_ms > 0]
        + [(line.end_ms, -1) for line in lines if line.duration_ms > 0]
    )
    active = peak = 0
    for _, step in edges:
        active += step
        peak = max(peak, active)
    return TimingStats(
        first_start_ms=min(line.start_ms for line in lines),
        last_end_ms=max(line.end_ms for line in lines),
        median_duration_ms=int(statistics.median(line.duration_ms for line in lines)),
        overlapping_lines=overlapping,
        max_simultaneous=peak,
        zero_or_negative_duration=sum(1 for line in lines if line.duration_ms <= 0),
        out_of_order=out_of_order,
        longer_than_20s=sum(1 for line in lines if line.duration_ms > _LONG_LINE_MS),
    )


def inspect_subtitle(subtitle: Subtitle, labels: list[LineLabel] | None = None) -> InspectionReport:
    """Summarize a subtitle file: structure, content markers and timing oddities."""
    labels = labels if labels is not None else classify_lines(subtitle)
    label_by_id = {label.id: label for label in labels}
    lines = subtitle.lines
    events = [line for line in lines if not line.is_comment]
    plain = {line.id: to_plain(line.text) for line in events}

    styles = []
    for style, count in Counter(line.style for line in events).most_common():
        members = [line for line in events if line.style == style]
        sample = next((plain[line.id] for line in members if plain[line.id]), "")
        styles.append(
            StyleStat(
                name=style,
                lines=count,
                share=round(count / len(events), 3),
                labels=dict(Counter(label_by_id[line.id].label for line in members)),
                sample=sample.replace("\n", " / ")[:_SAMPLE_CHARS],
            )
        )

    dialogue = [line for line in events if label_by_id[line.id].label == "dialogue"]
    removable_parts = sum(1 for line in dialogue if remove_hearing_impaired(line.text) != line.text)
    duplicates = sum(
        1
        for prev, cur in zip(events, events[1:], strict=False)
        if plain[cur.id] and plain[cur.id] == plain[prev.id]
    )
    scripts = _scripts([plain[line.id] for line in dialogue])

    return InspectionReport(
        format=subtitle.format,
        encoding=subtitle.encoding,
        line_count=len(lines),
        comment_count=len(lines) - len(events),
        scripts=scripts,
        language_guess=_language_guess(scripts),
        labels=dict(Counter(label.label for label in labels)),
        low_confidence_lines=sum(1 for label in labels if label.confidence < 0.8),
        styles=styles,
        tags=TagStats(
            lines_with_override_tags=sum(1 for line in events if has_override_tags(line.text)),
            karaoke_lines=sum(1 for line in events if has_karaoke_tags(line.text)),
            drawing_lines=sum(1 for line in events if is_drawing(line.text)),
            positioned_lines=sum(1 for line in events if is_positioned(line.text)),
        ),
        hearing_impaired=HearingImpairedStats(
            full_lines=sum(1 for label in labels if label.label == "sound_description"),
            lines_with_removable_parts=removable_parts,
        ),
        lines_with_music_symbols=sum(1 for line in events if has_music_symbol(plain[line.id])),
        consecutive_duplicates=duplicates,
        timing=_timing(events),
    )


def sample_lines(
    subtitle: Subtitle,
    labels: list[LineLabel] | None = None,
    *,
    style: str | None = None,
    label: Label | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    pattern: str | None = None,
    max_confidence: float | None = None,
    limit: int = 20,
) -> list[SampledLine]:
    """Return up to `limit` lines that pass every filter, spread evenly over the matches."""
    labels = labels if labels is not None else classify_lines(subtitle)
    label_by_id = {entry.id: entry for entry in labels}
    regex = re.compile(pattern, re.IGNORECASE) if pattern else None

    matches = []
    for line in subtitle.lines:
        entry = label_by_id[line.id]
        if style is not None and line.style != style:
            continue
        if label is not None and entry.label != label:
            continue
        if from_ms is not None and line.start_ms < from_ms:
            continue
        if to_ms is not None and line.start_ms >= to_ms:
            continue
        if max_confidence is not None and entry.confidence > max_confidence:
            continue
        if regex is not None and not regex.search(to_plain(line.text)):
            continue
        matches.append(line)

    if limit > 0 and len(matches) > limit:
        step = (len(matches) - 1) / (limit - 1) if limit > 1 else 0
        matches = [matches[round(index * step)] for index in range(limit)]

    return [
        SampledLine(
            id=line.id,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            style=line.style,
            label=label_by_id[line.id].label,
            text=line.text,
        )
        for line in matches
    ]
