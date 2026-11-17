import numpy as np
import pytest

from retimer.pgs import BACKGROUND, PgsError, decode_rle, looks_like_pgs, read_pgs

from .pgs_helpers import (
    BLACK,
    WHITE,
    block,
    clear_set,
    display_set,
    encode_rle,
    png_pixels,
    png_size,
    segment,
    simple_stream,
)

MARGIN = 8


def test_every_subtitle_has_its_times_and_its_picture():
    events = read_pgs(simple_stream(3))

    assert [(event.start_ms, event.end_ms) for event in events] == [
        (10_000, 12_500),
        (14_000, 16_500),
        (18_000, 20_500),
    ]
    assert all(len(event.pictures) == 1 for event in events)
    assert events[0].screen == (1920, 1080)
    assert len({event.pictures[0].key for event in events}) == 3


def test_a_picture_is_cut_to_what_is_visible_with_a_margin():
    events = read_pgs(display_set(1_000, [(0, 600, 900, block(24, 6))]) + clear_set(3_000))

    picture = events[0].pictures[0]
    assert (picture.width, picture.height) == (24 + 2 * MARGIN, 6 + 2 * MARGIN)
    assert png_size(picture.png) == (picture.width, picture.height)
    # The block began one pixel inside its object, and the margin lies around it.
    assert (picture.x, picture.y) == (600 + 1 - MARGIN, 900 + 1 - MARGIN)
    pixels = png_pixels(picture.png)
    assert tuple(pixels[0, 0]) == BACKGROUND
    assert tuple(pixels[MARGIN, MARGIN]) == (255, 255, 255)


def test_a_subtitle_without_a_clearing_set_ends_where_the_next_begins():
    stream = display_set(1_000, [(0, 600, 900, block(20))]) + display_set(
        4_000, [(1, 600, 900, block(30))]
    )

    events = read_pgs(stream)

    assert [(event.start_ms, event.end_ms) for event in events] == [(1_000, 4_000), (4_000, 6_000)]


def test_a_fade_is_one_subtitle_and_the_picture_is_its_brightest_frame():
    faint = {1: (*WHITE[:3], 40)}
    half = {1: (*WHITE[:3], 128)}
    stream = (
        display_set(1_000, [(0, 600, 900, block())], palette=faint)
        + display_set(1_040, [(0, 600, 900, None)], palette=half, state=0)
        + display_set(1_080, [(0, 600, 900, None)], palette={1: WHITE}, state=0)
        + display_set(2_900, [(0, 600, 900, None)], palette=half, state=0)
        + clear_set(3_000)
    )

    events = read_pgs(stream)

    assert [(event.start_ms, event.end_ms) for event in events] == [(1_000, 3_000)]
    pixels = png_pixels(events[0].pictures[0].png)
    assert tuple(pixels[MARGIN, MARGIN]) == (255, 255, 255)


def test_the_same_object_sent_again_does_not_start_a_new_subtitle():
    stream = (
        display_set(1_000, [(0, 600, 900, block())])
        + display_set(2_000, [(0, 600, 900, block())], state=0x40)
        + clear_set(3_000)
    )

    assert [(event.start_ms, event.end_ms) for event in read_pgs(stream)] == [(1_000, 3_000)]


def test_two_objects_are_two_pictures_from_top_to_bottom():
    stream = display_set(
        1_000, [(0, 600, 900, block(20)), (1, 600, 100, block(30))], forced=True
    ) + clear_set(3_000)

    event = read_pgs(stream)[0]

    assert [picture.width for picture in event.pictures] == [30 + 2 * MARGIN, 20 + 2 * MARGIN]
    assert event.forced


def test_an_object_nobody_can_see_is_no_subtitle():
    invisible = {1: (*WHITE[:3], 0)}
    stream = display_set(1_000, [(0, 600, 900, block())], palette=invisible) + clear_set(3_000)

    assert read_pgs(stream) == []


def test_dark_text_stands_out_from_the_background_too():
    stream = display_set(1_000, [(0, 600, 900, block(color=2))]) + clear_set(3_000)

    pixels = png_pixels(read_pgs(stream)[0].pictures[0].png)

    assert tuple(pixels[MARGIN, MARGIN]) == (0, 0, 0)
    assert all(abs(channel - 0) > 60 for channel in BACKGROUND)
    assert BLACK[0] == 16


def test_a_stream_that_was_cut_off_gives_what_was_complete():
    stream = simple_stream(2)

    events = read_pgs(stream + display_set(30_000, [(5, 600, 900, block())])[:40])

    assert len(events) == 2


def test_what_is_not_a_pgs_stream_is_refused():
    assert not looks_like_pgs(b"1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    with pytest.raises(PgsError):
        read_pgs(b"1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    with pytest.raises(PgsError):
        read_pgs(simple_stream(1) + b"garbage that is no segment")
    with pytest.raises(PgsError):
        read_pgs(segment(1_000, 0x42, b"unknown"))


def test_run_lengths_decode_to_rows():
    bitmap = np.zeros((3, 300), dtype=np.uint8)
    bitmap[0, :2] = 5
    bitmap[1, 10:290] = 7
    bitmap[2, 100:140] = 9

    decoded = decode_rle(encode_rle(bitmap), 300, 3)

    assert np.array_equal(decoded, bitmap)


def test_a_row_longer_than_the_object_is_cut_and_a_missing_one_is_empty():
    wide = np.full((1, 50), 3, dtype=np.uint8)

    decoded = decode_rle(encode_rle(wide), 20, 2)

    assert decoded.shape == (2, 20)
    assert (decoded[0] == 3).all()
    assert (decoded[1] == 0).all()


def test_an_object_of_absurd_size_is_refused_before_it_is_decoded():
    with pytest.raises(PgsError):
        decode_rle(b"\x00\x00", 60_000, 60_000)
    with pytest.raises(PgsError):
        read_pgs(display_set(1_000, [], palette=None, screen=(60_000, 1_080)))
