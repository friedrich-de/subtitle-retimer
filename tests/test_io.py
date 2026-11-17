import contextlib
import time

import pytest

from retimer import SubtitleError, dumps, loads
from retimer.io import _usual_event_times, decode, load_bytes

from .conftest import DATA


def test_loads_both_formats(ass, srt):
    assert ass.format == "ass"
    assert len(ass) == 13
    assert srt.format == "srt"
    assert len(srt) == 6
    assert srt.lines[1].text == "{\\i1}It's me.{\\i0}"
    assert srt.lines[0].text == "- [phone rings]\\N- Hello?"


def test_line_ids_are_file_positions(ass):
    assert [line.id for line in ass.lines] == list(range(13))
    assert ass.lines[0].is_comment
    assert ass.lines[3].start_ms == 90_000
    assert ass.lines[3].duration_ms == 2_500


def test_srt_round_trip_is_lossless(srt):
    original = (DATA / "sample.srt").read_text(encoding="utf-8")
    assert dumps(srt).strip() == original.strip()


def test_ass_round_trip_keeps_styles_and_events(ass):
    again = loads(dumps(ass))
    assert [line.text for line in again.lines] == [line.text for line in ass.lines]
    assert set(again.ssa.styles) == {"Default", "Signs", "OP-Romaji"}
    assert again.ssa.info["PlayResX"] == "1280"


def test_converts_between_formats(ass):
    as_srt = loads(dumps(ass, "srt"))
    assert as_srt.format == "srt"
    assert "Good morning." in [line.text for line in as_srt.lines]


_FRENCH = "Je suis désolé, mais il était déjà très tard quand nous sommes arrivés à l'hôtel."
_POLISH = "Nie wiem, czy to możliwe. Proszę zaczekać chwilę, dziękuję bardzo."
_RUSSIAN = "Привет, как дела? Я не знаю, что сказать."
_JAPANESE = "おはようございます。今日はいい天気ですね。"


@pytest.mark.parametrize(
    ("text", "encoding"),
    [
        (_FRENCH, "utf-8-sig"),
        (_FRENCH, "utf-16"),
        (_FRENCH, "cp1252"),
        (_POLISH, "cp1250"),
        (_RUSSIAN, "cp1251"),
        (_JAPANESE, "shift_jis"),
        (_JAPANESE, "euc_jp"),
        ("はい。", "shift_jis"),
    ],
)
def test_decodes_common_encodings(text, encoding):
    data = f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n".encode(encoding)
    assert load_bytes(data).lines[0].text == text


def test_decode_reports_the_encoding():
    assert decode("abc".encode("utf-16"))[1] == "utf-16"
    assert decode(b"abc")[1] == "utf-8"


def test_rejects_unsupported_and_empty_input():
    with pytest.raises(SubtitleError, match="unsupported"):
        loads("WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n")
    with pytest.raises(SubtitleError):
        loads("this is not a subtitle file")
    with pytest.raises(SubtitleError):
        dumps(loads("1\n00:00:01,000 --> 00:00:02,000\nhi\n"), "vtt")


def test_a_file_with_a_long_header_is_still_recognized():
    # A muxer can write the fonts of a release into the header, which puts the
    # first line of dialogue far behind where a format is usually looked for.
    font = "fontname: FANCY_0.ttf\n" + "\n".join("M" + "!" * 79 for _ in range(4_000))
    text = (
        "[Script Info]\nTitle: Episode\nScriptType: v4.00+\n"
        + font
        + "\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Good morning.\n"
    )

    subtitle = loads(text)

    assert subtitle.format == "ass"
    assert [line.text for line in subtitle.lines] == ["Good morning."]

    late = "\n" * 20_000 + "1\n00:00:01,000 --> 00:00:02,000\nGood morning.\n"
    assert loads(late).format == "srt"
    with pytest.raises(SubtitleError):
        loads("x" * 30_000)


@pytest.mark.parametrize(
    "filler",
    ["\n", "\r\n", " \n", "\t\n\n", "1\n", "00:00:01,000\n\n", "Dialogue:\n\n", "[Events] \n"],
)
def test_the_format_is_looked_for_in_time_proportional_to_the_text(filler):
    # No format is found at the start of such a file, so all of it is looked
    # at. A hundred thousand blank lines once took most of a minute, and a
    # megabyte of them an hour, with the whole server waiting.
    text = "x\n" + filler * 100_000 + "x"

    started = time.monotonic()
    with contextlib.suppress(SubtitleError):
        loads(text)
    assert time.monotonic() - started < 5


def _script(*times: tuple[str, str]) -> str:
    events = "".join(
        f"Dialogue: 0,{start},{end},Default,,0,0,0,,Line {index}\n"
        for index, (start, end) in enumerate(times)
    )
    return (
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + events
    )


def test_times_are_read_as_players_read_them():
    # A tool that cut a film's script into parts wrote these. Players add the
    # parts up, each with its sign: the first line is over long before zero,
    # the last one comes after two hours.
    subtitle = loads(
        _script(
            ("0:-39:-28.91", "0:-39:-28.61"),
            ("-1:-81:3578.09", "-1:-81:3581.18"),
            ("0:0:57.09", "0:1:0.18"),
            ("-0:00:01.00", "0:00:02.50"),
            ("2:129:-7187.48", "2:129:-7182.13"),
            ("0:-35:-1.0900000000001", "0:0:3.0900000000001"),
        )
    )

    assert [(line.start_ms, line.end_ms) for line in subtitle.lines] == [
        (0, 0),
        (0, 0),
        (57_090, 60_180),
        (0, 2_500),
        (7_753_480, 7_758_130),
        (0, 3_090),
    ]
    assert "0:00:57.09,0:01:00.18" in dumps(subtitle)


def test_a_time_that_is_no_time_is_still_refused():
    with pytest.raises(SubtitleError, match="times that cannot be read"):
        loads(_script(("0:00:xx.00", "0:00:02.00")))


def test_usual_times_are_left_exactly_as_they_are():
    text = _script(("0:00:01.00", "0:00:02.00"), ("1:02:03.45", "1:02:04.5"))

    assert _usual_event_times(text) == text
