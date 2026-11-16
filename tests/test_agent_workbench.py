import pytest

from retimer.agent import ToolError, Workbench
from retimer.agent.workbench import clock, reference_units
from retimer.cache import Cache
from retimer.pipeline import prepare
from retimer.plan import RemoveLines, Shift
from retimer.reference import timeline_from_transcript

from .agent_helpers import FOREIGN, three_parts
from .sync_helpers import (
    SENTENCES,
    FakeClient,
    make_subtitle,
    make_transcript,
    spread,
    store_transcript,
)

OUTRO = "next time our heroes travel north by sea"
CREDIT = "Subtitles by the Example Group"


@pytest.fixture
def desk(tmp_path):
    """Builds the workbench of a job, with the hosted services replaced."""
    cache = Cache(tmp_path / "cache")

    def build(target, **kwargs) -> Workbench:
        return Workbench(prepare(target, client=FakeClient(), cache=cache, **kwargs))

    build.cache = cache
    build.audio = tmp_path / "film.ogg"
    return build


def starts_of(bench: Workbench) -> list[int]:
    return [line.start_ms for line in bench.draft.result.subtitle.lines]


def pairs(*found: tuple[int, int]) -> list[dict]:
    return [{"line": line, "place": place} for line, place in found]


def test_times_are_shown_to_the_tenth_of_a_second():
    assert clock(83_460) == "01:23.5"
    assert clock(3_723_000) == "1:02:03.0"
    assert clock(-1_500) == "-00:01.5"


def test_a_transcribed_utterance_is_split_into_clauses():
    transcript = make_transcript(
        [10_000, 20_000],
        ["the harbor was empty, when we finally arrived there", "nobody told me"],
    )
    units = reference_units(timeline_from_transcript(transcript))

    assert [(unit.event, unit.start_ms, unit.text) for unit in units] == [
        (0, 10_000, "the harbor was empty,"),
        (0, 11_200, "when we finally arrived there."),
        (1, 20_000, "nobody told me."),
    ]
    assert all(unit.heard for unit in units)
    # A clause ends where the next one begins.
    assert units[0].end_ms == units[1].start_ms


def test_a_line_of_a_reference_subtitle_is_one_place(desk):
    bench = desk(make_subtitle(spread(20)), reference=make_subtitle(spread(20)))
    assert [unit.start_ms for unit in bench.units] == spread(20)
    assert not any(unit.heard for unit in bench.units)


def test_a_draft_that_stands_leaves_nothing_open(desk):
    target = make_subtitle([start + 3_500 for start in spread(30)])
    bench = desk(target, reference=make_subtitle(spread(30)))

    assert bench.open_questions() == []
    assert not bench.changed
    dossier = bench.dossier()
    assert "Target: 30 lines (30 dialogue), ids L0 to L29" in dossier
    assert "Reference: a subtitle that is in sync. 30 places, R0 to R29" in dossier
    assert "B1: L0 to L29, moved -3.5 s, 30 of 30 dialogue lines placed" in dossier
    assert dossier.endswith("OPEN QUESTIONS\nNothing is open.")


def test_unplaced_lines_between_two_blocks_are_an_open_question(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -7_700]
    assert bench.open_questions() == [
        "3 lines (L12 to L14) found no counterpart. They sit between block B1 and block B2."
    ]
    (run,) = bench.runs()
    assert (run.position, run.line_ids, run.before, run.after) == ("between", [12, 13, 14], 11, 15)
    # Both sides keep their order, so only the places between the two neighbours come into it.
    assert (run.first_unit, run.last_unit) == (12, 14)
    dossier = bench.dossier()
    assert f'L12 01:27.3 dialogue "{FOREIGN[0]}"  [no counterpart found]' in dossier
    assert 'R13 01:28.0 "the engine failed halfway up the mountain"' in dossier
    assert "R15" not in dossier.split("What the reference holds between those two:")[1]


def test_the_distances_between_lines_suggest_where_they_belong(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))
    (run,) = bench.runs()

    ways = bench.by_distance(run)

    assert ways[0] == [(12, 12), (13, 13), (14, 14)]
    assert "L12 = R12, L13 = R13, L14 = R14  (all 3 lines)" in bench.render_run(run)


def test_pairing_lines_opens_the_block_they_need(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    answer = bench.pair_lines(pairs((12, 12), (13, 13), (14, 14)))

    assert "L13 = R13: in place (+0.0 s off)." in answer
    assert "Nothing is open any more." in answer
    assert bench.changed
    assert starts_of(bench) == truth
    plan = bench.draft.result.plan
    assert plan.outcome == "synced"
    assert [op.ms for op in plan.timing_ops] == [-2_000, -5_300, -7_700]
    assert all(isinstance(op, Shift) for op in plan.timing_ops)
    assert bench.draft.in_place == 30

    again = bench.unpair_lines([12, 13, 14])
    assert "Unpaired: L12, L13, L14." in again
    assert not bench.changed
    assert bench.draft is bench.baseline


def test_a_line_that_is_settled_cannot_be_paired(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    answer = bench.pair_lines(pairs((5, 6)))

    assert "L5 = R6: not taken. L5 is not open: it sits in block B1" in answer
    assert bench.pairs == {}
    assert not bench.changed


def test_a_pair_has_to_keep_the_order_of_the_placed_lines(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    answer = bench.pair_lines(pairs((12, 20)))

    assert "L12 = R20: not taken." in answer
    assert "L12 can only be at R12 to R14" in answer
    assert bench.pairs == {}


def test_pairs_that_cross_are_an_error_and_change_nothing(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    with pytest.raises(ToolError, match="L12 = R13 and L13 = R12 cross"):
        bench.pair_lines(pairs((12, 13), (13, 12)))

    assert bench.pairs == {}
    assert bench.draft is bench.baseline


def test_lines_and_places_that_do_not_exist_are_errors(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    with pytest.raises(ToolError, match="There is no line L99"):
        bench.pair_lines(pairs((99, 3)))
    with pytest.raises(ToolError, match="The reference has R0 to R29"):
        bench.pair_lines(pairs((12, 30)))
    with pytest.raises(ToolError, match="There is no place R-1"):
        bench.read_reference(-1, 4)


def test_short_lines_said_within_one_place_share_it(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    answer = bench.pair_lines(pairs((12, 12), (13, 12)))

    assert "L12 = R12: in place" in answer
    assert "L13 = R12: taken. It follows another line in that place." in answer
    # Only the first line of a place says when the block starts. The other moves with it.
    assert [pin.line_id for pin in bench.adjustments().pins] == [12]
    new = {line.id: line.start_ms for line in bench.draft.result.subtitle.lines}
    assert (new[12], new[13]) == (truth[12], truth[13])


def test_one_line_of_a_reference_subtitle_can_be_a_block_of_its_own(desk):
    target, truth = three_parts(second=13)
    bench = desk(target, reference=make_subtitle(truth))

    answer = bench.pair_lines(pairs((12, 12)))

    assert "L12 = R12: in place (+0.0 s off)." in answer
    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -5_300, -7_700]
    assert starts_of(bench) == truth


def test_one_pair_does_not_move_a_line_by_what_two_subtitles_differ_anyway(desk):
    # Line 12 belongs to the first part. The reference shows its counterpart 0.8 s
    # later than this subtitle would, as another hand timing the same line does.
    target, truth = three_parts(offsets=(2_000, 2_000, 7_700), second=13)
    styled = [start + (800 if index == 12 else 0) for index, start in enumerate(truth)]
    bench = desk(target, reference=make_subtitle(styled))
    assert bench.open_questions() == [
        "1 line (L12) found no counterpart. It sits between block B1 and block B2."
    ]

    answer = bench.pair_lines(pairs((12, 12)))

    assert (
        "L12 = R12: taken. The line goes with block B1 and starts -0.8 s from the place" in answer
    )
    assert bench.pairs == {12: 12}
    # The pair says which block the line belongs to. It does not move the line alone.
    assert [(pin.line_id, pin.slack_ms) for pin in bench.adjustments().pins] == [(12, 1_500)]
    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -7_700]
    assert bench.block_of(12) == 0
    assert starts_of(bench) == truth
    assert bench.open_questions() == []

    bench.unpair_lines([12])
    assert not bench.changed


def test_one_line_heard_in_a_transcript_is_no_block_of_its_own(desk):
    target, truth = three_parts(second=13)
    store_transcript(desk.cache, desk.audio, make_transcript(truth))
    bench = desk(target, audio=desk.audio)
    assert bench.open_questions() == [
        "1 line (L12) found no counterpart. It sits between block B1 and block B2."
    ]

    answer = bench.pair_lines(pairs((12, 12)))

    assert "L12 = R12: not taken. It would move this one line +2.4 s" in answer
    assert bench.pairs == {}
    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -7_700]


def test_pairs_on_places_the_transcript_dated_wrongly_are_taken_back(desk):
    truth = spread(30)
    texts = list(SENTENCES[:30])
    texts[15:18] = FOREIGN
    target = make_subtitle(
        [start + (2_000 if index < 18 else 7_700) for index, start in enumerate(truth)], texts
    )
    # The words of two utterances are right and their times four seconds late.
    heard = [start + (4_000 if index in (15, 16) else 0) for index, start in enumerate(truth)]
    store_transcript(desk.cache, desk.audio, make_transcript(heard))
    bench = desk(target, audio=desk.audio)

    answer = bench.pair_lines(pairs((15, 15), (16, 16), (17, 17)))

    assert "L15 = R15: not taken. It would move the line +4.0 s against the lines before" in answer
    assert "the time the reference gives for R16 is probably wrong" in answer
    assert "L17 = R17: in place" in answer
    assert bench.pairs == {17: 17}
    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -7_700]
    assert starts_of(bench) == truth


def test_trailing_lines_without_counterpart_are_removed_or_kept_by_decision(desk):
    truth = spread(20)
    target = make_subtitle(
        [start + 4_000 for start in truth] + [truth[-1] + 14_000, truth[-1] + 18_000],
        SENTENCES[:20] + [OUTRO, CREDIT],
    )
    bench = desk(target, reference=make_subtitle(truth))

    # The rules remove the credit. Whether the preview is spoken they cannot tell.
    assert bench.draft.result.report.trimmed == [21]
    assert bench.open_questions() == [
        "2 lines after the last placed line (L20 to L21) found no counterpart."
    ]
    assert "The reference holds nothing after the last placed line." in bench.dossier()
    assert bench.edge_lines() == {20, 21}

    with pytest.raises(ToolError, match="L3 is not a line without counterpart"):
        bench.set_edge_lines([3], [])

    answer = bench.set_edge_lines([20], [])
    assert "Removed: L20." in answer and "Nothing is open any more." in answer
    assert bench.draft.result.plan.content_ops == [
        RemoveLines(reason="absent_from_reference", ids=[20, 21])
    ]

    bench.set_edge_lines([], [20, 21])
    assert bench.draft.result.plan.content_ops == []
    assert len(bench.draft.result.subtitle.lines) == 22
    assert bench.open_questions() == []


def test_a_block_that_rests_on_little_is_confirmed_by_reading(desk):
    # Five short answers after a cut: too generic to be certain pairs, so the
    # block they form has nothing certain behind it.
    truth = spread(35)
    texts = SENTENCES[:30] + ["Yes.", "No.", "Okay.", "Right.", "Sure."]
    target = make_subtitle(
        [start + (2_000 if index < 30 else 9_000) for index, start in enumerate(truth)], texts
    )
    bench = desk(target, reference=make_subtitle(truth, texts))

    assert bench.baseline.result.plan.outcome == "partial"
    assert bench.weak_blocks() == [1]
    assert bench.open_questions() == [
        "Block B2 rests on little: 5 of 5 lines placed, confidence 0.33."
    ]
    assert 'L31 03:25.0 dialogue "No."  [placed] lands on R31 "No."' in bench.dossier()

    # A placed line keeps its place: the same words elsewhere do not move it.
    moved = bench.pair_lines(pairs((30, 32)))
    assert "L30 = R32: not taken. L30 is placed: its words were found at R30" in moved
    assert not bench.changed

    answer = bench.pair_lines(pairs((30, 30), (31, 31), (32, 32)))
    assert "L31 = R31: in place, as the draft had it." in answer
    assert "Nothing is open any more." in answer
    assert bench.draft.result.plan.outcome == "synced"
    assert [block.confidence for block in bench.draft.blocks] == [1.0, 1.0]
    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -9_000]
    assert starts_of(bench) == truth


def test_when_no_timing_could_be_built_the_lines_between_are_the_question(desk):
    # The outer parts are so far apart that the lines between them, moved with
    # either, run into the other. The fixed pipeline has no plan for that.
    target, truth = three_parts(offsets=(2_000, 22_500, 53_800))
    bench = desk(target, reference=make_subtitle(truth))

    assert bench.unbuilt
    assert bench.baseline.result.plan.timing_ops == []
    assert "No valid timing could be built" in bench.baseline.result.report.warnings[-1]
    assert [(group.pairs[0], group.pairs[-1], group.offset_ms) for group in bench.groups()] == [
        ((0, 0), (11, 11), -2_000),
        ((15, 15), (29, 29), -53_800),
    ]
    assert bench.open_questions() == [
        "No timing could be built from the lines that found their counterpart.",
        "3 lines (L12 to L14) found no counterpart. They sit between two stretches of "
        "placed lines that have different timings.",
    ]
    dossier = bench.dossier()
    assert "L0 to L11: 12 pairs, -2.0 s (L0 = R0, L11 = R11)" in dossier
    assert "L15 to L29: 15 pairs, -53.8 s (L15 = R15, L29 = R29)" in dossier
    assert 'R12 01:22.0 "you promised to return the silver key"' in dossier

    answer = bench.pair_lines(pairs((12, 12), (13, 13), (14, 14)))

    assert "Nothing is open any more." in answer
    assert not bench.unbuilt
    assert [block.shift_ms for block in bench.draft.blocks] == [-2_000, -22_500, -53_800]
    assert bench.draft.result.plan.outcome == "synced"
    assert starts_of(bench) == truth


def test_little_agreement_puts_the_verdict_in_question(desk):
    truth = spread(30)
    other = [f"qa{index} qb{index} qc{index} qd{index} qe{index}" for index in range(30)]
    target = make_subtitle([start + 3_000 for start in truth])

    same = desk(target, reference=make_subtitle(truth, SENTENCES[:5] + other[5:]))
    assert same.baseline.alignment.verdict == "aligned"
    assert same.open_questions()[0].startswith("Same content? Only 5 of 30 comparable lines")
    assert "How the reference begins:" in same.dossier()

    rejected = desk(target, reference=make_subtitle(truth, SENTENCES[:4] + other[4:]))
    assert rejected.baseline.result.plan.outcome == "rejected_no_match"
    assert "The rules call that other content." in rejected.open_questions()[0]

    plainly_other = desk(
        make_subtitle(spread(14), SENTENCES[:14]),
        reference=make_subtitle(spread(14), SENTENCES[15:29]),
    )
    assert plainly_other.baseline.result.plan.outcome == "rejected_no_match"
    assert plainly_other.open_questions() == []


def test_another_fit_can_be_chosen_and_taken_back(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    answer = bench.choose_fit("constant")
    assert answer.startswith("Fit: constant.")
    assert len(bench.draft.blocks) == 1
    assert bench.changed

    bench.choose_fit("best")
    assert len(bench.draft.blocks) == 2
    assert not bench.changed

    with pytest.raises(ToolError, match="There is no fit of kind rate_and_more"):
        bench.choose_fit("rate_and_more")


def test_lines_and_the_reference_can_be_read(desk):
    target, truth = three_parts()
    bench = desk(target, reference=make_subtitle(truth))

    lines = bench.read_lines(11, 13).splitlines()
    assert lines[0].startswith('L11 01:18.0 dialogue "I still remember the smell of the bakery"')
    assert lines[0].endswith('[placed] lands on R11 "I still remember the smell of the bakery"')
    assert lines[1].endswith("[no counterpart found] lands where the reference has nothing")
    assert len(lines) == 3

    assert bench.read_reference(3, 2).splitlines() == [
        'R2 00:22.0 "keep the lantern away from the curtains"',
        'R3 00:28.0 "she counted every coin on the table"',
    ]
    found = bench.find_counterparts([3])
    assert 'R3 00:28.0 "she counted every coin on the table"  (stands out' in found


def test_long_answers_are_cut_and_say_so(desk):
    count = 80
    texts = [f"wa{index} wb{index} wc{index} wd{index}" for index in range(count)]
    truth = spread(count)
    target = make_subtitle([start + 1_000 for start in truth], texts)
    bench = desk(target, reference=make_subtitle(truth, texts))

    assert bench.read_lines(0, 79).splitlines()[-1] == (
        "… 20 more lines; ask for a smaller range …"
    )
    assert bench.read_reference(0, 79).splitlines()[-1] == (
        "… 20 more places; ask for a smaller range …"
    )
