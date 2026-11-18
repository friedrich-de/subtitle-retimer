import json
import random
from collections import defaultdict

import pytest

from retimer import apply_plan, dumps, load, loads
from retimer.bench.cases import generate_cases, load_cases, single_track
from retimer.bench.distort import Break, Distortion, Extra, distort
from retimer.bench.realfile import SONG_STYLES, loosen, real_file_recipes, signs, song
from retimer.choice import MAX_REFERENCES

from .bench_helpers import _stamp, make_base, write_seeds

FAMILIES = {
    "loose_reference",
    "cold_open",
    "caption_drift",
    "pack",
    "mid_scene",
    "next_episode",
    "signs_reference",
    "several_references",
    "karaoke",
    "captions",
    "bilingual",
    "odd_times",
}


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    root = tmp_path_factory.mktemp("real-files")
    seeds = write_seeds(root / "seeds")
    generate_cases(
        seeds, root / "media", root / "cases", recipes=[], real_recipes=real_file_recipes()
    )
    return {case.case.id: case for case in load_cases(root / "cases", families=FAMILIES)}


def talk(lines: int = 60) -> str:
    """Lines close enough to be joined and long enough to be split."""
    blocks = []
    for index in range(lines):
        start = 10_000 + index * 3_500
        text = f"one two three four five {index}"
        blocks.append(f"{index + 1}\n{_stamp(start)} --> {_stamp(start + 3_200)}\n{text}\n")
    return "\n".join(blocks)


def test_every_recipe_builds_a_case_against_a_reference_of_its_own(cases):
    recipes = real_file_recipes()
    assert {recipe.family for recipe in recipes} == FAMILIES
    assert len({recipe.label for recipe in recipes}) == len(recipes)
    # Two seeds, each as the Japanese target against the English reference.
    assert len(cases) == 2 * len(recipes)
    for case in cases.values():
        assert case.case.target_language == "ja"
        assert case.case.reference.subtitle_language == "en"
        assert case.reference_subtitle_path.parent == case.directory
        assert len(load(case.target_path)) > 0 and len(load(case.reference_subtitle_path)) > 0


def test_a_case_says_what_it_asks_for(cases):
    asked = defaultdict(set)
    for case in cases.values():
        asked[case.case.family].add(case.case.expected_outcome)
    assert asked.pop("next_episode") == {"rejected_no_match"}
    assert asked.pop("signs_reference") == {"left_alone"}
    assert asked.pop("several_references") == {
        "synced",
        "already_in_sync",
        "left_alone",
        "rejected_no_match",
    }
    assert asked.pop("loose_reference") == {"synced", "already_in_sync"}
    assert all(outcomes == {"synced"} for outcomes in asked.values())


def test_the_ideal_plan_undoes_every_case_that_has_one(cases):
    without = set()
    for case in cases.values():
        if case.case.ideal_plan is None:
            without.add(case.case.family)
            continue
        synced = apply_plan(load(case.target_path), case.case.ideal_plan).subtitle
        starts = {line.id: line.start_ms for line in synced.lines}
        timed = [line for line in case.case.truth if line.true_start_ms is not None]
        assert timed, case.case.id
        # A script keeps hundredths of a second, so its lines are up to 5 ms off.
        assert max(abs(starts[line.id] - line.true_start_ms) for line in timed) <= 5, case.case.id
        absent = {line.id for line in case.case.truth if line.true_start_ms is None}
        assert not absent & set(starts), case.case.id
    assert without == {"next_episode", "signs_reference", "several_references", "odd_times"}


def test_a_pack_holds_the_other_episode_as_lines_without_a_place(cases):
    case = cases["alpha.ja.subtitle-en.pack-middle-of-three"]
    kinds = defaultdict(list)
    for line in case.case.truth:
        kinds[line.kind].append(line)
    assert len(kinds["base"]) == 40 and len(kinds["other_content"]) == 46
    assert all(line.true_start_ms is None for line in kinds["other_content"])
    target = load(case.target_path)
    own = [line.id for line in kinds["base"]]
    # The episode stands in the middle of the file, with the other one around it.
    assert min(own) > 0 and max(own) < len(target) - 1


def test_the_next_episode_shares_lines_with_the_reference_it_is_sent_with(cases):
    case = cases["alpha.ja.subtitle-en.next-episode-shared-opening"]
    target = [line.text for line in load(case.target_path).lines]
    alpha = [line.text for line in load(case.directory / "../../seeds/alpha/ja.srt").lines]
    beta = [line.text for line in load(case.directory / "../../seeds/beta/ja.srt").lines]
    shared = [text for text in target if text in alpha[:6]]
    assert len(shared) >= 3 and target[: len(shared)] == shared
    assert len([text for text in target if text in beta]) > len(target) // 2
    assert not case.case.truth


def test_karaoke_cases_are_as_heavy_as_a_typeset_script(cases):
    plain = cases["alpha.ja.subtitle-en.karaoke-heavy-reference"]
    assert len(load(plain.target_path)) == 40
    reference = load(plain.reference_subtitle_path)
    assert reference.format == "ass" and len(reference) > 1_500
    assert sum(line.is_comment for line in reference.lines) > 10
    huge = cases["alpha.ja.subtitle-en.karaoke-huge-reference"]
    assert len(load(huge.reference_subtitle_path)) > 4 * len(reference)

    heavy = cases["alpha.ja.subtitle-en.karaoke-heavy-target"]
    kinds = defaultdict(int)
    for line in heavy.case.truth:
        kinds[line.kind] += 1
    assert kinds["base"] == 40 and kinds["karaoke"] > 500 and kinds["sign"] > 500


def test_a_reference_of_signs_has_no_line_of_dialogue(cases):
    case = cases["alpha.ja.subtitle-en.signs-reference-plain-signs-late-8s"]
    reference = load(case.reference_subtitle_path)
    said = {line.text for line in load(case.directory / "../../seeds/alpha/en.srt").lines}
    assert len(reference) > 0 and not said & {line.text for line in reference.lines}
    # The target is late, and nothing in the reference can say so.
    target = load(case.target_path)
    seed = load(case.directory / "../../seeds/alpha/ja.srt")
    assert target.lines[0].start_ms - seed.lines[0].start_ms == 8_000


def several(cases, seed="alpha", kind="subtitle-en"):
    prefix = f"{seed}.ja.{kind}.several-references-"
    return {name.removeprefix(prefix): case for name, case in cases.items() if prefix in name}


def test_a_case_with_several_references_holds_its_tracks_beside_the_target(cases):
    made = several(cases)
    assert len(made) == 9
    for name, case in made.items():
        tracks = case.reference_tracks
        assert 2 <= len(tracks) <= MAX_REFERENCES, name
        assert [path.name.split(".")[1] for path, _ in tracks] == [
            str(number) for number in range(len(tracks))
        ]
        assert all(path.parent == case.directory and len(load(path)) > 0 for path, _ in tracks)
        # A system that takes one reference gets the track a player would show.
        described = case.case.reference.subtitles
        assert case.reference_subtitle_path == case.directory / single_track(described).file
        roles = {track.role for track in described}
        asked = case.case.expected_outcome
        assert ("full" in roles) == (asked in ("synced", "already_in_sync")), name
        if asked == "left_alone":
            assert roles == {"signs"}
        if "full" not in roles:
            assert case.case.ideal_plan is None
    # Only the other families describe one reference and no tracks.
    assert all(
        not case.case.reference.subtitles
        for case in cases.values()
        if case.case.family != "several_references"
    )


def test_the_tracks_of_a_release_differ_only_by_the_dialogue(cases):
    case = several(cases)["full-and-signs"]
    (full_path, full), (signs_path, only_signs) = case.reference_tracks
    assert (full.role, full.title, full.default) == ("full", "Full Subtitles", True)
    assert (only_signs.role, only_signs.title, only_signs.forced) == (
        "signs",
        "Signs & Songs",
        True,
    )
    full_lines = [line.text for line in load(full_path).lines]
    signs_lines = [line.text for line in load(signs_path).lines]
    said = [line.text for line in load(case.directory / "../../seeds/alpha/en.srt").lines]
    # What the full script has more than the other track is the dialogue.
    assert len(signs_lines) > 500 and not set(said) & set(signs_lines)
    assert 0 < len(full_lines) - len(signs_lines) <= len(said)
    assert set(signs_lines) <= set(full_lines)


def test_labels_can_point_to_the_wrong_track_or_say_nothing(cases):
    made = several(cases)
    misled = made["signs-marked-default"].case.reference
    assert [(track.role, track.default, track.title) for track in misled.subtitles] == [
        ("signs", True, None),
        ("full", False, None),
    ]
    # So a client that sends one track sends the signs.
    assert single_track(misled.subtitles).role == "signs"

    bare = made["three-without-labels"].case.reference
    assert [track.role for track in bare.subtitles] == ["signs", "other_episode", "full"]
    for track in bare.subtitles:
        assert (track.language, track.title, track.default, track.forced) == (
            None,
            None,
            False,
            False,
        )
    assert single_track(bare.subtitles).role == "signs"

    most = made["six-tracks"].case.reference
    assert len(most.subtitles) == MAX_REFERENCES
    assert {track.role for track in most.subtitles} == {"full", "signs", "other_episode"}
    twice = made["same-track-twice"]
    (_, first), (_, second) = [item for item in twice.reference_tracks if item[1].role == "full"]
    assert first.title != second.title
    assert (twice.directory / first.file).read_bytes() == (
        twice.directory / second.file
    ).read_bytes()


def test_another_episode_among_the_tracks_is_the_other_seed(cases):
    case = several(cases)["other-episode-and-signs"]
    (path, other), (_, only_signs) = case.reference_tracks
    assert (other.role, other.default, only_signs.role) == ("other_episode", True, "signs")
    assert case.case.expected_outcome == "rejected_no_match"
    beta = {line.text for line in load(case.directory / "../../seeds/beta/en.srt").lines}
    texts = [part for line in load(path).lines for part in line.text.split("\\N")]
    assert sum(text in beta for text in texts) > len(texts) // 2


def test_with_the_audio_beside_the_tracks_a_case_is_synced_on_it(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    described = json.loads((seeds / "seeds.json").read_text(encoding="utf-8"))
    for seed in described["seeds"]:
        seed["audio"] = {"file": f"{seed['id']}.ogg", "language": "en"}
        (tmp_path / "media").mkdir(exist_ok=True)
        (tmp_path / "media" / f"{seed['id']}.ogg").write_bytes(b"sound")
    (seeds / "seeds.json").write_text(json.dumps(described), encoding="utf-8")
    recipes = [recipe for recipe in real_file_recipes() if recipe.family == "several_references"]
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases", recipes=[], real_recipes=recipes)
    cases = {case.case.id: case for case in load_cases(tmp_path / "cases", families=FAMILIES)}

    both = several(cases, kind="both-en")
    assert set(both) == {recipe.name for recipe in recipes if recipe.both}
    assert set(both) == {
        "full-and-signs",
        "three-without-labels",
        "only-signs",
        "other-episode-and-signs",
    }
    alone = several(cases)
    for name, case in both.items():
        reference = case.case.reference
        assert reference.kind == "both" and case.audio_path.name == "alpha.ogg"
        assert reference.audio_language == "en"
        # The same tracks as without the audio, and now there is always a right timing.
        assert [track.role for track in reference.subtitles] == [
            track.role for track in alone[name].case.reference.subtitles
        ]
        assert case.case.expected_outcome == "synced" and case.case.ideal_plan is not None
    assert alone["only-signs"].case.expected_outcome == "left_alone"
    # No recipe of this family is built against the audio alone.
    assert not several(cases, kind="audio")


def test_odd_times_are_written_as_the_tool_left_them(cases):
    case = cases["alpha.ja.subtitle-en.odd-times-script-cut-into-parts"]
    text = case.target_path.read_text(encoding="utf-8")
    assert ":-" in text
    target = load(case.target_path)
    timed = [line for line in case.case.truth if line.true_start_ms is not None]
    assert len(timed) == 40 and len(target) > 40
    assert case.case.ideal_plan is None and case.case.expected_outcome == "synced"


def test_captions_come_with_and_without_the_request_to_clean_them(cases):
    kept = cases["alpha.ja.subtitle-en.captions-kept"]
    cleaned = cases["alpha.ja.subtitle-en.captions-cleaned"]
    assert kept.case.remove_kinds() == set()
    assert cleaned.case.remove_kinds() == {"sound_description", "karaoke"}
    assert any("（" in line.text for line in load(kept.target_path).lines)


def test_a_loosened_reference_is_cut_anew_and_stays_where_it_was():
    base = loads(talk())
    before = {line.text: line.start_ms for line in base.lines}
    joined = split = dropped = 0
    for seed in range(6):
        loose = loosen(base, random.Random(seed))
        moves = [line.start_ms - before[line.text] for line in loose.lines if line.text in before]
        assert len(moves) > 20
        assert abs(sum(moves) / len(moves)) <= 40
        assert max(abs(move) for move in moves) <= 700
        starts = [line.start_ms for line in loose.lines]
        assert starts == sorted(starts)
        texts = [line.text for line in loose.lines]
        joined += sum("\\N" in text for text in texts)
        split += sum(text not in before and "\\N" not in text for text in texts)
        dropped += sum(text not in " ".join(texts) for text in before)
    assert joined and split and dropped
    assert dumps(loosen(base, random.Random(3))) == dumps(loosen(base, random.Random(3)))


def test_a_short_line_is_never_cut_in_two():
    blocks = [
        f"{index + 1}\n{_stamp(index * 5_000)} --> {_stamp(index * 5_000 + 3_500)}\nScales...\n"
        for index in range(1, 40)
    ]
    for seed in range(6):
        loose = loosen(loads("\n".join(blocks)), random.Random(seed), join_share=0)
        assert {line.text for line in loose.lines} == {"Scales..."}


def test_a_rough_start_moves_only_the_lines_before_it():
    base = loads(talk())
    before = {line.text: line.start_ms for line in base.lines}
    loose = loosen(
        base, random.Random(1), join_share=0, split_share=0, drop_share=0, rough_until_ms=40_000
    )
    early = [line.start_ms - before[line.text] for line in loose.lines if line.start_ms < 38_000]
    late = [line.start_ms - before[line.text] for line in loose.lines if line.start_ms > 45_000]
    assert max(early) > 700 and max(abs(move) for move in late) <= 700


def test_a_song_is_what_a_karaoke_template_leaves_behind():
    extras = song(10_000, 40_000, random.Random(1), "OP", layers=3)
    sung = [extra for extra in extras if extra.comment]
    effects = [extra for extra in extras if extra.effect == "fx"]
    assert len(sung) >= 4 and all("\\k" in extra.text for extra in sung)
    # Every syllable of every sung line, once per layer.
    assert len(effects) >= 8 * 3 * len(sung)
    assert {extra.layer for extra in effects} == {0, 1, 2}
    assert {extra.style for extra in extras} == {"OP-Romaji", "OP-Kanji", "OP-English"}
    assert all(extra.kind == "karaoke" for extra in extras)
    assert min(extra.start_ms for extra in extras) >= 10_000
    assert max(extra.end_ms for extra in extras) <= 40_200


def test_every_second_sign_is_redrawn_for_each_frame():
    extras = signs(0, 60_000, 4, random.Random(2))
    frames = [extra for extra in extras if extra.end_ms - extra.start_ms == 42]
    assert len(frames) > 100 and len(extras) - len(frames) == 2
    assert any("\\p1" in extra.text for extra in frames)
    assert len(signs(0, 60_000, 4, random.Random(2), frame_by_frame=False)) == 4


def test_added_lines_keep_what_a_script_knows_about_them():
    extras = [
        Extra(
            30_000, 34_000, "{\\k20}la{\\k30}li", "karaoke", "OP-Romaji", "karaoke", comment=True
        ),
        Extra(31_000, 31_042, "Harbour", "sign", "Signs", layer=2),
        Extra(400_000, 403_000, "from another episode", "other_content", timed=False),
    ]
    result = distort(
        make_base(), Distortion(offset_ms=1_500), fmt="ass", extras=extras, styles=SONG_STYLES
    )
    stored = loads(dumps(result.subtitle))
    assert len(stored) == len(result.subtitle) == 43
    by_kind = {line.kind: line for line in result.truth if line.kind != "base"}
    events = result.subtitle.ssa.events
    sung, sign = events[by_kind["karaoke"].id], events[by_kind["sign"].id]
    assert sung.is_comment and sung.effect == "karaoke" and sung.style == "OP-Romaji"
    assert sign.layer == 2 and not sign.is_comment
    assert "Signs" in result.subtitle.ssa.styles
    assert by_kind["karaoke"].true_start_ms == 30_000
    other = by_kind["other_content"]
    assert other.true_start_ms is None
    assert result.ideal_plan.content_ops[0].ids == [other.id]


def test_the_ideal_plan_of_a_script_is_cut_where_the_file_has_its_lines():
    # A sign redrawn every frame starts on odd milliseconds. The script keeps
    # hundredths of a second, so the first line after a break is stored a
    # little off, and the plan has to be cut there.
    extras = [Extra(at, at + 42, "Harbour", "sign", "Signs") for at in range(75_281, 80_000, 42)]
    distortion = Distortion(offset_ms=5_400, breaks=[Break(at_ms=75_250, delta_ms=25_000)])
    result = distort(make_base(), distortion, fmt="ass", extras=extras, styles=SONG_STYLES)
    stored = loads(dumps(result.subtitle))
    synced = apply_plan(stored, result.ideal_plan).subtitle
    starts = {line.id: line.start_ms for line in synced.lines}
    assert max(abs(starts[line.id] - line.true_start_ms) for line in result.truth) <= 5
