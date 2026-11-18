import json
import shutil
import wave

import numpy as np
import pytest

from retimer.bench.soundcheck import (
    CLEAR_STRENGTH,
    SAMPLE_RATE,
    check_sound,
    line_spans,
    read_offset,
    render_text,
    voice_presence,
)
from retimer.cli import main
from retimer.io import loads

from .test_counterpart import make_script

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def _stamp(ms: int) -> str:
    return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def srt(spans: list[tuple[int, int]]) -> str:
    return "\n".join(
        f"{number}\n{_stamp(start)} --> {_stamp(end)}\nSentence number {number}.\n"
        for number, (start, end) in enumerate(spans, 1)
    )


def voice(duration_ms: int, pitch_hz: float, rng: np.random.Generator) -> np.ndarray:
    """Something with a pitch and overtones, as a vowel has, swelling and fading."""
    time = np.arange(duration_ms * SAMPLE_RATE // 1000) / SAMPLE_RATE
    glide = pitch_hz * (1 + 0.1 * np.sin(2 * np.pi * rng.uniform(1, 3) * time))
    phase = 2 * np.pi * np.cumsum(glide) / SAMPLE_RATE
    tone = sum(np.sin(harmonic * phase) / harmonic for harmonic in range(1, 16))
    syllables = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * time)
    edge = np.minimum(1, np.minimum(time, time[-1] - time) / 0.03)
    return 0.2 * tone * syllables * edge


def film(seconds: int, seed: int = 3) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Hiss, a drone that never stops, and sentences at irregular moments."""
    rng = np.random.default_rng(seed)
    time = np.arange(seconds * SAMPLE_RATE) / SAMPLE_RATE
    samples = 0.01 * rng.standard_normal(len(time)) + 0.03 * np.sin(2 * np.pi * 220 * time)
    spans = []
    at = 2_000
    while at < seconds * 1000 - 6_000:
        length = int(rng.integers(700, 2_600))
        spoken = voice(length, rng.uniform(110, 240), rng)
        first = at * SAMPLE_RATE // 1000
        samples[first : first + len(spoken)] += spoken
        spans.append((at, at + length))
        at += length + int(rng.integers(400, 5_000))
    return samples.astype(np.float32), spans


def moved(spans: list[tuple[int, int]], by_ms: int) -> list[tuple[int, int]]:
    return [(start + by_ms, end + by_ms) for start, end in spans]


# What the sound is read as


def test_a_voice_stands_out_and_a_drone_and_hiss_do_not():
    rng = np.random.default_rng(1)
    time = np.arange(20 * SAMPLE_RATE) / SAMPLE_RATE
    samples = 0.01 * rng.standard_normal(len(time)) + 0.03 * np.sin(2 * np.pi * 220 * time)
    spoken = voice(1_500, 160, rng)
    samples[10 * SAMPLE_RATE : 10 * SAMPLE_RATE + len(spoken)] += spoken

    presence = voice_presence(samples.astype(np.float32))

    assert len(presence) == 2_000
    during = presence[1_010:1_140].mean()
    assert during > 20 * presence[200:900].mean()
    assert during > 20 * presence[1_300:1_900].mean()


def test_the_lines_checked_are_the_dialogue_with_layers_as_one_start():
    script = make_script(
        [
            (5_000, "Default", "Good evening."),
            (5_000, "Default", "{\\blur3}Good evening."),
            (9_000, "Signs", "{\\pos(640,80)}Fuyuki City"),
            (12_000, "OP", "{\\k20}la {\\k30}la"),
            (15_000, "Default", "[door slams]"),
            (20_000, "Default", "Who is there?"),
        ]
    )

    assert [start for start, _ in line_spans(script)] == [5_000, 20_000]


# The offset and how far it can be believed


def test_lines_in_sync_are_found_at_no_offset():
    samples, spans = film(400)

    check = check_sound(samples, loads(srt(spans)))

    assert check.lines == len(spans)
    assert (check.clear, abs(check.offset_ms) <= 30) == (True, True)
    assert check.strength > 2 * CLEAR_STRENGTH
    assert [window.lines for window in check.windows] == [
        sum(1 for start, _ in spans if low <= start < low + 180_000)
        for low in (0, 180_000, 360_000)
    ]
    for window in check.windows[:2]:
        assert (window.clear, abs(window.offset_ms) <= 30) == (True, True)


@pytest.mark.parametrize("late_ms", [-3_200, -700, 450, 2_600])
def test_the_offset_is_what_the_lines_would_have_to_move(late_ms):
    samples, spans = film(200)

    check = check_sound(samples, loads(srt(moved(spans, late_ms))))

    # Lines that come too late have to move back.
    assert check.clear
    assert abs(check.offset_ms + late_ms) <= 30


def test_every_stretch_gets_its_own_offset():
    samples, spans = film(400)
    # After three minutes something was cut out, and every later line is 1.5 s early.
    broken = [span if span[0] < 180_000 else moved([span], -1_500)[0] for span in spans]

    check = check_sound(samples, loads(srt(broken)))

    first, second = check.windows[:2]
    assert (first.clear, second.clear) == (True, True)
    assert abs(first.offset_ms) <= 30
    assert abs(second.offset_ms - 1_500) <= 30


def test_lines_of_something_else_are_not_clear():
    samples, _ = film(400)
    stretches = []
    for seed in (11, 12, 13, 14):
        _, other = film(400, seed=seed)
        check = check_sound(samples, loads(srt(other)))
        assert check.clear is False
        assert check.strength < CLEAR_STRENGTH
        stretches += [window.clear for window in check.windows if window.strength is not None]

    # Chance gets a stretch over the bar now and then, which is why the bar
    # is no promise. It must stay rare.
    assert len(stretches) >= 8
    assert sum(stretches) <= 1


def test_lines_further_off_than_the_search_reaches_are_not_clear():
    samples, spans = film(300)

    check = check_sound(samples, loads(srt(moved(spans, 9_000))))
    wider = check_sound(samples, loads(srt(moved(spans, 9_000))), reach_ms=12_000)

    assert check.clear is False
    assert (wider.clear, abs(wider.offset_ms + 9_000) <= 30) == (True, True)


def test_a_few_lines_tell_nothing():
    samples, spans = film(200)

    check = check_sound(samples, loads(srt(spans[:5])))

    assert (check.lines, check.offset_ms, check.strength, check.clear) == (5, None, None, False)
    assert check.windows[0].offset_ms is None
    assert "too few to tell" in render_text(check)


def test_a_flat_curve_has_no_strength():
    flat = np.zeros(2 * 8_708 + 1)

    assert read_offset(flat)[1] == 0.0


# Command line


@needs_ffmpeg
def test_the_command_reads_a_file_and_prints_the_check(tmp_path, capsys):
    samples, spans = film(200)
    audio = tmp_path / "film.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((np.clip(samples, -1, 1) * 32_767).astype(np.int16).tobytes())
    subtitle = tmp_path / "late.srt"
    subtitle.write_text(srt(moved(spans, 1_000)), encoding="utf-8")

    assert main(["bench", "soundcheck", str(audio), str(subtitle), "--json"]) == 0
    check = json.loads(capsys.readouterr().out)
    assert (check["clear"], abs(check["offset_ms"] + 1_000) <= 30) == (True, True)
    assert check["windows"][0]["lines"] > 20

    assert main(["bench", "soundcheck", str(audio), str(subtitle)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("whole file") and "clear" in printed

    assert main(["bench", "soundcheck", str(tmp_path / "missing.wav"), str(subtitle)]) == 1
    assert "error:" in capsys.readouterr().err
