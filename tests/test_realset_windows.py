from retimer.realset.windows import (
    WINDOW_MS,
    disagreements,
    judge_sound,
    judge_windows,
    line_shifts,
    segments,
    window_errors,
    window_shifts,
)


def test_errors_are_medians_per_window_of_the_reference():
    evidence = [(0, 10_000), (1, 20_000), (2, 30_000), (3, WINDOW_MS + 5_000), (9, 50_000)]
    starts = {0: 10_100, 1: 20_200, 2: 34_000, 3: WINDOW_MS + 4_000}
    # Line 9 is not in the output and gives no error. One odd pair does not move a median.
    assert window_errors(evidence, starts) == [(0, 3, 200), (1, 1, -1_000)]


def test_a_file_is_verified_when_every_judged_window_is_in_tolerance():
    assert judge_windows([(0, 5, 120), (1, 4, -340), (2, 2, 9_000)]) == ("verified", 2, 0)
    assert judge_windows([(0, 5, 120), (1, 4, -360)]) == ("off", 2, 1)
    # Windows with too few pairs say nothing, and a file with only those is not judged.
    assert judge_windows([(0, 2, 5_000), (3, 1, 0)]) == ("unjudged", 0, 0)
    assert judge_windows([]) == ("unjudged", 0, 0)


def test_shifts_per_line_and_per_window():
    original = {0: 1_000, 1: 2_000, 2: WINDOW_MS + 1_000, 3: WINDOW_MS + 2_000, 4: 5_000}
    moved = {0: 1_500, 1: 2_500, 2: WINDOW_MS + 3_000, 3: WINDOW_MS + 4_000}
    assert line_shifts(original, moved) == {0: 500, 1: 500, 2: 2_000, 3: 2_000}
    assert window_shifts(original, moved) == {0: 500, 1: 2_000}


def test_segments_are_stretches_with_one_shift():
    original = {index: index * 1_000 for index in range(6)}
    moved = {0: 200, 1: 1_210, 2: 2_190, 3: 8_000, 4: 9_000, 5: 10_000}
    assert segments(original, moved) == [(0, 2_000, 200, 3), (3_000, 5_000, 5_000, 3)]


def test_disagreements_are_windows_with_different_shifts():
    one = {0: 1_000, 1: 1_000, 2: 1_000}
    other = {0: 1_250, 1: 1_400, 3: 0}
    assert disagreements(one, other) == [(1, 1_000, 1_400)]
    assert disagreements(one, one) == []


def reading(offset, clear, windows):
    return {"lines": 40, "offset_ms": offset, "strength": 2.0, "clear": clear, "windows": windows}


def test_what_the_sound_check_comes_to():
    assert judge_sound(None) is None
    fine = [[0, 20, 120, 2.1, True], [1, 12, 2_000, 1.1, False], [2, 3, None, None, False]]
    assert judge_sound(reading(150, True, fine)) == "confirmed"
    # One window that reads clear and far off is enough, as a cold open is one window.
    assert judge_sound(reading(150, True, [*fine, [3, 15, -1_800, 1.9, True]])) == "off"
    assert judge_sound(reading(-900, True, fine)) == "off"
    # Nothing clear over the whole file says nothing, whatever the number is.
    assert judge_sound(reading(-900, False, fine)) == "unclear"
    assert judge_sound(reading(None, False, [])) == "unclear"
