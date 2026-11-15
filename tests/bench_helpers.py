"""Shared builders for the benchmark tests."""

import json
from pathlib import Path

from retimer import Subtitle, loads

WORDS = [
    "apple",
    "river",
    "stone",
    "candle",
    "window",
    "garden",
    "silver",
    "thunder",
    "marble",
    "lantern",
]


def make_base(
    lines: int = 40, first_ms: int = 20_000, step_ms: int = 4_000, pause_every: int = 0
) -> Subtitle:
    """An in-sync SRT with distinct dialogue lines, one every `step_ms`.

    `pause_every` adds ten quiet seconds after every that many lines.
    """
    blocks = []
    for index in range(lines):
        start = first_ms + index * step_ms
        if pause_every:
            start += 10_000 * (index // pause_every)
        end = start + 2_500
        text = f"{WORDS[index % len(WORDS)]} number {index}"
        blocks.append(f"{index + 1}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    return loads("\n".join(blocks))


def _stamp(ms: int) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def write_seeds(directory: Path, names: tuple[str, ...] = ("alpha", "beta")) -> Path:
    """A seed directory with two languages per seed and no audio."""
    from retimer import dumps

    seeds = []
    for position, name in enumerate(names):
        base = make_base(lines=40 + 6 * position, first_ms=20_000 + 7_000 * position)
        (directory / name).mkdir(parents=True)
        for language in ("en", "ja"):
            (directory / name / f"{language}.srt").write_text(dumps(base), encoding="utf-8")
        seeds.append(
            {
                "id": name,
                "title": name.title(),
                "subtitles": {"en": f"{name}/en.srt", "ja": f"{name}/ja.srt"},
            }
        )
    (directory / "seeds.json").write_text(json.dumps({"seeds": seeds}), encoding="utf-8")
    return directory
