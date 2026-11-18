from retimer.realset.manifest import Manifest, Pair, Target, Track, Video
from retimer.realset.report import (
    by_file_type,
    confirmed,
    disagreement_rows,
    head_to_head,
    is_right,
    own_tracks,
    refusal_rows,
    spot_checks,
    system_order,
    two_witnesses,
    verdict_counts,
)
from retimer.realset.windows import WINDOW_MS


def made_up_set() -> Manifest:
    track = Track(id="v1s1", stream=1, codec="ass", file="x", sha256="0", bytes=1, role="main")
    video = Video(id="v1", source="1 - Show/Show - 01.mkv", folder="1 - Show", tracks=[track])
    kinds = ["Netflix", "Netflix", "AT-X", "fansub ASS"]
    targets = [
        Target(
            id=f"t{index}",
            entry=1,
            name=f"file {index}",
            file="x",
            sha256="0",
            bytes=1,
            format="srt",
            type=kind,
            videos=["v1"],
        )
        for index, kind in enumerate(kinds)
    ]
    pairs = [
        Pair(
            id=f"t{index}.v1s1",
            kind="main",
            target=f"t{index}",
            video="v1",
            track="v1s1",
            expected="placed",
            tuning=index == 3,
        )
        for index in range(4)
    ]
    return Manifest(videos=[video], targets=targets, pairs=pairs)


def entry(pair, system, verdict, shifts=None, kind="main", expected="placed", **more):
    found = {"pair": pair, "system": system, "kind": kind, "expected": expected, "verdict": verdict}
    if shifts is not None:
        found["shift_windows"] = [[index, shift] for index, shift in enumerate(shifts)]
        found["moved"] = any(abs(shift) > 100 for shift in shifts)
    return {**found, **more}


JUDGMENTS = [
    entry("t0.v1s1", "input", "off", [0, 0]),
    entry("t0.v1s1", "retimer", "verified", [1_000, 1_000], seconds=2.0, judged_windows=2),
    entry("t0.v1s1", "alass", "verified", [1_000, 1_050], seconds=0.5, judged_windows=2),
    entry("t1.v1s1", "input", "off", [0, 0]),
    entry("t1.v1s1", "retimer", "verified", [500, 900], seconds=4.0, judged_windows=2),
    entry("t1.v1s1", "alass", "off", [500, 500], seconds=0.5, judged_windows=2, off_windows=1),
    entry("t2.v1s1", "input", "verified", [0, 0]),
    entry("t2.v1s1", "retimer", "verified", [0, 0], seconds=3.0, judged_windows=2),
    entry("t2.v1s1", "alass", "verified", [-450, -450], seconds=0.5, judged_windows=2),
    entry("t3.v1s1", "retimer", "refused", seconds=1.0),
    entry("t3.v1s1", "alass", "verified", [0, 0], seconds=0.5, judged_windows=2),
]


def test_systems_come_in_the_kits_order():
    assert system_order(JUDGMENTS) == ["input", "retimer", "alass"]
    assert system_order([entry("p", "zeta", "off"), entry("p", "alass", "off")]) == [
        "alass",
        "zeta",
    ]


def test_share_verified_by_kind_of_file_leaves_the_tuning_pairs_out():
    manifest = made_up_set()
    held_out = {pair.id for pair in manifest.pairs if not pair.tuning}
    rows = by_file_type(manifest, JUDGMENTS, ["input", "retimer", "alass"], held_out)
    assert rows == [
        ["Netflix", 2, "0%", "100%", "50%"],
        ["AT-X", 1, "100%", "100%", "100%"],
        ["All", 3, "33%", "100%", "67%"],
    ]


def test_what_became_of_the_main_pairs():
    rows = verdict_counts(JUDGMENTS, ["retimer", "alass"], "main")
    assert rows[0] == ["retimer", 4, "75%", 3, 0, 0, 1, 0, "100%", "2.5"]
    assert rows[1] == ["alass", 4, "75%", 3, 1, 0, 0, 0, "88%", "0.5"]
    assert verdict_counts(JUDGMENTS, ["retimer"], "wrong") == []


def test_head_to_head_counts_the_same_pairs_for_both():
    pairs = {"t0.v1s1", "t1.v1s1", "t2.v1s1", "t3.v1s1"}
    assert head_to_head(JUDGMENTS, ["input", "retimer", "alass"], pairs) == [
        ["alass", 4, 2, 1, 1, 0]
    ]


def test_where_two_systems_moved_a_window_differently():
    pairs = {"t0.v1s1", "t1.v1s1", "t2.v1s1", "t3.v1s1"}
    rows = disagreement_rows(JUDGMENTS, ["retimer", "alass"], pairs)
    # Three pairs have both outputs. In two of them a window differs: one where
    # only the retimer was verified, one where both were.
    assert rows == [["alass", 3, 2, "50%", 1, 0, 1, 0]]


def test_spot_checks_put_broadcast_captions_first():
    manifest = made_up_set()
    # Every target of the made-up set is for the same video. Here each gets one of its own.
    manifest.videos += [
        manifest.videos[0].model_copy(update={"id": f"v{n}", "source": f"1 - Show/Show - 0{n}.mkv"})
        for n in (2, 3, 4)
    ]
    for number, pair in enumerate(manifest.pairs, start=1):
        pair.video = f"v{number}"
    checks = spot_checks(manifest, JUDGMENTS)
    assert [item["pair"] for item in checks] == ["t2.v1s1", "t1.v1s1"]
    assert checks[0]["type"] == "AT-X"
    assert checks[0]["windows"] == [(0, 0, -450, None, None), (1, 0, -450, None, None)]
    assert checks[1]["windows"] == [(1, 900, 500, None, None)]
    # A difference of many seconds is one system being lost, not a question for a person.
    far = [
        entry("t0.v1s1", "retimer", "verified", [1_000]),
        entry("t0.v1s1", "alass", "off", [1_000 + WINDOW_MS]),
    ]
    assert spot_checks(manifest, far) == []


def test_a_video_is_named_once_and_only_for_windows_inside_it():
    manifest = made_up_set()
    # Two files for one video: the one that differs in more windows stands for it.
    assert [item["pair"] for item in spot_checks(manifest, JUDGMENTS)] == ["t2.v1s1"]
    manifest.videos[0].duration_s = 170.0
    judgments = [
        entry("t0.v1s1", "retimer", "verified", [1_000, 1_000, 1_000]),
        entry("t0.v1s1", "alass", "verified", [1_000, 1_500, 1_500]),
    ]
    # The second and the third window begin after the video has ended.
    assert spot_checks(manifest, judgments) == []
    manifest.videos[0].duration_s = 300.0
    (check,) = spot_checks(manifest, judgments)
    assert [window[0] for window in check["windows"]] == [1]


def test_what_counts_as_right():
    assert is_right(entry("p", "s", "verified"))
    assert not is_right(entry("p", "s", "unjudged"))
    refused = {"kind": "wrong", "expected": "refused"}
    assert is_right(entry("p", "s", "refused", **refused))
    assert is_right(entry("p", "s", "unjudged", [0, 40], **refused))
    assert not is_right(entry("p", "s", "unjudged", [0, 4_000], **refused))
    assert not is_right(entry("p", "s", "failed", **refused))


def test_what_a_system_did_with_pairs_it_should_refuse():
    wrong = {"kind": "wrong", "expected": "refused"}
    judgments = [
        entry("a", "retimer", "refused", **wrong),
        entry("b", "retimer", "unjudged", [0, 0], **wrong),
        entry("a", "alass", "unjudged", [9_000, 9_000], **wrong),
        entry("b", "alass", "failed", **wrong),
        entry("c", "alass", "off", [700, 700], **wrong),
    ]
    notes = {"a": "the next episode", "b": "the next episode", "c": "another series"}
    rows = refusal_rows(judgments, ["retimer", "alass"], "wrong", "the next episode", notes)
    assert rows == [["retimer", 2, 1, 1, 0, 0, 0, 0], ["alass", 2, 0, 0, 1, 1, 0, 0]]
    assert refusal_rows(judgments, ["alass"], "wrong", "another series", notes) == [
        ["alass", 1, 0, 0, 1, 0, 0, 1]
    ]


def sound(offset, clear=True):
    return {"lines": 30, "offset_ms": offset, "strength": 3.0, "clear": clear, "windows": []}


def test_the_sound_check_as_a_second_witness():
    manifest = made_up_set()
    pairs = {pair.id for pair in manifest.pairs}
    judgments = [
        entry("t0.v1s1", "retimer", "verified", [0], sound=sound(100)),
        entry("t1.v1s1", "retimer", "verified", [0], sound=sound(900)),
        entry("t2.v1s1", "retimer", "off", [0], sound=sound(100, clear=False)),
        entry("t3.v1s1", "retimer", "refused"),
        entry("t0.v1s1", "alass", "off", [0], sound=sound(-2_000)),
    ]
    assert two_witnesses(judgments, ["retimer", "alass", "ffsubsync"], pairs) == [
        ["retimer", 3, 1, 1, 0, 0, 0, 1, 0, 0, 0],
        ["alass", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0],
    ]
    rows = by_file_type(manifest, judgments, ["retimer", "alass"], pairs, confirmed)
    assert rows[0] == ["Netflix", 2, "50%", "0%"]
    assert rows[-1] == ["All", 4, "25%", "0%"]


def test_a_spot_check_says_what_the_sound_read_where_the_window_went():
    manifest = made_up_set()

    def heard(*windows):
        return {
            "lines": 30,
            "offset_ms": 0,
            "strength": 3.0,
            "clear": True,
            "windows": list(windows),
        }

    judgments = [
        # The retimer moved the second window into the third. That is where the sound is asked.
        entry(
            "t2.v1s1",
            "retimer",
            "verified",
            [0, 120_000],
            sound=heard([0, 20, 50, 2.0, True], [2, 20, 90, 2.0, True]),
        ),
        entry(
            "t2.v1s1",
            "alass",
            "off",
            [0, 119_400],
            sound=heard([1, 20, 700, 1.0, False], [2, 20, 640, 2.0, True]),
        ),
    ]
    (check,) = spot_checks(manifest, judgments)
    assert check["windows"] == [(1, 120_000, 119_400, 90, 640)]


def test_the_videos_own_tracks_against_the_sound():
    manifest = made_up_set()
    readings = [
        {"video": "v1", "track": "v1s1", "role": "main", "sound": sound(-700)},
        {"video": "v1", "track": "v1s2", "role": "full", "sound": sound(100)},
        {"video": "v1", "track": "v1s3", "role": "full", "sound": sound(100, clear=False)},
    ]
    rows, off = own_tracks(manifest, readings)
    assert rows == [["full", 2, 1, 0, 1], ["main", 1, 0, 1, 0]]
    (line,) = off
    assert line.startswith("- `1 - Show/Show - 01.mkv`: whole file -0.70 s, 0 of 0 clear windows")
