import numpy as np
import pytest

from retimer.fit import KNOWN_RATES, Evidence, Fit, choose_fit, fit_offsets, with_stretches
from retimer.match import Anchor


def evidence(
    starts,
    refs,
    *,
    strength: float = 1.0,
    extra_refs=(),
    weak=(),
    anchors: bool = True,
) -> Evidence:
    """Lines at `starts` whose counterparts are at `refs`, plus events nobody matches.

    Lines listed in `weak` resemble their counterpart only a little and are no anchors.
    """
    starts = np.asarray(starts, dtype=np.float64)
    refs = np.asarray(refs, dtype=np.float64)
    all_refs = np.concatenate([refs, np.asarray(extra_refs, dtype=np.float64)])
    order = np.argsort(all_refs, kind="stable")
    table = np.zeros((len(starts), len(all_refs)), dtype=np.float32)
    for index in range(len(starts)):
        table[index, index] = 0.4 if index in weak else strength
    found = [
        Anchor(
            target_id=index,
            target_start_ms=int(starts[index]),
            ref_index=int(np.where(order == index)[0][0]),
            ref_start_ms=int(refs[index]),
            offset_ms=int(refs[index] - starts[index]),
            score=4.0,
        )
        for index in range(len(starts))
        if index not in weak
    ]
    return Evidence(
        line_ids=list(range(len(starts))),
        line_starts=starts,
        ref_starts=all_refs[order],
        strength=table[:, order],
        anchors=found if anchors else [],
    )


def spaced(count: int, step: int = 5_000, first: int = 30_000) -> np.ndarray:
    return first + step * np.arange(count, dtype=np.float64)


def test_one_shift_is_a_constant_fit():
    starts = spaced(30)
    fits = fit_offsets(evidence(starts, starts - 4_200))
    fit = choose_fit(fits)

    assert fit.kind == "constant"
    assert fit.rate == 1.0
    assert [block.offset_ms for block in fit.blocks] == [-4_200]
    assert fit.blocks[0].from_ms is None and fit.blocks[0].to_ms is None
    assert fit.matched == 30
    assert fit.median_error_ms == 0
    assert fit.tolerance_ms == 150


def test_scattered_speech_timestamps_still_give_the_middle():
    rng = np.random.default_rng(3)
    starts = spaced(40)
    refs = starts + 12_000 + rng.normal(0, 150, 40)
    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert fit.kind == "constant"
    assert abs(fit.blocks[0].offset_ms - 12_000) < 100
    assert fit.tolerance_ms > 150


def test_a_few_wrong_pairs_do_not_move_the_fit():
    starts = spaced(30)
    refs = starts + 2_000
    refs[[4, 11, 23]] += [40_000, -70_000, 15_000]
    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert fit.kind == "constant"
    assert fit.blocks[0].offset_ms == 2_000
    assert fit.matched == 27
    assert sorted(fit.matched_ids) == [i for i in range(30) if i not in (4, 11, 23)]


@pytest.mark.parametrize("name", ["25 fps to 23.976 fps", "23.976 fps to 25 fps"])
def test_a_frame_rate_conversion_is_named(name):
    rate = KNOWN_RATES[name]
    starts = spaced(60, step=20_000)
    fit = choose_fit(fit_offsets(evidence(starts, starts * rate + 1_500)))

    assert fit.kind == "rate"
    assert fit.rate_name == name
    assert abs(fit.blocks[0].offset_ms - 1_500) <= 1


def test_close_frame_rates_are_told_apart_by_how_tightly_they_fit():
    rng = np.random.default_rng(5)
    starts = spaced(40, step=15_000)
    refs = starts * KNOWN_RATES["25 fps to 24 fps"] + rng.normal(0, 120, 40)
    fit = choose_fit(fit_offsets(evidence(starts, refs)))
    assert fit.rate_name == "25 fps to 24 fps"


def test_an_inserted_stretch_makes_a_second_block():
    true = spaced(30)
    starts = np.where(true >= true[18], true + 60_000, true)
    fit = choose_fit(fit_offsets(evidence(starts, true - 1_000)))

    assert fit.kind == "blocks"
    assert [(block.from_ms, block.offset_ms) for block in fit.blocks] == [
        (None, -1_000),
        (int(starts[18]), -61_000),
    ]
    assert fit.blocks[0].to_ms == int(starts[18])
    assert [block.lines for block in fit.blocks] == [18, 12]


def test_a_cut_makes_a_second_block_too():
    true = np.delete(spaced(34), slice(14, 18))
    starts = np.where(true >= true[14], true - 20_000, true)
    fit = choose_fit(fit_offsets(evidence(starts, true)))

    assert fit.kind == "blocks"
    assert [block.offset_ms for block in fit.blocks] == [0, 20_000]
    assert fit.blocks[1].from_ms == int(starts[14])


def test_lines_without_a_counterpart_go_with_the_longest_pause():
    true = spaced(24)
    starts = np.where(true >= true[12], true + 90_000, true)
    # The two lines on either side of the break resemble nothing in the reference.
    refs = true.copy()
    refs[[11, 12]] += 400_000
    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert [(block.from_ms, block.offset_ms) for block in fit.blocks] == [
        (None, 0),
        (int(starts[12]), -90_000),
    ]


def test_two_agreeing_lines_are_no_block_against_scattered_timestamps():
    rng = np.random.default_rng(8)
    starts = spaced(30)
    refs = starts + rng.normal(0, 200, 30)
    refs[[28, 29]] += 30_000
    fit = choose_fit(fit_offsets(evidence(starts, refs)))
    assert fit.kind == "constant"


def test_three_exact_lines_at_the_end_are_a_block():
    true = spaced(30)
    starts = np.where(true >= true[27], true + 45_000, true)
    fit = choose_fit(fit_offsets(evidence(starts, true)))
    assert [block.offset_ms for block in fit.blocks] == [0, -45_000]


def test_rate_and_blocks_together():
    rate = KNOWN_RATES["23.976 fps to 25 fps"]
    true = spaced(60, step=12_000)
    shifted = np.where(true >= true[35], true + 50_000, true)
    starts = (shifted + 3_000) / rate
    fit = choose_fit(fit_offsets(evidence(starts, true)))

    assert fit.kind == "rate_blocks"
    assert fit.rate_name == "23.976 fps to 25 fps"
    assert [round(block.offset_ms, -1) for block in fit.blocks] == [-3_000, -53_000]


def test_weak_resemblance_counts_when_the_timing_agrees():
    true = spaced(30)
    starts = np.where(true >= true[20], true + 70_000, true)
    weak = set(range(21, 30))
    fit = choose_fit(fit_offsets(evidence(starts, true, weak=weak)))

    assert [block.offset_ms for block in fit.blocks] == [0, -70_000]
    assert fit.blocks[1].matched == 10


def test_every_kind_that_fits_is_reported_best_first():
    true = spaced(30)
    starts = np.where(true >= true[15], true + 60_000, true)
    fits = fit_offsets(evidence(starts, true))

    assert fits[0].kind == "blocks"
    assert "constant" in [fit.kind for fit in fits]
    assert [fit.score for fit in fits] == sorted((fit.score for fit in fits), reverse=True)
    constant = next(fit for fit in fits if fit.kind == "constant")
    assert constant.matched == 15


def test_explained_anchors_are_those_the_fit_puts_in_place():
    starts = spaced(20)
    refs = starts + 5_000
    refs[7] += 9_000
    data = evidence(starts, refs)
    fit = choose_fit(fit_offsets(data))

    assert fit.explained(data.anchors) == set(range(20)) - {7}
    assert fit.residual(data.anchors[7]) == 9_000
    assert fit.block_at(123).offset_ms == 5_000


def test_too_few_anchors_give_no_fit():
    starts = spaced(10)
    assert fit_offsets(evidence(starts, starts, anchors=False)) == []
    assert choose_fit([]) is None


def test_matched_ids_stay_out_of_the_report():
    starts = spaced(10)
    fit = choose_fit(fit_offsets(evidence(starts, starts + 1_000)))
    assert "matched_ids" not in fit.model_dump()
    assert isinstance(Fit.model_validate(fit.model_dump()), Fit)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_file_in_sync_is_not_explained_by_a_frame_rate_it_does_not_have(seed):
    # Fifty-five minutes, in sync. Three lines in ten have a counterpart that
    # starts a little later, as happens where two languages cut a sentence
    # differently. Under a rate a tenth of a percent off, the pairs drift apart
    # by three seconds. That made the tolerance for the rate wide, the wide
    # tolerance let it gather the late lines too, and with blocks that follow
    # the drift it outscored the truth.
    rng = np.random.default_rng(seed)
    starts = spaced(700, step=4_700)
    late = rng.random(700) < 0.3
    refs = starts + rng.normal(0, 25, 700) + np.where(late, rng.uniform(300, 650, 700), 0)

    fits = fit_offsets(evidence(starts, refs))
    fit = choose_fit(fits)

    assert {found.rate for found in fits} == {1.0}
    assert fit.rate_name is None
    at_rest = sum(block.lines for block in fit.blocks if abs(block.offset_ms) <= 60)
    assert at_rest >= 0.6 * 700


def test_a_rate_is_still_found_where_the_pairs_agree_less_closely_under_it():
    # Speech timestamps scatter by a few hundred milliseconds. The right rate
    # still pulls the pairs together, though not to a point.
    rng = np.random.default_rng(3)
    rate = KNOWN_RATES["24 fps to 23.976 fps"]
    starts = spaced(300)
    refs = starts * rate + 2_000 + rng.normal(0, 120, 300)

    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert fit.kind == "rate"
    assert fit.rate_name == "24 fps to 23.976 fps"


def with_neighbours(starts, refs, alike: float = 0.35, reach: int = 2) -> Evidence:
    """Every line also resembles the neighbours of its counterpart a little.

    That is how lines of one scene are: the embedding of a line with its
    context is close to the lines around the one that says the same.
    """
    found = evidence(starts, refs)
    order = np.argsort(np.argsort(np.asarray(refs, dtype=np.float64), kind="stable"))
    for line in range(len(starts)):
        for step in range(-reach, reach + 1):
            column = order[line] + step
            if step and 0 <= column < len(refs):
                found.strength[line, column] = max(found.strength[line, column], alike)
    return found


def test_a_short_scene_before_the_opening_gets_its_own_block():
    # Seven lines before the opening are in place, everything after it is ten
    # seconds late. Pairs with the neighbours of a counterpart gather a few
    # seconds to either side of the large block, and used to take every place
    # among the candidates, so the short block was never tried.
    rng = np.random.default_rng(5)
    gaps = rng.uniform(2_000, 8_000, 320)
    truth = 20_000 + np.cumsum(gaps)
    truth[7:] += 90_000
    starts = truth.copy()
    starts[7:] += 10_000
    found = with_neighbours(starts, truth)

    fit = choose_fit(fit_offsets(found))

    assert fit.kind == "blocks"
    assert [(block.lines, round(block.offset_ms, -2)) for block in fit.blocks] == [
        (7, 0),
        (313, -10_000),
    ]


def test_a_few_late_lines_in_the_middle_are_no_block_of_their_own():
    # Three lines whose counterparts start a little later, between stretches
    # that agree: two languages cut a sentence differently there.
    starts = spaced(120)
    refs = starts.copy()
    refs[60:63] += 450

    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert fit.kind == "constant"
    assert [block.offset_ms for block in fit.blocks] == [0]


def test_an_island_with_many_lines_behind_it_stays():
    starts = spaced(120)
    refs = starts.copy()
    refs[60:75] += 2_000

    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert [(block.lines, block.offset_ms) for block in fit.blocks] == [
        (60, 0),
        (15, 2_000),
        (45, 0),
    ]


def test_a_few_lines_at_the_end_with_their_own_offset_are_a_block():
    starts = spaced(120)
    refs = starts.copy()
    refs[115:] += 3_000

    fit = choose_fit(fit_offsets(evidence(starts, refs)))

    assert [(block.lines, block.offset_ms) for block in fit.blocks] == [(115, 0), (5, 3_000)]


def drifting(count: int = 40, first: int = 20, late: int = 400) -> tuple[Evidence, Fit]:
    """Lines 2 s late as a whole, and from `first` on another `late` ms."""
    refs = [10_000 + 5_000 * index for index in range(count)]
    starts = [ref + 2_000 + (late if index >= first else 0) for index, ref in enumerate(refs)]
    found = evidence(starts, refs)
    return found, choose_fit(fit_offsets(found))


def lines_of(found: Evidence) -> list[tuple[int, int]]:
    pairs = zip(found.line_ids, found.line_starts, strict=True)
    return [(line_id, int(start)) for line_id, start in pairs]


def test_a_stretch_a_little_off_is_scatter_to_the_fit():
    found, fit = drifting()

    # Half the lines are 0.4 s off against the other half. That is within what
    # the pairs of one block are taken to scatter by, so it stays one block.
    assert fit.kind == "constant"
    assert fit.tolerance_ms > 400


def test_a_block_cut_at_a_stretch_times_each_piece_by_its_own_pairs():
    found, fit = drifting()
    cut = int(found.line_starts[20])

    pieces = with_stretches(fit, found.anchors, lines_of(found), [cut])

    assert pieces.kind == "blocks"
    assert [(block.from_ms, block.to_ms) for block in pieces.blocks] == [(None, cut), (cut, None)]
    assert [block.offset_ms for block in pieces.blocks] == [-2_000, -2_400]
    assert [(block.lines, block.matched) for block in pieces.blocks] == [(20, 20), (20, 20)]
    assert sum(block.support for block in pieces.blocks) == pytest.approx(fit.blocks[0].support)
    # The fit that was cut is left as it was.
    assert len(fit.blocks) == 1


def test_a_piece_with_too_few_pairs_stays_with_its_neighbour():
    found, fit = drifting()
    starts = [int(start) for start in found.line_starts]

    # Two lines between two cuts are no piece of their own.
    pieces = with_stretches(fit, found.anchors, lines_of(found), [starts[20], starts[22]])
    assert [(block.from_ms, block.to_ms) for block in pieces.blocks] == [
        (None, starts[22]),
        (starts[22], None),
    ]
    # Neither are two lines before the first cut.
    pieces = with_stretches(fit, found.anchors, lines_of(found), [starts[2], starts[20]])
    assert [(block.from_ms, block.to_ms) for block in pieces.blocks] == [
        (None, starts[20]),
        (starts[20], None),
    ]


def test_cuts_outside_the_lines_change_nothing():
    found, fit = drifting()
    starts = [int(start) for start in found.line_starts]

    assert with_stretches(fit, found.anchors, lines_of(found), []) is fit
    assert with_stretches(fit, found.anchors, lines_of(found), [starts[0]]) is fit
    assert with_stretches(fit, found.anchors, lines_of(found), [starts[-1] + 1]) is fit
