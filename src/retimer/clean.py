"""Text-level cleaning: hearing-impaired text, symbols, formatting.

Every function takes and returns ASS-form text. A function returns an empty string
when nothing readable is left, which tells the caller to drop the line.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, NamedTuple

from retimer.text import LINE_BREAK, OVERRIDE_BLOCK, is_drawing, strip_tags

if TYPE_CHECKING:
    from retimer.model import Line, Subtitle

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
_ROUND = "()（）"
_DOUBLED = ("((", "（（", "))", "））")
# A Netflix caption puts the reading of a word right after it: 演奏(えんそう),
# 滅却師(クインシー). It is part of a line, not a bracket of its own.
_READING = re.compile("[\u3041-\u309f\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f]{1,12}")
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
# Where a line is placed: {\pos(x,y)}.
_POSITION = re.compile(r"\\pos\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
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
    f"^((?:{_PLACEHOLDER_CLASS}|\\s)*(?:[{_VOICE_MARK_OPENERS}]|\\(\\(|（（)"
    f"(?:{_PLACEHOLDER_CLASS}|[ \\u3000])*)"
    f"(?![0-9０-９]+[:：]){_NAME_CHAR}{{1,12}}{_PLACEHOLDER_CLASS}*[:：]"
    f"(?:{_PLACEHOLDER_CLASS}|[ \\u3000])*"
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


def _is_reading(text: str, opener: int, closer: int) -> bool:
    """Whether the brackets at these positions hold the reading of the word before them."""
    return (
        opener > 0
        and text[opener - 1].isalnum()
        and _READING.fullmatch(text, opener + 1, closer) is not None
    )


def _plain_openers(text: str, pairs: dict[int, int]) -> list[int]:
    """How many opening brackets before each position, readings not counted."""
    readings = {opener for opener, closer in pairs.items() if _is_reading(text, opener, closer)}
    before = [0]
    for position, char in enumerate(text):
        before.append(before[-1] + (char in _OPENERS and position not in readings))
    return before


def _paired_positions(text: str) -> set[int]:
    """Positions of the brackets that pair in the text, both halves of doubled parentheses."""
    used: set[int] = set()
    for opener, closer in _bracket_pairs(text).items():
        width = 2 if text[opener : opener + 2] in _THOUGHT_CLOSERS else 1
        used.update(range(opener, opener + width))
        used.update(range(closer, closer + width))
    return used


def _stray_round_brackets(text: str) -> set[int]:
    """Positions of single round brackets that pair with nothing in the text.

    Captions sometimes open a thought with doubled parentheses and close it
    with a single one, and a stretch may run over several subtitles. Doubled
    parentheses on their own are left to the thought marks.
    """
    used = _paired_positions(text)
    strays: set[int] = set()
    position = 0
    while position < len(text):
        if position not in used and text[position : position + 2] in _DOUBLED:
            position += 2
            continue
        if text[position] in _ROUND and position not in used:
            strays.add(position)
        position += 1
    return strays


def _without_stray_brackets(sublines: list[str]) -> list[str]:
    """The visual lines of one subtitle without the round brackets that pair with nothing.

    A subtitle with nothing readable besides such brackets is left as it is:
    it is a piece of a caption whose other pieces are lines of their own.
    """
    text = _BREAK.join(sublines)
    strays = _stray_round_brackets(text)
    if not strays:
        return sublines
    kept = "".join(char for position, char in enumerate(text) if position not in strays)
    edited = kept.split(_BREAK)
    if not _readable(kept) or len(edited) != len(sublines):
        return sublines
    return edited


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
    for char in text:
        breaks_before.append(breaks_before[-1] + (char == _BREAK))
    openers_before = _plain_openers(text, pairs)
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
            # The reading of a word is not another bracket.
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


def _edit_sublines(
    text: str,
    edit: Callable[[str], str],
    prepare: Callable[[list[str]], list[str]] | None = None,
) -> str:
    """Apply `edit` to each visual line.

    `prepare` may first change the visual lines of the subtitle together. A
    visual line that is changed and left without a letter or digit is
    dropped. Lines nothing changes are kept exactly as they are, so a line such
    as "..." survives.
    """
    protected, blocks = _protect(text)
    originals = LINE_BREAK.split(protected)
    sublines = prepare(originals) if prepare else originals
    kept: list[str] = []
    changed = False
    for original, subline in zip(originals, sublines, strict=True):
        edited = edit(subline)
        if edited == original:
            kept.append(original)
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


def remove_symbols(text: str, *, japanese: bool = True, stray_brackets: bool = True) -> str:
    """Remove music notes, caption symbols, and hash signs used as music markers around a line.

    With `japanese`, the brackets and doubled parentheses that Japanese captions
    put around a thought, a narration or a voice off screen go as well. Their
    words stay. With `stray_brackets` too, so do round brackets that pair with
    nothing in the subtitle: a thought opened with (( and closed with a single
    ), or a stretch that runs over several subtitles.
    """

    def edit(subline: str) -> str:
        subline = _remove_hash_markers(_SYMBOLS.sub("", subline))
        if japanese:
            subline = _VOICE_MARKS.sub("", _without_thought_marks(subline))
        return subline

    prepare = _without_stray_brackets if japanese and stray_brackets else None
    return _edit_sublines(text, edit, prepare)


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


def _shown_together(lines: Iterable[Line]) -> list[list[Line]]:
    """Lines on screen over exactly the same time, two or more, in reading order.

    Lines that are all placed with \\pos are read top to bottom and left to
    right, others in the order of the file.
    """
    groups: defaultdict[tuple[int, int], list[Line]] = defaultdict(list)
    for line in lines:
        if not line.is_comment and not is_drawing(line.text):
            groups[(line.start_ms, line.end_ms)].append(line)
    together = []
    for group in groups.values():
        if len(group) < 2:
            continue
        places = [_POSITION.search("".join(OVERRIDE_BLOCK.findall(line.text))) for line in group]
        if all(place is not None for place in places):
            rows = [(float(place[2]), float(place[1])) for place in places if place is not None]
            group = [
                line for _, _, line in sorted(zip(rows, range(len(group)), group, strict=True))
            ]
        together.append(group)
    return together


class _SplitCaption(NamedTuple):
    """Where a caption puts a speaker's name or a sound in lines apart from its brackets."""

    # Lines wholly inside such brackets.
    inside: set[int]
    # Lines that hold the closer with words after it, or the opener with words before it.
    end_with_closer: set[int]
    start_with_opener: set[int]
    # Whether any brackets open and close in different lines shown together.
    found: bool


def _split_stretches(lines: Sequence[Line]) -> _SplitCaption:
    """Brackets that open and close in different lines shown together.

    Some broadcast captions put each piece of a caption in a line of its own,
    with the brackets of a speaker's name or a sound apart: （ / 少女 / ）.
    """
    inside: set[int] = set()
    end_with_closer: set[int] = set()
    start_with_opener: set[int] = set()
    found = False
    for group in _shown_together(lines):
        text = ""
        starts: list[int] = []
        spans: list[tuple[int, int]] = []
        for line in group:
            starts.append(len(text))
            text += LINE_BREAK.sub(" ", strip_tags(line.text))
            spans.append((starts[-1], len(text)))
            text += _BREAK
        pairs = _bracket_pairs(text)
        openers_before: list[int] | None = None
        for opener, closer in pairs.items():
            if text[opener : opener + 2] in _THOUGHT_CLOSERS:
                continue
            first = bisect_right(starts, opener) - 1
            last = bisect_right(starts, closer) - 1
            if first == last:
                continue
            found = True
            if openers_before is None:
                openers_before = _plain_openers(text, pairs)
            if openers_before[closer] - openers_before[opener + 1] > 0:
                continue
            for index in range(first, last + 1):
                start, end = spans[index]
                line_id = group[index].id
                if index == first and text[start:opener].strip():
                    start_with_opener.add(line_id)
                elif index == last and text[closer + 1 : end].strip():
                    end_with_closer.add(line_id)
                else:
                    inside.add(line_id)
    return _SplitCaption(inside, end_with_closer, start_with_opener, found)


def _cut_at_lone_bracket(text: str, *, through_closer: bool, from_opener: bool) -> str:
    """A line without what lies before its first lone closer, or after its last lone opener.

    The tags stay. A lone bracket is one that pairs with nothing in the line.
    """
    if not (through_closer or from_opener):
        return text
    protected, blocks = _protect(text)
    paired = _paired_positions(protected)
    closers = [i for i, char in enumerate(protected) if char in _CLOSERS and i not in paired]
    openers = [i for i, char in enumerate(protected) if char in _OPENERS and i not in paired]
    cut = [False] * len(protected)
    if through_closer and closers:
        cut[: closers[0] + 1] = [True] * (closers[0] + 1)
    if from_opener and openers:
        cut[openers[-1] :] = [True] * (len(protected) - openers[-1])
    if not any(cut):
        return text
    kept = "".join(
        char
        for char, gone in zip(protected, cut, strict=True)
        if not gone or _PLACEHOLDER.match(char)
    )
    return _restore("\\N".join(_tidy(subline) for subline in LINE_BREAK.split(kept)), blocks)


Stripper = Callable[[int, str], str]


def strippers_for(subtitle: Subtitle) -> dict[str, Stripper]:
    """The text strippers of a plan's `strip_text`, by name, fitted to the subtitle.

    Each takes the id and the text of a line. Symbols are removed according
    to the language. Where a caption puts a speaker's name or a sound in lines
    apart from its brackets, the hearing-impaired stripper empties the lines
    inside the brackets and cuts the rest from the lines that hold them. In
    such a caption a bracket on its own pairs with one in another line, so no
    bracket counts as stray.
    """
    lines = [line for line in subtitle.lines if not line.is_comment]
    japanese = reads_as_japanese(line.text for line in lines)
    split = _split_stretches(lines)

    def hearing_impaired(line_id: int, text: str) -> str:
        if line_id in split.inside:
            return ""
        text = _cut_at_lone_bracket(
            text,
            through_closer=line_id in split.end_with_closer,
            from_opener=line_id in split.start_with_opener,
        )
        return remove_hearing_impaired(text)

    def symbols(_: int, text: str) -> str:
        return remove_symbols(text, japanese=japanese, stray_brackets=japanese and not split.found)

    return {
        "hearing_impaired": hearing_impaired,
        "symbols": symbols,
        "formatting": lambda _, text: strip_formatting(text),
    }
