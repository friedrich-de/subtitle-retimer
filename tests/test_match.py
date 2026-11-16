import numpy as np

from retimer.cache import NoCache
from retimer.embed import Embedder
from retimer.match import (
    best_chain,
    context_texts,
    distinctiveness,
    find_anchors,
    is_generic,
    match_content,
    match_lines,
)
from retimer.reference import timeline_from_subtitle
from retimer.views import MatchLine

from .sync_helpers import SENTENCES, FakeClient, make_subtitle, spread


def embedder() -> Embedder:
    return Embedder(client=FakeClient(), cache=NoCache())


def lines_of(subtitle) -> list[MatchLine]:
    return [
        MatchLine(id=line.id, start_ms=line.start_ms, end_ms=line.end_ms, text=line.text)
        for line in subtitle.lines
    ]


def test_short_lines_are_too_generic_to_anchor_on():
    assert is_generic("Yes.")
    assert is_generic("Thank you.")
    assert is_generic("え？")
    assert not is_generic("Keep the lantern away.")
    assert not is_generic("どこに行くんですか")


def test_context_joins_neighbours_that_are_close_in_time():
    items = [(0, 1_000, "a"), (2_000, 3_000, "b"), (20_000, 21_000, "c")]
    assert context_texts(items) == ["a b", "a b", "c"]


def test_distinctiveness_marks_the_pair_that_stands_out_both_ways():
    scores = np.full((6, 6), 0.3) + np.eye(6) * 0.5
    standing = distinctiveness(scores)
    assert standing.diagonal().min() > 2.0
    assert standing[0, 1] < 0
    assert distinctiveness(np.zeros((0, 0))).size == 0


def test_best_chain_keeps_pairs_in_order():
    weights = np.zeros((4, 4))
    weights[0, 0] = weights[1, 1] = weights[3, 3] = 1.0
    weights[2, 0] = 0.9  # would have to go back in the reference
    assert best_chain(weights) == [(0, 0), (1, 1), (3, 3)]
    assert best_chain(np.zeros((0, 3))) == []


def test_matching_finds_the_same_lines_at_other_times():
    target = make_subtitle(spread(20, first_ms=40_000))
    reference = timeline_from_subtitle(make_subtitle(spread(20)))

    matching = match_lines(lines_of(target), reference, embedder())

    assert len(matching.anchors) == 20
    assert {anchor.offset_ms for anchor in matching.anchors} == {-30_000}
    assert [anchor.ref_index for anchor in matching.anchors] == list(range(20))
    assert matching.result.eligible_lines == 20
    strength = matching.strength()
    assert strength.shape == (20, 20)
    assert strength.diagonal().min() == 1.0


def test_generic_lines_are_no_anchors_and_count_for_less():
    texts = ["Yes.", *SENTENCES[:11]]
    target = make_subtitle(spread(12), texts)
    reference = timeline_from_subtitle(make_subtitle(spread(12), texts))

    matching = match_lines(lines_of(target), reference, embedder())

    assert 0 not in {anchor.target_id for anchor in matching.anchors}
    assert matching.result.eligible_lines == 11
    assert matching.strength()[0].max() <= 0.5


def test_other_content_gives_no_run_of_anchors():
    target = make_subtitle(spread(12), SENTENCES[:12])
    reference = timeline_from_subtitle(make_subtitle(spread(12), SENTENCES[15:27]))
    result = find_anchors(lines_of(target), reference, embedder())
    assert len(result.anchors) < 3


def test_coverage_is_counted_per_region():
    lines = lines_of(make_subtitle(spread(12)))
    coverage = match_content(lines, matched_ids={0, 1, 2, 3, 4, 5})

    assert coverage.eligible_lines == 12
    assert coverage.share == 0.5
    assert len(coverage.regions) == 6
    assert [region.matched_lines for region in coverage.regions] == [2, 2, 2, 0, 0, 0]
