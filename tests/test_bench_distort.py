import random

import pytest

from retimer import apply_plan, classify_lines, dumps, loads
from retimer.bench.cases import FPS_FILM, build_distortion, choose_breaks, seed_recipes
from retimer.bench.distort import Break, Distortion, DistortionError, Noise, distort

from .bench_helpers import make_base


def starts(subtitle):
    return [line.start_ms for line in subtitle.lines]


def undo(result):
    """Apply the ideal plan and return the error of every timed line, in ms."""
    synced = apply_plan(result.subtitle, result.ideal_plan).subtitle
    new_start = {line.id: line.start_ms for line in synced.lines}
    return [
        abs(new_start[line.id] - line.true_start_ms)
        for line in result.truth
        if line.true_start_ms is not None
    ], synced


def test_identity_keeps_the_file_and_needs_no_plan():
    base = make_base()
    result = distort(base, Distortion())
    assert starts(result.subtitle) == starts(base)
    assert result.ideal_plan.outcome == "already_in_sync"
    assert result.ideal_plan.timing_ops == []
    assert [line.true_start_ms for line in result.truth] == starts(base)


def test_offset_moves_every_line_and_the_truth_stays():
    base = make_base()
    result = distort(base, Distortion(offset_ms=-1_500))
    assert starts(result.subtitle) == [start - 1_500 for start in starts(base)]
    assert [line.true_start_ms for line in result.truth] == starts(base)
    assert [op.ms for op in result.ideal_plan.timing_ops] == [1_500]


def test_rate_stretches_starts_and_durations():
    base = make_base()
    rate = 25 / FPS_FILM
    result = distort(base, Distortion(rate=rate))
    first = result.subtitle.lines[0]
    assert first.start_ms == round(20_000 * rate)
    assert first.duration_ms == pytest.approx(2_500 * rate, abs=1)
    errors, _ = undo(result)
    assert max(errors) <= 1


def test_an_inserted_segment_pushes_later_lines_back():
    base = make_base()
    result = distort(base, Distortion(breaks=[Break(at_ms=59_000, delta_ms=30_000)]))
    moved = [new - old for new, old in zip(starts(result.subtitle), starts(base), strict=True)]
    assert moved[:10] == [0] * 10
    assert set(moved[10:]) == {30_000}
    assert result.dropped_base_ids == []
    shift = result.ideal_plan.timing_ops[0]
    assert (shift.from_ms, shift.to_ms, shift.ms) == (90_000, None, -30_000)


def test_a_cut_drops_its_lines_and_pulls_later_lines_forward():
    base = make_base()
    # Lines 10 to 14 start inside the cut from 59 s to 79.5 s.
    result = distort(base, Distortion(breaks=[Break(at_ms=59_000, delta_ms=-20_500)]))
    assert result.dropped_base_ids == [10, 11, 12, 13, 14]
    assert len(result.subtitle) == 35
    assert [line.base_id for line in result.truth[:11]] == [*range(10), 15]
    assert result.subtitle.lines[10].start_ms == 80_000 - 20_500
    errors, _ = undo(result)
    assert max(errors) == 0


def test_breaks_that_overlap_are_refused():
    base = make_base()
    crossing = [Break(at_ms=30_000, delta_ms=-40_000), Break(at_ms=50_000, delta_ms=10_000)]
    with pytest.raises(DistortionError):
        distort(base, Distortion(breaks=crossing))
    with pytest.raises(DistortionError):
        distort(base, Distortion(offset_ms=-30_000))


def test_noise_lines_carry_their_kind_and_trailing_lines_have_no_true_time():
    base = make_base(pause_every=10)
    noise = Noise(sound_descriptions=4, credits=2, karaoke_lines=5, trailing_lines=3)
    result = distort(base, Distortion(offset_ms=2_000, noise=noise), fmt="ass", seed=7)
    kinds = [line.kind for line in result.truth]
    assert kinds.count("sound_description") == 4
    assert kinds.count("credit") == 2
    assert kinds.count("karaoke") == 5
    assert kinds.count("trailing") == 3
    assert kinds.count("base") == 40

    trailing = [line for line in result.truth if line.kind == "trailing"]
    assert all(line.true_start_ms is None for line in trailing)
    assert [line.id for line in trailing] == [len(result.truth) - 3 + i for i in range(3)]
    removal = result.ideal_plan.content_ops[0]
    assert removal.ids == [line.id for line in trailing]


def test_the_classifier_recognizes_the_added_noise():
    base = make_base()
    noise = Noise(sound_descriptions=4, credits=2, karaoke_lines=5)
    for fmt in ("srt", "ass"):
        result = distort(base, Distortion(noise=noise), fmt=fmt, seed=3)
        reloaded = loads(dumps(result.subtitle))
        labels = {label.id: label.label for label in classify_lines(reloaded)}
        expected = {"sound_description": "sound_description", "credit": "credit", "karaoke": "song"}
        for line in result.truth:
            assert labels[line.id] == expected.get(line.kind, "dialogue"), (fmt, line)


def test_noise_stays_clear_of_an_early_shift():
    base = make_base()
    noise = Noise(credits=2, karaoke_lines=6)
    result = distort(base, Distortion(offset_ms=-15_000, noise=noise))
    assert min(starts(result.subtitle)) >= 0


@pytest.mark.parametrize("seed", range(12))
def test_the_ideal_plan_undoes_every_recipe(seed):
    base = make_base(lines=60)
    for recipe in seed_recipes():
        distortion = build_distortion(base, recipe, random.Random(seed))
        result = distort(base, distortion, fmt=recipe.target_format, seed=seed)
        errors, synced = undo(result)
        assert max(errors) <= 1, recipe.name
        assert len(synced) == sum(1 for line in result.truth if line.true_start_ms is not None)
        if recipe.family == "none":
            assert result.ideal_plan.outcome == "already_in_sync"


@pytest.mark.parametrize("seed", range(20))
def test_chosen_breaks_leave_lines_in_every_block(seed):
    base = make_base(lines=30)
    rng = random.Random(seed)
    breaks = choose_breaks(base, 5, ("insert", "cut"), rng)
    assert 1 <= len(breaks) <= 5
    result = distort(base, Distortion(breaks=breaks))
    # One shift per block that moved, and no block lost all of its lines.
    assert len(result.ideal_plan.timing_ops) == len(breaks)
    assert len(result.subtitle) >= 30 - 5 * 6


def test_short_files_get_fewer_breaks():
    assert choose_breaks(make_base(lines=5), 3, ("insert",), random.Random(0)) == []
    assert len(choose_breaks(make_base(lines=13), 5, ("insert",), random.Random(0))) == 2
