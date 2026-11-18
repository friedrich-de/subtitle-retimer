from retimer.realset.manifest import Manifest, Target, TargetOverride, Track, TrackOverride, Video
from retimer.realset.pairs import (
    assign_roles,
    assign_videos,
    build_pairs,
    number_videos,
    settle_roles,
)


def track(track_id, spoken, language="eng", stream=None, title=None):
    return Track(
        id=track_id,
        stream=stream if stream is not None else int(track_id[-1]),
        codec="ass",
        language=language,
        title=title,
        file=f"tracks/{track_id}.ass.xz",
        sha256="0" * 64,
        bytes=1,
        spoken=spoken,
    )


def video(video_id, name, folder="1 - Show", tracks=()):
    return Video(id=video_id, source=f"{folder}/{name}", folder=folder, tracks=list(tracks))


def target(target_id, name, entry=7):
    return Target(
        id=target_id,
        entry=entry,
        name=name,
        file=f"targets/{target_id}.srt",
        sha256="0" * 64,
        bytes=1,
        format="srt",
        type="fansub SRT",
    )


def test_the_main_track_is_the_english_one_with_the_most_speech():
    tracks = [
        # The title lies: this one holds the signs.
        track("a1", 12, title="English [Full]"),
        track("a2", 310, title="English [Signs/Songs]"),
        track("a3", 290, language="ger"),
        track("a4", 300, language=None),
        track("a5", 20, language="spa"),
    ]
    assert assign_roles(tracks) == {
        "a1": "signs",
        "a2": "main",
        "a3": "foreign",
        "a4": "full",
        "a5": "signs",
    }


def test_captions_for_the_hard_of_hearing_are_not_the_main_track():
    plain, captions = track("a1", 340), track("a2", 480)
    captions.described = 60
    assert assign_roles([plain, captions]) == {"a1": "main", "a2": "full"}
    # Alone, they are what there is.
    assert assign_roles([captions, track("a3", 9)]) == {"a2": "main", "a3": "signs"}


def test_a_video_without_a_full_track_has_no_main_track():
    assert assign_roles([track("a1", 8), track("a2", 3)]) == {"a1": "signs", "a2": "signs"}
    # Without an English track, the fullest one serves.
    assert assign_roles([track("a1", 200, language="ger")]) == {"a1": "main"}


def test_a_role_that_was_found_out_by_hand_is_set():
    # The second track holds another episode. It has more lines, and must not become the main one.
    one = video("v1", "Show - 04.mkv", tracks=[track("a1", 300), track("a2", 370), track("a3", 9)])
    other = video("v2", "Show - 05.mkv", tracks=[track("b1", 300), track("b2", 280)])
    settle_roles(
        [one, other],
        [TrackOverride(video_contains="- 04", stream=2, role="wrong_episode", reason="episode 3")],
    )
    assert [t.role for t in one.tracks] == ["main", "wrong_episode", "signs"]
    assert one.tracks[1].note == "episode 3"
    assert [t.role for t in other.tracks] == ["main", "full"]
    settle_roles([other], [TrackOverride(video_contains="- 05", stream=2, role="main")])
    assert [t.role for t in other.tracks] == ["full", "main"]


def test_a_lone_special_is_episode_zero():
    videos = [
        video("v0", "Show - S00E01 - Prologue.mkv"),
        video("v2", "Show - S01E02 - Two.mkv"),
        video("v1", "Show - S01E01 - One.mkv"),
    ]
    number_videos(videos)
    assert [(v.number, v.ordinal) for v in videos] == [(0, 0), (2, 2), (1, 1)]


def second_season():
    videos = [video(f"v{n}", f"Show - S01E{n} - Title.mkv") for n in range(13, 26)]
    number_videos(videos)
    return videos


def test_names_that_count_from_the_series_start_and_from_the_season_start():
    videos = second_season()
    targets = [target(f"a{n}", f"[Group] Show - {n} [BD].ass") for n in range(13, 26)]
    targets += [
        target(f"s{n}", f"Show.S02E{n:02d}.Title.WEBRip.Service.ja.srt") for n in range(1, 14)
    ]
    assign_videos(videos, targets)
    found = {t.id: t.videos for t in targets}
    assert found["a13"] == ["v13"] and found["a25"] == ["v25"]
    # Thirteen is both an episode of the series and a place in the season.
    # The other files of the release settle which.
    assert found["s1"] == ["v13"] and found["s13"] == ["v25"]


def test_every_file_of_a_film_is_for_its_one_video():
    film = [video("v1", "A Film (2017).mkv", folder="2 - Film")]
    number_videos(film)
    targets = [target("t1", "[Group] A Film [BD].ass"), target("t2", "A Film - 03 (TV).srt")]
    assign_videos(film, targets)
    assert [t.videos for t in targets] == [["v1"], ["v1"]]


def test_overrides_shift_the_episode_or_leave_a_file_out():
    videos = [video("v0", "Show - S00E01.mkv")] + [
        video(f"v{n}", f"Show - S01E{n:02d}.mkv") for n in range(1, 4)
    ]
    number_videos(videos)
    targets = [target(f"n{n}", f"Show.S01E{n:02d}.WEBRip.Service.ja.srt") for n in range(1, 5)]
    targets.append(target("x1", "Show.S02E01.WEBRip.Service.ja.srt"))
    assign_videos(
        videos,
        targets,
        [
            TargetOverride(
                entry=7, name_contains=".S01E", episode_offset=-1, reason="counts from 1"
            ),
            TargetOverride(entry=7, name_contains=".S02E", skip="no such video"),
            TargetOverride(entry=8, name_contains="", skip="another entry"),
        ],
    )
    found = {t.id: t.videos for t in targets}
    assert found == {"n1": ["v0"], "n2": ["v1"], "n3": ["v2"], "n4": ["v3"], "x1": []}
    assert targets[-1].note == "no such video"
    assert targets[0].note == "counts from 1"


def test_a_file_of_several_episodes_gets_its_first_and_last_video():
    videos = [video(f"v{n}", f"Show - E{n:03d}.mkv") for n in range(1, 9)]
    number_videos(videos)
    pack, single, lost = (
        target("p1", "Show - E003-006 [TV].srt"),
        target("s1", "Show - E002 [TV].srt"),
        target("l1", "Show - E040 [TV].srt"),
    )
    nameless = target("n1", "Show complete.srt")
    assign_videos(videos, [pack, single, lost, nameless])
    assert pack.videos == ["v3", "v6"]
    assert single.videos == ["v2"]
    assert lost.videos == [] and lost.note == "no video has this episode"
    assert nameless.videos == [] and nameless.note == "the name gives no episode"


def small_manifest(episodes=30):
    videos = []
    for folder in ("1 - Show", "2 - Other"):
        for n in range(1, episodes + 1):
            vid = f"v{folder[0]}{n:02d}"
            tracks = [track(f"{vid}s1", 300), track(f"{vid}s2", 10), track(f"{vid}s3", 280, "fre")]
            tracks[0].role, tracks[2].role = "main", "foreign"
            videos.append(video(vid, f"Show - {n:02d}.mkv", folder, tracks))
    targets = []
    for index, one in enumerate(videos):
        for release in ("a", "b"):
            item = target(f"t{release}{index:03d}", f"[{release}] Show - {one.id}.srt")
            item.videos = [one.id]
            targets.append(item)
    by_folder = {}
    for one in videos:
        by_folder.setdefault(one.folder, []).append(one)
    for members in by_folder.values():
        number_videos(members)
    return Manifest(videos=videos, targets=targets)


def test_pairs_of_every_kind():
    manifest = small_manifest()
    pairs = build_pairs(manifest)
    assert pairs == build_pairs(manifest)
    assert len({pair.id for pair in pairs}) == len(pairs)
    main = [pair for pair in pairs if pair.kind == "main"]
    assert len(main) == 120 and all(pair.expected == "placed" for pair in main)
    assert all(manifest.track(pair.track).role == "main" for pair in main)
    # About a tenth is set aside for choosing settings, always the same ones.
    assert 4 <= sum(pair.tuning for pair in main) <= 24

    other = [pair for pair in pairs if pair.kind == "other_track"]
    assert len(other) == 120
    for pair in other:
        role = manifest.track(pair.track).role
        assert pair.note == role and pair.expected == ("refused" if role == "signs" else "placed")
        assert pair.video in manifest.target(pair.target).videos

    wrong = [pair for pair in pairs if pair.kind == "wrong"]
    assert 4 <= len(wrong) <= 24
    for pair in wrong:
        assert pair.expected == "refused"
        assert pair.video not in manifest.target(pair.target).videos
        assert manifest.track(pair.track).role == "main"
    assert {pair.note for pair in wrong} == {"the next episode", "another series"}
    for pair in wrong:
        own = manifest.video(manifest.target(pair.target).videos[0])
        same_folder = manifest.video(pair.video).folder == own.folder
        assert same_folder == (pair.note == "the next episode")


def test_a_video_without_a_main_track_gives_no_pairs():
    manifest = small_manifest(episodes=2)
    for one in manifest.videos:
        one.tracks[0].role = "signs"
    assert build_pairs(manifest) == []
