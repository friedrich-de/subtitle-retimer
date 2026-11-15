import json

from retimer import apply_plan, load
from retimer.bench.cases import generate_cases, load_case, load_cases, seed_recipes

from .bench_helpers import write_seeds


def test_generated_cases_cover_the_recipes_and_reload(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    out = tmp_path / "cases"
    ids = generate_cases(seeds, tmp_path / "media", out)
    recipes = seed_recipes()
    # Two seeds, two languages each, one subtitle pairing per language, plus wrong content.
    assert len(ids) == 2 * 2 * len(recipes) + 2
    assert "alpha.en.subtitle-ja.blocks-3" in ids
    assert "alpha.en.subtitle.wrong-content-beta" in ids

    cases = load_cases(out)
    assert [case.case.id for case in cases] == sorted(ids)
    families = {case.case.family for case in cases}
    assert families == {"none", "constant", "rate", "blocks", "mixed", "noise", "wrong_content"}
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
    assert len(with_audio) == 5 * len(seed_recipes())
    both = load_case(tmp_path / "b" / "alpha.en.both-ja.late-8s")
    assert both.audio_path == (tmp_path / "media" / "alpha.ogg").resolve()
    assert both.case.reference.audio_language == "en"
    assert both.reference_subtitle_path.name == "ja.srt"


def test_only_filters_cases_by_id(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    assert len(load_cases(tmp_path / "cases", "alpha.en.subtitle-ja.rate")) == 3
