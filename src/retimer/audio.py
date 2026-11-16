"""Audio preparation with ffmpeg: probe, find quiet spots, cut into chunks."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Mono Opus at 24 kbps: the form the client uploads and the provider accepts.
SPEECH_CODEC_ARGS = ("-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip")

_SILENCE_END = re.compile(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)")
_SILENCE_NOISE = "-35dB"
_SILENCE_MIN_S = 0.35


class AudioError(RuntimeError):
    """The audio could not be read or converted."""


@dataclass(frozen=True)
class Chunk:
    path: Path
    offset_ms: int
    duration_ms: int


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise AudioError(f"{tool} is not installed")
    return path


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
    if done.returncode != 0:
        raise AudioError(f"{Path(command[0]).name} failed: {done.stderr.strip()[-400:]}")
    return done


def probe_duration_ms(path: Path) -> int:
    done = _run(
        [
            _require("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    try:
        return round(float(done.stdout.strip()) * 1000)
    except ValueError as error:
        raise AudioError("the file has no readable duration; is it audio?") from error


def find_silences(path: Path) -> list[tuple[int, int]]:
    """Quiet stretches as (start_ms, end_ms), from the signal level alone."""
    done = _run(
        [
            _require("ffmpeg"),
            "-hide_banner",
            "-nostats",
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
