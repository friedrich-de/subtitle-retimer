import shutil
import subprocess

import pytest

from retimer import load, loads
from retimer.realset.media import (
    Stream,
    count_spoken,
    extract,
    pick_audio,
    probe,
    read_probe,
    text_tracks,
    two_scripts,
)

from .sync_helpers import make_subtitle

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def audio(index, language):
    return Stream(index, "audio", "opus", language, None)


def test_the_probe_keeps_audio_and_subtitle_streams():
    body = {
        "format": {"duration": "1420.5"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "hevc"},
            {"index": 1, "codec_type": "audio", "codec_name": "opus", "tags": {"language": "jpn"}},
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "ass",
                "tags": {"LANGUAGE": "eng", "title": "Full"},
                "disposition": {"default": 1, "forced": 0},
            },
            {"index": 3, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
        ],
    }
    duration, streams = read_probe(body)
    assert duration == 1420.5
    assert streams == [
        Stream(1, "audio", "opus", "jpn", None),
        Stream(2, "subtitle", "ass", "eng", "Full", default=True),
        Stream(3, "subtitle", "hdmv_pgs_subtitle", None, None),
    ]
    # Image subtitles are not text tracks.
    assert [stream.index for stream in text_tracks(streams)] == [2]


def test_the_japanese_audio_is_picked():
    assert pick_audio([audio(1, "eng"), audio(2, "jpn")]).index == 2
    # Untagged next to English is taken to be the original.
    assert pick_audio([audio(1, "eng"), audio(2, None)]).index == 2
    assert pick_audio([audio(1, None)]).index == 1
    assert pick_audio([audio(1, "eng"), audio(2, "eng")]).index == 1
    assert pick_audio([]) is None


ASS = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Somebody says this.
Dialogue: 1,0:00:01.00,0:00:02.00,Default,,0,0,0,,Somebody says this.
Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\i1}And this.
Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\pos(100,200)}A SIGN
Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,{\\k20}la {\\k30}la
Dialogue: 0,0:00:09.00,0:00:10.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10
Dialogue: 0,0:00:11.00,0:00:11.00,Default,,0,0,0,,No time at all.
Comment: 0,0:00:12.00,0:00:13.00,Default,,0,0,0,,A note.
"""


def test_only_lines_that_read_like_speech_are_counted():
    assert count_spoken(loads(ASS)) == (2, 0)


def test_lines_that_describe_a_sound_are_counted_apart():
    texts = [
        "[door slams]",
        "Who is there?",
        "(sighs)\\N[footsteps]",
        "- [laughs] You again.",
        "（物音）",
    ]
    subtitle = make_subtitle([1_000, 3_000, 5_000, 7_000, 9_000], texts)
    assert count_spoken(subtitle) == (5, 3)


def test_two_scripts_in_lines_of_their_own_or_in_the_same_line():
    japanese = [
        "これは日本語の台詞です",
        "もう一つの台詞ですね",
        "そうだと思います",
        "行きましょう",
    ]
    assert not two_scripts(make_subtitle([1_000, 3_000, 5_000, 7_000], japanese))
    mixed = [japanese[0], "This is the same line", japanese[1], "And another line"]
    assert two_scripts(make_subtitle([1_000, 1_000, 5_000, 5_000], mixed))
    chinese = [japanese[0], "这是同一句话", japanese[1], "还有一句"]
    assert two_scripts(make_subtitle([1_000, 1_000, 5_000, 5_000], chinese))
    stacked = [f"{line}\\NThe same line in English" for line in japanese]
    assert two_scripts(make_subtitle([1_000, 3_000, 5_000, 7_000], stacked))
    assert not two_scripts(make_subtitle([1_000, 3_000], ["Only English here", "And here too"]))


@needs_ffmpeg
def test_tracks_and_audio_come_out_of_a_video(tmp_path):
    subtitle = tmp_path / "in.srt"
    subtitle.write_text("1\n00:00:00,500 --> 00:00:01,500\nA line of speech.\n", encoding="utf-8")
    video = tmp_path / "video.mkv"
    made = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
            "-i", str(subtitle),
            "-map", "0:a", "-map", "1:a", "-map", "2:s",
            "-c:a", "libopus", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=jpn",
            "-metadata:s:s:0", "language=eng",
            str(video),
        ],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    duration, streams = probe(video)
    assert 1.5 < duration < 2.5
    japanese = pick_audio(streams)
    assert japanese.language == "jpn"
    tracks = text_tracks(streams)
    assert [(stream.codec, stream.language) for stream in tracks] == [("subrip", "eng")]

    out, ogg = tmp_path / "out" / "s02.srt", tmp_path / "out" / "audio.ogg"
    extract(video, [(tracks[0], out)], (japanese, ogg))
    assert [line.text for line in load(out).lines] == ["A line of speech."]
    assert ogg.read_bytes().startswith(b"OggS")
    assert not list((tmp_path / "out").glob(".part-*"))
    before = video.stat().st_mtime_ns
    extract(video, [], None)
    assert video.stat().st_mtime_ns == before
