"""A small set made by hand, taken through filling the cache, running, judging and reporting."""

import json
import shutil
import stat
import sys
import wave

import numpy as np
import pytest

from retimer import load, loads
from retimer.bench.soundcheck import SAMPLE_RATE, check_sound, voice_presence
from retimer.cli import main
from retimer.io import save
from retimer.realset.embedding import CompactCache, fill_cache
from retimer.realset.judge import (
    INPUT,
    clear_pairs,
    judge_run,
    load_judgments,
    load_track_readings,
    sound_reading,
    starts_by_id,
)
from retimer.realset.manifest import (
    CACHE_DIR,
    PLAIN_DIR,
    TUNING_FILE,
    AudioFile,
    Manifest,
    Pair,
    Target,
    Track,
    Video,
    pack,
    plain_path,
    save_manifest,
    tidy,
)
from retimer.realset.report import is_right, render, spot_checks, write_report
from retimer.realset.run import load_results, run_systems, select_pairs
from retimer.realset.systems import _resave, resolve
from retimer.realset.tune import tune

from .sync_helpers import SENTENCES, FakeClient, make_subtitle, spread
from .test_bench_soundcheck import film, moved, srt

# Stand-ins for the two tools. The first moves every line by 150 ms for each
# point of `--split-penalty`, so one setting undoes the target's offset. The
# second leaves the file as it is.
FAKE_ALASS = """#!{python}
import sys
from retimer import Plan, apply_plan, load, save

args = sys.argv[1:]
if "--version" in args:
    print("fake-alass 1.0")
    raise SystemExit(0)
ms = -150 * int(args[args.index("--split-penalty") + 1]) if "--split-penalty" in args else 0
plan = Plan.model_validate({{"timing_ops": [{{"op": "shift", "ms": ms}}]}})
save(apply_plan(load(args[-2]), plan).subtitle, args[-1])
"""
FAKE_FFSUBSYNC = """#!{python}
import shutil, sys

args = sys.argv[1:]
shutil.copyfile(args[args.index("-i") + 1], args[args.index("-o") + 1])
"""
SIGNS = """[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:05.00,0:00:08.00,Sign,,0,0,0,,{\\pos(100,200)}STATION
Dialogue: 0,0:01:05.00,0:01:08.00,Sign,,0,0,0,,{\\pos(100,200)}HARBOR
"""
OFFSET_MS = 1_500


def write_tool(path, source):
    path.write_text(source.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def stored_track(root, track_id, subtitle, role):
    path = root / "tracks" / f"{track_id}.{subtitle.format}"
    path.parent.mkdir(parents=True, exist_ok=True)
    save(subtitle, path)
    packed = pack(path)
    return Track(
        id=track_id,
        stream=int(track_id[-1]),
        codec=subtitle.format,
        language="eng",
        file=packed.relative_to(root).as_posix(),
        sha256="0" * 64,
        bytes=1,
        role=role,
    )


@pytest.fixture
def small_set(tmp_path, monkeypatch):
    root = tmp_path / "set"
    (root / "targets").mkdir(parents=True)
    starts = spread(20)
    save(
        make_subtitle([start + OFFSET_MS for start in starts], SENTENCES[:20]),
        root / "targets/t1.srt",
    )
    videos = [
        Video(
            id="v1",
            source="1 - Show/Show - 01.mkv",
            folder="1 - Show",
            tracks=[
                stored_track(root, "v1s1", make_subtitle(starts, SENTENCES[:20]), "main"),
                stored_track(root, "v1s2", loads(SIGNS), "signs"),
            ],
        ),
        Video(
            id="v2",
            source="1 - Show/Show - 02.mkv",
            folder="1 - Show",
            tracks=[stored_track(root, "v2s1", make_subtitle(spread(10), SENTENCES[20:]), "main")],
        ),
    ]
    target = Target(
        id="t1",
        entry=7,
        name="[Group] Show - 01 (AT-X 1440x1080).srt",
        file="targets/t1.srt",
        sha256="0" * 64,
        bytes=1,
        format="srt",
        type="AT-X",
        videos=["v1"],
    )
    pairs = [
        Pair(id="t1.v1s1", kind="main", target="t1", video="v1", track="v1s1", expected="placed",
             tuning=True),
        Pair(id="t1.v1s2", kind="other_track", target="t1", video="v1", track="v1s2",
             expected="refused", note="signs"),
        Pair(id="t1.v2s1", kind="wrong", target="t1", video="v2", track="v2s1",
             expected="refused", note="the next episode"),
    ]  # fmt: skip
    manifest = Manifest(built="2026-01-01", videos=videos, targets=[target], pairs=pairs)
    save_manifest(root, manifest)
    fill_cache(
        [root / "targets/t1.srt"],
        [plain_path(root, track.file) for video in videos for track in video.tracks],
        CompactCache(root / CACHE_DIR),
        client=FakeClient(),
    )
    monkeypatch.setenv("RETIMER_ALASS", write_tool(tmp_path / "fake-alass", FAKE_ALASS))
    monkeypatch.setenv("RETIMER_FFSUBSYNC", write_tool(tmp_path / "fake-ffs", FAKE_FFSUBSYNC))
    return root, manifest


def test_clear_pairs_come_from_the_text_alone(small_set):
    root, manifest = small_set
    from retimer.embed import Embedder
    from retimer.realset.embedding import CachedOnly

    embedder = Embedder(client=CachedOnly(), cache=CompactCache(root / CACHE_DIR))
    target = load(root / "targets/t1.srt")
    same = clear_pairs(target, load(plain_path(root, "tracks/v1s1.srt.xz")), embedder)
    assert len(same) >= 15
    assert all(target.lines[line_id].start_ms - start == OFFSET_MS for line_id, start in same)
    assert clear_pairs(target, load(plain_path(root, "tracks/v2s1.srt.xz")), embedder) == []
    # An output is traced back to the target by its text when no ids came with it.
    assert starts_by_id(target, target, None) == {line.id: line.start_ms for line in target.lines}
    assert starts_by_id(target, target, [3]) == starts_by_id(target, target, None)


def test_run_judge_and_report(small_set):
    root, manifest = small_set
    run_dir = run_systems(root, "first", ["retimer", "alass"], workers=2)
    records = {(record["pair"], record["system"]): record for record in load_results(run_dir)}
    assert len(records) == 6
    assert records[("t1.v1s1", "retimer")]["outcome"] == "synced"
    assert records[("t1.v2s1", "retimer")]["outcome"] == "rejected_no_match"
    assert records[("t1.v2s1", "retimer")]["output"] is None
    assert records[("t1.v1s1", "retimer")]["details"]["plan"]["timing_ops"]
    assert (run_dir / records[("t1.v1s1", "alass")]["output"]).exists()
    info = json.loads((run_dir / "run.json").read_text())
    assert info["systems"] == {"retimer": [], "alass": []}
    assert set(info["code"]) == {"retimer", "alass"}
    assert info["versions"] == {"alass": "fake-alass 1.0"}

    # What is on record is not run again, and a system can be added later.
    assert run_systems(root, "first", ["retimer", "alass"], workers=1) == run_dir
    assert len((run_dir / "results.jsonl").read_text().splitlines()) == 6
    # Unless that is asked for. The new record of a pair then takes the place of the old one.
    run_systems(root, "first", ["retimer"], workers=1, again=True)
    assert len((run_dir / "results.jsonl").read_text().splitlines()) == 9
    assert len(load_results(run_dir)) == 6

    judge_run(root, run_dir, workers=2)
    judged = {(entry["pair"], entry["system"]): entry for entry in load_judgments(run_dir)}
    assert len(judged) == 9
    main = "t1.v1s1"
    assert judged[(main, INPUT)]["verdict"] == "off" and not judged[(main, INPUT)]["moved"]
    assert judged[(main, "retimer")]["verdict"] == "verified"
    assert judged[(main, "retimer")]["shift_windows"] == [[0, -OFFSET_MS]]
    assert judged[(main, "retimer")]["largest_segments"][0][2:] == [-OFFSET_MS, 20]
    assert judged[(main, "alass")]["verdict"] == "off"
    assert judged[(main, "alass")]["windows"][0][2] == OFFSET_MS
    assert is_right(judged[(main, "retimer")]) and not is_right(judged[(main, "alass")])
    # The wrong reference is refused by the one, and left alone by the other, which moves nothing.
    assert judged[("t1.v2s1", "retimer")]["verdict"] == "refused"
    assert judged[("t1.v2s1", "alass")]["verdict"] == "unjudged"
    assert is_right(judged[("t1.v2s1", "retimer")]) and is_right(judged[("t1.v2s1", "alass")])
    assert not judged[("t1.v1s2", "retimer")]["moved"]

    text = write_report(root, run_dir)
    assert (run_dir / "report.md").read_text() == text
    assert "| AT-X | 0 |" not in text
    for heading in (
        "## Main pairs: what became of them",
        "## Other tracks of the same video: `signs`",
        "## Deliberately wrong pairs: the next episode",
        "## What the retimer reported",
        "## Candidates for a spot check",
    ):
        assert heading in text
    assert "| retimer | 1 | 100% | 1 | 0 | 0 | 0 | 0 |" in text
    checks = spot_checks(manifest, list(judged.values()))
    assert [(item["pair"], item["windows"]) for item in checks] == [
        (main, [(0, -OFFSET_MS, 0, None, None)])
    ]


def test_tuning_chooses_and_the_tuned_system_runs_with_the_choice(small_set):
    root, manifest = small_set
    with pytest.raises(ValueError, match="realset tune"):
        resolve(["alass-tuned"], root)
    chosen = tune(root, workers=2)
    assert chosen["alass-tuned"]["setting"] == "split-penalty-10"
    assert chosen["alass-tuned"]["verified"]["default"] == 0.0
    assert chosen["ffsubsync-tuned"]["setting"] == "default"
    # No video of this set has audio, so nothing was tried against it.
    assert "alass-audio-tuned" not in chosen
    assert json.loads((root / TUNING_FILE).read_text()) == chosen

    run_dir = run_systems(root, "second", ["alass-tuned", "alass-audio"], workers=1)
    judged = {(e["pair"], e["system"]): e for e in judge_run(root, run_dir, workers=1)}
    assert judged[("t1.v1s1", "alass-tuned")]["verdict"] == "verified"
    assert not any(system == "alass-audio" for _, system in judged)
    held_out = render(manifest, list(judged.values()), [], chosen, {}, "second")
    assert "split-penalty-10" in held_out


def test_a_tidied_run_can_be_judged_again(small_set):
    root, manifest = small_set
    run_dir = run_systems(root, "kept", ["retimer", "alass"], workers=1)
    before = judge_run(root, run_dir, workers=1)
    assert tidy(root) == 5
    assert not (root / PLAIN_DIR).exists()
    outputs = [path for path in (run_dir / "outputs").rglob("*") if path.is_file()]
    assert len(outputs) == 5 and all(path.suffix == ".xz" for path in outputs)
    assert judge_run(root, run_dir, workers=1) == before
    # Nothing is run again, and nothing is packed twice.
    run_systems(root, "kept", ["retimer", "alass"], workers=1)
    assert len(load_results(run_dir)) == 6
    assert tidy(root) == 0


def test_a_tuned_tool_gets_a_resaved_copy_of_a_file_it_cannot_read(
    small_set, tmp_path, monkeypatch
):
    root, manifest = small_set
    # One line of the target has an empty row in it, which the stand-in refuses to read.
    target = root / "targets/t1.srt"
    loose = target.read_text().replace("the harbor was empty", "the harbor\n \nwas empty")
    target.write_text(loose)
    assert "\n \n" in loose
    strict = FAKE_ALASS.replace(
        "plan = ",
        "if '\\n \\n' in open(args[-2]).read():\n"
        "    print('cannot read this', file=sys.stderr)\n"
        "    raise SystemExit(2)\nplan = ",
    )
    monkeypatch.setenv("RETIMER_ALASS", write_tool(tmp_path / "strict-alass", strict))
    (root / TUNING_FILE).write_text(
        json.dumps({"alass-tuned": {"args": ["--split-penalty", "10"]}})
    )
    pairs = select_pairs(manifest, kinds=["main"])
    run_dir = run_systems(root, "loose", ["alass", "alass-tuned"], pairs=pairs, workers=1)
    records = {record["system"]: record for record in load_results(run_dir)}
    assert (
        records["alass"]["outcome"] == "failed" and "cannot read this" in records["alass"]["error"]
    )
    assert records["alass-tuned"]["outcome"] is None
    assert records["alass-tuned"]["details"]["resaved_input"] is True
    assert "cannot read this" in records["alass-tuned"]["details"]["first_error"]
    judged = {entry["system"]: entry for entry in judge_run(root, run_dir, workers=1)}
    assert judged["alass"]["verdict"] == "failed"
    assert judged["alass-tuned"]["verdict"] == "verified"
    assert not (run_dir / "outputs/alass-tuned/t1.v1s1/resaved").exists()


def test_a_tuned_tool_also_gets_a_resaved_reference(small_set, tmp_path, monkeypatch):
    root, manifest = small_set
    # The stand-in cannot read a reference that does not begin with a cue number,
    # as a track does not when a muxer wrote something in front of it.
    picky = FAKE_ALASS.replace(
        "plan = ",
        "if not open(args[-3]).read().startswith('1'):\n"
        "    print('cannot read the reference', file=sys.stderr)\n"
        "    raise SystemExit(2)\nplan = ",
    )
    monkeypatch.setenv("RETIMER_ALASS", write_tool(tmp_path / "picky-alass", picky))
    track = plain_path(root, "tracks/v1s1.srt.xz")
    track.write_text("\n\n" + track.read_text())
    (root / TUNING_FILE).write_text(
        json.dumps({"alass-tuned": {"args": ["--split-penalty", "10"]}})
    )
    pairs = select_pairs(manifest, kinds=["main"])
    run_dir = run_systems(root, "header", ["alass", "alass-tuned"], pairs=pairs, workers=1)
    records = {record["system"]: record for record in load_results(run_dir)}
    assert "cannot read the reference" in records["alass"]["error"]
    assert records["alass-tuned"]["outcome"] is None
    assert records["alass-tuned"]["details"]["resaved_input"] is True


def test_a_resaved_file_has_no_empty_rows_and_no_fonts_in_its_header(tmp_path):
    fonts = "\n".join(f"{chr(33 + index % 60)}{index}: M`F9O;G1S" for index in range(400))
    script = SIGNS.replace("ScriptType: v4.00+", f"ScriptType: v4.00+\nTitle: A show\n{fonts}")
    script = script.replace("STATION", "STATION\\N \\NPLATFORM 2")
    (tmp_path / "loose.ass").write_text(script, encoding="utf-8")
    assert _resave(tmp_path / "loose.ass", tmp_path / "tidy.ass")
    tidy = (tmp_path / "tidy.ass").read_text(encoding="utf-8")
    assert "Title: A show" in tidy and "F9O;G1S" not in tidy
    assert "STATION\\NPLATFORM 2" in tidy
    assert len(load(tmp_path / "tidy.ass")) == 2
    (tmp_path / "broken.srt").write_text("not a subtitle", encoding="utf-8")
    assert not _resave(tmp_path / "broken.srt", tmp_path / "never.srt")


def test_the_commands(small_set, capsys):
    root, manifest = small_set
    assert main(["realset", "run", str(root), "--run", "cli", "--systems", "retimer,alass"]) == 0
    assert (
        main(["realset", "run", str(root), "--run", "cli", "--kinds", "wrong", "--limit", "1"]) == 1
    )
    assert "alass-tuned has no settings yet" in capsys.readouterr().err
    assert main(["realset", "judge", str(root), "--run", "cli", "--workers", "1"]) == 0
    assert main(["realset", "report", str(root), "--run", "cli"]) == 0
    assert "# Real-file run `cli`" in capsys.readouterr().out
    assert main(["realset", "tune", str(root), "--workers", "1"]) == 0
    assert "alass-tuned: split-penalty-10 (--split-penalty 10)" in capsys.readouterr().out
    assert main(["realset", "tidy", str(root)]) == 0
    assert not (root / PLAIN_DIR).exists()
    # Everything of the set is in the cache already, so there is nothing to embed.
    assert main(["realset", "embed", str(root)]) == 0
    assert "0 new texts" in capsys.readouterr().err


def test_pairs_can_be_selected(small_set):
    root, manifest = small_set
    assert [pair.id for pair in select_pairs(manifest, kinds=["wrong"])] == ["t1.v2s1"]
    assert [pair.id for pair in select_pairs(manifest, tuning=True)] == ["t1.v1s1"]
    assert len(select_pairs(manifest, only="v1s", limit=1)) == 1
    assert (root / PLAIN_DIR).is_dir()


def test_the_sound_reading_is_the_sound_checks_own():
    samples, spans = film(420)
    subtitle = loads(srt(moved(spans, 300)))
    official = check_sound(samples, subtitle)
    mine = sound_reading(voice_presence(samples), subtitle)
    assert official.clear and official.offset_ms == -300
    assert (mine["lines"], mine["offset_ms"], mine["strength"], mine["clear"]) == (
        official.lines,
        official.offset_ms,
        official.strength,
        official.clear,
    )
    assert mine["windows"] == [
        [index, window.lines, window.offset_ms, window.strength, window.clear]
        for index, window in enumerate(official.windows)
        if window.lines
    ]
    assert len(mine["windows"]) == 3


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_outputs_are_also_asked_of_the_sound(small_set):
    root, manifest = small_set
    samples, _ = film(140)
    (root / "audio").mkdir()
    with wave.open(str(root / "audio/v1.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((samples * 32_767).astype(np.int16).tobytes())
    manifest.video("v1").audio = AudioFile(file="audio/v1.wav", stream=1, sha256="0" * 64, bytes=1)
    save_manifest(root, manifest)
    run_dir = run_systems(root, "sound", ["alass", "alass-audio"], workers=1)
    judged = {(e["pair"], e["system"]): e for e in judge_run(root, run_dir, workers=1)}
    # The fake tool is also run against the audio, for the pairs of the video that has some.
    assert ("t1.v1s1", "alass-audio") in judged and ("t1.v2s1", "alass-audio") not in judged
    # Another track of the same video would be the same job against the same audio.
    assert ("t1.v1s2", "alass") in judged and ("t1.v1s2", "alass-audio") not in judged
    sound = judged[("t1.v1s1", "alass")]["sound"]
    assert sound["lines"] == 20 and len(sound["windows"]) == 1
    assert judged[("t1.v1s1", INPUT)]["sound"] == sound
    assert "sound" not in judged[("t1.v2s1", "alass")]
    # The video's own full track is asked of the sound too, and its signs track is not.
    assert [(read["track"], read["role"]) for read in load_track_readings(run_dir)] == [
        ("v1s1", "main")
    ]
    assert "## The videos' own tracks against the sound" in write_report(root, run_dir)
