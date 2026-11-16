import pytest

from retimer.audio import choose_cut_points
from retimer.cache import Cache
from retimer.transcribe import normalize_language, parse_transcription, transcribe_audio

from .sync_helpers import FakeClient, make_transcript, store_transcript

BODY = {
    "language": "English",
    "duration": 12.5,
    "segments": [
        {"start": 0.0, "end": 3.0, "text": " Hello there.", "avg_logprob": -0.2},
        {"start": 3.0, "end": 9.0, "text": " How are you?", "avg_logprob": -1.4},
    ],
    "words": [
        {"word": "Hello", "start": 0.5, "end": 0.9},
        {"word": "there.", "start": 0.9, "end": 1.4},
        {"word": "How", "start": 6.0, "end": 6.2},
        {"word": "are", "start": 6.2, "end": 6.4},
        {"word": "you?", "start": 6.4, "end": 6.9},
    ],
}


def test_cuts_go_to_the_longest_quiet_stretch_near_the_limit():
    silences = [(100_000, 101_000), (500_000, 500_400), (550_000, 553_000), (900_000, 901_000)]
    assert choose_cut_points(1_000_000, silences, 600_000) == [551_500]
    assert choose_cut_points(500_000, silences, 600_000) == []


def test_without_a_quiet_stretch_the_cut_is_made_at_the_limit():
    assert choose_cut_points(1_300_000, [], 600_000) == [600_000, 1_200_000]


def test_language_names_become_codes():
    assert normalize_language("English") == "en"
    assert normalize_language(" JA ") == "ja"
    assert normalize_language(None) is None


def test_words_are_dealt_to_their_segments_and_shifted():
    transcript = parse_transcription(BODY, offset_ms=60_000, first_segment=4)

    assert transcript.language == "en"
    assert transcript.duration_ms == 12_500
    assert [word.segment for word in transcript.words] == [4, 4, 5, 5, 5]
    assert transcript.words[0].start_ms == 60_500
    assert transcript.words[-1].end_ms == 66_900
    assert transcript.segments[1].avg_logprob == pytest.approx(-1.4)


def test_words_that_do_not_spell_their_segments_go_by_time():
    body = {**BODY, "segments": [{**BODY["segments"][0], "text": "Hi."}, BODY["segments"][1]]}
    transcript = parse_transcription(body)
    assert [word.segment for word in transcript.words] == [0, 0, 1, 1, 1]


def test_a_cached_transcript_costs_nothing(tmp_path):
    cache = Cache(tmp_path / "cache")
    audio = tmp_path / "film.ogg"
    stored = make_transcript([1_000, 5_000])
    stored.cost_usd = 0.02
    store_transcript(cache, audio, stored)
    client = FakeClient()

    transcript = transcribe_audio(audio, client=client, cache=cache)

    assert transcript.from_cache
    assert transcript.cost_usd == 0.0
    assert client.transcriptions == 0
    assert [word.text for word in transcript.words][:3] == ["the", "harbor", "was"]
