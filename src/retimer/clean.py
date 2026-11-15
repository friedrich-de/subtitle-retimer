"""Text-level cleaning: hearing-impaired text, symbols, formatting.

Every function takes and returns ASS-form text. A function returns an empty string
when nothing readable is left, which tells the caller to drop the line.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from retimer.text import LINE_BREAK, MUSIC_SYMBOLS, OVERRIDE_BLOCK, is_drawing, strip_tags

# Sound descriptions and speaker names: [door slams], (laughs), （笑い）, ［音楽］
_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|（[^）]*）|［[^］]*］")
# Speaker labels in capitals at the start of a line: "JOHN:", "- MAN 2:"
_SPEAKER_LABEL = re.compile(r"^(\s*[-–—]?\s*)[A-Z][A-Z0-9 .'’-]{1,24}:(?=\s|$)")
_HASH_MARKERS = re.compile(r"^(\s*[-–—]?\s*)#+\s*|\s*#+\s*$")
_NO_BREAK_SPACE = chr(0xA0)
_SPACES = re.compile(f"[ \t{_NO_BREAK_SPACE}]{{2,}}")
_LEADING_DASH = re.compile(r"^\s*[-–—]\s*")

# Override blocks are swapped for private-use characters while the text is edited,
# so that a pattern never matches inside a tag such as {\pos(320,240)}.
_PLACEHOLDER_BASE = 0xE000
_PLACEHOLDER_CLASS = f"[{chr(_PLACEHOLDER_BASE)}-{chr(0xF8FF)}]"
_PLACEHOLDER = re.compile(_PLACEHOLDER_CLASS)
_SPACE_AFTER_LEADING_TAGS = re.compile(f"^({_PLACEHOLDER_CLASS}+)\\s+")
_SPACE_BEFORE_TRAILING_TAGS = re.compile(f"\\s+({_PLACEHOLDER_CLASS}+)$")


def _protect(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def stash(match: re.Match[str]) -> str:
        blocks.append(match.group(0))
        return chr(_PLACEHOLDER_BASE + len(blocks) - 1)

    return OVERRIDE_BLOCK.sub(stash, text), blocks


def _restore(text: str, blocks: list[str]) -> str:
    return _PLACEHOLDER.sub(lambda m: blocks[ord(m.group(0)) - _PLACEHOLDER_BASE], text)


def _readable(text: str) -> bool:
    return any(char.isalnum() for char in _PLACEHOLDER.sub("", text))


def _edit_sublines(text: str, edit: Callable[[str], str]) -> str:
    """Apply `edit` to each visual line.

    A visual line that the edit changes and leaves without a letter or digit is
    dropped. Lines the edit does not touch are kept exactly as they are, so a
    line such as "..." survives.
    """
    protected, blocks = _protect(text)
    kept: list[str] = []
    changed = False
    for subline in LINE_BREAK.split(protected):
        edited = edit(subline)
        if edited == subline:
            kept.append(subline)
            continue
        changed = True
        edited = _SPACES.sub(" ", edited).strip()
        edited = _SPACE_AFTER_LEADING_TAGS.sub(r"\1", edited)
        edited = _SPACE_BEFORE_TRAILING_TAGS.sub(r"\1", edited)
        if _readable(edited):
            kept.append(edited)
    if not changed:
        return text
    return _restore("\\N".join(kept), blocks)


def remove_hearing_impaired(text: str) -> str:
    """Remove sound descriptions in brackets and speaker labels in capitals."""

    def edit(subline: str) -> str:
        subline = _BRACKETED.sub("", subline)
        return _SPEAKER_LABEL.sub(r"\1", subline)

    return _edit_sublines(text, edit)


def remove_symbols(text: str) -> str:
    """Remove music notes, and hash signs used as music markers around a line."""

    def edit(subline: str) -> str:
        for symbol in MUSIC_SYMBOLS:
            subline = subline.replace(symbol, "")
        return _HASH_MARKERS.sub(r"\1", subline)

    return _edit_sublines(text, edit)


def strip_formatting(text: str) -> str:
    """Remove override tags and keep the text. A vector drawing has no text to keep."""
    if is_drawing(text):
        return ""
    return strip_tags(text)


def normalize_for_match(text: str) -> str:
    """Reduce a line to the words that are spoken, on one line, for matching."""
    if is_drawing(text):
        return ""
    cleaned = remove_symbols(remove_hearing_impaired(strip_tags(text)))
    parts = [_LEADING_DASH.sub("", part) for part in LINE_BREAK.split(cleaned)]
    return _SPACES.sub(" ", " ".join(parts).replace("\\h", " ")).strip()
