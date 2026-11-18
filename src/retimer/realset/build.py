"""Builds a real-file set: from a directory of videos, and from Jimaku.

Every step leaves alone what is already there, so a build that was cut off is
continued by running it again, and a set is brought up to date the same way.
The videos are only read.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from retimer.io import SubtitleError, load
from retimer.realset.jimaku import Jimaku
from retimer.realset.manifest import (
    AUDIO_DIR,
    LISTING_FILE,
    MANIFEST_FILE,
    OVERRIDES_FILE,
    PACKED,
    TARGETS_DIR,
    TRACKS_DIR,
    AudioFile,
    Manifest,
    Overrides,
    SkippedFile,
    Target,
    Track,
    Video,
    load_manifest,
    load_overrides,
    pack,
    plain_path,
    save_manifest,
    sha256_file,
    short_hash,
)
from retimer.realset.media import (
    TEXT_CODECS,
    MediaError,
    extract,
    pick_audio,
    probe,
    text_tracks,
    track_counts,
    two_scripts,
)
from retimer.realset.names import file_type, parse_numbering, split_extension
from retimer.realset.pairs import assign_videos, build_pairs, number_videos, settle_roles

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v")
_FOLDER_ID = re.compile(r"^(\d+)\s+-\s")
_INDEX_FILE = "jimaku/files.json"

Say = Callable[[str], None]

_READ_LOCK = threading.Lock()
_READ_CHUNK = 8 * 1024 * 1024


def _read_through(path: Path) -> None:
    """Read a file once from front to back, one file at a time.

    On a hard disk, ten readers side by side spend their time seeking. After
    this the file is in the system's memory, and what reads it next does not
    touch the disk.
    """
    with _READ_LOCK, path.open("rb", buffering=0) as handle:
        while handle.read(_READ_CHUNK):
            pass


def find_videos(videos_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in videos_dir.rglob("*")
        if path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file()
    )


def _facts(plain: Path) -> dict:
    """What the manifest says about a track, taken from the track as it is."""
    events, spoken, described = track_counts(plain)
    return {
        "sha256": sha256_file(plain),
        "bytes": plain.stat().st_size,
        "events": events,
        "spoken": spoken,
        "described": described,
    }


def _take_video(videos_dir: Path, path: Path, root: Path, known: dict[str, Track]) -> Video:
    """Probe one video and write out its tracks and audio, unless they are there.

    `known` are the tracks of an earlier build, by id. What it says about a
    track that is still there is taken over.
    """
    source = path.relative_to(videos_dir).as_posix()
    video_id = f"v{short_hash(source)}"
    duration, streams = probe(path)
    video = Video(
        id=video_id,
        source=source,
        folder=source.split("/", 1)[0] if "/" in source else "",
        duration_s=round(duration, 2),
        image_tracks=sum(
            1 for stream in streams if stream.kind == "subtitle" and stream.codec not in TEXT_CODECS
        ),
    )
    wanted = [
        (stream, root / TRACKS_DIR / video_id / f"s{stream.index:02d}.{TEXT_CODECS[stream.codec]}")
        for stream in text_tracks(streams)
    ]
    audio = pick_audio(streams)
    audio_path = root / AUDIO_DIR / f"{video_id}.ogg"
    missing = [
        (stream, file)
        for stream, file in wanted
        if not file.exists() and not file.with_name(file.name + PACKED).exists()
    ]
    missing_audio = (audio, audio_path) if audio is not None and not audio_path.exists() else None
    if missing or missing_audio:
        _read_through(path)
        extract(path, missing, missing_audio)
    for stream, file in wanted:
        track_id = f"{video_id}s{stream.index:02d}"
        packed = file.with_name(file.name + PACKED).relative_to(root).as_posix()
        if file.exists():
            facts = _facts(file)
            pack(file)
        elif track_id in known:
            facts = known[track_id].model_dump(
                include={"sha256", "bytes", "events", "spoken", "described"}
            )
        else:
            # Packed by a build that was cut off before it wrote its manifest.
            facts = _facts(plain_path(root, packed))
        video.tracks.append(
            Track(
                id=track_id,
                stream=stream.index,
                codec=stream.codec,
                language=stream.language,
                title=stream.title,
                default=stream.default,
                forced=stream.forced,
                file=packed,
                **facts,
            )
        )
    if audio is not None:
        video.audio = AudioFile(
            file=audio_path.relative_to(root).as_posix(),
            stream=audio.index,
            language=audio.language,
            sha256=sha256_file(audio_path),
            bytes=audio_path.stat().st_size,
        )
    return video


def take_videos(
    videos_dir: Path, root: Path, workers: int, say: Say, known: dict[str, Track] | None = None
) -> list[Video]:
    """The tracks and the audio of every video below `videos_dir`."""
    known = known or {}
    paths = find_videos(videos_dir)
    say(f"{len(paths)} videos")
    videos: list[Video] = []
    done = 0

    def work(path: Path) -> Video | None:
        nonlocal done
        try:
            video = _take_video(videos_dir, path, root, known)
        except MediaError as error:
            say(f"skipped {path.name}: {error}")
            video = None
        done += 1
        if done % 25 == 0 or done == len(paths):
            say(f"videos: {done}/{len(paths)}")
        return video

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        videos = [video for video in pool.map(work, paths) if video is not None]
    by_folder: dict[str, list[Video]] = defaultdict(list)
    for video in videos:
        by_folder[video.folder].append(video)
    for members in by_folder.values():
        number_videos(members)
    return videos


def folder_ids(videos: list[Video], overrides: Overrides) -> dict[str, int]:
    """The AniList id of every folder: the number its name begins with, or what was noted."""
    ids: dict[str, int] = {}
    for folder in sorted({video.folder for video in videos}):
        found = _FOLDER_ID.match(folder)
        if folder in overrides.folders:
            ids[folder] = overrides.folders[folder].anilist_id
        elif found:
            ids[folder] = int(found[1])
    return ids


def fetch_listing(jimaku: Jimaku, ids: dict[str, int], root: Path, refresh: bool, say: Say) -> dict:
    """What Jimaku lists for every folder. Kept, so a rebuild sees the same files."""
    path = root / LISTING_FILE
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    folders = {}
    for folder, anilist_id in ids.items():
        entries = []
        for entry in jimaku.entries(anilist_id=anilist_id):
            files = jimaku.files(entry["id"])
            entries.append({"id": entry["id"], "name": entry.get("name"), "files": files})
            say(f"listed entry {entry['id']}: {len(files)} files")
        folders[folder] = {"anilist_id": anilist_id, "entries": entries}
    listing = {"fetched": datetime.now(UTC).isoformat(timespec="seconds"), "folders": folders}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(listing, ensure_ascii=False, indent=1), encoding="utf-8")
    return listing


def fetch_targets(
    jimaku: Jimaku | None, listing: dict, root: Path, workers: int, say: Say
) -> tuple[dict[str, list[Target]], list[SkippedFile]]:
    """Download every text subtitle the listing names. Returns the targets by folder.

    A file is stored under a name made from its content, so the same bytes
    shared under two names are one target. Archives are left out: on Jimaku
    they hold image subtitles, which this set does not cover.
    """
    index_path = root / _INDEX_FILE
    index: dict[str, dict] = (
        json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    )
    skipped: list[SkippedFile] = []
    wanted: list[tuple[str, int, dict]] = []
    for folder, record in listing["folders"].items():
        for entry in record["entries"]:
            for file in entry["files"]:
                extension = split_extension(file["name"])[1]
                if not extension:
                    reason = "an archive or another format, not a text subtitle"
                    skipped.append(SkippedFile(entry=entry["id"], name=file["name"], reason=reason))
                else:
                    wanted.append((folder, entry["id"], file))

    def fetch(item: tuple[str, int, dict]) -> None:
        _, entry_id, file = item
        key = f"{entry_id}/{file['name']}"
        known = index.get(key)
        if known and (root / known["file"]).exists():
            return
        if jimaku is None:
            raise RuntimeError(f"{key} is not in the set and no Jimaku key was given")
        extension = split_extension(file["name"])[1]
        part = root / TARGETS_DIR / f".part-{short_hash(key, 16)}.{extension}"
        jimaku.download(file["url"], part)
        digest = sha256_file(part)
        stored = root / TARGETS_DIR / f"t{digest[:10]}.{extension}"
        part.replace(stored)
        index[key] = {
            "file": stored.relative_to(root).as_posix(),
            "sha256": digest,
            "bytes": stored.stat().st_size,
        }

    missing = sum(
        1
        for _, entry_id, file in wanted
        if f"{entry_id}/{file['name']}" not in index
        or not (root / index[f"{entry_id}/{file['name']}"]["file"]).exists()
    )
    say(f"{len(wanted)} text subtitles listed, {missing} to download")
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(fetch, wanted))
    finally:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    targets: dict[str, list[Target]] = defaultdict(list)
    seen: dict[str, Target] = {}
    for folder, entry_id, file in wanted:
        stored = index[f"{entry_id}/{file['name']}"]
        target_id = Path(stored["file"]).stem
        if target_id in seen:
            seen[target_id].also.append(file["name"])
            continue
        try:
            subtitle = load(root / stored["file"])
        except (SubtitleError, OSError) as error:
            skipped.append(
                SkippedFile(entry=entry_id, name=file["name"], reason=f"unreadable: {error}")
            )
            continue
        episodes = parse_numbering(file["name"]).episodes
        target = Target(
            id=target_id,
            entry=entry_id,
            name=file["name"],
            file=stored["file"],
            sha256=stored["sha256"],
            bytes=stored["bytes"],
            format=subtitle.format,
            type=file_type(file["name"], len(episodes), two_scripts(subtitle)),
        )
        seen[target_id] = target
        targets[folder].append(target)
    return targets, skipped


def build(
    videos_dir: Path,
    root: Path,
    *,
    jimaku_key: str | None = None,
    overrides_path: Path | None = None,
    workers: int = 8,
    refresh: bool = False,
    say: Say | None = None,
) -> Manifest:
    """Build the set at `root`, or bring it up to date, and write its manifest."""
    say = say or (lambda message: None)
    root.mkdir(parents=True, exist_ok=True)
    overrides = load_overrides(overrides_path or root / OVERRIDES_FILE)
    known: dict[str, Track] = {}
    if (root / MANIFEST_FILE).exists():
        known = {track.id: track for video in load_manifest(root).videos for track in video.tracks}
    videos = take_videos(videos_dir, root, workers, say, known)
    settle_roles(videos, overrides.tracks)
    jimaku = Jimaku(jimaku_key) if jimaku_key else None
    ids = folder_ids(videos, overrides)
    if jimaku is None and not (root / LISTING_FILE).exists():
        raise RuntimeError("the set has no listing yet, so a Jimaku key is needed")
    listing = fetch_listing(jimaku, ids, root, refresh, say)
    # Downloads go out a few at a time: the site is a volunteer's.
    targets, skipped = fetch_targets(jimaku, listing, root, min(workers, 4), say)
    by_folder: dict[str, list[Video]] = defaultdict(list)
    for video in videos:
        by_folder[video.folder].append(video)
    for folder, members in targets.items():
        assign_videos(by_folder.get(folder, []), members, overrides.targets)
    manifest = Manifest(
        built=datetime.now(UTC).isoformat(timespec="seconds"),
        videos=sorted(videos, key=lambda video: video.source),
        targets=sorted(
            (target for members in targets.values() for target in members),
            key=lambda target: (target.entry, target.name),
        ),
        skipped=skipped,
    )
    manifest.pairs = build_pairs(manifest)
    save_manifest(root, manifest)
    say(
        f"{len(manifest.videos)} videos, "
        f"{sum(len(video.tracks) for video in manifest.videos)} tracks, "
        f"{len(manifest.targets)} targets, {len(manifest.pairs)} pairs"
    )
    return manifest
