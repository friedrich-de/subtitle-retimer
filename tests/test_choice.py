"""Several reference subtitles: which one a job is synced against, and what is said of the rest."""

import json

import pytest

from retimer import dumps
from retimer.agent import AgentSettings, sync_with_agent
from retimer.cache import Cache
from retimer.choice import MAX_REFERENCES, ReferenceChoice
from retimer.cli import EXIT_REJECTED, main
from retimer.fit import Pin
from retimer.pipeline import Adjustments, prepare, propose, sync
from retimer.plan import Shift

from .sync_helpers import (
    SENTENCES,
    FakeClient,
    make_subtitle,
    make_transcript,
    spread,
    store_transcript,
)
from .track_helpers import (
    COUNT,
    LATE_MS,
    TRUTH,
    full,
    other_episode,
    part,
    script,
    signs,
    target,
    track,
)


@pytest.fixture
def run(tmp_path):
    cache = Cache(tmp_path / "cache")

    def call(subtitle, **kwargs):
        return sync(subtitle, client=FakeClient(), cache=cache, **kwargs)

    call.cache = cache
    return call


def starts_of(subtitle) -> list[int]:
    return [line.start_ms for line in subtitle.lines]


def verdicts(choice: ReferenceChoice) -> list[str]:
    return [candidate.verdict for candidate in choice.candidates]


# The choice


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_the_track_with_the_dialogue_is_chosen_wherever_it_stands(run, order):
    tracks = [
        track(signs(), title="Signs & Songs", language="en", forced=True),
        track(full(), title="Full Subtitles", language="en", default=True),
        track(other_episode(), title="Commentary"),
    ]
    sent = [tracks[index] for index in order]

    result = run(target(), references=sent)

    assert result.plan.outcome == "synced"
    assert result.plan.timing_ops == [Shift(ms=-LATE_MS)]
    assert starts_of(result.subtitle) == TRUTH
    choice = result.report.reference_choice
    assert choice.chosen == order.index(1)
    by_title = {candidate.title: candidate for candidate in choice.candidates}
    assert by_title["Full Subtitles"].verdict == "chosen"
    assert by_title["Full Subtitles"].matched_lines == COUNT
    assert by_title["Full Subtitles"].comparable_lines == COUNT
    assert by_title["Signs & Songs"].verdict == "too_little_dialogue"
    assert by_title["Signs & Songs"].dialogue_lines == 0
    assert by_title["Signs & Songs"].left_out == {"sign": 5, "song": 4}
    assert "no dialogue" in by_title["Signs & Songs"].reason
    assert by_title["Commentary"].verdict == "no_match"
    assert "other content" in by_title["Commentary"].reason
    assert [candidate.index for candidate in choice.candidates] == [0, 1, 2]
    assert '"Full Subtitles"' in choice.reason and "Of 3 reference subtitles" in choice.reason
    # A choice that went well is no warning.
    assert result.report.warnings == []


def test_the_result_is_what_the_chosen_track_gives_alone(run):
    alone = run(target(), reference=full())
    among = run(target(), references=[track(signs()), track(other_episode()), track(full())])

    assert dumps(among.subtitle) == dumps(alone.subtitle)
    assert among.plan == alone.plan
    assert among.report.reference == alone.report.reference
    assert among.report.blocks == alone.report.blocks


def test_of_two_tracks_that_fit_the_one_with_more_of_the_dialogue_is_chosen(run):
    half = part(list(range(0, COUNT, 2)))

    result = run(target(), references=[track(half, default=True), track(full())])

    choice = result.report.reference_choice
    assert choice.chosen == 1
    assert verdicts(choice) == ["fits", "chosen"]
    assert choice.candidates[0].matched_lines == COUNT // 2
    assert "the chosen track fits more" in choice.candidates[0].reason
    assert "Most of the target's dialogue" in choice.candidates[1].reason
    assert "2 fit the target" in choice.reason


def test_between_tracks_that_fit_equally_well_the_labels_decide(run):
    # The same dialogue, cut a little differently, so neither is a copy of the other.
    first, second = full(length_ms=2_000), full(length_ms=2_300)

    plain = run(target(), references=[track(first), track(second)])
    by_default = run(target(), references=[track(first), track(second, default=True)])
    not_forced = run(target(), references=[track(first, forced=True, default=True), track(second)])

    assert plain.report.reference_choice.chosen == 0
    assert "comes first" in plain.report.reference_choice.candidates[1].reason
    assert by_default.report.reference_choice.chosen == 1
    assert "is the default track" in by_default.report.reference_choice.candidates[0].reason
    assert not_forced.report.reference_choice.chosen == 1
    assert "is not a forced track" in not_forced.report.reference_choice.candidates[0].reason
    for result in (plain, by_default, not_forced):
        assert sorted(verdicts(result.report.reference_choice)) == ["chosen", "fits"]
        assert starts_of(result.subtitle) == TRUTH


def test_the_reason_names_the_chosen_track_as_the_file_does(run):
    half = part(list(range(0, COUNT, 2)))

    two = run(target(), references=[track(half), track(full(), title="English (Full)")])
    one = run(target(), references=[track(signs(), title="Signs"), track(full(), title="Full")])
    unnamed = run(target(), references=[track(signs()), track(full())])

    assert '2 fit the target. "English (Full)" was taken' in two.report.reference_choice.reason
    assert 'only "Full" fits the target' in one.report.reference_choice.reason
    assert "only one fits the target" in unnamed.report.reference_choice.reason


def test_a_label_does_not_win_over_a_track_that_fits_more(run):
    half = part(list(range(0, COUNT, 2)))

    result = run(target(), references=[track(full(), forced=True), track(half, default=True)])

    assert result.report.reference_choice.chosen == 0


def test_a_track_with_the_same_dialogue_as_an_earlier_one_is_not_matched_again(run):
    with_signs = script(
        [(start, "Default", text) for start, text in zip(TRUTH, SENTENCES, strict=False)]
        + [(4_000, "Signs", "{\\pos(10,10)}Harbor Office")]
    )
    provider = FakeClient()

    result = sync(
        target(),
        references=[track(full()), track(with_signs), track(full())],
        client=provider,
        cache=run.cache,
    )

    choice = result.report.reference_choice
    assert verdicts(choice) == ["chosen", "duplicate", "duplicate"]
    assert [candidate.duplicate_of for candidate in choice.candidates] == [None, 0, 0]
    assert "same dialogue" in choice.candidates[1].reason
    # Each text went to the embedding service once.
    assert len(provider.embedded) == len(set(provider.embedded))


def test_a_track_that_could_not_be_read_is_reported_and_left_out(run):
    broken = track(None, title="Broken")
    broken.problem = "reference_subtitle[0]: the file could not be read as a subtitle."

    result = run(target(), references=[broken, track(full())])

    choice = result.report.reference_choice
    assert choice.chosen == 1
    assert verdicts(choice) == ["unreadable", "chosen"]
    assert choice.candidates[0].reason == broken.problem
    assert result.plan.outcome == "synced"


def test_a_few_stray_lines_are_too_little_to_call_a_track_other_content(run):
    # Signs that pass for dialogue: plain lines, in no style that says what they are.
    stray = make_subtitle(
        [20_000 + index * 31_000 for index in range(6)],
        [
            "harbor office opening hours",
            "platform three northbound departures",
            "closed on sundays and holidays",
            "chapter two the long winter",
            "north gate deliveries only",
            "tickets sold at the counter",
        ],
    )
    # An episode has many times the lines of such a track.
    starts = spread(45)
    texts = SENTENCES + [f"{sentence} again tomorrow" for sentence in SENTENCES[:15]]
    long_target = make_subtitle([start + LATE_MS for start in starts], texts)

    result = run(long_target, references=[track(stray), track(signs())])

    choice = result.report.reference_choice
    assert verdicts(choice) == ["too_little_dialogue", "too_little_dialogue"]
    assert "only 6 lines of dialogue" in choice.candidates[0].reason
    assert result.plan.outcome == "rejected_no_match"


# No track fits


def test_when_no_track_has_dialogue_the_job_is_refused(run):
    provider = FakeClient()

    result = sync(
        target(),
        references=[track(signs(), title="Signs"), track(signs(), title="Songs")],
        client=provider,
        cache=run.cache,
    )

    assert result.plan.outcome == "rejected_no_match"
    assert result.subtitle is None
    assert result.plan.timing_ops == [] and result.plan.confidence == 0.0
    choice = result.report.reference_choice
    assert choice.chosen is None
    assert verdicts(choice) == ["too_little_dialogue", "too_little_dialogue"]
    assert choice.reason == "None of the 2 reference subtitles fits the target."
    assert result.report.warnings == [
        "None of the 2 reference subtitles fits the target. There is no audio to sync "
        "against instead, so the job is refused."
    ]
    # Nothing was there to compare, so nothing was paid for.
    assert provider.embedded == []


def test_when_every_track_is_other_content_the_closest_one_is_what_the_job_is_refused_on(run):
    result = run(target(), references=[track(signs()), track(other_episode())])

    assert result.plan.outcome == "rejected_no_match"
    assert result.subtitle is None
    choice = result.report.reference_choice
    assert choice.chosen is None
    assert verdicts(choice) == ["too_little_dialogue", "no_match"]
    assert result.report.warnings[0] == "None of the 2 reference subtitles fits the target."
    assert "treated as other content" in result.report.warnings[1]
    assert result.report.reference.events == COUNT


def test_a_reader_can_still_find_the_closest_track_to_be_the_same_content(run):
    prepared = prepare(
        target(),
        references=[track(signs()), track(other_episode())],
        client=FakeClient(),
        cache=run.cache,
    )
    assert prepared.used_reference == 1 and not prepared.refused

    pins = [
        Pin(line_id=index, line_start_ms=TRUTH[index] + LATE_MS, ref_ms=TRUTH[index])
        for index in range(COUNT)
    ]
    placed = propose(prepared, Adjustments(same_content=True, pins=pins)).result

    assert placed.plan.outcome != "rejected_no_match"
    choice = placed.report.reference_choice
    assert choice.chosen == 1
    assert verdicts(choice) == ["too_little_dialogue", "chosen"]
    assert "Read side by side" in choice.candidates[1].reason
    # The choice as it was made is left as it was.
    assert prepared.choice.chosen is None


def test_with_audio_a_job_whose_tracks_do_not_fit_is_synced_against_the_audio(run, tmp_path):
    audio = tmp_path / "episode.ogg"
    store_transcript(run.cache, audio, make_transcript(TRUTH, SENTENCES[:COUNT]))

    result = run(target(), audio=audio, references=[track(signs()), track(other_episode())])

    assert result.plan.outcome == "synced"
    assert result.report.reference.kind == "audio"
    moved = zip(starts_of(result.subtitle), TRUTH, strict=True)
    assert all(abs(new - true) < 400 for new, true in moved)
    choice = result.report.reference_choice
    assert choice.chosen is None
    assert verdicts(choice) == ["too_little_dialogue", "no_match"]
    assert result.report.warnings == [
        "None of the 2 reference subtitles fits the target. The target was synced against "
        "the audio alone."
    ]


def test_with_audio_the_chosen_track_is_still_checked_against_it(run, tmp_path):
    audio = tmp_path / "episode.ogg"
    store_transcript(run.cache, audio, make_transcript(TRUTH, SENTENCES[:COUNT]))

    result = run(target(), audio=audio, references=[track(signs()), track(full())])

    assert result.plan.outcome == "synced"
    assert result.report.reference.kind == "both"
    assert "The reference subtitle agrees with the audio." in result.report.reference.notes
    assert result.report.reference_choice.chosen == 1
    assert starts_of(result.subtitle) == TRUTH


# One reference subtitle


def test_one_reference_subtitle_is_reported_like_a_choice_of_one(run):
    result = run(target(), reference=full(), reference_language="en")

    choice = result.report.reference_choice
    assert choice.chosen == 0
    assert choice.reason == "The reference subtitle fits the target."
    assert verdicts(choice) == ["chosen"]
    assert choice.candidates[0].language == "en"
    assert result.report.reference.language == "en"
    assert result.report.warnings == []


def test_one_reference_subtitle_of_signs_is_handled_as_it_always_was(run):
    result = run(target(), reference=signs())

    assert result.plan.outcome == "partial"
    assert starts_of(result.subtitle) == starts_of(target())
    assert any("has no dialogue" in warning for warning in result.report.warnings)
    choice = result.report.reference_choice
    assert choice.chosen is None
    assert verdicts(choice) == ["too_little_dialogue"]
    assert choice.reason == "The reference subtitle does not fit the target."


def test_one_reference_subtitle_of_other_content_is_rejected_as_it_always_was(run):
    result = run(target(), reference=other_episode())

    assert result.plan.outcome == "rejected_no_match"
    assert result.report.reference_choice.chosen is None
    assert verdicts(result.report.reference_choice) == ["no_match"]
    assert len(result.report.warnings) == 1
    assert "treated as other content" in result.report.warnings[0]


def test_a_job_without_a_reference_subtitle_has_no_choice_to_report(run, tmp_path):
    audio = tmp_path / "episode.ogg"
    store_transcript(run.cache, audio, make_transcript(TRUTH, SENTENCES[:COUNT]))

    result = run(target(), audio=audio)

    assert result.report.reference_choice is None


# What follows from the choice


def test_the_matched_reference_is_made_of_the_chosen_track(run):
    result = run(
        target(),
        references=[track(signs()), track(part(list(range(4, COUNT))))],
        matched_reference=True,
    )

    assert result.report.reference_choice.chosen == 1
    assert [line.text for line in result.reference.lines] == SENTENCES[4:COUNT]
    assert result.report.matched_reference.delivered


def test_no_matched_reference_when_no_track_fits(run, tmp_path):
    audio = tmp_path / "episode.ogg"
    store_transcript(run.cache, audio, make_transcript(TRUTH, SENTENCES[:COUNT]))

    result = run(
        target(), audio=audio, references=[track(signs()), track(signs())], matched_reference=True
    )

    assert result.plan.outcome == "synced"
    assert result.reference is None
    assert "none to hand back" in result.report.matched_reference.note


def test_a_model_is_not_asked_when_no_track_can_serve(run):
    class Unreachable:
        def __getattr__(self, name):
            raise AssertionError("the model should not be asked")

    result = sync_with_agent(
        target(),
        references=[track(signs()), track(signs())],
        agent=AgentSettings(consult="always"),
        client=FakeClient(),
        cache=run.cache,
        model_client=Unreachable(),
    )

    assert result.plan.outcome == "rejected_no_match"
    assert result.report.agent is None


def test_the_sync_command_takes_several_references(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("retimer.embed.OpenRouter", FakeClient)
    monkeypatch.setenv("RETIMER_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "target.srt").write_text(dumps(target()))
    (tmp_path / "signs.ass").write_text(dumps(signs()))
    (tmp_path / "full.srt").write_text(dumps(full()))
    command = ["sync", str(tmp_path / "target.srt"), "-o", str(tmp_path / "out.srt")]

    code = main(
        [
            *command,
            "--reference",
            str(tmp_path / "signs.ass"),
            "--reference",
            str(tmp_path / "full.srt"),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["outcome"] == "synced"
    assert summary["reference"]["chosen"] == "full.srt"
    assert [found["verdict"] for found in summary["reference"]["candidates"]] == [
        "too_little_dialogue",
        "chosen",
    ]

    code = main([*command, *["--reference", str(tmp_path / "signs.ass")] * 2])
    assert code == EXIT_REJECTED
    assert json.loads(capsys.readouterr().out)["reference"]["chosen"] is None

    code = main([*command, *["--reference", str(tmp_path / "full.srt")] * (MAX_REFERENCES + 1)])
    assert code == 1
    assert f"up to {MAX_REFERENCES} times" in capsys.readouterr().err
