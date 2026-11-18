"""Reference subtitles as a video file holds them: dialogue, signs and songs, another episode."""

from retimer import Subtitle, loads
from retimer.choice import Candidate, TrackLabel

from .sync_helpers import SENTENCES, make_subtitle, spread

COUNT = 14
LATE_MS = 3_500
TRUTH = spread(COUNT)
SIGNS = ["Harbor Office", "Platform 3", "Closed on Sundays", "Chapter Two", "North Gate"]


def _stamp(ms: int) -> str:
    return f"{ms // 3_600_000}:{ms // 60_000 % 60:02d}:{ms % 60_000 / 1000:05.2f}"


def script(events: list[tuple[int, str, str]]) -> Subtitle:
    """An ASS file. An event is (start, style, text)."""
    styles = "\n".join(
        f"Style: {name},Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,20,20,30,1"
        for name in sorted({style for _, style, _ in events})
    )
    rows = "\n".join(
        f"Dialogue: 0,{_stamp(start)},{_stamp(start + 2_000)},{style},,0,0,0,,{text}"
        for start, style, text in events
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


def target(count: int = COUNT, late_ms: int = LATE_MS) -> Subtitle:
    """The subtitle to sync: the first sentences, late."""
    return make_subtitle([start + late_ms for start in spread(count)], SENTENCES[:count])


def full(count: int = COUNT, length_ms: int = 2_000) -> Subtitle:
    """The dialogue of the episode, in sync."""
    return make_subtitle(spread(count), SENTENCES[:count], length_ms)


def part(indexes: list[int]) -> Subtitle:
    """Some lines of the dialogue, in sync."""
    return make_subtitle(
        [TRUTH[index] for index in indexes], [SENTENCES[index] for index in indexes]
    )


def signs() -> Subtitle:
    """A track of signs and a song, without a line of dialogue."""
    events = [
        (5_000 + index * 17_000, "Signs", "{\\pos(320,60)}" + text)
        for index, text in enumerate(SIGNS)
    ]
    events += [(90_000 + index * 4_000, "OP Romaji", f"la la {index}") for index in range(4)]
    return script(events)


def other_episode() -> Subtitle:
    """Dialogue of another episode: as much of it, and none of it the same."""
    return make_subtitle(spread(COUNT), SENTENCES[15 : 15 + COUNT])


def track(subtitle: Subtitle | None, **label) -> Candidate:
    return Candidate(subtitle=subtitle, label=TrackLabel(**label))
