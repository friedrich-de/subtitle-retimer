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

from retimer.model import Line, Subtitle
from retimer.text import (
    MUSIC_SYMBOLS,
    has_karaoke_tags,
    is_drawing,
    is_positioned,
    to_plain,
)

Label = Literal["dialogue", "sign", "song", "sound_description", "credit", "comment", "empty"]

_SONG_STYLE = re.compile(
    r"(?<![a-z])(?:op|ed)(?![a-z])|(?<![a-z])(?:op|ed)\d*(?:rom|eng|jap|jpn|kan|tl|trans|kara)"
    r"|opening|ending|song|karaoke|(?<![a-z])kara(?![a-z])"
    r"|lyric|romaji|insert|(?<![a-z])ins(?![a-z])|theme"
)
# What tells the styles of one song apart: the script, the layer, the effect.
# "Wonder Stella Romaji" and "Wonder Stella English" are one family.
_STYLE_VARIANT = re.compile(
    r"romaji|romanji|kanji|english|translation|japanese|lyrics?|karaoke"
    r"|(?<![a-z])(?:rom|kan|kana|eng|trans|tl|jpn|jap|jp|kara|kfx|fx|border|glow|shadow"
    r"|outline|blur|layer|l\d+)(?![a-z])|\d+"
)
_MIN_FAMILY_NAME = 4
# A style in which this share of the lines carries karaoke timing is a song style.
_KARAOKE_STYLE_SHARE = 0.5
# Typesetting that follows the picture is redrawn for every frame or two. A
# positioned line that is gone this fast is such a frame and nothing anyone says.
_FRAME_MS = 250
# The effect field of lines a karaoke template generated.
_GENERATED_EFFECT = "fx"
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
# One bracket around everything, also over a line break, as a long sound
# description is written. Doubled parentheses mark a thought and are speech.
_BRACKETED_BLOCK = re.compile(
    r"^[-–—]?\s*(?:\[[^\[\]［］]*[\]］]|［[^\[\]［］]*[］\]]"
    r"|\((?!\()[^()（）]*[)）]|（(?!（)[^()（）]*[）)])$"
)
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
    if not sublines:
        return False
    return all(_BRACKETED_LINE.match(part) for part in sublines) or bool(
        _BRACKETED_BLOCK.match(plain)
    )


def _is_plain(line: Line) -> bool:
    """A line as dialogue is written: no effect, no position, shown long enough to read."""
    return not (
        line.is_comment
        or line.effect.strip().lower() == _GENERATED_EFFECT
        or line.duration_ms < _FRAME_MS
        or is_drawing(line.text)
        or has_karaoke_tags(line.text)
        or is_positioned(line.text)
    )


def _family(style_name: str) -> str:
    return re.sub(r"[^a-z]+", "", _STYLE_VARIANT.sub(" ", style_name.lower()))


def _song_styles(subtitle: Subtitle) -> set[str]:
    """Styles that carry a song: by name, by karaoke timing, or as a variant of one that does."""
    lines = [line for line in subtitle.lines if not line.is_comment]
    total = Counter(line.style for line in lines)
    timed = Counter(line.style for line in lines if has_karaoke_tags(line.text))
    songs = {
        style
        for style, count in total.items()
        if _SONG_STYLE.search(style.lower()) or timed[style] / count >= _KARAOKE_STYLE_SHARE
    }
    families = {_family(style) for style in songs}
    for style in total:
        family = _family(style)
        if (
            style not in songs
            and len(family) >= _MIN_FAMILY_NAME
            and family in families
            and _STYLE_VARIANT.search(style.lower())
            and not _SIGN_STYLE.search(style.lower())
        ):
            songs.add(style)
    return songs


def _dialogue_styles(subtitle: Subtitle, song_styles: set[str]) -> set[str]:
    """Styles that look like dialogue by name or by how much of the file they carry.

    Only plain lines count for the share. Typesetting and karaoke effects can
    be most of the lines of a file, and the style they are in is no dialogue.
    """
    counts = Counter(line.style for line in subtitle.lines if _is_plain(line))
    total = sum(counts.values()) or 1
    names = {line.style for line in subtitle.lines if not line.is_comment}
    styles = set()
    for style in names:
        name = style.lower()
        if style in song_styles or _SONG_STYLE.search(name) or _SIGN_STYLE.search(name):
            continue
        if _DIALOGUE_STYLE.search(name) or counts[style] / total >= _MAIN_STYLE_SHARE:
            styles.add(style)
    return styles


def classify_lines(subtitle: Subtitle) -> list[LineLabel]:
    """Label every line of the file. The result has one entry per line, in file order."""
    song_styles = _song_styles(subtitle)
    dialogue_styles = _dialogue_styles(subtitle, song_styles)
    labels = []
    for line in subtitle.lines:
        label, confidence, reasons = _classify(line, dialogue_styles, song_styles)
        labels.append(LineLabel(line.id, label, confidence, tuple(reasons)))
    return labels


def _classify(
    line: Line, dialogue_styles: set[str], song_styles: set[str]
) -> tuple[Label, float, list[str]]:
    text, style = line.text, line.style
    if line.is_comment:
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
    if style in song_styles:
        return "song", 0.85, [f"style '{style}' belongs to a song"]
    if _CREDIT.search(plain.lower()):
        return "credit", 0.9, ["credit or advertisement wording"]
    if _SIGN_STYLE.search(style_name):
        return "sign", 0.9, [f"style name '{style}'"]

    positioned = is_positioned(text)
    if positioned and line.duration_ms < _FRAME_MS:
        return "sign", 0.85, ["positioned on screen", "shown for a moment only"]
    if positioned and line.effect.strip().lower() == _GENERATED_EFFECT:
        return "sign", 0.8, ["positioned on screen", "generated by an effect template"]
    known_dialogue_style = style in dialogue_styles
    if positioned and not known_dialogue_style:
        return "sign", 0.75, ["positioned on screen", f"style '{style}' is not a dialogue style"]
    if _is_music_line(plain):
        return "song", 0.85, ["wrapped in music symbols"]
    if _is_sound_description(plain):
        return "sound_description", 0.9, ["whole line is in brackets"]

    if positioned:
        return "dialogue", 0.6, ["dialogue style", "positioned on screen"]
    if known_dialogue_style:
        return "dialogue", 0.9, ["dialogue style"]
    return "dialogue", 0.7, [f"style '{style}' is not recognized"]
