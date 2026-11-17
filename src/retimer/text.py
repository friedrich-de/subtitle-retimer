"""Helpers for ASS-form subtitle text: override blocks, line breaks, symbols."""

from __future__ import annotations

import re

OVERRIDE_BLOCK = re.compile(r"\{[^{}]*\}")
LINE_BREAK = re.compile(r"\\[Nn]")
MUSIC_SYMBOLS = "♪♫♬♩\U0001f3b5\U0001f3b6\U0001f3bc"

_KARAOKE_TAG = re.compile(r"\\(?:kf|ko|k|K)\d+")
_POSITION_TAG = re.compile(r"\\(?:pos|move|org|i?clip)\s*\(")
_DRAWING_TAG = re.compile(r"\\p[1-9]")


def _tags(text: str) -> str:
    return "".join(OVERRIDE_BLOCK.findall(text))


def has_override_tags(text: str) -> bool:
    return OVERRIDE_BLOCK.search(text) is not None


def has_karaoke_tags(text: str) -> bool:
    return _KARAOKE_TAG.search(_tags(text)) is not None


def is_positioned(text: str) -> bool:
    return _POSITION_TAG.search(_tags(text)) is not None


def is_drawing(text: str) -> bool:
    return _DRAWING_TAG.search(_tags(text)) is not None


def strip_tags(text: str) -> str:
    """Remove override blocks and keep the text and its line breaks."""
    return OVERRIDE_BLOCK.sub("", text)


def to_plain(text: str) -> str:
    """Text as a viewer reads it: no tags, real newlines, plain spaces."""
    if is_drawing(text):
        return ""
    plain = LINE_BREAK.sub("\n", strip_tags(text)).replace("\\h", " ")
    return "\n".join(part.strip() for part in plain.split("\n")).strip()


def has_music_symbol(text: str) -> bool:
    return any(symbol in text for symbol in MUSIC_SYMBOLS)
