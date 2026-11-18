"""The manifest of a real-file set: its videos, tracks, targets and pairs.

The manifest lives next to the data and never in the repository, because it
names the releases the set was built from. Everything else in the kit reads it
and nothing else, so a set can be moved, or built from other videos, without a
change to the code.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

MANIFEST_FILE = "manifest.json"
OVERRIDES_FILE = "overrides.json"
LISTING_FILE = "jimaku/listing.json"
TRACKS_DIR = "tracks"
AUDIO_DIR = "audio"
TARGETS_DIR = "targets"
CACHE_DIR = "cache"
RUNS_DIR = "runs"
TUNING_FILE = "tuning.json"
# Tracks are kept packed. What is unpacked for a run goes here and can be deleted at any time.
PLAIN_DIR = ".plain"
PACKED = ".xz"

# What a track of a video is, judged by its lines and not by its title:
# the one full track the set syncs against, another full track in the same
# language, a full track in another language, a track of signs and songs, or
# a track that was found to hold another episode.
Role = Literal["main", "full", "foreign", "signs", "wrong_episode"]
PairKind = Literal["main", "other_track", "wrong"]
# `placed`: the target belongs to the reference and should end up in sync.
# `refused`: it does not, and a system should say so or leave the file alone.
Expected = Literal["placed", "refused"]


class AudioFile(BaseModel):
    file: str
    stream: int
    language: str | None = None
    sha256: str
    bytes: int


class Track(BaseModel):
    id: str
    stream: int
    codec: str
    language: str | None = None
    title: str | None = None
    # The flags of the stream in the video: shown unless another is picked, and
    # shown even with subtitles off.
    default: bool = False
    forced: bool = False
    # The packed file. The hash and the size are those of the track itself.
    file: str
    sha256: str
    bytes: int
    events: int = 0
    # Lines that read like speech: timed text that is not positioned, drawn or sung.
    spoken: int = 0
    # Of those, the lines that are nothing but a sound described in brackets.
    described: int = 0
    role: Role = "signs"
    note: str | None = None


class Video(BaseModel):
    id: str
    # Path below the videos directory the set was built from.
    source: str
    folder: str
    season: int | None = None
    # The episode number as the file name gives it, and the place among the
    # regular episodes of its folder, counted from one.
    number: int | None = None
    ordinal: int | None = None
    duration_s: float = 0.0
    audio: AudioFile | None = None
    tracks: list[Track] = Field(default_factory=list)
    image_tracks: int = 0

    def track(self, role: Role = "main") -> Track | None:
        return next((track for track in self.tracks if track.role == role), None)


class Target(BaseModel):
    id: str
    entry: int
    name: str
    file: str
    sha256: str
    bytes: int
    format: str
    season: int | None = None
    episodes: list[int] = Field(default_factory=list)
    type: str
    # The videos it was paired with: one, or for a file of several episodes
    # the first and the last of them.
    videos: list[str] = Field(default_factory=list)
    # Other names the same bytes are shared under.
    also: list[str] = Field(default_factory=list)
    note: str | None = None


class SkippedFile(BaseModel):
    entry: int
    name: str
    reason: str


class Pair(BaseModel):
    id: str
    kind: PairKind
    target: str
    video: str
    track: str
    expected: Expected
    # One of the fixed tenth of the main pairs that settings are chosen on.
    tuning: bool = False
    note: str | None = None


class Manifest(BaseModel):
    version: int = 1
    built: str = ""
    videos: list[Video] = Field(default_factory=list)
    targets: list[Target] = Field(default_factory=list)
    skipped: list[SkippedFile] = Field(default_factory=list)
    pairs: list[Pair] = Field(default_factory=list)
    _indexes: dict = PrivateAttr(default_factory=dict)

    def video(self, video_id: str) -> Video:
        return self._index("videos")[video_id]

    def target(self, target_id: str) -> Target:
        return self._index("targets")[target_id]

    def track(self, track_id: str) -> Track:
        return self._index("tracks")[track_id]

    def _index(self, what: str) -> dict:
        cached = self._indexes
        if what not in cached:
            if what == "tracks":
                items = [track for video in self.videos for track in video.tracks]
            else:
                items = getattr(self, what)
            cached[what] = {item.id: item for item in items}
        return cached[what]


class FolderOverride(BaseModel):
    """What a folder of videos is, where its name does not begin with the AniList id."""

    anilist_id: int


class TargetOverride(BaseModel):
    """A correction for the files of one entry whose name contains a text.

    `episode_offset` is added to the episode the name gives. `skip` leaves
    the files out of the pairs and says why.
    """

    entry: int
    name_contains: str = ""
    episode_offset: int = 0
    skip: str | None = None
    reason: str | None = None


class TrackOverride(BaseModel):
    """The role of one track, where the lines alone do not tell it."""

    video_contains: str
    stream: int
    role: Role
    reason: str | None = None


class Overrides(BaseModel):
    """What was learned about a set by hand. It stays with the set."""

    folders: dict[str, FolderOverride] = Field(default_factory=dict)
    targets: list[TargetOverride] = Field(default_factory=list)
    tracks: list[TrackOverride] = Field(default_factory=list)
    # What else is worth knowing about the set and needs no correction.
    notes: list[str] = Field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def short_hash(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def load_manifest(root: Path) -> Manifest:
    return Manifest.model_validate_json((root / MANIFEST_FILE).read_text(encoding="utf-8"))


def save_manifest(root: Path, manifest: Manifest) -> None:
    text = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=1)
    (root / MANIFEST_FILE).write_text(text + "\n", encoding="utf-8")


def load_overrides(path: Path) -> Overrides:
    if not path.exists():
        return Overrides()
    return Overrides.model_validate_json(path.read_text(encoding="utf-8"))


def pack(path: Path) -> Path:
    """Pack a file in place and return the packed file.

    A track with karaoke effects is tens of megabytes of nearly the same
    line, and packs to a fiftieth.
    """
    packed = path.with_name(path.name + PACKED)
    part = packed.with_name(f".part-{packed.name}")
    with path.open("rb") as source, lzma.open(part, "wb", preset=6) as destination:
        shutil.copyfileobj(source, destination)
    part.replace(packed)
    path.unlink()
    return packed


def plain_path(root: Path, file: str) -> Path:
    """Where a file can be read as it is. A packed one is unpacked on first use.

    `file` is relative to `root` and may name the packed file or the file
    that was packed. The unpacked copy goes to `.plain` under `root`.
    """
    if not file.endswith(PACKED):
        if (root / file).exists() or not (root / (file + PACKED)).exists():
            return root / file
        file += PACKED
    plain = root / PLAIN_DIR / file[: -len(PACKED)]
    if not plain.exists():
        plain.parent.mkdir(parents=True, exist_ok=True)
        # Several workers may want the same file at once. Each writes its own copy.
        part = plain.with_name(f".part-{os.getpid()}-{plain.name}")
        with lzma.open(root / file, "rb") as source, part.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        part.replace(plain)
    return plain


def tidy(root: Path) -> int:
    """Pack the outputs of every run and delete what was unpacked. Returns how many were packed.

    A run's outputs are small files that are mostly the same text as their
    targets. They are kept, because judging them again needs them, and packed
    they take a third of the room.
    """
    packed = 0
    runs = root / RUNS_DIR
    for output in sorted(runs.glob("*/outputs/**/*")) if runs.exists() else []:
        if output.is_file() and output.suffix != PACKED and not output.name.startswith(".part-"):
            pack(output)
            packed += 1
    for plain in (root / PLAIN_DIR, *runs.glob(f"*/{PLAIN_DIR}")):
        shutil.rmtree(plain, ignore_errors=True)
    return packed
