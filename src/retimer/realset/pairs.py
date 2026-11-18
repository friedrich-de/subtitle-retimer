"""Decides what is synced against what: the roles of tracks, and the pairs.

Everything here works on names and counts, so it can be tested with made-up
data. A pair is a target and a track of a video. The main pairs are the ones
a user makes: a shared subtitle against the full track of the episode it was
made for. The other kinds are there to see what a system does when it should
not move a file.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from retimer.realset.manifest import (
    Manifest,
    Pair,
    Role,
    Target,
    TargetOverride,
    Track,
    TrackOverride,
    Video,
    short_hash,
)
from retimer.realset.media import is_english
from retimer.realset.names import parse_numbering

# A track with fewer lines of speech than this is not a full track.
MIN_SPOKEN = 30
# A track with less than this share of the main track's lines of speech holds signs and songs.
SIGNS_SHARE = 0.4
# A track in which this share of the lines of speech describes a sound is captions
# for the hard of hearing.
CAPTIONS_SHARE = 0.05
# One main pair in this many is used to choose settings, and one target in
# this many gets a deliberately wrong reference.
TUNING_ONE_IN = 10
WRONG_ONE_IN = 10


def is_captions(track: Track) -> bool:
    """Whether a track is captions for the hard of hearing, by how many lines describe a sound."""
    return track.spoken > 0 and track.described >= CAPTIONS_SHARE * track.spoken


def assign_roles(tracks: list[Track]) -> dict[str, Role]:
    """The role of every text track of one video, by how much speech each holds.

    The main track is the English one with the most lines of speech. Captions
    for the hard of hearing only become it when there is no other: they have
    the most lines, but in a release with two audio languages they follow the
    English voices. Titles are not looked at: releases mislabel their tracks.
    """
    roles: dict[str, Role] = {}
    full = [track for track in tracks if track.spoken >= MIN_SPOKEN]
    english = [track for track in full if is_english(track.language)]
    main = max(
        english or full,
        key=lambda track: (not is_captions(track), track.spoken, -track.stream),
        default=None,
    )
    for track in tracks:
        if main is not None and track.id == main.id:
            roles[track.id] = "main"
        elif main is None or track.spoken < max(MIN_SPOKEN, SIGNS_SHARE * main.spoken):
            roles[track.id] = "signs"
        else:
            roles[track.id] = "full" if is_english(track.language) else "foreign"
    return roles


def settle_roles(videos: list[Video], overrides: Iterable[TrackOverride] = ()) -> None:
    """Give every track its role: what a person found out first, the rest by the lines.

    A track whose role was set by hand takes no part in choosing the main
    track, so a track that holds another episode cannot become it.
    """
    overrides = list(overrides)
    for video in videos:
        fixed: dict[str, TrackOverride] = {}
        for override in overrides:
            if override.video_contains in video.source:
                for track in video.tracks:
                    if track.stream == override.stream:
                        fixed[track.id] = override
        roles = assign_roles([track for track in video.tracks if track.id not in fixed])
        main_by_hand = any(override.role == "main" for override in fixed.values())
        for track in video.tracks:
            if track.id in fixed:
                track.role, track.note = fixed[track.id].role, fixed[track.id].reason
            elif main_by_hand and roles[track.id] == "main":
                track.role = "full"
            else:
                track.role = roles[track.id]


def number_videos(videos: list[Video]) -> None:
    """Give every video of one folder its episode number and its place in the folder.

    A lone special among regular episodes is episode 0, the way a prologue is
    numbered by those who number it at all.
    """
    for video in videos:
        numbering = parse_numbering(PurePosixPath(video.source).stem)
        video.season = numbering.season
        video.number = numbering.episodes[0] if numbering.episodes else None
    specials = [video for video in videos if video.season == 0]
    regular = [video for video in videos if video.season != 0 and video.number is not None]
    if regular and len(specials) == 1:
        specials[0].number = 0
        specials[0].ordinal = 0
    for place, video in enumerate(sorted(regular, key=lambda video: video.number), start=1):
        video.ordinal = place


def assign_videos(
    videos: list[Video], targets: list[Target], overrides: Iterable[TargetOverride] = ()
) -> None:
    """Pair every target of one folder with its video, by the episode in its name.

    Files that were named alike were numbered alike. Some count from the
    series' first episode, some from the season's, so for each such group
    both readings are tried and the one that finds more videos is taken.
    A folder with a single video is a film or a special: every file of its
    entry is for that video, whatever number its name carries.
    """
    overrides = list(overrides)
    by_number = {video.number: video for video in videos if video.number is not None}
    by_ordinal = {video.ordinal: video for video in videos if video.ordinal}
    groups: dict[tuple[int | None, str], list[tuple[Target, list[int]]]] = defaultdict(list)
    for target in targets:
        target.videos = []
        numbering = parse_numbering(target.name)
        target.season = numbering.season
        target.episodes = list(numbering.episodes)
        applying = [
            override
            for override in overrides
            if override.entry == target.entry and override.name_contains in target.name
        ]
        target.note = "; ".join(o.skip or o.reason or "" for o in applying).strip("; ") or None
        if any(override.skip for override in applying):
            continue
        offset = sum(override.episode_offset for override in applying)
        if len(videos) == 1:
            target.videos = [videos[0].id]
            continue
        if not numbering.episodes:
            target.note = target.note or "the name gives no episode"
            continue
        episodes = [episode + offset for episode in numbering.episodes]
        groups[(numbering.season, numbering.template)].append((target, episodes))

    for members in groups.values():
        absolute = sum(1 for _, episodes in members if episodes[0] in by_number)
        ordinal = sum(1 for _, episodes in members if episodes[0] in by_ordinal)
        lookup = by_ordinal if ordinal > absolute else by_number
        for target, episodes in members:
            found = [lookup[episode].id for episode in episodes if episode in lookup]
            if not found:
                target.note = target.note or "no video has this episode"
            elif len(found) == 1:
                target.videos = found
            else:
                # A file of several episodes: its first and its last.
                target.videos = [found[0], found[-1]]


def _one_in(key: str, n: int) -> bool:
    return int(short_hash(key), 16) % n == 0


def build_pairs(manifest: Manifest) -> list[Pair]:
    """The pairs of a set, in a fixed order that does not depend on how it was built."""
    videos = {video.id: video for video in manifest.videos}
    pairs: list[Pair] = []
    by_video: dict[str, list[Target]] = defaultdict(list)
    for target in sorted(manifest.targets, key=lambda target: target.id):
        for video_id in target.videos:
            main = videos[video_id].track("main")
            if main is None:
                continue
            by_video[video_id].append(target)
            pair_id = f"{target.id}.{main.id}"
            pairs.append(
                Pair(
                    id=pair_id,
                    kind="main",
                    target=target.id,
                    video=video_id,
                    track=main.id,
                    expected="placed",
                    tuning=_one_in(pair_id, TUNING_ONE_IN),
                )
            )

    # Every other track of a video is tried with one of the video's targets.
    for video in sorted(manifest.videos, key=lambda video: video.id):
        candidates = by_video.get(video.id)
        if not candidates:
            continue
        for track in video.tracks:
            if track.role == "main":
                continue
            target = candidates[int(short_hash(track.id), 16) % len(candidates)]
            pairs.append(
                Pair(
                    id=f"{target.id}.{track.id}",
                    kind="other_track",
                    target=target.id,
                    video=video.id,
                    track=track.id,
                    expected="placed" if track.role in ("full", "foreign") else "refused",
                    note=track.role,
                )
            )

    # A deliberately wrong reference: by turns the next episode of the same
    # series, which shares its names and its words, and an episode of another.
    with_main = sorted(
        (video for video in manifest.videos if video.track("main") and by_video.get(video.id)),
        key=lambda video: (video.folder, video.ordinal or 0, video.id),
    )
    place = {video.id: index for index, video in enumerate(with_main)}
    turn = 0
    for pair in [pair for pair in pairs if pair.kind == "main"]:
        if not _one_in(f"wrong:{pair.id}", WRONG_ONE_IN):
            continue
        own = videos[pair.video]
        same = [video for video in with_main if video.folder == own.folder and video.id != own.id]
        other = [video for video in with_main if video.folder != own.folder]
        neighbour = turn % 2 == 0 and bool(same)
        turn += 1
        if neighbour:
            later = [video for video in same if place[video.id] > place[own.id]]
            wrong = (later or same)[0]
        elif other:
            wrong = other[int(short_hash(pair.id), 16) % len(other)]
        else:
            continue
        track = wrong.track("main")
        pairs.append(
            Pair(
                id=f"{pair.target}.{track.id}",
                kind="wrong",
                target=pair.target,
                video=wrong.id,
                track=track.id,
                expected="refused",
                note="the next episode" if neighbour else "another series",
            )
        )
    unique = {pair.id: pair for pair in pairs}
    return list(unique.values())
