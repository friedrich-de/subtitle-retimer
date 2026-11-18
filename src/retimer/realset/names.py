"""What a file name says: which episode a file is, and what kind of file it is.

Nothing here opens a file. A name is all that says which subtitle belongs to
which video, and people write episode numbers in a dozen ways, so the rules
are tried from the most explicit form to the loosest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SUBTITLE_EXTENSIONS = ("srt", "ass", "ssa")

_SEASON_EPISODE = re.compile(
    r"(?<![A-Za-z0-9])S(\d{1,2})\s?(?:E|SP)(\d{1,3})(?:v\d+)?(?![0-9])", re.IGNORECASE
)
_RANGE = re.compile(r"(?<![A-Za-z0-9])E(\d{1,3})\s?[-~]\s?E?(\d{1,3})(?![0-9A-Za-z])")
_COUNTER = re.compile(r"第\s?(\d{1,3})\s?[話回]")
_EP_SUFFIX = re.compile(r"(?<![0-9A-Za-z])(\d{1,3})_ep\d+", re.IGNORECASE)
_E_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])(?:EP?|#)\s?(\d{1,3})(?:v\d+)?(?![0-9A-Za-z])", re.IGNORECASE
)
_DASHED = re.compile(r"\s-\s?(\d{1,3})(?:\s?v\d+)?(?![0-9A-Za-z])")
_BRACKET_NUMBER = re.compile(r"\[(\d{1,3})(?:v\d+)?\]")
_LOOSE = re.compile(r"(?<![0-9A-Za-z])(\d{1,3})(?:v\d+)?(?![0-9A-Za-z])")
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
# A checksum some groups put in the name. It differs from file to file.
_CHECKSUM = re.compile(r"\[[0-9A-Fa-f]{8}\]")
# Language and caption tags between the name and the extension: ".ja", ".ja-en", ".jpn".
_TAG_SUFFIX = re.compile(r"(?:\.[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?(?:\[[a-z]+\])?)+$")
_VERSION = re.compile(r"\s?v\d+|\s(?:END|rev)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Numbering:
    """What a name says about the episode.

    `episodes` is empty when the name has no number, as a film's has, and
    holds several for a file that covers a range. `template` is the name with
    the number taken out: files that share it were numbered by the same hand.
    """

    season: int | None
    episodes: tuple[int, ...]
    template: str


def split_extension(name: str) -> tuple[str, str]:
    """The name without its subtitle extension, and the extension in lower case."""
    stem, dot, extension = name.rpartition(".")
    if dot and extension.lower() in _SUBTITLE_EXTENSIONS:
        return stem, extension.lower()
    return name, ""


def _template(name: str, start: int, end: int) -> str:
    """The name with the episode number cut out, and with what else differs per episode."""
    head = name[:start]
    tail = _CHECKSUM.sub("", name[end:])
    # Scene-style names carry the episode's title after the number. What
    # follows the source tag is the same for the whole release.
    marker = re.search(r"\.(?:WEB|BD|TV|HDTV|DVD)", tail, re.IGNORECASE)
    if tail.startswith(".") and marker:
        tail = tail[marker.start() :]
    return _VERSION.sub("", f"{head}{{}}{tail}").strip()


def parse_numbering(name: str) -> Numbering:
    """Read the season and the episode, or the range of episodes, off a file name."""
    found = _SEASON_EPISODE.search(name)
    if found:
        # The season stays in the template: "S02E01" is not numbered like "S01E01".
        template = _template(name, found.start(2), found.end(2))
        return Numbering(int(found[1]), (int(found[2]),), template)
    found = _RANGE.search(name)
    if found and int(found[1]) < int(found[2]):
        episodes = tuple(range(int(found[1]), int(found[2]) + 1))
        return Numbering(None, episodes, _template(name, found.start(1), found.end(2)))
    for pattern in (_COUNTER, _EP_SUFFIX, _E_NUMBER, _DASHED, _BRACKET_NUMBER):
        found = pattern.search(name)
        if found:
            template = _template(name, found.start(1), found.end(1))
            return Numbering(None, (int(found[1]),), template)
    # The loosest reading: the last number that stands by itself, outside of
    # brackets, where groups put the resolution and the codec.
    stem, extension = split_extension(name)
    stem = _TAG_SUFFIX.sub("", stem)
    blanked = _BRACKETS.sub(lambda group: " " * len(group[0]), stem)
    numbers = list(_LOOSE.finditer(blanked))
    if numbers:
        last = numbers[-1]
        template = _template(name, last.start(1), last.end(1))
        return Numbering(None, (int(last[1]),), template)
    return Numbering(None, (), _CHECKSUM.sub("", name).strip())


# The kinds of target file the report tells apart. A file is of exactly one
# kind, the first in this order whose rule holds.
FILE_TYPES = (
    "TV pack",
    "furigana",
    "bilingual",
    "Netflix",
    "Amazon",
    "AT-X",
    "WOWOW",
    "TV (station not named)",
    "web captions (service not named)",
    "fansub ASS",
    "fansub SRT",
)

_FURIGANA = re.compile(r"furigana|ふりがな|振り仮名|ルビ", re.IGNORECASE)
_LANGUAGE = r"(?:JPN?|JAP?|JA|CH[ST]|ZH|ENG?|EN|KOR?)"
_TWO_LANGUAGES = re.compile(
    rf"\[{_LANGUAGE}\s?[,&+]\s?{_LANGUAGE}\]|\.{_LANGUAGE}-{_LANGUAGE}\.[a-z]+$", re.IGNORECASE
)
_STATION = {
    "Netflix": re.compile(r"netflix", re.IGNORECASE),
    "Amazon": re.compile(r"amazon|amzn", re.IGNORECASE),
    "AT-X": re.compile(r"AT-?X(?![a-z])", re.IGNORECASE),
    "WOWOW": re.compile(r"wowow", re.IGNORECASE),
}
_TV = re.compile(r"\[TV\]|\(TV\)|(?<![A-Za-z])(?:TVRip|HDTV|BS11|TOKYO\s?MX|NHK)(?![A-Za-z])")
# The dotted names of files taken straight off a streaming service.
_WEB = re.compile(r"\.WEB-?(?:Rip|DL)\.", re.IGNORECASE)


def file_type(name: str, episodes: int = 1, two_scripts: bool = False) -> str:
    """The kind of a target file, from its name.

    `episodes` is how many episodes the name covers. `two_scripts` says that
    the lines themselves are in two languages, for files whose name does not
    say so.
    """
    if episodes > 1:
        return "TV pack"
    if _FURIGANA.search(name):
        return "furigana"
    if two_scripts or _TWO_LANGUAGES.search(name):
        return "bilingual"
    for kind, pattern in _STATION.items():
        if pattern.search(name):
            return kind
    if _TV.search(name):
        return "TV (station not named)"
    if _WEB.search(name):
        return "web captions (service not named)"
    return "fansub ASS" if split_extension(name)[1] in ("ass", "ssa") else "fansub SRT"


def is_broadcast(kind: str) -> bool:
    """Whether a kind of file is captions taken off a television broadcast."""
    return kind in ("AT-X", "WOWOW", "TV (station not named)", "TV pack")
