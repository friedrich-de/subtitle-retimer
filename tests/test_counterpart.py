"""The matched reference: the reference subtitle, reduced to what the target says too."""

import json

import pytest

from retimer import dumps, load, loads
from retimer.cache import Cache
from retimer.cli import main
from retimer.counterpart import link_by_time, pair_by_time
from retimer.pipeline import prepare, propose, sync, with_matched_reference
from retimer.reference import RefEvent
from retimer.views import Options

from .sync_helpers import (
    SENTENCES,
    FakeClient,
    make_subtitle,
    make_transcript,
    spread,
    store_transcript,
)


@pytest.fixture
def run(tmp_path):
    cache = Cache(tmp_path / "cache")

    def call(target, **kwargs):
        return sync(target, client=FakeClient(), cache=cache, matched_reference=True, **kwargs)

    call.cache = cache
    return call


def starts_of(subtitle) -> list[int]:
    return [line.start_ms for line in subtitle.lines]


def texts_of(subtitle) -> list[str]:
    return [line.text for line in subtitle.lines]


def _stamp(ms: int) -> str:
    return f"{ms // 3_600_000}:{ms // 60_000 % 60:02d}:{ms % 60_000 / 1000:05.2f}"


def make_script(events: list[tuple]) -> object:
    """An ASS file. An event is (start, style, text), with "Comment" as a fourth for a note."""
    styles = "\n".join(
        f"Style: {name},Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,20,20,30,1"
        for name in sorted({event[1] for event in events})
    )
    rows = "\n".join(
        f"{(*event, 'Dialogue')[3]}: 0,{_stamp(event[0])},{_stamp(event[0] + 2_000)},"
        f"{event[1]},,0,0,0,,{event[2]}"
        for event in events
    )
    return loads(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{styles}\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"{rows}\n"
    )


def event(start_ms: int, end_ms: int) -> RefEvent:
    return RefEvent(start_ms=start_ms, end_ms=end_ms, text="", line_ids=[0])


# Which lines belong together


def test_two_lines_belong_together_when_they_share_half_of_the_shorter_one():
    events = [event(10_000, 12_000), event(20_000, 22_000), event(30_000, 32_000)]
    spans = [(10_300, 12_400), (21_200, 24_000), (32_000, 34_000)]

    assert pair_by_time(events, spans) == ([True, False, False], [True, False, False])
    # A line that the next sentence only runs into has no counterpart.
    assert pair_by_time([event(20_000, 22_000)], [(21_000, 24_000)])[0] == [True]
    assert pair_by_time([event(20_000, 22_000)], [(21_001, 24_000)])[0] == [False]


def test_a_long_line_belongs_to_every_short_line_it_covers():
    long_event = [event(10_000, 18_000)]
    short_spans = [(10_100, 12_000), (12_100, 14_500), (15_000, 17_900), (18_500, 20_000)]

    assert pair_by_time(long_event, short_spans) == ([True], [True, True, True, False])

    short_events = [event(10_100, 12_000), event(12_100, 14_500), event(40_000, 41_000)]
    assert pair_by_time(short_events, [(10_000, 18_000)]) == ([True, True, False], [True])


def test_a_line_that_is_never_on_screen_belongs_to_nothing():
    assert pair_by_time([event(5_000, 5_000)], [(4_000, 6_000)]) == ([False], [False])
    assert pair_by_time([event(4_000, 6_000)], [(5_000, 5_000)]) == ([False], [False])
    assert pair_by_time([], [(0, 1_000)]) == ([], [False])
    assert pair_by_time([event(0, 1_000)], []) == ([False], [])


def test_lines_that_overlap_each_other_are_all_looked_at():
    # A span that began long ago and is still on screen is found behind later ones.
    spans = [(1_000, 60_000), (20_000, 21_000), (30_000, 31_000), (50_000, 50_500)]

    found = pair_by_time([event(50_000, 52_000), event(70_000, 71_000)], spans)

    assert found == ([True, False], [True, False, False, True])


# The file that comes back


def test_the_reference_comes_back_at_its_own_times(run):
    truth = spread(30)
    reference = make_subtitle(truth)
    target = make_subtitle([start + 4_200 for start in truth])

    result = run(target, reference=reference)

    assert result.plan.outcome == "synced"
    assert starts_of(result.subtitle) == truth
    assert starts_of(result.reference) == truth
    assert texts_of(result.reference) == texts_of(reference)
    found = result.report.matched_reference
    assert (found.delivered, found.lines, found.left_out, found.note) == (True, 30, {}, None)
    assert (found.reference_lines, found.dialogue_lines) == (30, 30)
    assert (found.target_dialogue_lines, found.target_paired_lines) == (30, 30)
    assert found.moved_onto_audio is False
    assert result.report.warnings == []


def test_without_being_asked_nothing_is_added(tmp_path):
    truth = spread(20)

    result = sync(
        make_subtitle([start + 1_500 for start in truth]),
        reference=make_subtitle(truth),
        client=FakeClient(),
        cache=Cache(tmp_path / "cache"),
    )

    assert result.reference is None
    assert result.report.matched_reference is None


def test_lines_the_target_has_nothing_for_are_left_out(run):
    truth = spread(30)
    reference = make_subtitle(truth)
    said = [index for index in range(30) if index not in (9, 10, 11, 20)]
    target = make_subtitle(
        [truth[index] - 2_600 for index in said], [SENTENCES[index] for index in said]
    )

    result = run(target, reference=reference)

    assert starts_of(result.subtitle) == [truth[index] for index in said]
    assert starts_of(result.reference) == [truth[index] for index in said]
    assert texts_of(result.reference) == [SENTENCES[index] for index in said]
    assert result.reference.ids == said
    found = result.report.matched_reference
    assert found.left_out == {"no_counterpart": 4}
    assert (found.lines, found.target_paired_lines) == (26, 26)


def test_lines_only_the_target_has_stay_in_the_target_alone(run):
    truth = spread(30)
    said = [index for index in range(30) if index not in (13, 14)]
    reference = make_subtitle(
        [truth[index] for index in said], [SENTENCES[index] for index in said]
    )
    target = make_subtitle([start + 3_000 for start in truth])

    result = run(target, reference=reference)

    assert starts_of(result.subtitle) == truth
    assert starts_of(result.reference) == [truth[index] for index in said]
    found = result.report.matched_reference
    assert found.left_out == {}
    assert (found.target_dialogue_lines, found.target_paired_lines) == (30, 28)


def test_signs_songs_notes_and_sounds_are_left_out(run):
    truth = spread(24)
    events = [(start, "Default", SENTENCES[index]) for index, start in enumerate(truth)]
    events += [
        (truth[2], "Signs", "{\\pos(640,80)}Fuyuki City"),
        (truth[5], "OP-Romaji", "{\\k20}ko{\\k30}no {\\k25}so{\\k40}ra"),
        (truth[5], "OP-English", "Under this sky"),
        (truth[8], "Default", "[door slams]"),
        (truth[11], "Default", "typeset by nobody in particular", "Comment"),
    ]
    reference = make_script(events)
    target = make_subtitle([start + 2_000 for start in truth])

    result = run(target, reference=reference)

    assert result.reference.format == "ass"
    assert starts_of(result.reference) == truth
    assert texts_of(result.reference) == SENTENCES[:24]
    assert {line.style for line in result.reference.lines} == {"Default"}
    assert result.report.matched_reference.left_out == {
        "sign": 1,
        "song": 2,
        "sound_description": 1,
        "comment": 1,
    }
    # The styles of the file are still there, so it plays as it did.
    assert "Style: Default," in dumps(result.reference)


def test_two_languages_may_cut_a_sentence_differently(run):
    truth = spread(24)
    # The reference says lines 4 and 5 in one line, and line 10 in two.
    starts = [start for index, start in enumerate(truth) if index != 5]
    texts = [text for index, text in enumerate(SENTENCES[:24]) if index != 5]
    texts[4] = f"{SENTENCES[4]} {SENTENCES[5]}"
    reference = loads(
        dumps(make_subtitle(starts, texts)).replace(
            "00:00:34,000 --> 00:00:36,000", "00:00:34,000 --> 00:00:42,000"
        )
    )
    halves = dumps(reference).replace(
        f"00:01:10,000 --> 00:01:12,000\n{SENTENCES[10]}",
        f"00:01:10,000 --> 00:01:10,900\n{' '.join(SENTENCES[10].split()[:4])}\n\n"
        f"99\n00:01:11,000 --> 00:01:12,000\n{' '.join(SENTENCES[10].split()[4:])}",
    )
    reference = loads(halves)
    assert len(reference) == 24
    target = make_subtitle([start - 3_300 for start in truth])

    result = run(target, reference=reference)

    assert starts_of(result.subtitle) == truth
    assert len(result.reference) == 24
    found = result.report.matched_reference
    assert found.left_out == {}
    assert (found.target_dialogue_lines, found.target_paired_lines) == (24, 24)


def test_the_content_options_apply_to_the_reference_too(run):
    truth = spread(20)
    texts = list(SENTENCES[:20])
    texts[3] = f"[sighs] {texts[3]}"
    texts[6] = f"<i>{texts[6]}</i>"
    reference = make_subtitle(truth, texts)
    target = make_subtitle([start + 2_000 for start in truth])

    plain = run(target, reference=reference)
    cleaned = run(
        target,
        reference=reference,
        options=Options(remove_hearing_impaired=True, strip_formatting=True),
    )

    assert plain.reference.lines[3].text == f"[sighs] {SENTENCES[3]}"
    assert plain.reference.lines[6].text == f"{{\\i1}}{SENTENCES[6]}{{\\i0}}"
    assert texts_of(cleaned.reference) == SENTENCES[:20]


def test_the_layers_of_one_line_stay_together(run):
    truth = spread(20)
    events = [(start, "Default", SENTENCES[index]) for index, start in enumerate(truth)]
    # Line 7 is drawn twice, once for its glow.
    events.insert(8, (truth[7], "Default", SENTENCES[7]))
    reference = make_script(events)
    without = [index for index in range(20) if index != 12]
    target = make_subtitle(
        [truth[index] + 2_000 for index in without], [SENTENCES[index] for index in without]
    )

    result = run(target, reference=reference)

    assert texts_of(result.reference).count(SENTENCES[7]) == 2
    assert SENTENCES[12] not in texts_of(result.reference)
    assert len(result.reference) == 20
    assert result.report.matched_reference.left_out == {"no_counterpart": 1}


def test_lines_the_plan_removed_are_no_counterpart(run):
    truth = spread(24)
    texts = list(SENTENCES[:24])
    target_texts = list(texts)
    target_texts[5] = "[thunder rumbles]"
    reference = make_subtitle(truth, texts)
    target = make_subtitle([start + 2_000 for start in truth], target_texts)

    result = run(target, reference=reference, options=Options(remove_hearing_impaired=True))

    assert len(result.subtitle) == 23
    assert SENTENCES[5] not in texts_of(result.reference)
    assert result.report.matched_reference.left_out == {"no_counterpart": 1}


def test_a_reference_the_audio_corrected_comes_back_on_the_audio(run, tmp_path):
    truth = spread(24)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth))
    reference = make_subtitle([start + 5_000 for start in truth])
    target = make_subtitle([start + 12_000 for start in truth])

    result = run(target, audio=audio, reference=reference)

    assert starts_of(result.subtitle) == truth
    assert starts_of(result.reference) == truth
    assert result.report.matched_reference.moved_onto_audio is True
    assert result.report.matched_reference.lines == 24


def test_a_reference_that_agrees_with_the_audio_is_not_moved(run, tmp_path):
    truth = spread(24)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth))
    # Hand-timed lines lead the voice a little. That is style, and it stays.
    reference = make_subtitle([start - 120 for start in truth])

    result = run(
        make_subtitle([start - 7_000 for start in truth]), audio=audio, reference=reference
    )

    assert starts_of(result.reference) == [start - 120 for start in truth]
    assert result.report.matched_reference.moved_onto_audio is False


# When there is no file


def test_other_content_has_no_matched_reference(run):
    target = make_subtitle(spread(14), SENTENCES[:14])
    reference = make_subtitle(spread(14), SENTENCES[15:29])

    result = run(target, reference=reference)

    assert result.plan.outcome == "rejected_no_match"
    assert result.reference is None
    found = result.report.matched_reference
    assert (found.delivered, found.note) == (
        False,
        "The reference is other content than the target.",
    )
    assert result.report.warnings[-1] == (
        "No matched reference was made. The reference is other content than the target."
    )


def test_audio_alone_has_no_lines_to_hand_back(run, tmp_path):
    truth = spread(20)
    audio = tmp_path / "film.ogg"
    store_transcript(run.cache, audio, make_transcript(truth))

    result = run(make_subtitle([start + 3_000 for start in truth]), audio=audio)

    assert starts_of(result.subtitle) == truth
    assert result.reference is None
    assert result.report.matched_reference.note == "The job has no reference subtitle."


def test_a_target_placed_without_its_text_has_no_matched_reference(run):
    truth = [5_000, 9_300, 21_000, 26_400, 40_100, 48_800, 61_000, 73_500]
    words = ["Yes.", "No.", "Okay.", "Right.", "Sure.", "Fine.", "What?", "Go."]
    target = make_subtitle([start + 2_500 for start in truth], words)

    result = run(target, reference=make_subtitle(truth, words))

    assert result.report.fallback is not None
    assert result.reference is None
    assert "could not be placed against the reference by its text" in (
        result.report.matched_reference.note
    )


def test_lines_of_a_block_that_is_not_trusted_pair_with_nothing(tmp_path):
    truth = spread(30)
    reference = make_subtitle(truth)
    # Two parts that are late by different amounts.
    target = make_subtitle(
        [start + (2_000 if index < 15 else 9_000) for index, start in enumerate(truth)]
    )
    prepared = prepare(
        target, reference=reference, client=FakeClient(), cache=Cache(tmp_path / "cache")
    )
    result = propose(prepared).result
    assert [block.shift_ms for block in result.report.blocks] == [-2_000, -9_000]

    trusted = with_matched_reference(prepared, result)
    assert starts_of(trusted.reference) == truth
    assert trusted.report.matched_reference.target_unplaced_lines == 0

    result.report.blocks[1].confidence = 0.2
    doubted = with_matched_reference(prepared, result)

    assert starts_of(doubted.reference) == truth[:15]
    found = doubted.report.matched_reference
    assert found.left_out == {"no_counterpart": 15}
    assert (found.target_dialogue_lines, found.target_paired_lines) == (30, 15)
    assert found.target_unplaced_lines == 15
    # The result that came in is left as it was.
    assert result.reference is None
    assert result.report.matched_reference is None


# Which lines of the two files belong together


def shown(subtitle) -> list[tuple[int, int, str]]:
    """A delivered file as a player reads it back: start, end and text of every line."""
    again = loads(dumps(subtitle))
    return [(line.start_ms, line.end_ms, line.text.replace("\\N", "\n")) for line in again.lines]


def test_the_links_name_every_event_and_span_that_belong_together():
    events = [event(10_000, 18_000), event(20_000, 22_000), event(40_000, 41_000)]
    spans = [(20_100, 22_000), (10_100, 12_000), (12_100, 14_500), (50_000, 51_000)]

    assert link_by_time(events, spans) == [(0, 1), (0, 2), (1, 0)]
    assert link_by_time([], spans) == []


def test_the_report_lists_which_lines_belong_together(run):
    truth = spread(30)
    said = [index for index in range(30) if index not in (6, 7)]
    reference = make_subtitle(truth)
    target = make_subtitle(
        [truth[index] + 4_200 for index in said], [SENTENCES[index] for index in said]
    )

    result = run(target, reference=reference)

    pairs = result.report.matched_reference.pairs
    assert len(pairs) == 28
    ours, theirs = shown(result.reference), shown(result.subtitle)
    for number, found in enumerate(pairs, 1):
        (ref,), (line,) = found.reference, found.target
        # Both files lost lines 6 and 7, so the pairs count through both alike.
        assert (ref.index, line.index) == (number, number)
        assert (ref.start_ms, ref.end_ms, ref.text) == ours[ref.index - 1]
        assert (line.start_ms, line.end_ms, line.text) == theirs[line.index - 1]
        assert (found.start_ms, found.end_ms) == (ref.start_ms, ref.end_ms)
    assert [found.reference[0].text for found in pairs] == [SENTENCES[index] for index in said]


def test_a_pair_holds_every_line_of_a_sentence_that_was_cut_differently(run):
    truth = spread(24)
    # The reference says lines 4 and 5 in one line.
    starts = [start for index, start in enumerate(truth) if index != 5]
    texts = [text for index, text in enumerate(SENTENCES[:24]) if index != 5]
    texts[4] = f"{SENTENCES[4]} {SENTENCES[5]}"
    reference = loads(
        dumps(make_subtitle(starts, texts)).replace(
            "00:00:34,000 --> 00:00:36,000", "00:00:34,000 --> 00:00:42,000"
        )
    )
    target = make_subtitle([start - 3_300 for start in truth])

    result = run(target, reference=reference)

    pairs = result.report.matched_reference.pairs
    assert len(pairs) == 23
    long = pairs[4]
    assert [line.index for line in long.reference] == [5]
    assert [line.index for line in long.target] == [5, 6]
    assert [line.text for line in long.target] == SENTENCES[4:6]
    assert (long.start_ms, long.end_ms) == (truth[4], truth[5] + 2_000)
    # Every line of both files is in exactly one pair.
    assert sorted(line.index for found in pairs for line in found.target) == list(range(1, 25))
    assert sorted(line.index for found in pairs for line in found.reference) == list(range(1, 24))


def test_a_pair_names_the_lines_as_the_delivered_files_count_them(run):
    truth = spread(20)
    events = [(start, "Default", SENTENCES[index]) for index, start in enumerate(truth)]
    events.insert(0, (truth[0], "Signs", "{\\pos(640,80)}Fuyuki City"))
    events.insert(3, (truth[2], "Default", "a note for the editor", "Comment"))
    # Line 7 is drawn twice, once for its glow.
    events.insert(10, (truth[7], "Default", f"{{\\blur3}}{SENTENCES[7]}"))
    reference = make_script(events)
    texts = list(SENTENCES[:20])
    texts[3] = "[thunder rumbles]"
    target = make_subtitle([start + 2_000 for start in truth], texts)

    result = run(target, reference=reference, options=Options(remove_hearing_impaired=True))

    pairs = result.report.matched_reference.pairs
    ours, theirs = shown(result.reference), shown(result.subtitle)
    assert (len(ours), len(theirs), len(pairs)) == (20, 19, 19)
    for found in pairs:
        for ref in found.reference:
            assert (ref.start_ms, ref.end_ms) == ours[ref.index - 1][:2]
        for line in found.target:
            assert (line.start_ms, line.end_ms, line.text) == theirs[line.index - 1]
    # The sign and the note are in no pair, and the two layers of line 7 are in one.
    layered = next(found for found in pairs if found.target[0].text == SENTENCES[7])
    assert [ref.text for ref in layered.reference] == [SENTENCES[7], SENTENCES[7]]
    assert all(SENTENCES[3] not in ref.text for found in pairs for ref in found.reference)


def test_a_pair_shows_the_text_as_the_content_options_left_it(run):
    truth = spread(20)
    texts = list(SENTENCES[:20])
    texts[9] = f"{texts[9]}\n[door slams]"
    target = make_subtitle([start + 2_000 for start in truth])

    result = run(
        target,
        reference=make_subtitle(truth, texts),
        options=Options(remove_hearing_impaired=True),
    )

    pairs = result.report.matched_reference.pairs
    assert len(pairs) == 20
    assert pairs[9].reference[0].text == SENTENCES[9]


def test_without_a_file_there_are_no_pairs(run):
    target = make_subtitle(spread(14), SENTENCES[:14])
    other = make_subtitle(spread(14), SENTENCES[15:29])

    result = run(target, reference=other)

    assert result.reference is None
    assert result.report.matched_reference.pairs == []


# Command line


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr("retimer.embed.OpenRouter", FakeClient)
    monkeypatch.setenv("RETIMER_CACHE_DIR", str(tmp_path / "cache"))


def test_the_sync_command_writes_the_matched_reference(offline, tmp_path, capsys):
    truth = spread(20)
    said = [index for index in range(20) if index != 4]
    (tmp_path / "target.srt").write_text(
        dumps(
            make_subtitle(
                [truth[index] + 3_000 for index in said], [SENTENCES[index] for index in said]
            )
        )
    )
    (tmp_path / "reference.srt").write_text(dumps(make_subtitle(truth)))

    code = main(
        [
            "sync",
            str(tmp_path / "target.srt"),
            "--reference",
            str(tmp_path / "reference.srt"),
            "-o",
            str(tmp_path / "out.srt"),
            "--matched-reference",
            str(tmp_path / "pair.srt"),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["matched_reference"]["lines"] == 19
    assert summary["matched_reference"]["left_out"] == {"no_counterpart": 1}
    # The summary counts the pairs. The report lists them.
    assert summary["matched_reference"]["pairs"] == 19
    assert starts_of(load(tmp_path / "pair.srt")) == [truth[index] for index in said]
    assert starts_of(load(tmp_path / "out.srt")) == [truth[index] for index in said]


def test_the_sync_command_needs_a_reference_subtitle_to_hand_one_back(tmp_path, capsys):
    (tmp_path / "target.srt").write_text(dumps(make_subtitle(spread(5))))

    code = main(
        [
            "sync",
            str(tmp_path / "target.srt"),
            "--audio",
            str(tmp_path / "film.ogg"),
            "-o",
            str(tmp_path / "out.srt"),
            "--matched-reference",
            str(tmp_path / "pair.srt"),
        ]
    )

    assert code == 1
    assert "--matched-reference needs --reference" in capsys.readouterr().err
    assert not (tmp_path / "pair.srt").exists()
