"""Fetches the audio of the seed material."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from retimer.bench.cases import load_seeds

# The form a client uploads: one mono speech track, small enough for the upload limit.
OPUS_ARGS = ("-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip")


class SeedError(RuntimeError):
    pass


def encode_speech_audio(source: Path, destination: Path) -> None:
    """Keep one mono Opus track of `source`, in an OGG container."""
    if shutil.which("ffmpeg") is None:
        raise SeedError("ffmpeg is not installed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            *OPUS_ARGS,
            "-f",
            "ogg",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        destination.unlink(missing_ok=True)
        raise SeedError(f"ffmpeg failed: {done.stderr.strip()[-400:]}")


def fetch_seed_audio(
    seeds_dir: Path, media_dir: Path, progress: Callable[[str], None] | None = None
) -> list[Path]:
    """Download and encode the audio of every seed that names a source. Existing files stay."""
    say = progress or (lambda message: None)
    fetched = []
    for seed in load_seeds(seeds_dir).seeds:
        if seed.audio is None:
            continue
        destination = media_dir / seed.audio.file
        if destination.exists():
            say(f"{seed.id}: audio is already there")
            continue
        if not seed.audio.source_url:
            say(f"{seed.id}: no source to download, put the audio at {destination}")
            continue
        say(f"{seed.id}: downloading {seed.audio.source_url}")
        with tempfile.TemporaryDirectory() as temp:
            download = Path(temp) / "source"
            with (
                urllib.request.urlopen(seed.audio.source_url) as response,
                download.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle)
            encode_speech_audio(download, destination)
        say(f"{seed.id}: wrote {destination}")
        fetched.append(destination)
    return fetched
