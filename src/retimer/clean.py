"""Text-level cleaning: hearing-impaired text, symbols, formatting.

Every function takes and returns ASS-form text. A function returns an empty string
when nothing readable is left, which tells the caller to drop the line.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from functools import partial
from typing import TYPE_CHECKING

from retimer.text import LINE_BREAK, OVERRIDE_BLOCK, is_drawing, strip_tags

if TYPE_CHECKING:
    from retimer.model import Subtitle

# Every pattern here runs on text a stranger wrote, so each one is written to
# take time in proportion to the length of a line, whatever the line holds. A
# pattern that can start at every position and then scan ahead does not.

# Sound descriptions and speaker names: [door slams], (laughs), （笑い）, ［音楽］,
# 〔エラー音〕. A bracket may be closed by its half-width or its full-width
# partner, because files that were typed by hand mix the two.
_OPENERS = {"(": "round", "（": "round", "[": "square", "［": "square", "〔": "shell"}
_CLOSERS = {")": "round", "）": "round", "]": "square", "］": "square", "〕": "shell"}
# Doubled parentheses are how Japanese broadcast captions mark a thought or a
# voice-over: ((そうだったのか)). What is inside is spoken, and it stays.
_THOUGHT_CLOSERS = {"((": "))", "（（": "））"}
_BREAK = "\n"
# Speaker labels in capitals at the start of a line: "JOHN:", "- MAN 2:"
_SPEAKER_LABEL = re.compile(r"^(\s*(?:[-–—]\s*)?)[A-Z][A-Z0-9 .'’-]{1,24}:(?=\s|$)")
_LEADING_HASHES = re.compile(r"^(\s*(?:[-–—]\s*)?)#+\s*")
# Marks that captions carry and nobody says: music notes, the arrows with which
# Japanese broadcast captions run a sentence on into the next line, and the
# little pictures that say a voice comes from a telephone, a television or a
# loudspeaker. Stars and hearts are among them. A variation selector or a
# joiner only shapes the symbol before it.
_SYMBOLS = re.compile(
    "["
    "\u2190-\u21ff"  # arrows
    "\u2600-\u27bf"  # miscellaneous symbols and dingbats
    "\u2b00-\u2bff"  # more arrows
    "\U0001f000-\U0001faff"  # pictographs
    "\ufe00-\ufe0f\u200d"
    "]+"
)
# Brackets with which Japanese broadcast captions say how a voice is heard, not
# what it says: a thought or a narrator 《 》 ≪ ≫ ＜ ＞ 〈 〉, a voice off screen
# or from a device ｟ ｠ ⦅ ⦆ ⸨ ⸩. A stretch often runs over several subtitles,
# so a bracket goes whether or not its partner is in the same line. The words
# stay. Chinese and Korean write titles in 《 》 and 〈 〉, so these go only from
# a subtitle in Japanese.
_VOICE_MARKS = re.compile("[《》≪≫＜＞〈〉｟｠⦅⦆⸨⸩]+")
_VOICE_MARK_OPENERS = "《≪＜〈｟⦅⸨"
_KANA = re.compile("[\u3041-\u30ff\u31f0-\u31ff\uff66-\uff9d]")
_NO_BREAK_SPACE = chr(0xA0)
_SPACES = re.compile(f"[ \t{_NO_BREAK_SPACE}]{{2,}}")
_LEADING_DASH = re.compile(r"^\s*[-–—]\s*")

# Override blocks are swapped for private-use characters while the text is edited,
# so that a pattern never matches inside a tag such as {\pos(320,240)}.
_PLACEHOLDER_BASE = 0xE000
_PLACEHOLDER_CLASS = f"[{chr(_PLACEHOLDER_BASE)}-{chr(0xF8FF)}]"
_PLACEHOLDER = re.compile(_PLACEHOLDER_CLASS)
_SPACE_AFTER_LEADING_TAGS = re.compile(f"^({_PLACEHOLDER_CLASS}+)\\s+")
# A speaker's name in Japanese captions, right after the bracket that opens a
# thought, a narration or a voice off screen: 《ハルリ：この女は, ((ＭＥＭちょ：かなちゃん.
# Captions often set the name or the colon in a narrower font, so tags may
# surround them; the tags stay. A time such as 《20：00 is not a name.
_NAME_CHAR = (
    "[\u3041-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\u3005"
    "\uff10-\uff19\uff21-\uff3a\uff41-\uff5a\uff65-\uff9f0-9A-Za-z]"
)
_MARKED_SPEAKER_LABEL = re.compile(
    f"^((?:{_PLACEHOLDER_CLASS}|\\s)*(?:[{_VOICE_MARK_OPENERS}]|\\(\\(|（（)(?:{_PLACEHOLDER_CLASS}|[ \\u3000])*)"
    f"(?![0-9０-９]+[:：]){_NAME_CHAR}{{1,12}}{_PLACEHOLDER_CLASS}*[:：](?:{_PLACEHOLDER_CLASS}|[ \\u3000])*"
)


def _bracket_pairs(text: str) -> dict[int, int]:
    """Where each bracket that is closed closes: the opening position to the closing one.

    Brackets nest, as in （ＮＡ(ナレーション):ヤチヨ）, and a closer belongs to
    the innermost bracket still open. A closer that does not fit it is left
    alone. Doubled parentheses open a thought, which only doubled ones close;
    a thought is recorded at the position of its first parenthesis.
    """
    pairs: dict[int, int] = {}
    stack: list[tuple[int, str]] = []
    position = 0
    while position < len(text):
        two = text[position : position + 2]
        if two in _THOUGHT_CLOSERS:
            stack.append((position, two))
            position += 2
            continue
        if stack and _THOUGHT_CLOSERS.get(stack[-1][1]) == two:
            pairs[stack.pop()[0]] = position
            position += 2
            continue
        char = text[position]
        if char in _OPENERS:
            stack.append((position, _OPENERS[char]))
        elif char in _CLOSERS and stack and stack[-1][1] == _CLOSERS[char]:
            pairs[stack.pop()[0]] = position
        position += 1
    return pairs


def _without_brackets(sublines: list[str]) -> list[tuple[str, bool]]:
    """The visual lines of one subtitle without their bracketed stretches.

    A stretch runs from an opening bracket to the closer that pairs with it,
    with any brackets inside. It may run over a line break, as a long sound
    description does: the lines it joins become one. A thought in doubled
    parentheses is spoken and stays. Returns each remaining line and whether
    anything was taken from it.
    """
    text = _BREAK.join(sublines)
    pairs = _bracket_pairs(text)
    # Breaks and opening brackets before each position, to look into a
    # stretch without reading it again.
    breaks_before = [0]
    openers_before = [0]
    for char in text:
        breaks_before.append(breaks_before[-1] + (char == _BREAK))
        openers_before.append(openers_before[-1] + (char in _OPENERS))
    lines: list[tuple[str, bool]] = []
    kept: list[str] = []
    touched = False
    position = 0
    while position < len(text):
        char = text[position]
        if char == _BREAK:
            lines.append(("".join(kept), touched))
            kept, touched = [], False
            position += 1
            continue
        end = pairs.get(position)
        if end is not None and text[position : position + 2] not in _THOUGHT_CLOSERS:
            # Over a break only a plain stretch counts. With another bracket in
            # it, the opener may never have been closed and the closer be another's.
            over_break = breaks_before[end] > breaks_before[position]
            nested = openers_before[end] - openers_before[position + 1] > 0
            if not (over_break and nested):
                touched = True
                position = end + 1
                continue
        kept.append(char)
        position += 1
    lines.append(("".join(kept), touched))
    return lines


def _without_thought_marks(subline: str) -> str:
    """A visual line without the doubled parentheses that mark a thought.

    A thought often runs over several subtitles, so either half goes on its
    own. Doubled parentheses inside a bracket that is open close that bracket
    and a reading in it, as in (百目(ひゃくめ)), and stay.
    """
    kept: list[str] = []
    depth = 0
    position = 0
    while position < len(subline):
        two = subline[position : position + 2]
        if depth == 0 and (two in _THOUGHT_CLOSERS or two in _THOUGHT_CLOSERS.values()):
            position += 2
            continue
        char = subline[position]
        if char in "(（":
            depth += 1
        elif char in ")）" and depth:
            depth -= 1
        kept.append(char)
        position += 1
    return "".join(kept)


def _remove_hash_markers(text: str) -> str:
    """Drop hash signs that open or close a line, with the space next to them."""
    text = _LEADING_HASHES.sub(r"\1", text)
    stripped = text.rstrip()
    if stripped.endswith("#"):
        return stripped.rstrip("#").rstrip()
    return text


def _close_up_trailing_tags(text: str) -> str:
    """Remove the space between the text and the tags a line ends with."""
    end = len(text)
    while end and _PLACEHOLDER_BASE <= ord(text[end - 1]) <= 0xF8FF:
        end -= 1
    if end == len(text):
        return text
    return text[:end].rstrip() + text[end:]


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
        edited = _tidy(edited)
        if _readable(edited):
            kept.append(edited)
    if not changed:
        return text
    return _restore("\\N".join(kept), blocks)


def _tidy(subline: str) -> str:
    """An edited visual line without the space the edit left behind."""
    subline = _SPACES.sub(" ", subline).strip()
    subline = _SPACE_AFTER_LEADING_TAGS.sub(r"\1", subline)
    return _close_up_trailing_tags(subline)


def _keep_marks_and_tags(label: re.Match[str]) -> str:
    """What a speaker label after a voice's bracket leaves: the bracket and the tags."""
    return label.group(1) + "".join(_PLACEHOLDER.findall(label.string, label.end(1), label.end()))


def remove_hearing_impaired(text: str) -> str:
    """Remove sound descriptions in brackets and speaker labels.

    A speaker label is a name in capitals at the start of a line, or a name
    after the bracket that opens a thought or a voice in Japanese captions. A
    visual line that loses something and is left without a letter or digit is
    dropped. Lines nothing is taken from stay exactly as they are.
    """
    protected, blocks = _protect(text)
    kept: list[str] = []
    changed = False
    for subline, touched in _without_brackets(LINE_BREAK.split(protected)):
        edited = _MARKED_SPEAKER_LABEL.sub(_keep_marks_and_tags, _SPEAKER_LABEL.sub(r"\1", subline))
        if not touched and edited == subline:
            kept.append(subline)
            continue
        changed = True
        edited = _tidy(edited)
        if _readable(edited):
            kept.append(edited)
    if not changed:
        return text
    return _restore("\\N".join(kept), blocks)


def remove_symbols(text: str, *, japanese: bool = True) -> str:
    """Remove music notes, caption symbols, and hash signs used as music markers around a line.

    With `japanese`, the brackets and doubled parentheses that Japanese captions
    put around a thought, a narration or a voice off screen go as well. Their
    words stay.
    """

    def edit(subline: str) -> str:
        subline = _remove_hash_markers(_SYMBOLS.sub("", subline))
        if japanese:
            subline = _VOICE_MARKS.sub("", _without_thought_marks(subline))
        return subline

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


def reads_as_japanese(texts: Iterable[str]) -> bool:
    """Whether text is written in Japanese: a tenth or more of its letters are kana."""
    letters = kana = 0
    for text in texts:
        plain = strip_tags(text)
        letters += sum(1 for char in plain if char.isalpha())
        kana += len(_KANA.findall(plain))
    return letters > 0 and kana >= letters / 10


def strippers_for(subtitle: Subtitle) -> dict[str, Callable[[str], str]]:
    """The text strippers of a plan's `strip_text`, by name, fitted to the subtitle's language."""
    japanese = reads_as_japanese(line.text for line in subtitle.lines if not line.is_comment)
    return {
        "hearing_impaired": remove_hearing_impaired,
        "symbols": partial(remove_symbols, japanese=japanese),
        "formatting": strip_formatting,
    }
