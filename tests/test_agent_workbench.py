import pytest

from retimer.agent import ToolError, Workbench
from retimer.agent.prompt import SYSTEM
from retimer.agent.workbench import clock, reference_units
from retimer.cache import Cache
from retimer.pipeline import prepare
from retimer.plan import RemoveLines, Shift
from retimer.reference import timeline_from_transcript
from retimer.views import Options

from .agent_helpers import FOREIGN, NARRATION, episode, pack, three_parts, timed
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

    with pytest.raises(ToolError, match="L3: not taken. Only lines without counterpart"):
        bench.set_edge_lines([3], [])
    assert not bench.changed

    # Each line is decided on its own: one that does not qualify stops no other.
    answer = bench.set_edge_lines([3, 4, 20], [])
    assert "Removed: L20." in answer and "Nothing is open any more." in answer
    assert "Not changed: L3 to L4. Only lines without counterpart" in answer
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


def test_the_question_of_same_content_shows_the_rest_of_the_target(desk):
    # The next episode of a series: it opens with the lines the reference opens
    # with, and goes on with scenes of its own.
    truth = spread(30)
    other = [f"qa{index} qb{index} qc{index} qd{index} qe{index}" for index in range(30)]
    scenes = [f"za{index} zb{index} zc{index} zd{index} ze{index}" for index in range(30)]
    target = make_subtitle([start + 3_000 for start in truth], SENTENCES[:5] + scenes[5:])

    episode = desk(target, reference=make_subtitle(truth, SENTENCES[:5] + other[5:]))
    question = episode.open_questions()[0]
    assert "Only 6 of 30 comparable lines found a certain counterpart." in question
    # One of the six is a chance pair further on. Where most of them lie is what is said.
    assert "5 of those lie between L0 and L4, and the target runs from L0 to L29." in question
    rest = episode.render_verdict().split("Lines from all through the rest of the target")[1]
    assert "Closest is not the same: read whether the place says what the line says." in rest
    # Twelve lines after the ten of the opening, up to the last one.
    shown = [line.split()[0] for line in rest.splitlines() if line.startswith("    L")]
    assert len(shown) == 12 and shown[0] == "L10" and shown[-1] == "L29"
    assert rest.count("closest: nothing comes close") == 10
    assert (
        'L12 01:25.0 dialogue "za12 zb12 zc12 zd12 ze12"\n      closest: nothing comes close'
    ) in rest
    # A place can come closest without saying the same. It is shown, for the reader to judge.
    assert (
        'L17 01:55.0 dialogue "za17 zb17 zc17 zd17 ze17"\n'
        '      closest: R18 01:58.0 "qa18 qb18 qc18 qd18 qe18"  (stands out 2.8)'
    ) in rest

    # A film whose lines have their counterpart all through shows them line by line.
    every_third = [SENTENCES[index] if index % 3 == 0 else other[index] for index in range(30)]
    film = desk(
        make_subtitle([start + 3_000 for start in truth]),
        reference=make_subtitle(truth, every_third),
    )
    assert film.baseline.alignment.verdict == "aligned" and film.open_questions() == []
    rest = film.render_verdict().split("Lines from all through the rest of the target")[1]
    assert rest.count("(stands out") == 4
    assert (
        'L12 01:25.0 dialogue "you promised to return the silver key"\n'
        '      closest: R12 01:22.0 "you promised to return the silver key"  (stands out 3.9)'
    ) in rest


def test_a_short_target_has_no_rest_to_show(desk):
    truth = spread(8)
    bench = desk(make_subtitle([start + 3_000 for start in truth]), reference=make_subtitle(truth))
    assert "Lines from all through the rest" not in bench.render_verdict()


def test_the_agent_is_told_that_episodes_of_a_series_begin_alike():
    assert "Two episodes of one series are not" in SYSTEM
    assert "an opening that agrees line by line settles nothing" in SYSTEM
    assert "A place that only speaks of the same things is not a counterpart." in SYSTEM


def test_a_file_of_several_episodes_is_a_question_of_its_own(desk):
    target, reference, truth = pack("second")
    bench = desk(target, reference=reference)

    # The routine steps find the episode, and stop at the other one before it.
    assert (
        "24 lines would be over before the reference begins"
        in (bench.baseline.result.report.warnings[-1])
    )
    question = (
        "The target holds more than the reference covers: 24 lines before L24 lie outside "
        "the part in which the reference was found."
    )
    assert bench.open_questions() == [question]
    dossier = bench.dossier()
    assert "call cut_to_reference with first_line 24 and last_line 53" in dossier
    assert "The reference was found in L25 to L53: 29 certain pairs" in dossier
    assert "[outside: over 04:10.0 before the reference begins]" in dossier
    assert f'L24 06:50.0 dialogue "{NARRATION[0]}"  [lands at 00:10.0]' in dossier
    # The questions that only make sense for one episode are not asked yet.
    assert "No timing could be built" not in dossier.split("OPEN QUESTIONS")[1]

    answer = bench.cut_to_reference(24, 53)

    assert "Cut down to L24 to L53. 24 lines outside that part are removed" in answer
    assert "Nothing is open any more." in answer
    assert bench.cover == (24, 53)
    result = bench.draft.result
    assert result.plan.outcome == "synced"
    assert result.plan.content_ops == [
        RemoveLines(reason="absent_from_reference", ids=list(range(24)))
    ]
    assert result.plan.timing_ops == [Shift(ms=-400_000)]
    assert result.report.cut == list(range(24))
    assert starts_of(bench) == truth
    assert "removed, outside the part the reference covers" in bench.read_lines(22, 24)


def test_lines_the_episodes_share_do_not_stop_the_cut(desk):
    # Both episodes begin with the same narration. The other episode's copy of
    # it finds the reference too, so its lines no longer sit at the edge of
    # the file, and naming them one by one gets nowhere.
    target, reference, truth = pack("second", reworded=True)
    bench = desk(target, reference=reference)
    assert [(group.pairs[0], group.pairs[-1]) for group in bench.groups()] == [
        ((0, 0), (1, 1)),
        ((26, 2), (53, 29)),
    ]
    with pytest.raises(ToolError, match="L2 to L23: not taken"):
        bench.set_edge_lines(list(range(2, 24)), [])
    assert "call cut_to_reference with first_line 24 and last_line 53" in bench.dossier()

    bench.cut_to_reference(24, 53)

    # In the part, the narration is the reference's own: every line is placed.
    assert bench.draft.matched == set(range(24, 54))
    assert bench.draft.in_place == 30
    assert starts_of(bench) == truth


def test_an_episode_followed_by_another_is_cut_at_its_end(desk):
    target, reference, truth = pack("first")
    bench = desk(target, reference=reference)

    assert bench.baseline.result.plan.outcome == "partial"
    assert bench.open_questions() == [
        "The target holds more than the reference covers: 24 lines after L29 lie outside "
        "the part in which the reference was found."
    ]
    dossier = bench.dossier()
    assert "call cut_to_reference with first_line 0 and last_line 29" in dossier
    assert "after the end of the reference]" in dossier

    bench.cut_to_reference(0, 29)

    assert bench.draft.result.plan.outcome == "synced"
    assert len(bench.draft.result.subtitle.lines) == 30
    assert starts_of(bench) == truth


def test_a_job_that_keeps_unmatched_lines_keeps_what_follows_the_part(desk):
    target, reference, truth = pack("first")
    bench = desk(target, reference=reference, options=Options(drop_unmatched=False))

    answer = bench.cut_to_reference(0, 29)

    assert "so the lines after the part stay" in answer
    result = bench.draft.result
    assert len(result.subtitle.lines) == 54
    assert result.report.cut == []
    assert "24 after it were kept as the job asks" in result.report.warnings[-1]
    assert starts_of(bench)[:30] == truth

    # Lines before the part cannot be shown at all, whatever the job asks.
    target, reference, truth = pack("second")
    bench = desk(target, reference=reference, options=Options(drop_unmatched=False))
    bench.cut_to_reference(24, 53)
    assert bench.draft.result.report.cut == list(range(24))
    assert starts_of(bench) == truth


def test_a_cut_has_to_hold_what_the_routine_steps_placed(desk):
    target, reference, _ = pack("second")
    bench = desk(target, reference=reference)

    with pytest.raises(ToolError, match="found their counterpart in the reference"):
        bench.cut_to_reference(40, 53)
    with pytest.raises(ToolError, match="L53 comes after L24"):
        bench.cut_to_reference(53, 24)
    with pytest.raises(ToolError, match="is the whole file"):
        bench.cut_to_reference(0, 53)
    with pytest.raises(ToolError, match="There is no line L99"):
        bench.cut_to_reference(24, 99)
    assert bench.cover is None and not bench.changed


def test_a_cut_can_be_moved_and_taken_back(desk):
    target, reference, truth = pack("second")
    bench = desk(target, reference=reference)

    bench.cut_to_reference(26, 53)
    assert len(bench.draft.result.subtitle.lines) == 28
    bench.cut_to_reference(24, 53)
    assert starts_of(bench) == truth

    answer = bench.cut_to_reference(0, 53)

    assert "The cut was taken back" in answer
    assert bench.cover is None and not bench.changed
    assert bench.draft is bench.original
    assert len(bench.open_questions()) == 1


def test_lines_outside_the_part_cannot_be_paired(desk):
    target, reference, _ = pack("second")
    bench = desk(target, reference=reference)
    bench.cut_to_reference(24, 53)

    answer = bench.pair_lines(pairs((3, 3)))

    assert "L3 = R3: not taken. L3 is outside the part the reference covers." in answer
    assert bench.pairs == {}


def test_the_next_episode_cannot_be_cut_down_to_the_recap_it_shares(desk):
    # Another episode that begins with the reference's first eight lines, as
    # a recap does. By the rules that is the same content, by a small margin.
    truth = spread(30)
    target = make_subtitle(truth, SENTENCES[:8] + episode(2, 24)[2:])
    bench = desk(target, reference=make_subtitle(truth))
    assert bench.baseline.alignment.verdict == "aligned"
    assert any(question.startswith("Same content?") for question in bench.open_questions())
    # The reference was not found as a whole, so no cut is offered.
    assert bench.excess() is None

    with pytest.raises(ToolError, match="Nothing says that the target holds more"):
        bench.cut_to_reference(0, 7)
    assert bench.cover is None


def test_other_content_cannot_be_cut_down_to_anything(desk):
    truth = spread(30)
    target = make_subtitle(truth, episode(2, 30))
    bench = desk(target, reference=make_subtitle(truth))
    assert bench.baseline.alignment.verdict == "no_match"

    with pytest.raises(ToolError, match="did not find the reference in the target"):
        bench.cut_to_reference(0, 9)


def test_a_preview_the_reference_lacks_is_no_file_of_several_episodes(desk):
    # Five lines that follow the last line closely stay with their episode.
    truth = spread(30)
    preview = [truth[-1] + 12_000 + 4_000 * index for index in range(5)]
    target = make_subtitle(
        [start + 4_000 for start in truth + preview], SENTENCES[:30] + episode(2, 7)[2:]
    )
    bench = desk(target, reference=make_subtitle(truth))

    assert bench.excess() is None


@pytest.mark.parametrize("where", ["start", "end"])
def test_a_file_timed_in_stretches_is_no_file_of_several_episodes(desk, monkeypatch, where):
    # Seven lines are in sync and the rest is off by 400 s: under the timing of the
    # rest they would land far outside the reference. The reference covers them all
    # the same, and five of the seven found their counterpart at a shift of their own.
    texts = list(SENTENCES[:30])
    if where == "start":
        truth = spread(30)
        starts = [start + (0 if index < 7 else 400_000) for index, start in enumerate(truth)]
        texts[3], texts[6] = FOREIGN[:2]
    else:
        truth = spread(30, first_ms=500_000)
        starts = [start - (400_000 if index < 23 else 0) for index, start in enumerate(truth)]
        texts[23], texts[24] = FOREIGN[:2]
    bench = desk(make_subtitle(starts, texts), reference=make_subtitle(truth))

    assert sorted(block.shift_ms for block in bench.baseline.blocks) in (
        [-400_000, 0],
        [0, 400_000],
    )
    assert bench.excess() is None
    assert not any("more than the reference covers" in q for q in bench.open_questions())

    # Without the look at the stretch, the seven lines would count as another episode.
    monkeypatch.setattr("retimer.agent.workbench.MIN_STRETCH", 99)
    again = desk(make_subtitle(starts, texts), reference=make_subtitle(truth))
    assert again.excess() is not None


def test_songs_and_lines_without_a_time_are_no_other_episode(desk):
    # Ten lines that carry no time open the file, and ten lines of the ending
    # song follow the last line after a minute. The reference has neither.
    truth = spread(30)
    untimed = [(0, 0, f"amber{index} gravel{index} tinsel{index}") for index in range(10)]
    spoken = [
        (start + 4_000, start + 6_000, text)
        for start, text in zip(truth, SENTENCES[:30], strict=True)
    ]
    after = [truth[-1] + 64_000 + 5_000 * index for index in range(10)]
    song = [
        (start, start + 2_000, f"♪ la la lantern {index} ♪") for index, start in enumerate(after)
    ]
    bench = desk(timed(untimed + spoken + song), reference=make_subtitle(truth))

    assert bench.baseline.alignment.verdict == "aligned"
    assert bench.excess() is None
    with pytest.raises(ToolError, match="Nothing says that the target holds more"):
        bench.cut_to_reference(10, 39)

    # Ten spoken lines in the place of the song are content the reference lacks.
    said = [
        (start, start + 2_000, text) for start, text in zip(after, episode(2, 12)[2:], strict=True)
    ]
    bench = desk(timed(untimed + spoken + said), reference=make_subtitle(truth))
    assert bench.open_questions()[0] == (
        "The target holds more than the reference covers: 10 lines after L39 lie outside "
        "the part in which the reference was found."
    )


def test_a_cut_is_not_taken_where_the_question_is_not_asked(desk):
    # The reference holds the last ten lines only, as a track of signs and songs
    # holds the ending song. The lines before it are shown in the video all the same.
    truth = spread(30)
    target = make_subtitle([start + 4_000 for start in truth])
    bench = desk(target, reference=make_subtitle(truth[20:], SENTENCES[20:30]))
    assert bench.baseline.alignment.verdict == "aligned"
    assert bench.excess() is None

    with pytest.raises(ToolError, match="Nothing says that the target holds more"):
        bench.cut_to_reference(20, 29)
    assert bench.cover is None


def test_a_reference_of_a_minute_is_no_episode_to_cut_down_to(desk):
    # A track of signs that ends after a minute. The episode goes on for
    # three more, after a pause, with lines the track has nothing for.
    truth = spread(10)
    starts = [start + 4_000 for start in truth]
    starts += [truth[-1] + 44_000 + 6_000 * index for index in range(30)]
    target = make_subtitle(starts, SENTENCES[:10] + episode(2, 32)[2:])
    bench = desk(target, reference=make_subtitle(truth))
    assert bench.baseline.alignment.verdict == "aligned"

    assert bench.excess() is None
    with pytest.raises(ToolError, match="Nothing says that the target holds more"):
        bench.cut_to_reference(0, 9)
    assert bench.cover is None

    # The same file against a reference that goes on for three minutes is asked about.
    longer = spread(30)
    starts = [start + 4_000 for start in longer]
    starts += [longer[-1] + 44_000 + 6_000 * index for index in range(30)]
    target = make_subtitle(starts, SENTENCES[:30] + episode(2, 32)[2:])
    bench = desk(target, reference=make_subtitle(longer))
    assert "30 lines after L29 lie outside the part" in bench.open_questions()[0]


def test_lines_shown_while_the_reference_runs_are_not_cut_away(desk):
    # The episode opens with eight lines the reference has no line for, and
    # another episode follows it. Only that one lies outside the reference.
    truth = spread(30)
    own = episode(3, 10)[2:] + list(SENTENCES[8:30])
    other = episode(2, 24)[2:]
    starts = [start + 1_500 for start in truth]
    starts += [start + truth[-1] + 400_000 for start in spread(len(other))]
    bench = desk(make_subtitle(starts, own + other), reference=make_subtitle(truth))
    assert "22 lines after L29 lie outside the part" in bench.open_questions()[0]

    with pytest.raises(
        ToolError, match="8 lines outside L8 to L29 are shown while the reference runs"
    ):
        bench.cut_to_reference(8, 29)
    assert bench.cover is None

    assert "Cut down to L0 to L29" in bench.cut_to_reference(0, 29)
    assert len(bench.draft.result.subtitle.lines) == 30


def test_what_follows_the_reference_for_minutes_is_not_its_last_scene(desk):
    # The next episode follows without a pause. Forty of its lines go on for four minutes.
    truth = spread(30)
    other = episode(2, 42)[2:]
    starts = [start + 1_500 for start in truth]
    starts += [truth[-1] + 9_500 + 6_000 * index for index in range(len(other))]
    bench = desk(make_subtitle(starts, SENTENCES[:30] + other), reference=make_subtitle(truth))

    assert bench.open_questions() == [
        "The target holds more than the reference covers: 40 lines after L29 lie outside "
        "the part in which the reference was found."
    ]


def test_the_agent_is_told_what_a_file_of_several_episodes_is():
    assert "The target holds more than the reference covers" in SYSTEM
    assert "call cut_to_reference with the first and the last line of the part" in SYSTEM
    assert "Do not cut when the lines outside carry on" in SYSTEM
    assert "do not make them part of it" in SYSTEM
