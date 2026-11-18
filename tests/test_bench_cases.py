import json

from retimer import apply_plan, load
from retimer.bench.cases import (
    generate_cases,
    load_case,
    load_cases,
    load_seeds,
    other_episode,
    seed_recipes,
)
from retimer.bench.realfile import real_file_recipes

from .bench_helpers import write_seeds


def test_generated_cases_cover_the_recipes_and_reload(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    out = tmp_path / "cases"
    ids = generate_cases(seeds, tmp_path / "media", out)
    recipes = seed_recipes()
    real = real_file_recipes()
    # Two seeds, two languages each, one subtitle pairing per language, plus wrong
    # content. Then every seed once more for each case that replicates a real file.
    assert len(ids) == 2 * 2 * len(recipes) + 2 + 2 * len(real)
    assert "alpha.en.subtitle-ja.blocks-3" in ids
    assert "alpha.en.subtitle.wrong-content-beta" in ids
    assert "alpha.ja.subtitle-en.pack-second-of-two" in ids

    cases = load_cases(out)
    assert [case.case.id for case in cases] == sorted(ids)
    families = {case.case.family for case in cases}
    plain = {"none", "constant", "rate", "blocks", "mixed", "noise", "wrong_content"}
    assert families == plain | {recipe.family for recipe in real}
    assert {case.case.family for case in load_cases(out, families=["pack", "none"])} == {
        "pack",
        "none",
    }
    assert len(load_cases(out, "alpha", families=["karaoke"])) == 5

    only_plain = generate_cases(seeds, tmp_path / "media", tmp_path / "plain", recipes=recipes)
    assert len(only_plain) == 2 * 2 * len(recipes) + 2
    cases = [case for case in cases if case.case.family in plain]
    assert all(case.case.reference.kind == "subtitle" for case in cases)
    assert all(case.reference_subtitle_path.exists() for case in cases)
    assert all(case.audio_path is None for case in cases)


def test_a_stored_case_carries_its_truth_and_ideal_plan(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    case = load_case(tmp_path / "cases" / "alpha.ja.subtitle-en.mixed-rate-offset-2-blocks")
    assert case.case.expected_outcome == "synced"
    assert case.case.reference.subtitle_language == "en"
    target = load(case.target_path)
    assert len(target) == len(case.case.truth)

    synced = apply_plan(target, case.case.ideal_plan).subtitle
    truth = {line.id: line.true_start_ms for line in case.case.truth}
    assert max(abs(line.start_ms - truth[line.id]) for line in synced.lines) <= 1


def test_generation_is_deterministic(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "one")
    generate_cases(seeds, tmp_path / "media", tmp_path / "two")
    name = "beta.en.subtitle-ja.blocks-5"
    first = json.loads((tmp_path / "one" / name / "case.json").read_text())
    second = json.loads((tmp_path / "two" / name / "case.json").read_text())
    assert first == second
    assert len(first["distortion"]["breaks"]) == 5


def test_options_and_format_come_from_the_recipe(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    case = load_case(tmp_path / "cases" / "alpha.en.subtitle-ja.noise-removed")
    assert case.case.target == "target.ass"
    assert case.case.remove_kinds() == {"sound_description", "karaoke"}
    assert load(case.target_path).format == "ass"
    in_sync = load_case(tmp_path / "cases" / "alpha.en.subtitle-ja.in-sync")
    assert in_sync.case.expected_outcome == "already_in_sync"
    assert in_sync.case.remove_kinds() == set()


def test_audio_cases_appear_once_the_audio_is_there(tmp_path):
    seeds = write_seeds(tmp_path / "seeds", names=("alpha",))
    manifest = json.loads((seeds / "seeds.json").read_text())
    manifest["seeds"][0]["audio"] = {"file": "alpha.ogg", "language": "en"}
    (seeds / "seeds.json").write_text(json.dumps(manifest))
    notes = []

    without = generate_cases(seeds, tmp_path / "media", tmp_path / "a", progress=notes.append)
    assert not any(".audio." in case_id or ".both-" in case_id for case_id in without)
    assert any("no audio" in note for note in notes)

    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "alpha.ogg").write_bytes(b"not really audio")
    with_audio = generate_cases(seeds, tmp_path / "media", tmp_path / "b")
    # One seed has no other episode, so the cases that need one are not written.
    assert len(with_audio) == 5 * len(seed_recipes())
    both = load_case(tmp_path / "b" / "alpha.en.both-ja.late-8s")
    assert both.audio_path == (tmp_path / "media" / "alpha.ogg").resolve()
    assert both.case.reference.audio_language == "en"
    assert both.reference_subtitle_path.name == "ja.srt"


def test_only_filters_cases_by_id(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    assert len(load_cases(tmp_path / "cases", "alpha.en.subtitle-ja.rate")) == 3


def test_the_wrong_episode_is_the_next_one_of_the_same_series(tmp_path):
    seeds = write_seeds(
        tmp_path / "seeds", names=("alpha", "beta", "show-01", "show-02", "show-03")
    )
    manifest = json.loads((seeds / "seeds.json").read_text())
    for seed in manifest["seeds"][2:]:
        seed["series"] = "show"
    (seeds / "seeds.json").write_text(json.dumps(manifest))

    listed = load_seeds(seeds).seeds
    pairs = {seed.id: other_episode(listed, seed).id for seed in listed}
    assert pairs == {
        "alpha": "beta",
        "beta": "alpha",
        "show-01": "show-02",
        "show-02": "show-03",
        "show-03": "show-01",
    }
    assert other_episode(listed[:1], listed[0]) is None

    next_episode = [recipe for recipe in real_file_recipes() if recipe.family == "next_episode"]
    ids = generate_cases(
        seeds, tmp_path / "media", tmp_path / "cases", recipes=[], real_recipes=next_episode
    )
    assert "show-03.en.subtitle.wrong-content-show-01" in ids
    assert "alpha.en.subtitle.wrong-content-beta" in ids
    case = load_case(tmp_path / "cases" / "show-03.ja.subtitle-en.next-episode-shared-phrases")
    sent = {line.text for line in load(case.target_path).lines}
    first = {line.text for line in load(seeds / "show-01" / "ja.srt").lines}
    assert len(sent & first) > len(sent) // 2
