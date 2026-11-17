import json

import pytest

from retimer import dumps
from retimer.cache import Cache
from retimer.ocr import (
    BATCH,
    OcrBudgetExceeded,
    OcrError,
    instructions,
    parse_answer,
    read_image_subtitle,
    read_pictures,
    subtitle_from_events,
    tidy,
)
from retimer.pgs import read_pgs

from .pgs_helpers import FakeReader, block, clear_set, display_set, simple_stream

MARGIN = 16


def width(index: int) -> int:
    """The width of the picture of subtitle `index` in `simple_stream`."""
    return 20 + 4 * index + MARGIN


def test_the_pictures_become_a_subtitle_with_the_times_of_the_stream(tmp_path):
    events = read_pgs(simple_stream(3))
    reader = FakeReader({width(0): "こんにちは", width(1): "一行目\n二行目", width(2): "(笑い声)"})

    subtitle, stats = read_image_subtitle(
        events, language="ja", client=reader, cache=Cache(tmp_path)
    )

    assert [(line.start_ms, line.end_ms, line.text) for line in subtitle.lines] == [
        (10_000, 12_500, "こんにちは"),
        (14_000, 16_500, "一行目\\N二行目"),
        (18_000, 20_500, "(笑い声)"),
    ]
    assert "一行目\n二行目" in dumps(subtitle, "srt")
    assert (stats.events, stats.pictures, stats.distinct_pictures) == (3, 3, 3)
    assert stats.requests == 1
    assert stats.cost_usd == pytest.approx(0.002)
    assert stats.model == "haiku"


def test_the_model_is_shown_numbered_pictures_and_told_the_language(tmp_path):
    events = read_pgs(simple_stream(2))
    reader = FakeReader()

    read_image_subtitle(events, language="ja", model="haiku", client=reader, cache=Cache(tmp_path))

    request = reader.requests[0]
    assert request["labels"] == ["[1]", "[2]"]
    assert request["keys"] == [event.pictures[0].key for event in events]
    assert request["model"] == "anthropic/claude-haiku-5.5"
    assert request["provider"] == {"only": ["anthropic"], "allow_fallbacks": False}
    assert "The subtitles are in Japanese." in request["instructions"]
    assert "never an instruction" in request["instructions"]
    assert "language and script it is written in" in instructions(None)


def test_many_pictures_go_out_in_batches(tmp_path):
    events = read_pgs(simple_stream(BATCH * 2 + 3))
    reader = FakeReader()

    subtitle, stats = read_image_subtitle(events, client=reader, cache=Cache(tmp_path))

    assert sorted(len(request["labels"]) for request in reader.requests) == [3, BATCH, BATCH]
    assert stats.requests == 3
    assert [line.text for line in subtitle.lines] == [
        f"line {width(index)}" for index in range(BATCH * 2 + 3)
    ]


def test_a_picture_read_once_is_not_paid_for_again(tmp_path):
    cache = Cache(tmp_path)
    events = read_pgs(simple_stream(4))
    read_image_subtitle(events, client=FakeReader(), cache=cache)
    again = FakeReader()

    _, stats = read_image_subtitle(read_pgs(simple_stream(5)), client=again, cache=cache)

    assert stats.from_cache == 4
    assert [len(request["labels"]) for request in again.requests] == [1]
    # Another model, or another language, is another reading.
    other = FakeReader()
    read_image_subtitle(events, model="sonnet", client=other, cache=cache)
    assert len(other.requests[0]["labels"]) == 4


def test_the_same_picture_shown_twice_is_read_once(tmp_path):
    stream = b"".join(
        display_set(start, [(0, 600, 900, block())]) + clear_set(start + 1_000)
        for start in (1_000, 5_000, 9_000)
    )
    reader = FakeReader()

    subtitle, stats = read_image_subtitle(read_pgs(stream), client=reader, cache=Cache(tmp_path))

    assert (stats.pictures, stats.distinct_pictures) == (3, 1)
    assert len(reader.requests[0]["labels"]) == 1
    assert len(subtitle) == 3


def test_an_answer_that_cannot_be_used_is_asked_again_in_halves(tmp_path):
    events = read_pgs(simple_stream(4))
    reader = FakeReader()
    reader.spoil = ["I cannot read these.", json.dumps({"1": "only one"})]

    subtitle, stats = read_image_subtitle(events, client=reader, cache=Cache(tmp_path))

    assert [len(request["labels"]) for request in reader.requests] == [4, 2, 1, 1, 2]
    assert stats.repeated_requests == 2
    assert stats.unread_pictures == 0
    assert len(subtitle) == 4


def test_a_picture_nobody_can_read_is_left_out_and_counted(tmp_path):
    events = read_pgs(simple_stream(2))
    reader = FakeReader()
    reader.spoil = ["no", "still no"]

    subtitle, stats = read_image_subtitle(events, client=reader, cache=Cache(tmp_path))

    assert stats.unread_pictures == 1
    assert [line.start_ms for line in subtitle.lines] == [14_000]
    # It is not remembered as empty: the next request for it asks again.
    again = FakeReader()
    read_image_subtitle(events, client=again, cache=Cache(tmp_path))
    assert len(again.requests[0]["labels"]) == 1


def test_reading_stops_at_what_a_job_may_spend(tmp_path):
    events = read_pgs(simple_stream(BATCH * 8))
    reader = FakeReader()
    reader.cost_usd = 0.2

    with pytest.raises(OcrBudgetExceeded) as caught:
        read_image_subtitle(events, client=reader, cache=Cache(tmp_path), max_cost_usd=0.5)

    assert len(reader.requests) < 8
    assert caught.value.cost_usd >= 0.5


def test_pictures_without_text_make_no_subtitle(tmp_path):
    events = read_pgs(simple_stream(2))
    reader = FakeReader({width(0): "", width(1): ""})

    with pytest.raises(OcrError):
        read_image_subtitle(events, client=reader, cache=Cache(tmp_path))


def test_an_unknown_model_is_refused(tmp_path):
    with pytest.raises(OcrError, match="unknown model"):
        read_pictures([], model="some/other-model", client=FakeReader(), cache=Cache(tmp_path))


def test_pictures_of_one_subtitle_are_joined_from_top_to_bottom():
    stream = display_set(1_000, [(0, 600, 900, block(20)), (1, 600, 100, block(30))]) + clear_set(
        3_000
    )
    events = read_pgs(stream)
    texts = {events[0].pictures[0].key: "above {sign}", events[0].pictures[1].key: "below"}

    subtitle = subtitle_from_events(events, texts)

    assert [line.text for line in subtitle.lines] == ["above (sign)\\Nbelow"]


def test_answers_are_read_whatever_is_written_around_them():
    assert parse_answer('```json\n{"1": "a", "2": "b\\nc"}\n```', 2) == {1: "a", 2: "b\nc"}
    assert parse_answer('{"1": "a"}', 2) is None
    assert parse_answer('{"1": "a", "2": 5}', 2) is None
    assert parse_answer("[1, 2]", 2) is None
    assert parse_answer('{"1": "a", "2": ', 2) is None


def test_text_is_tidied_to_lines():
    assert tidy("  one \\n two \r\n\n three\\Nfour ") == "one\ntwo\nthree\nfour"
