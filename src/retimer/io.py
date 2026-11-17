"""Reading and writing subtitle files. SRT and ASS are supported."""

from __future__ import annotations

import codecs
import re
from pathlib import Path

import charset_normalizer
import pysubs2

from retimer.model import Subtitle

SUPPORTED_FORMATS = ("srt", "ass")

# SSA is the predecessor of ASS and parses into the same model. It is written back as ASS.
_FORMAT_ALIASES = {"ssa": "ass"}


class SubtitleError(ValueError):
    """The input is not a subtitle file this service can read or write."""


# Japanese is a priority language and the general detector confuses its legacy
# encodings with Korean ones, so they are tried first and recognized by hiragana.
_JAPANESE_ENCODINGS = ("cp932", "euc_jp")
_MIN_HIRAGANA_SHARE = 0.15
# The general detector cannot tell the Windows Latin code pages apart. These two
# cover most legacy subtitle files, and Western European wins a tie.
_LATIN_ENCODINGS = ("cp1252", "cp1250")
_LATIN_LIMIT = 0x024F


def _non_ascii(text: str) -> list[str]:
    return [char for char in text if ord(char) > 0x7F]


def _hiragana_share(text: str) -> float:
    chars = _non_ascii(text)
    if not chars:
        return 0.0
    return sum(1 for char in chars if 0x3040 <= ord(char) <= 0x309F) / len(chars)


def _is_latin_script(text: str) -> bool:
    letters = [char for char in _non_ascii(text) if char.isalpha()]
    latin = sum(1 for char in letters if ord(char) <= _LATIN_LIMIT)
    return not letters or latin / len(letters) >= 0.5


def _decode_latin(data: bytes) -> tuple[str, str]:
    matches = charset_normalizer.from_bytes(data, cp_isolation=list(_LATIN_ENCODINGS))
    ranked = sorted(
        matches,
        key=lambda m: (m.chaos, -m.coherence, _LATIN_ENCODINGS.index(m.encoding)),
    )
    if ranked:
        return str(ranked[0]), ranked[0].encoding
    return data.decode("cp1252", errors="replace"), "cp1252"


def decode(data: bytes) -> tuple[str, str]:
    """Decode subtitle bytes and return the text with the encoding that was used.

    UTF-8 and UTF-16 are recognized exactly. Legacy encodings are a best effort:
    Japanese first, then other scripts through the general detector, then the two
    common Windows Latin code pages. Some Central European text reads equally
    well in both code pages and is then taken as Western European.
    """
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for encoding in _JAPANESE_ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _hiragana_share(text) >= _MIN_HIRAGANA_SHARE:
            return text, encoding
    best = charset_normalizer.from_bytes(data).best()
    if best is not None and not _is_latin_script(str(best)):
        return str(best), best.encoding
    return _decode_latin(data)


# The parser tells the format from the first few thousand characters. A file
# that begins with something long, such as fonts a muxer wrote into the header,
# has its first line of dialogue far behind that, so the whole text is looked at.
# The text is a stranger's and can be megabytes of anything, so a mark is looked
# for within one line: space that ran on over line breaks would be gone through
# again from every blank line of a file that has thousands of them.
_ASS_MARK = re.compile(r"^\[Events\]\s*$|^Dialogue:\s*\d", re.MULTILINE | re.IGNORECASE)
_SRT_MARK = re.compile(
    r"^[^\S\n]*\d+:\d+:\d+[,.]\d+[^\S\n]*-->[^\S\n]*\d+:\d+:\d+[,.]\d+", re.MULTILINE
)


def _sniff_format(text: str) -> str | None:
    if _ASS_MARK.search(text):
        return "ass"
    if _SRT_MARK.search(text):
        return "srt"
    return None


def loads(text: str, encoding: str = "utf-8") -> Subtitle:
    """Parse subtitle text. The format is detected from the content."""
    try:
        try:
            ssa = pysubs2.SSAFile.from_string(text)
        except pysubs2.exceptions.FormatAutodetectionError:
            fmt = _sniff_format(text)
            if fmt is None:
                raise
            ssa = pysubs2.SSAFile.from_string(text, format_=fmt)
    except pysubs2.exceptions.Pysubs2Error as exc:
        raise SubtitleError("the file is not a recognizable subtitle format") from exc
    except ValueError as exc:
        # The parser gives up on a time such as "0:-39:-28.91".
        raise SubtitleError("the file has lines with times that cannot be read") from exc
    fmt = _FORMAT_ALIASES.get(ssa.format, ssa.format)
    if fmt not in SUPPORTED_FORMATS:
        raise SubtitleError(f"unsupported subtitle format: {ssa.format}")
    if not ssa.events:
        raise SubtitleError("the file contains no subtitle lines")
    return Subtitle(ssa, fmt, encoding)


def load_bytes(data: bytes) -> Subtitle:
    try:
        text, encoding = decode(data)
    except UnicodeDecodeError as error:
        raise SubtitleError("the text of the file could not be decoded") from error
    return loads(text, encoding)


def load(path: str | Path) -> Subtitle:
    return load_bytes(Path(path).read_bytes())


def dumps(subtitle: Subtitle, fmt: str | None = None) -> str:
    """Serialize a subtitle. The format defaults to the one it was loaded from."""
    fmt = fmt or subtitle.format
    if fmt not in SUPPORTED_FORMATS:
        raise SubtitleError(f"unsupported output format: {fmt}")
    return subtitle.ssa.to_string(fmt)


def save(subtitle: Subtitle, path: str | Path, fmt: str | None = None) -> None:
    """Write a subtitle as UTF-8."""
    Path(path).write_text(dumps(subtitle, fmt), encoding="utf-8")
