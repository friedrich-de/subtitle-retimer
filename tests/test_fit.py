import numpy as np
import pytest

from retimer.fit import KNOWN_RATES, Evidence, Fit, choose_fit, fit_offsets
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
