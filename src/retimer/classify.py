"""Rule-based labels for subtitle lines.

The labels decide what takes part in matching (dialogue) and what the content
options remove. Rules that are unsure say so through a lower confidence, so that
the agent can look at those lines itself.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from retimer.model import Subtitle
from retimer.text import (
    MUSIC_SYMBOLS,
    has_karaoke_tags,
    is_drawing,
    is_positioned,
    to_plain,
)

Label = Literal["dialogue", "sign", "song", "sound_description", "credit", "comment", "empty"]

_SONG_STYLE = re.compile(
    r"(?<![a-z])(?:op|ed)(?![a-z])|opening|ending|song|karaoke|(?<![a-z])kara(?![a-z])"
    r"|lyric|romaji|insert|theme"
)
_SIGN_STYLE = re.compile(
    r"sign|(?<![a-z])ts(?![a-z])|typeset|(?<!sub)title|caption|screen|(?<![a-z])note"
    r"|label|eyecatch|logo|credit|staff"
)
_DIALOGUE_STYLE = re.compile(
    r"default|main|dialog|(?<![a-z])alt|italic|flashback|thought|narrat|overlap"
    r"|(?<![a-z])top(?![a-z])|speech|internal|normal|regular|subtitle"
)
_CREDIT = re.compile(
    r"sub(?:title)?s?\s+by|subbed\s+by|sync(?:ed)?\s+(?:and|&)\s+corrected|sync(?:ed)?\s+by"
    r"|corrections?\s+by|opensubtitles|addic7ed|subscene|podnapisi"
    r"|www\.[a-z0-9-]+\.[a-z]{2,}|https?://|downloaded\s+from|encoded\s+by|ripped\s+by"
    r"|translat(?:ed|ion)\s+by|timing\s+by|typeset(?:ting)?\s+by|advertise\s+your"
    r"|become\s+(?:a\s+)?vip\s+member"
)
_BRACKETED_LINE = re.compile(r"^[-–—]?\s*(?:\[[^\]]*\]|\([^)]*\)|（[^）]*）|［[^］]*］)$")
# A style that carries at least this share of the lines is taken to be dialogue.
_MAIN_STYLE_SHARE = 0.3


@dataclass(frozen=True)
class LineLabel:
    id: int
    label: Label
    confidence: float
    reasons: tuple[str, ...]


def _is_music_line(plain: str) -> bool:
    sublines = [part for part in plain.split("\n") if part]
    if not sublines:
        return False

    def wrapped(part: str) -> bool:
        return (
            part[0] in MUSIC_SYMBOLS
            or part[-1] in MUSIC_SYMBOLS
            or (len(part) > 2 and part.startswith("#") and part.endswith("#"))
        )

    return all(wrapped(part) for part in sublines)


def _is_sound_description(plain: str) -> bool:
    sublines = [part for part in plain.split("\n") if part]
    return bool(sublines) and all(_BRACKETED_LINE.match(part) for part in sublines)


def _dialogue_styles(subtitle: Subtitle) -> set[str]:
    """Styles that look like dialogue by name or by how much of the file they carry."""
    counts = Counter(line.style for line in subtitle.lines if not line.is_comment)
    total = sum(counts.values()) or 1
    styles = set()
    for style, count in counts.items():
        name = style.lower()
        if _SONG_STYLE.search(name) or _SIGN_STYLE.search(name):
            continue
        if _DIALOGUE_STYLE.search(name) or count / total >= _MAIN_STYLE_SHARE:
            styles.add(style)
    return styles


def classify_lines(subtitle: Subtitle) -> list[LineLabel]:
    """Label every line of the file. The result has one entry per line, in file order."""
    dialogue_styles = _dialogue_styles(subtitle)
    labels = []
    for line in subtitle.lines:
        label, confidence, reasons = _classify(
            line.text, line.style, line.is_comment, dialogue_styles
        )
        labels.append(LineLabel(line.id, label, confidence, tuple(reasons)))
    return labels


def _classify(
    text: str, style: str, is_comment: bool, dialogue_styles: set[str]
) -> tuple[Label, float, list[str]]:
    if is_comment:
        return "comment", 1.0, ["comment event"]
    if is_drawing(text):
        return "sign", 1.0, ["vector drawing"]
    plain = to_plain(text)
    if not plain:
        return "empty", 1.0, ["no text"]

    style_name = style.lower()
    if has_karaoke_tags(text):
        return "song", 0.95, ["karaoke timing tags"]
    if _SONG_STYLE.search(style_name):
        return "song", 0.9, [f"style name '{style}'"]
    if _CREDIT.search(plain.lower()):
        return "credit", 0.9, ["credit or advertisement wording"]
    if _SIGN_STYLE.search(style_name):
        return "sign", 0.9, [f"style name '{style}'"]

    known_dialogue_style = style in dialogue_styles
    if is_positioned(text) and not known_dialogue_style:
        return "sign", 0.75, ["positioned on screen", f"style '{style}' is not a dialogue style"]
    if _is_music_line(plain):
        return "song", 0.85, ["wrapped in music symbols"]
    if _is_sound_description(plain):
        return "sound_description", 0.9, ["whole line is in brackets"]

    if is_positioned(text):
        return "dialogue", 0.6, ["dialogue style", "positioned on screen"]
    if known_dialogue_style:
        return "dialogue", 0.9, ["dialogue style"]
    return "dialogue", 0.7, [f"style '{style}' is not recognized"]
