from retimer import loads
from retimer.reference import (
    merge_spans,
    repair_word_times,
    timeline_from_subtitle,
    timeline_from_transcript,
    utterances_from_transcript,
)
from retimer.transcribe import Segment, Transcript, Word


def transcript(words, segments=None, duration_ms=60_000) -> Transcript:
    segments = segments or [Segment(start_ms=0, end_ms=duration_ms, text="", avg_logprob=-0.2)]
    return Transcript(
        model="m", language="en", duration_ms=duration_ms, words=words, segments=segments
    )


def word(start, end, text, segment=0) -> Word:
    return Word(start_ms=start, end_ms=end, text=text, segment=segment)


def test_a_word_stretched_across_a_pause_starts_shortly_before_its_end():
    words = [word(1_000, 9_000, "These"), word(9_000, 9_200, "are"), word(9_200, 9_650, "lands.")]
    repaired = repair_word_times(words)
    assert repaired[0].end_ms == 9_000
    assert 8_700 <= repaired[0].start_ms <= 8_850
    assert repaired[1:] == words[1:]


def test_a_slowly_spoken_word_is_left_alone():
    words = [word(1_000, 1_450, "Well,")]
    assert repair_word_times(words) == words


def test_wide_script_words_get_more_time_per_character():
    words = [word(0, 5_000, "ありがとう")]
    assert repair_word_times(words)[0].start_ms == 4_500


def test_words_are_grouped_into_utterances_at_pauses_and_sentence_ends():
    words = [
        word(1_000, 1_300, "Open"),
        word(1_300, 1_500, "the"),
        word(1_500, 1_900, "gate."),
        word(2_100, 2_400, "Now!"),
        word(5_000, 5_300, "Who"),
        word(5_300, 5_500, "goes"),
        word(5_500, 5_900, "there"),
    ]
    events, dropped = utterances_from_transcript(transcript(words))

    assert [(event.start_ms, event.text) for event in events] == [
        (1_000, "Open the gate."),
        (2_100, "Now!"),
        (5_000, "Who goes there"),
    ]
    assert dropped == {}


def test_japanese_words_are_joined_without_spaces():
    words = [word(0, 300, "これ"), word(300, 500, "は"), word(500, 900, "ペン")]
    events, _ = utterances_from_transcript(transcript(words))
    assert events[0].text == "これはペン"


def test_what_the_model_invented_is_dropped():
    segments = [
        Segment(start_ms=0, end_ms=5_000, text="", avg_logprob=-0.2),
        Segment(start_ms=5_000, end_ms=20_000, text="", avg_logprob=-1.6),
        Segment(start_ms=20_000, end_ms=40_000, text="", avg_logprob=-0.3),
    ]
    words = [
        word(1_000, 1_400, "Hello."),
        word(6_000, 6_400, "Mumble", segment=1),
        word(6_400, 6_800, "mumble.", segment=1),
        word(21_000, 21_300, "*Dramatic", segment=2),
        word(21_300, 21_900, "music*", segment=2),
        word(30_000, 30_400, "Thanks", segment=2),
        word(30_400, 30_600, "for", segment=2),
        word(30_600, 31_000, "watching!", segment=2),
    ]
    events, dropped = utterances_from_transcript(transcript(words, segments))

    assert [event.text for event in events] == ["Hello."]
    assert dropped == {"low_confidence_words": 2, "non_speech": 1, "invented_phrase": 1}


def test_spans_that_nearly_touch_are_joined():
    assert merge_spans([(0, 1_000), (1_200, 2_000), (5_000, 6_000)]) == [(0, 2_000), (5_000, 6_000)]
    assert merge_spans([(0, 1_000), (1_200, 2_000)], join_ms=0) == [(0, 1_000), (1_200, 2_000)]


def test_timeline_from_transcript_knows_when_speech_starts():
    words = [word(1_000, 1_400, "Hello."), word(9_000, 9_300, "Goodbye.")]
    timeline = timeline_from_transcript(transcript(words, duration_ms=30_000))

    assert timeline.kind == "audio"
    assert timeline.onsets == [1_000, 9_000]
    assert timeline.duration_ms == 30_000
    assert timeline.span == (1_000, 9_300)


def test_timeline_from_subtitle_keeps_only_what_is_spoken():
    subtitle = loads(
        "1\n00:00:01,000 --> 00:00:02,000\n[door slams]\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n<i>Come in.</i>\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nSubtitles by www.example.org\n"
    )
    timeline = timeline_from_subtitle(subtitle, "en")

    assert timeline.kind == "subtitle"
    assert [(event.start_ms, event.text) for event in timeline.events] == [(3_000, "Come in.")]
    assert timeline.dropped == {"sound_description": 1, "credit": 1}
    assert timeline.duration_ms is None


def test_unclear_words_are_no_events_but_still_speech():
    segments = [
        Segment(start_ms=0, end_ms=5_000, text="", avg_logprob=-0.2),
        Segment(start_ms=5_000, end_ms=20_000, text="", avg_logprob=-1.6),
    ]
    words = [
        word(1_000, 1_400, "Hello."),
        word(8_000, 8_400, "Better", segment=1),
        word(8_400, 8_900, "gum.", segment=1),
    ]
    timeline = timeline_from_transcript(transcript(words, segments))

    assert [event.text for event in timeline.events] == ["Hello."]
    assert timeline.onsets == [1_000]
    assert timeline.speech == [(1_000, 1_400), (8_000, 8_900)]
