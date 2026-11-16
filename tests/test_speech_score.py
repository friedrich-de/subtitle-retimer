import numpy as np

from retimer.match import Anchor
from retimer.score import block_confidence, score_alignment
from retimer.speech import correlate_speech


def test_the_offset_where_lines_meet_speech_is_found():
    rng = np.random.default_rng(2)
    onsets = np.sort(rng.uniform(0, 600_000, 80)).round()
    starts = onsets[10:60] - 7_340

    found = correlate_speech(starts, onsets, window_ms=20_000)

    assert abs(found.offset_ms - 7_340) <= 10
    assert found.matches > 40
    assert found.strength > 5
    assert found.lines == 50


def test_the_search_stays_near_the_centre_and_respects_the_rate():
    onsets = np.arange(20) * 9_700.0 + 5_000
    starts = (onsets - 60_250) / 1.04

    found = correlate_speech(starts, onsets, center_ms=60_000, window_ms=800, rate=1.04)

    assert abs(found.offset_ms - 60_250) <= 10
    assert correlate_speech([], onsets) is None
    assert correlate_speech(starts, []) is None


def test_nothing_in_reach_gives_no_matches():
    found = correlate_speech([0, 1_000], [500_000], center_ms=0, window_ms=800)
    assert found.matches == 0 and found.strength == 0


def anchor(target_id: int, start: int, ref: int) -> Anchor:
    return Anchor(
        target_id=target_id,
        target_start_ms=start,
        ref_index=target_id,
        ref_start_ms=ref,
        offset_ms=ref - start,
        score=4.0,
    )


def test_score_counts_anchors_in_place_per_block():
    anchors = [
        anchor(0, 10_000, 12_000),
        anchor(1, 20_000, 22_000),
        anchor(2, 30_000, 32_300),
        anchor(3, 40_000, 49_000),
        anchor(4, 90_000, 70_000),
        anchor(5, 99_000, 99_000),
    ]
    new_starts = {0: 12_000, 1: 22_000, 2: 32_000, 3: 42_000, 4: 70_100}

    score = score_alignment(anchors, new_starts, [(None, 60_000), (60_000, None)], [8.0, 1.0])

    assert score.anchors == 5
    assert score.within_tolerance == 0.8
    assert score.median_error_ms == 100
    first, second = score.blocks
    assert (first.anchors, first.within_tolerance) == (4, 0.75)
    assert (second.anchors, second.within_tolerance) == (1, 1.0)
    assert first.confidence == 0.75
    assert second.confidence == 0.5


def test_a_loosely_timed_counterpart_allows_its_line_more():
    anchors = [anchor(0, 10_000, 12_000), anchor(1, 20_000, 22_900)]
    new_starts = {0: 12_000, 1: 22_000}

    strict = score_alignment(anchors, new_starts, [(None, None)], [6.0])
    loose = score_alignment(anchors, new_starts, [(None, None)], [6.0], slack_ms={1: 1_000})

    assert strict.within_tolerance == 0.5
    assert loose.within_tolerance == 1.0
    assert loose.blocks[0].confidence == 1.0


def test_confidence_needs_support_and_anchors_in_place():
    assert block_confidence(0, 1.0) == 0.0
    assert block_confidence(6, 1.0) == 1.0
    assert block_confidence(6, None) == 0.5
    assert block_confidence(3, 1.0) == 0.7
    assert block_confidence(12, 0.5) == 0.5


def test_no_anchors_no_figures():
    score = score_alignment([], {}, [(None, None)], [0.0])
    assert score.anchors == 0
    assert score.within_tolerance is None
    assert score.blocks[0].confidence == 0.0
