"""Takes the subtitle tracks and the audio out of a video.

A video is only ever read. Every text subtitle track is copied out as it is,
and one audio track is encoded the way a client uploads it: mono Opus at
24 kbit/s. Image subtitles are counted and left where they are.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from retimer.bench.seeds import OPUS_ARGS
from retimer.io import SubtitleError, load
from retimer.model import Subtitle
from retimer.text import has_karaoke_tags, is_drawing, is_positioned, to_plain

# The codec of a text subtitle stream, and the extension it is written with.
TEXT_CODECS = {"ass": "ass", "ssa": "ass", "subrip": "srt", "mov_text": "srt", "webvtt": "srt"}
_JAPANESE = ("jpn", "ja", "jp", "japanese")
_ENGLISH = ("eng", "en", "english")


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stream:
    index: int
    kind: str
    codec: str
    language: str | None
    title: str | None
    # What the file says a player should do with the stream by itself.
    default: bool = False
    forced: bool = False


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaError(f"{name} is not installed")
    return path


def probe(video: Path) -> tuple[float, list[Stream]]:
    """The length of a video in seconds, and its audio and subtitle streams."""
    done = subprocess.run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name"
            ":stream_tags=language,title:stream_disposition=default,forced",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise MediaError(f"ffprobe failed: {done.stderr.strip()[-300:]}")
    return read_probe(json.loads(done.stdout))


def read_probe(body: dict) -> tuple[float, list[Stream]]:
    streams = []
    for stream in body.get("streams", []):
        if stream.get("codec_type") not in ("audio", "subtitle"):
            continue
        tags = {key.lower(): value for key, value in (stream.get("tags") or {}).items()}
        streams.append(
            Stream(
                index=int(stream["index"]),
                kind=stream["codec_type"],
                codec=stream.get("codec_name") or "",
                language=tags.get("language") or None,
                title=tags.get("title") or None,
                default=bool((stream.get("disposition") or {}).get("default")),
                forced=bool((stream.get("disposition") or {}).get("forced")),
            )
        )
    return float((body.get("format") or {}).get("duration") or 0.0), streams


def is_english(language: str | None) -> bool:
    """Whether a language tag says English, or says nothing, as many releases do."""
    return not language or language.lower() in (*_ENGLISH, "und")


def pick_audio(streams: list[Stream]) -> Stream | None:
    """The audio track a Japanese subtitle belongs to.

    The one tagged Japanese. Where no track says so, the first that is not
    tagged English, and failing that the first.
    """
    audio = [stream for stream in streams if stream.kind == "audio"]
    for stream in audio:
        if (stream.language or "").lower() in _JAPANESE:
            return stream
    for stream in audio:
        if (stream.language or "").lower() not in _ENGLISH:
            return stream
    return audio[0] if audio else None


def text_tracks(streams: list[Stream]) -> list[Stream]:
    return [
        stream for stream in streams if stream.kind == "subtitle" and stream.codec in TEXT_CODECS
    ]


def extract(
    video: Path, tracks: list[tuple[Stream, Path]], audio: tuple[Stream, Path] | None
) -> None:
    """Write the given tracks and the audio in one pass over the video.

    Each file is written under a temporary name first, so one that exists is whole.
    """
    command = [_tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(video)]
    moves: list[tuple[Path, Path]] = []
    for stream, path in tracks:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f".part-{path.name}")
        # ASS and SRT are copied as they are. Other text formats are written as SRT.
        codec = "copy" if stream.codec in ("ass", "ssa", "subrip") else "srt"
        command += ["-map", f"0:{stream.index}", "-c:s", codec, "-f", path.suffix[1:], str(part)]
        moves.append((part, path))
    if audio is not None:
        stream, path = audio
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f".part-{path.name}")
        command += ["-map", f"0:{stream.index}", *OPUS_ARGS, "-f", "ogg", str(part)]
        moves.append((part, path))
    if not moves:
        return
    done = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
    if done.returncode != 0:
        for part, _ in moves:
            part.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg failed: {done.stderr.strip()[-400:]}")
    for part, path in moves:
        part.replace(path)


# A line that is nothing but a description in brackets: "[door slams]", "(sighs)".
_DESCRIPTION = re.compile(r"^[-–—\s]*[\[(（［][^\])）］]*[\])）］]\s*$")


def count_spoken(subtitle: Subtitle) -> tuple[int, int]:
    """How many lines of a track read like speech, and how many of those describe a sound.

    Speech is timed text that is not positioned on the picture, not drawn and
    not sung. Lines that start together count once, since a line with a
    border or a glow is written several times. The counts are rough on
    purpose: they only have to tell a full track from a track of signs and
    songs, and captions for the hard of hearing from plain subtitles.
    """
    starts: set[int] = set()
    described: set[int] = set()
    for line in subtitle.lines:
        if line.is_comment or line.duration_ms <= 0:
            continue
        if is_positioned(line.text) or is_drawing(line.text) or has_karaoke_tags(line.text):
            continue
        plain = to_plain(line.text)
        if any(char.isalnum() for char in plain):
            starts.add(line.start_ms)
            if all(_DESCRIPTION.match(row) for row in plain.split("\n")):
                described.add(line.start_ms)
    return len(starts), len(described)


def track_counts(path: Path) -> tuple[int, int, int]:
    """The lines of a track: all of them, those that read like speech, those describing a sound."""
    try:
        subtitle = load(path)
    except (SubtitleError, OSError):
        return 0, 0, 0
    return (len(subtitle), *count_spoken(subtitle))


def _is_kana(char: str) -> bool:
    return "぀" <= char <= "ヿ"


def _is_han(char: str) -> bool:
    return "一" <= char <= "鿿"


def two_scripts(subtitle: Subtitle) -> bool:
    """Whether a Japanese subtitle also carries its lines in another language.

    Either as lines of their own, or as a second row in the same line. A
    Japanese line nearly always has kana in it. A line without any, made of
    Latin letters or of Chinese characters alone, is the other language.
    """
    japanese = other = both = total = 0
    for line in subtitle.lines:
        if line.is_comment:
            continue
        plain = to_plain(line.text)
        if not any(char.isalnum() for char in plain):
            continue
        total += 1
        kana = any(_is_kana(char) for char in plain)
        latin = sum(1 for char in plain if char.isascii() and char.isalpha())
        han = sum(1 for char in plain if _is_han(char))
        if kana and latin >= 8:
            both += 1
        elif kana:
            japanese += 1
        elif latin >= 4 or han >= 2:
            other += 1
    if not total:
        return False
    return both / total >= 0.2 or (japanese / total >= 0.2 and other / total >= 0.2)
