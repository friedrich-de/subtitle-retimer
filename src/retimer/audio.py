"""Audio preparation with ffmpeg: probe, find quiet spots, cut into chunks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Mono Opus at 24 kbps: the form the client uploads and the provider accepts.
SPEECH_CODEC_ARGS = ("-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip")

# The containers audio is accepted in. ffmpeg also reads playlists and scripts
# that name other files or addresses, and an upload must never get it to open
# anything but itself. So every input is held to these containers and to local
# files, whatever it claims to be.
ALLOWED_CONTAINERS = ("ogg", "matroska", "webm", "mp3", "mov", "mp4", "m4a", "flac", "wav", "aac")
INPUT_GUARD_ARGS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    ",".join(ALLOWED_CONTAINERS),
)
# No step on one file takes this long unless something is wrong with the file.
TOOL_TIMEOUT_S = 900.0

_SILENCE_END = re.compile(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)")
_SILENCE_NOISE = "-35dB"
_SILENCE_MIN_S = 0.35


class AudioError(RuntimeError):
    """The audio could not be read or converted."""


class ToolMissing(AudioError):
    """ffmpeg is not installed. That is the machine's fault and not the file's."""


@dataclass(frozen=True)
class Chunk:
    path: Path
    offset_ms: int
    duration_ms: int


@dataclass(frozen=True)
class AudioInfo:
    container: str
    codec: str
    duration_ms: int


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise ToolMissing(f"{tool} is not installed")
    return path


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    tool = Path(command[0]).name
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=TOOL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise AudioError(f"{tool} did not finish in {TOOL_TIMEOUT_S:.0f} s") from error
    if done.returncode != 0:
        raise AudioError(f"{tool} failed: {done.stderr.strip()[-400:]}")
    return done


def probe_audio(path: Path) -> AudioInfo:
    """What the file is, if it is audio in a container that is accepted."""
    done = _run(
        [
            _require("ffprobe"),
            "-v",
            "error",
            *INPUT_GUARD_ARGS,
            "-show_entries",
            "format=format_name,duration:stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        found = json.loads(done.stdout)
        container = str(found["format"]["format_name"])
        duration_ms = round(float(found["format"]["duration"]) * 1000)
    except (ValueError, KeyError, TypeError) as error:
        raise AudioError("the file has no readable duration; is it audio?") from error
    codecs = [
        str(stream.get("codec_name", ""))
        for stream in found.get("streams", [])
        if stream.get("codec_type") == "audio"
    ]
    if not codecs:
        raise AudioError("the file has no audio track")
    if duration_ms <= 0:
        raise AudioError("the file has no readable duration; is it audio?")
    return AudioInfo(container=container, codec=codecs[0], duration_ms=duration_ms)


def probe_duration_ms(path: Path) -> int:
    return probe_audio(path).duration_ms


def find_silences(path: Path) -> list[tuple[int, int]]:
    """Quiet stretches as (start_ms, end_ms), from the signal level alone."""
    done = _run(
        [
            _require("ffmpeg"),
            "-hide_banner",
            "-nostats",
            *INPUT_GUARD_ARGS,
            "-i",
            str(path),
            "-vn",
            "-af",
            f"silencedetect=noise={_SILENCE_NOISE}:d={_SILENCE_MIN_S}",
            "-f",
            "null",
            "-",
        ]
    )
    silences = []
    for match in _SILENCE_END.finditer(done.stderr):
        end = float(match.group(1))
        duration = float(match.group(2))
        silences.append((round((end - duration) * 1000), round(end * 1000)))
    return silences


def choose_cut_points(
    duration_ms: int, silences: list[tuple[int, int]], max_chunk_ms: int
) -> list[int]:
    """Where to cut so that no chunk is longer than `max_chunk_ms`.

    Each cut goes to the middle of the longest quiet stretch in the last third
    of the allowed span, so that a cut rarely lands inside a word. Without a
    quiet stretch the cut is made at the limit.
    """
    cuts = []
    position = 0
    while duration_ms - position > max_chunk_ms:
        limit = position + max_chunk_ms
        earliest = position + (max_chunk_ms * 2) // 3
        candidates = [
            (end - start, (start + end) // 2)
            for start, end in silences
            if earliest <= (start + end) // 2 <= limit
        ]
        position = max(candidates)[1] if candidates else limit
        cuts.append(position)
    return cuts


def split_audio(path: Path, work_dir: Path, max_chunk_ms: int) -> list[Chunk]:
    """Cut the audio into chunks of at most `max_chunk_ms`, each re-encoded as speech audio.

    A file that fits in one chunk is still re-encoded, so that whatever the
    client sent reaches the provider in the one form that was tested.
    """
    duration_ms = probe_duration_ms(path)
    silences = find_silences(path) if duration_ms > max_chunk_ms else []
    bounds = [0, *choose_cut_points(duration_ms, silences, max_chunk_ms), duration_ms]
    work_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, (start, end) in enumerate(zip(bounds, bounds[1:], strict=False)):
        target = work_dir / f"chunk-{index:03}.ogg"
        _run(
            [
                _require("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-ss",
                f"{start / 1000:.3f}",
                "-t",
                f"{(end - start) / 1000:.3f}",
                *INPUT_GUARD_ARGS,
                "-i",
                str(path),
                *SPEECH_CODEC_ARGS,
                "-f",
                "ogg",
                str(target),
            ]
        )
        chunks.append(Chunk(path=target, offset_ms=start, duration_ms=end - start))
    return chunks
