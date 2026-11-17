import json

import pytest

from retimer import dumps
from retimer.bench.cases import generate_cases, load_cases
from retimer.bench.harness import run_one
from retimer.bench.runners import PipelineRunner, builtin_runners
from retimer.cache import Cache
from retimer.cli import EXIT_REJECTED, main
from retimer.fit import HOLD_WINDOW_MS, KNOWN_RATES, Evidence, Pin
from retimer.model import Line
from retimer.pipeline import (
    Adjustments,
    BlockReport,
    _beyond_chance,
    _break_start,
    _merge_weakest,
    _rest_on_synced,
    _settle,
    prepare,
    propose,
    sync,
)
from retimer.plan import RemoveLines, Scale, Shift
from retimer.speech import Correlation
from retimer.views import Options

from .bench_helpers import write_seeds
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
def run(tmp_path):
    cache = Cache(tmp_path / "cache")

    def call(target, **kwargs):
        return sync(target, client=FakeClient(), cache=cache, **kwargs)

    call.cache = cache
    return call


def starts_of(subtitle) -> list[int]:
    return [line.start_ms for line in subtitle.lines]


def test_one_shift_against_a_reference_subtitle(run):
    reference = make_subtitle(spread(30))
    target = make_subtitle([start + 3_500 for start in spread(30)])

    result = run(target, reference=reference)

    assert result.plan.outcome == "synced"
    assert result.plan.timing_ops == [Shift(ms=-3_500)]
    assert result.plan.content_ops == []
    assert starts_of(result.subtitle) == spread(30)
    report = result.report
    assert report.chosen == "constant"
    assert report.anchors == 30
    assert report.score.within_tolerance == 1.0
    assert report.reference.kind == "subtitle"
    assert result.plan.confidence >= 0.9
    assert report.cost_usd["embeddings"] > 0


def test_a_file_in_sync_is_left_alone(run):
    reference = make_subtitle(spread(30))
    target = make_subtitle([start + 60 for start in spread(30)])

    result = run(target, reference=reference)

    assert result.plan.outcome == "already_in_sync"
    assert result.plan.timing_ops == []
    assert starts_of(result.subtitle) == starts_of(target)


def test_a_frame_rate_conversion_becomes_a_scale(run):
    rate = KNOWN_RATES["25 fps to 23.976 fps"]
    truth = spread(30, step_ms=25_000)
    target = make_subtitle([round(start / rate) for start in truth])

    result = run(target, reference=make_subtitle(truth))

    assert result.plan.outcome == "synced"
    scale = result.plan.timing_ops[0]
    assert isinstance(scale, Scale) and scale.factor == pytest.approx(rate)
    assert scale.reason == "25 fps to 23.976 fps"
    assert all(
        abs(got - want) <= 2 for got, want in zip(starts_of(result.subtitle), truth, strict=True)
    )


def test_an_inserted_stretch_becomes_a_block_and_signs_stay_on_their_side(run):
    truth = spread(30)
    moved = [start + (75_000 if index >= 15 else 0) + 2_000 for index, start in enumerate(truth)]
    texts = SENTENCES[:15] + ["[music]"] + SENTENCES[15:30]
    target = make_subtitle(moved[:15] + [moved[14] + 3_000] + moved[15:], texts)

    result = run(target, reference=make_subtitle(truth))

    assert result.plan.outcome == "synced"
    assert result.plan.timing_ops == [
        Shift(from_ms=None, to_ms=moved[15], ms=-2_000),
        Shift(from_ms=moved[15], to_ms=None, ms=-77_000),
    ]
    assert result.report.chosen == "blocks"
    new = {line.id: line.start_ms for line in result.subtitle.lines}
    assert new[15] == truth[14] + 3_000
    assert [new[i] for i in range(31) if i != 15] == truth


def test_other_content_is_rejected(run):
    target = make_subtitle(spread(14), SENTENCES[:14])
    reference = make_subtitle(spread(14), SENTENCES[15:29])

    result = run(target, reference=reference)

    assert result.plan.outcome == "rejected_no_match"
    assert result.subtitle is None
    assert result.plan.timing_ops == []
    assert "other content" in result.report.warnings[-1]


def test_a_trailing_credit_outside_the_reference_is_trimmed(run):
    truth = spread(20)
    target = make_subtitle(
        [start + 4_000 for start in truth] + [truth[-1] + 14_000, truth[-1] + 18_000],
        SENTENCES[:20] + [OUTRO, CREDIT],
    )

    result = run(target, reference=make_subtitle(truth))

    assert result.plan.content_ops == [RemoveLines(reason="absent_from_reference", ids=[21])]
    assert result.report.trimmed == [21]
    # A subtitle alone cannot confirm that a spoken line is missing, so it stays.
    assert result.report.unmatched_kept == [20]
    assert len(result.subtitle.lines) == 21

    kept = run(target, reference=make_subtitle(truth), options=Options(drop_unmatched=False))
    assert kept.plan.content_ops == []
    assert kept.report.unmatched_kept == [20, 21]


def test_audio_confirms_that_trailing_lines_are_absent(run, tmp_path):
    truth = spread(20)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth, duration_ms=truth[-1] + 60_000))
    target = make_subtitle(
        [start + 8_000 for start in truth] + [truth[-1] + 20_000], SENTENCES[:20] + [OUTRO]
    )

    result = run(target, audio=audio)

    assert result.plan.outcome == "synced"
    assert result.plan.timing_ops == [Shift(ms=-8_000)]
    assert result.report.trimmed == [20]
    assert starts_of(result.subtitle) == truth
    assert result.report.reference.kind == "audio"
    assert result.report.reference.duration_ms == truth[-1] + 60_000
    assert result.report.cost_usd["transcription"] == 0.0


def test_lines_past_the_end_of_the_audio_are_trimmed(run, tmp_path):
    truth = spread(20)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth, duration_ms=truth[-1] + 4_000))
    target = make_subtitle(truth + [truth[-1] + 30_000], SENTENCES[:20] + [OUTRO])

    result = run(target, audio=audio)

    assert result.plan.outcome == "already_in_sync"
    assert result.report.trimmed == [20]


def test_against_audio_a_small_lead_is_style_and_not_out_of_sync(run, tmp_path):
    truth = spread(20)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth))

    lead = run(make_subtitle([start - 250 for start in truth]), audio=audio)
    late = run(make_subtitle([start + 900 for start in truth]), audio=audio)

    assert lead.plan.outcome == "already_in_sync"
    assert late.plan.outcome == "synced"
    assert late.plan.timing_ops == [Shift(ms=-900)]


def test_when_reference_subtitle_and_audio_disagree_the_audio_wins(run, tmp_path):
    truth = spread(24)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth))
    reference = make_subtitle([start + 5_000 for start in truth])
    target = make_subtitle([start + 12_000 for start in truth])

    result = run(target, audio=audio, reference=reference)

    assert result.plan.outcome == "synced"
    assert starts_of(result.subtitle) == truth
    assert result.report.reference.kind == "both"
    assert any("The audio wins" in warning for warning in result.report.warnings)


def test_when_reference_subtitle_and_audio_agree_it_is_said(run, tmp_path):
    truth = spread(24)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth))

    result = run(
        make_subtitle([start - 7_000 for start in truth]),
        audio=audio,
        reference=make_subtitle(truth),
    )

    assert starts_of(result.subtitle) == truth
    assert "agrees with the audio" in result.report.reference.notes[-1]
    assert result.report.warnings == []


def test_too_little_text_falls_back_to_the_timing_of_speech(run):
    truth = [5_000, 9_300, 21_000, 26_400, 40_100, 48_800, 61_000, 73_500]
    words = ["Yes.", "No.", "Okay.", "Right.", "Sure.", "Fine.", "What?", "Go."]
    target = make_subtitle([start + 2_500 for start in truth], words)

    result = run(target, reference=make_subtitle(truth, words))

    assert result.plan.outcome == "partial"
    assert result.plan.timing_ops == [Shift(ms=-2_500)]
    assert result.report.fallback.offset_ms == -2_500
    assert "Too little text" in result.report.warnings[0]


def test_a_line_that_would_start_before_zero_is_removed(run):
    truth = spread(20, first_ms=6_000)
    target = make_subtitle(
        [1_000] + [start + 9_000 for start in truth],
        ["previously a stranger crossed the frozen lake"] + SENTENCES[:20],
    )

    result = run(target, reference=make_subtitle(truth), options=Options(drop_unmatched=False))

    assert result.plan.content_ops == [RemoveLines(reason="absent_from_reference", ids=[0])]
    assert "before the reference begins" in result.report.warnings[-1]
    assert starts_of(result.subtitle) == truth


def test_a_reference_is_required(run):
    with pytest.raises(ValueError, match="reference"):
        run(make_subtitle(spread(5)))


def block(from_ms, to_ms, shift_ms, support) -> BlockReport:
    return BlockReport(
        from_ms=from_ms,
        to_ms=to_ms,
        shift_ms=shift_ms,
        lines=5,
        matched=3,
        support=support,
        spread_ms=0.0,
    )


def line(line_id: int, start: int, end: int) -> Line:
    return Line(id=line_id, start_ms=start, end_ms=end, text="x", style="", is_comment=False)


def test_the_weakest_block_goes_to_its_stronger_neighbour():
    blocks = [
        block(None, 100, 0, 9.0),
        block(100, 200, 5_000, 1.0),
        block(200, 300, -3_000, 4.0),
        block(300, None, 700, 6.0),
    ]
    merged = _merge_weakest(blocks)
    assert [(b.from_ms, b.to_ms, b.shift_ms) for b in merged] == [
        (None, 200, 0),
        (200, 300, -3_000),
        (300, None, 700),
    ]
    again = _merge_weakest(merged)
    assert [(b.from_ms, b.to_ms, b.shift_ms) for b in again] == [(None, 300, 0), (300, None, 700)]


def test_a_break_goes_to_the_longest_pause_that_leaves_room():
    lines = [
        line(0, 10_000, 12_000),
        line(1, 13_000, 14_000),
        line(2, 50_000, 51_000),
        line(3, 52_000, 54_000),
    ]
    dialogue = {0, 3}
    assert _break_start(lines, dialogue, 52_000, room_ms=30_000, rate=1.0) == 50_000
    assert _break_start(lines, dialogue, 52_000, room_ms=-5_000, rate=1.0) == 50_000
    assert _break_start(lines, {3}, 52_000, room_ms=0, rate=1.0) == 52_000


def test_blocks_that_cross_by_a_hair_are_straightened_out():
    lines = [line(0, 10_000, 11_000), line(1, 70_200, 71_000)]
    blocks = [block(None, 70_200, 0, 5.0), block(70_200, None, -60_350, 5.0)]

    settled = _settle(blocks, lines, 1.0, slack_ms=300)
    assert settled[1].shift_ms == -60_200

    far = [block(None, 70_200, 0, 5.0), block(70_200, None, -65_000, 5.0)]
    assert _settle(far, lines, 1.0, slack_ms=300)[1].shift_ms == -65_000


def test_a_part_in_sync_is_left_alone_and_sets_the_style_for_the_rest():
    blocks = [block(None, 100, 60, 9.0), block(100, None, -20_000, 6.0)]
    rested = _rest_on_synced(blocks, bar_ms=100, exact_ms=100, style=False)
    assert [b.shift_ms for b in rested] == [0, -20_000]

    # Against audio the part in sync shows how far this subtitle leads the voice.
    # The other part was timed by the same hand, so it keeps that lead.
    led = [block(None, 100, -270, 9.0), block(100, None, -20_270, 6.0)]
    rested = _rest_on_synced(led, bar_ms=350, exact_ms=100, style=True)
    assert [b.shift_ms for b in rested] == [0, -20_000]

    out_of_sync = [block(None, 100, -900, 9.0), block(100, None, -20_000, 6.0)]
    assert _rest_on_synced(out_of_sync, bar_ms=350, exact_ms=100, style=True) == out_of_sync
    alone = [block(None, None, 60, 9.0)]
    assert _rest_on_synced(alone, bar_ms=100, exact_ms=100, style=False) == alone


@pytest.fixture
def one_line_unplaced(tmp_path):
    """A file that is two seconds late, with one line that matching cannot place."""
    truth = spread(30)
    texts = list(SENTENCES[:30])
    texts[12] = "Du hast versprochen, den silbernen Schlüssel zurückzugeben"
    target = make_subtitle([start + 2_000 for start in truth], texts)
    prepared = prepare(
        target,
        reference=make_subtitle(truth),
        client=FakeClient(),
        cache=Cache(tmp_path / "cache"),
    )
    return prepared, truth


def test_without_adjustments_a_proposal_is_the_fixed_pipeline(one_line_unplaced):
    prepared, truth = one_line_unplaced

    proposal = propose(prepared)

    assert Adjustments().empty
    assert not Adjustments(fit="constant").empty
    assert Adjustments(held={3: 28_000}).empty
    assert proposal.result.plan.timing_ops == [Shift(ms=-2_000)]
    assert 12 not in proposal.matched and {11, 13} <= proposal.matched
    assert proposal.in_place == 29
    assert proposal.new_times[12][0] == truth[12]
    assert proposal.result.report.agent is None


def test_an_asserted_pair_puts_its_line_where_the_place_is(one_line_unplaced):
    prepared, truth = one_line_unplaced
    start = truth[12] + 2_000

    def proposed(ref_ms: int, slack_ms: int = 0):
        pin = Pin(line_id=12, line_start_ms=start, ref_ms=ref_ms, slack_ms=slack_ms)
        return propose(prepared, Adjustments(pins=[pin]))

    # A pair that agrees with the block of its line joins it, and counts as placed.
    agreeing = proposed(truth[12])
    assert [b.shift_ms for b in agreeing.blocks] == [-2_000]
    assert 12 in agreeing.matched
    assert agreeing.in_place == 30

    # A place that is timed exactly is taken at its word: the line gets a block of its own.
    exact = proposed(truth[12] + 600)
    assert [(b.from_ms, b.shift_ms) for b in exact.blocks] == [
        (None, -2_000),
        (start, -1_400),
        (truth[13] + 2_000, -2_000),
    ]
    assert exact.new_times[12][0] == truth[12] + 600

    # A clause heard in a transcript is timed loosely. Such a pair says which block
    # its line belongs to, and the lines matching found say when the block starts.
    loose = proposed(truth[12] + 600, slack_ms=1_000)
    assert [b.shift_ms for b in loose.blocks] == [-2_000]
    assert 12 in loose.matched
    assert loose.in_place == 30
    assert loose.new_times[12][0] == truth[12]


def test_a_placed_line_is_held_to_its_counterpart_while_pairs_are_asserted(tmp_path):
    truth = spread(30)
    reference = list(SENTENCES[:30])
    # Line 5 is said again later on, so it has two counterparts to choose from.
    reference[20] = SENTENCES[5]
    target = make_subtitle([start + 2_000 for start in truth])
    prepared = prepare(
        target,
        reference=make_subtitle(truth, reference),
        client=FakeClient(),
        cache=Cache(tmp_path / "cache"),
    )
    pin = Pin(line_id=12, line_start_ms=truth[12] + 2_000, ref_ms=truth[12])

    free = Evidence.from_matching(prepared.matching)
    held = Evidence.from_matching(prepared.matching, [pin], {5: truth[5]})

    assert sorted(free.ref_starts[free.strength[5] > 0]) == [truth[5], truth[20]]
    assert list(held.ref_starts[held.strength[5] > 0]) == [truth[5]]
    assert held.held == [(5, float(truth[5]))]
    assert truth[20] - truth[5] > HOLD_WINDOW_MS
    # The pinned line counts for its pair alone, and the pair is an anchor of its own kind.
    assert held.pinned == [(12, float(truth[12]), 0)]
    assert held.strength[12].max() == 3.0 and (held.strength[12] > 0).sum() == 1
    assert [anchor.ref_index for anchor in held.anchors if anchor.target_id == 12] == [-1]


def test_the_reader_decides_about_edge_lines_and_the_verdict(run, tmp_path):
    truth = spread(20)
    target = make_subtitle(
        [start + 4_000 for start in truth] + [truth[-1] + 14_000, truth[-1] + 18_000],
        SENTENCES[:20] + [OUTRO, CREDIT],
    )
    prepared = prepare(target, reference=make_subtitle(truth), client=FakeClient(), cache=run.cache)

    removed = propose(prepared, Adjustments(trim=[20], edges_reviewed=True))
    assert removed.result.report.trimmed == [20, 21]
    assert removed.removed == {20, 21}

    kept = propose(prepared, Adjustments(keep=[21], edges_reviewed=True))
    assert kept.result.report.trimmed == []
    assert kept.result.report.unmatched_kept == [20, 21]

    rejected = propose(prepared, Adjustments(same_content=False))
    assert rejected.result.plan.outcome == "rejected_no_match"
    assert rejected.result.subtitle is None
    assert "Read side by side" in rejected.result.report.warnings[-1]

    one_block = propose(prepared, Adjustments(fit="constant"))
    assert one_block.result.report.chosen == "constant"


def test_the_pipeline_is_a_benchmark_system(tmp_path):
    assert "pipeline" in builtin_runners()
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    runner = PipelineRunner(client=FakeClient(), cache=Cache(tmp_path / "cache"))
    assert runner.unavailable() is None

    for name in ("late-8s", "blocks-2", "rate-25-to-23.976", "in-sync", "noise-kept"):
        (case,) = load_cases(tmp_path / "cases", f"alpha.en.subtitle-ja.{name}")
        record = run_one(case, runner, tmp_path / "work" / name)
        assert record["error"] is None
        assert record["metrics"]["success"], name
        assert record["outcome"] == case.case.expected_outcome
        assert record["details"]["plan"]["outcome"] == record["outcome"]


def test_without_a_key_the_pipeline_says_why_it_cannot_run(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert "OPENROUTER_API_KEY" in PipelineRunner().unavailable()


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr("retimer.embed.OpenRouter", FakeClient)
    monkeypatch.setenv("RETIMER_CACHE_DIR", str(tmp_path / "cache"))


def test_sync_command_writes_the_file_the_plan_and_the_report(offline, tmp_path, capsys):
    truth = spread(20)
    (tmp_path / "target.srt").write_text(dumps(make_subtitle([s + 3_000 for s in truth])))
    (tmp_path / "reference.srt").write_text(dumps(make_subtitle(truth)))

    code = main(
        [
            "sync",
            str(tmp_path / "target.srt"),
            "--reference",
            str(tmp_path / "reference.srt"),
            "-o",
            str(tmp_path / "out.srt"),
            "--plan",
            str(tmp_path / "plan.json"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["outcome"] == "synced"
    assert summary["blocks"] == [{"from_ms": None, "shift_ms": -3_000, "confidence": 1.0}]
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["timing_ops"] == [{"op": "shift", "ms": -3_000}]
    assert json.loads((tmp_path / "report.json").read_text())["chosen"] == "constant"
    assert "00:00:10,000" in (tmp_path / "out.srt").read_text()


def test_sync_command_writes_nothing_for_other_content(offline, tmp_path, capsys):
    (tmp_path / "target.srt").write_text(dumps(make_subtitle(spread(14), SENTENCES[:14])))
    (tmp_path / "reference.srt").write_text(dumps(make_subtitle(spread(14), SENTENCES[15:29])))

    code = main(
        [
            "sync",
            str(tmp_path / "target.srt"),
            "--reference",
            str(tmp_path / "reference.srt"),
            "-o",
            str(tmp_path / "out.srt"),
        ]
    )

    assert code == EXIT_REJECTED
    assert json.loads(capsys.readouterr().out)["outcome"] == "rejected_no_match"
    assert not (tmp_path / "out.srt").exists()


def test_sync_command_needs_a_reference(tmp_path, capsys):
    (tmp_path / "target.srt").write_text(dumps(make_subtitle(spread(5))))
    code = main(["sync", str(tmp_path / "target.srt"), "-o", str(tmp_path / "out.srt")])
    assert code == 1
    assert "--audio" in capsys.readouterr().err


def test_a_target_that_holds_far_more_than_the_reference_is_not_called_synced(run):
    # Several episodes in one file, against the subtitle of the first one.
    truth = spread(20)
    later = [f"{sentence} again in part {part}" for part in (2, 3) for sentence in SENTENCES[:20]]
    target = make_subtitle(
        [start + 4_000 for start in truth]
        + [start + 4_000 + part * 1_500_000 for part in (1, 2) for start in truth],
        SENTENCES[:20] + later,
    )

    result = run(target, reference=make_subtitle(truth))

    assert result.plan.outcome == "partial"
    assert result.plan.timing_ops == [Shift(ms=-4_000)]
    assert "40 lines land more than 5 minutes outside" in result.report.warnings[-1]
    assert len(result.subtitle.lines) == 60


def test_a_preview_the_reference_lacks_is_no_reason_to_doubt_the_rest(run):
    truth = spread(40)
    preview = [truth[-1] + 100_000 + 3_000 * index for index in range(8)]
    target = make_subtitle(
        [start + 4_000 for start in truth + preview],
        [f"{sentence} {index}" for index, sentence in enumerate((SENTENCES * 4)[:48])],
    )
    reference = make_subtitle(
        truth, [f"{sentence} {index}" for index, sentence in enumerate((SENTENCES * 4)[:40])]
    )

    result = run(target, reference=reference)

    assert result.plan.outcome == "synced"
    assert result.report.warnings == []


def test_a_file_with_most_lines_before_the_reference_says_why_it_was_left_alone(run):
    truth = spread(20)
    earlier = [f"{sentence} in the part before" for sentence in SENTENCES[:20]]
    target = make_subtitle(truth + [start + 1_500_000 for start in truth], earlier + SENTENCES[:20])

    result = run(target, reference=make_subtitle(truth))

    assert result.plan.outcome == "partial"
    assert result.plan.timing_ops == []
    assert "20 lines would be over before the reference begins" in result.report.warnings[-1]
    assert "Timing is unchanged" in result.report.warnings[-1]


def test_a_few_lines_meeting_by_accident_are_no_offset():
    # Six signs of a signs-and-songs track against 360 lines: somewhere in a
    # window of four minutes, four of them meet a line start.
    by_accident = Correlation(offset_ms=80_970, matches=4.2, expected=0.22, strength=19, lines=361)
    eight_of_eight = Correlation(offset_ms=-2_500, matches=8, expected=0.04, strength=200, lines=8)
    a_film = Correlation(offset_ms=1_200, matches=140, expected=11.0, strength=12.7, lines=600)

    assert not _beyond_chance(by_accident)
    assert _beyond_chance(eight_of_eight)
    assert _beyond_chance(a_film)
