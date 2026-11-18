"""Cases that replicate what real files did, built from the open seed material.

A run over 1,600 real subtitle pairs showed where syncing goes wrong or gets
hard: a short scene before a sponsor card, broadcast captions that sit a
fraction of a second early for minutes, several episodes in one file, a cut in
the middle of a scene, the next episode sent by mistake, a track of signs and
songs as the reference, scripts in which a karaoke is thousands of lines,
captions full of speaker names and sounds, two languages in one file, times
written in ways only players read, and a video that holds several subtitle
tracks of which one is the dialogue. Those files are other people's work and
cannot be published. So every problem is rebuilt here from the seed material,
the two open films and the series that was written for the benchmark, with
the same shape and with known-correct timing.

Nothing in these cases is taken from a real episode. Song words, signs and
speaker names are made up.

One thing differs from the plain families throughout. There, the two
languages of a seed are used as they are, and those of the films share their
timing to the millisecond, which no two subtitles of a real film do. Here a
reference subtitle is first timed anew,
the way a second translator would have: every line a little off, some
sentences joined, some cut in two, some left out. `loosen` does that.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pysubs2

from retimer.bench.distort import (
    Break,
    Distorted,
    Distortion,
    DistortionError,
    Extra,
    TruthLine,
    distort,
)
from retimer.io import dumps
from retimer.model import Line, Subtitle
from retimer.plan import Plan
from retimer.views import Options

# How far a second translator's line starts lie from the first one's. Pairs of
# real subtitles of one episode scatter by 0.15 to 0.7 s from end to end.
_SCATTER_MS = 150
_MAX_SCATTER_MS = 500
_JOIN_GAP_MS = 1_200
_SPLIT_MIN_MS = 3_000
_FRAME_MS = 42
_MIN_FIRST_START_MS = 500

_LYRICS = [
    "Run until the morning finds us",
    "Paper lanterns on the river",
    "Every road is turning homeward",
    "Count the stars above the harbour",
    "Hold the line and keep on breathing",
    "Silver rain on empty stations",
    "We were never meant to stay here",
    "Carry me beyond the skyline",
    "All the clocks have stopped at midnight",
    "Sing it louder than the thunder",
    "Tomorrow waits behind the curtain",
    "One more step and we are flying",
]
_SYLLABLES = ["ha", "shi", "re", "to", "o", "ku", "ma", "de", "yu", "me", "no", "sa", "ki", "ra"]
_KANA = ["は", "し", "れ", "と", "お", "く", "ま", "で", "ゆ", "め", "の", "さ", "き", "ら"]
_SIGNS = {
    "en": [
        "Central Station",
        "Three days later",
        "Research Wing B",
        "Authorized personnel only",
        "Harbour District",
        "Lecture Hall 2",
        "Emergency Exit",
        "Old Town Clock Tower",
        "Chapter Four",
        "The next morning",
        "Closed for repairs",
        "Platform 7",
    ],
    "ja": [
        "中央駅",
        "三日後",
        "研究棟Ｂ",
        "関係者以外立入禁止",
        "港湾地区",
        "第二講義室",
        "非常口",
        "旧市街の時計塔",
        "第四章",
        "翌朝",
        "修理中",
        "７番ホーム",
    ],
}
_SPEAKERS = ["男", "女", "少女", "老人", "隊長", "無線"]
_CAPTION_SOUNDS = [
    "（爆発音）",
    "（足音）",
    "（ドアが開く音）",
    "（荒い息）",
    "（機械の作動音）",
    "（風の音）",
    "（銃声）",
    "（遠くで鐘の音）",
]
_CAPTION_MUSIC = ["♪～", "♪～（静かな音楽）", "♬～"]
_FURIGANA = ["ゆめ", "おも", "こころ", "たび", "ちから", "やくそく", "せかい", "ひかり"]

SONG_STYLES = {
    "OP-Romaji": pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER, fontsize=36),
    "OP-Kanji": pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER, fontsize=30),
    "OP-English": pysubs2.SSAStyle(alignment=pysubs2.Alignment.BOTTOM_CENTER, fontsize=34),
    "ED-Romaji": pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER, fontsize=36),
    "ED-Kanji": pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER, fontsize=30),
    "ED-English": pysubs2.SSAStyle(alignment=pysubs2.Alignment.BOTTOM_CENTER, fontsize=34),
    "Signs": pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER, fontsize=40),
    "Furigana": pysubs2.SSAStyle(alignment=pysubs2.Alignment.BOTTOM_CENTER, fontsize=18),
}


@dataclass
class Material:
    """What a case is built from: one seed in two languages, and another seed."""

    seed_id: str
    language: str
    reference_language: str
    target: Subtitle
    reference: Subtitle
    other_target: Subtitle
    case_seed: int
    # The other seed in the language of the reference, where it has that one.
    other_reference: Subtitle | None = None

    def rng(self, salt: str = "") -> random.Random:
        return random.Random(f"{self.case_seed}:{salt}")

    @property
    def lines(self) -> list[Line]:
        return sorted(self.target.lines, key=lambda line: line.start_ms)


@dataclass
class Track:
    """One of several reference subtitles of a case, and what its container says of it.

    `role` says what the track is: "full" for the dialogue of this film,
    "signs" for a track of signs and songs, "other_episode" for the dialogue
    of another one. The labels are what a video file would say, and they may
    be missing or misleading, as they are in real files.
    """

    subtitle: Subtitle
    role: str
    language: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False


@dataclass
class Built:
    """One case as its builder leaves it.

    `reference` is a reference subtitle made for this case. Without one the
    case uses the seed's own. `references` are several to choose from, in the
    order a video file would hold them. `target_text` is the target as it has
    to be written where the usual writer would tidy away what the case is
    about.
    """

    target: Subtitle
    truth: list[TruthLine] = field(default_factory=list)
    ideal_plan: Plan | None = None
    distortion: Distortion | None = None
    reference: Subtitle | None = None
    references: list[Track] = field(default_factory=list)
    target_text: str | None = None
    expected_outcome: str = "synced"
    options: Options = field(default_factory=Options)


# The second argument says whether the seed's audio comes with the case.
Builder = Callable[[Material, bool], Built]


@dataclass(frozen=True)
class RealRecipe:
    """A named case.

    `audio` says whether it is also built against the seed's audio alone,
    `both` whether it is also built with the audio beside its reference
    subtitles.
    """

    name: str
    family: str
    build: Builder
    audio: bool = False
    both: bool = False

    @property
    def label(self) -> str:
        return f"{self.family.replace('_', '-')}-{self.name}"


# A reference as a second translator would have timed it


def _split_text(text: str) -> tuple[str, str] | None:
    """A line as two, cut between words. None when it is too short to be cut."""
    plain = text.replace("\\N", " ").replace("\r", "")
    words = plain.split(" ")
    if len(words) >= 4:
        half = len(words) // 2
        return " ".join(words[:half]), " ".join(words[half:])
    if len(words) == 1 and len(plain) >= 10 and not plain.isascii():
        # A script without spaces between words is cut in the middle.
        return plain[: len(plain) // 2], plain[len(plain) // 2 :]
    return None


def loosen(
    subtitle: Subtitle,
    rng: random.Random,
    *,
    scatter_ms: int = _SCATTER_MS,
    join_share: float = 0.15,
    split_share: float = 0.1,
    drop_share: float = 0.05,
    rough_until_ms: int = 0,
) -> Subtitle:
    """Time a subtitle anew, the way another person would have.

    Line starts and ends move by a few hundred milliseconds each, some
    neighbours become one line, some long lines become two, and a few short
    ones are left out. Taken over all its lines the file stays exactly where
    it was, so the timing it stands for is still the true one.

    Up to `rough_until_ms` the lines are timed far more freely, up to two
    seconds late, as a narration is that two people cut into sentences each
    in their own way.
    """
    rows = sorted((line.start_ms, line.end_ms, line.text) for line in subtitle.lines)
    shortest = sorted(end - start for start, end, _ in rows)[len(rows) // 3]
    kept = [
        row for row in rows if not (row[1] - row[0] <= shortest and rng.random() < drop_share * 3)
    ]
    joined: list[tuple[int, int, str]] = []
    for start, end, text in kept:
        if (
            joined
            and start - joined[-1][1] <= _JOIN_GAP_MS
            and "\\N" not in joined[-1][2]
            and rng.random() < join_share
        ):
            before = joined.pop()
            joined.append((before[0], end, f"{before[2]}\\N{text}"))
        else:
            joined.append((start, end, text))
    parts: list[tuple[int, int, str]] = []
    for start, end, text in joined:
        halves = _split_text(text) if end - start >= _SPLIT_MIN_MS and "\\N" not in text else None
        if halves is not None and rng.random() < split_share * 2:
            first, second = halves
            middle = start + (end - start) * len(first) // len(first + second)
            parts += [(start, middle - 40, first), (middle + 40, end, second)]
        else:
            parts.append((start, end, text))

    def off(spread: float) -> int:
        return round(max(-_MAX_SCATTER_MS, min(_MAX_SCATTER_MS, rng.gauss(0, spread))))

    moves = [off(scatter_ms) for _ in parts]
    steady = [move for move, part in zip(moves, parts, strict=True) if part[0] >= rough_until_ms]
    centre = round(sum(steady) / len(steady)) if steady else 0
    ssa = pysubs2.SSAFile()
    ssa.styles = {name: style.copy() for name, style in subtitle.ssa.styles.items()}
    floor = 0
    for (start, end, text), move in zip(parts, moves, strict=True):
        move -= centre
        if start < rough_until_ms:
            move = rng.randint(-200, 2_000)
        new_start = max(floor, start + move)
        new_end = max(new_start + 500, end + move + off(scatter_ms))
        ssa.events.append(pysubs2.SSAEvent(start=new_start, end=new_end, text=text))
        floor = new_start + 100
    return Subtitle(ssa, subtitle.format)


# Songs and signs as a fansub script carries them


def song(
    start_ms: int, end_ms: int, rng: random.Random, prefix: str = "OP", layers: int = 3
) -> list[Extra]:
    """A song between two moments, with the lines a karaoke template makes of it.

    Every sung line is there as the timed line the template read (a comment),
    as one effect line per syllable and layer, as a line in kana with karaoke
    tags, and as its translation.
    """
    extras: list[Extra] = []
    cursor = start_ms
    number = 0
    while cursor + 4_000 <= end_ms:
        length = min(rng.randint(4_000, 6_000), end_ms - cursor)
        count = rng.randint(8, 12)
        picks = [rng.randrange(len(_SYLLABLES)) for _ in range(count)]
        beats = [rng.randint(20, 60) for _ in range(count)]
        scale = (length - 400) / (sum(beats) * 10)
        beats = [max(8, round(beat * scale)) for beat in beats]
        timed = "".join(
            f"{{\\k{beat}}}{_SYLLABLES[pick]}" for beat, pick in zip(beats, picks, strict=True)
        )
        extras.append(
            Extra(
                cursor,
                cursor + length,
                timed,
                "karaoke",
                f"{prefix}-Romaji",
                effect="karaoke",
                comment=True,
            )
        )
        at = cursor + 200
        for position, (beat, pick) in enumerate(zip(beats, picks, strict=True)):
            x = 240 + position * 70
            for layer in range(layers):
                extras.append(
                    Extra(
                        max(0, at - 150),
                        at + beat * 10 + 150,
                        f"{{\\an5\\pos({x},60)\\fad(150,150)\\t(0,{beat * 10},\\fscx125\\fscy125)"
                        f"\\bord{2 + layer}\\blur{layer}}}{_SYLLABLES[pick]}",
                        "karaoke",
                        f"{prefix}-Romaji",
                        effect="fx",
                        layer=layer,
                    )
                )
            at += beat * 10
        kana = "".join(
            f"{{\\k{beat}}}{_KANA[pick]}" for beat, pick in zip(beats, picks, strict=True)
        )
        extras.append(Extra(cursor, cursor + length, kana, "karaoke", f"{prefix}-Kanji"))
        words = _LYRICS[(number + rng.randrange(3)) % len(_LYRICS)]
        extras.append(
            Extra(
                cursor,
                cursor + length,
                f"{{\\fad(200,200)}}{words}",
                "karaoke",
                f"{prefix}-English",
            )
        )
        cursor += length + rng.randint(100, 600)
        number += 1
    return extras


def signs(
    from_ms: int,
    to_ms: int,
    count: int,
    rng: random.Random,
    language: str = "en",
    frame_by_frame: bool = True,
) -> list[Extra]:
    """Signs spread over a stretch of the film.

    Every second one moves with the picture and is redrawn for each frame,
    with a box drawn under it, which is where a typeset script gets its
    thousands of lines from.
    """
    pool = _SIGNS.get(language, _SIGNS["en"])
    extras: list[Extra] = []
    for number in range(count):
        start = rng.randint(from_ms, max(from_ms, to_ms - 6_000))
        length = rng.randint(2_500, 5_000)
        text = pool[number % len(pool)]
        x, y = rng.randint(200, 1_000), rng.randint(60, 300)
        if not frame_by_frame or number % 2:
            extras.append(
                Extra(
                    start,
                    start + length,
                    f"{{\\an8\\pos({x},{y})\\fad(200,200)}}{text}",
                    "sign",
                    "Signs",
                )
            )
            continue
        for frame in range(length // _FRAME_MS):
            at = start + frame * _FRAME_MS
            moved = f"\\pos({x + frame * 2},{y})"
            extras.append(
                Extra(
                    at,
                    at + _FRAME_MS,
                    f"{{\\an7{moved}\\p1\\1c&H202020&}}m 0 0 l 320 0 320 64 0 64{{\\p0}}",
                    "sign",
                    "Signs",
                )
            )
            extras.append(
                Extra(
                    at,
                    at + _FRAME_MS,
                    f"{{\\an7{moved}\\blur0.6}}{text}",
                    "sign",
                    "Signs",
                    layer=1,
                )
            )
    return extras


def _widest_gap(lines: Sequence[Line], least_ms: int) -> tuple[int, int] | None:
    gaps = [
        (later.start_ms - earlier.end_ms, earlier.end_ms, later.start_ms)
        for earlier, later in zip(lines, lines[1:], strict=False)
    ]
    wide = [gap for gap in gaps if gap[0] >= least_ms]
    return max(wide)[1:] if wide else None


def typesetting(
    lines: Sequence[Line], rng: random.Random, language: str, heavy: int
) -> list[Extra]:
    """What a fansub script has besides its dialogue: an opening, an ending and signs."""
    extras: list[Extra] = []
    gap = _widest_gap(lines, 20_000)
    if gap is not None:
        extras += song(gap[0] + 2_000, min(gap[1] - 2_000, gap[0] + 92_000), rng, "OP", 3 * heavy)
    last = max(line.end_ms for line in lines)
    extras += song(last + 5_000, last + 95_000, rng, "ED", 3 * heavy)
    extras += signs(lines[0].start_ms, last, 6 * heavy, rng, language)
    return extras


def _script(extras: Sequence[Extra], fmt: str = "ass") -> Subtitle:
    """A subtitle that holds only added lines, such as a track of signs and songs."""
    ssa = pysubs2.SSAFile()
    for name, style in SONG_STYLES.items():
        ssa.styles[name] = style.copy()
    for extra in sorted(extras, key=lambda item: item.start_ms):
        ssa.events.append(
            pysubs2.SSAEvent(
                start=extra.start_ms,
                end=extra.end_ms,
                text=extra.text,
                style=extra.style if fmt == "ass" else "Default",
                effect=extra.effect,
                layer=extra.layer,
                type="Comment" if extra.comment else "Dialogue",
            )
        )
    return Subtitle(ssa, fmt)


# Helpers for the builders


def _as_format(subtitle: Subtitle, fmt: str) -> Subtitle:
    return Subtitle(subtitle.copy().ssa, fmt)


def _retexted(subtitle: Subtitle, texts: Sequence[str]) -> Subtitle:
    copy = subtitle.copy()
    for event, text in zip(copy.ssa.events, texts, strict=True):
        event.text = text
    return copy


def _built(
    made: Distorted, distortion: Distortion | None, reference: Subtitle | None = None, **more
) -> Built:
    return Built(
        target=made.subtitle,
        truth=made.truth,
        ideal_plan=made.ideal_plan,
        distortion=distortion,
        reference=reference,
        **more,
    )


def _reference(material: Material, audio: bool) -> Subtitle | None:
    """The reference subtitle of a plain case: the seed's, timed anew. None against audio."""
    return None if audio else loosen(material.reference, material.rng("reference"))


def _before(lines: Sequence[Line], index: int) -> int:
    """A moment in the pause before line `index`."""
    return (max(line.end_ms for line in lines[:index]) + lines[index].start_ms) // 2


def _pause_near(lines: Sequence[Line], share: float, least_ms: int = 1_500) -> int:
    """The line after the longest pause around `share` of the way through the file."""
    centre = round(share * len(lines))
    span = max(3, len(lines) // 6)
    candidates = range(max(3, centre - span), min(len(lines) - 3, centre + span) + 1)
    best = max(candidates, key=lambda index: lines[index].start_ms - lines[index - 1].end_ms)
    if lines[best].start_ms - lines[best - 1].end_ms < least_ms:
        raise DistortionError("no pause to put a break in")
    return best


def _dense_near(lines: Sequence[Line], share: float) -> int:
    """The line that follows its neighbour most closely around `share` of the way through."""
    centre = round(share * len(lines))
    span = max(3, len(lines) // 6)
    candidates = range(max(4, centre - span), min(len(lines) - 4, centre + span) + 1)
    return min(candidates, key=lambda index: lines[index].start_ms - lines[index - 1].end_ms)


def _make(
    material: Material,
    distortion: Distortion,
    audio: bool,
    *,
    base: Subtitle | None = None,
    fmt: str | None = None,
    extras: Sequence[Extra] = (),
    reference: Subtitle | None = None,
    **more,
) -> Built:
    base = base if base is not None else material.target
    first = min(line.start_ms for line in base.lines)
    if first + distortion.offset_ms < 0:
        # An episode that opens with its first line has less room for an early
        # file than the case asks for. It gets what there is.
        distortion = distortion.model_copy(update={"offset_ms": _MIN_FIRST_START_MS - first})
    made = distort(
        base,
        distortion,
        fmt=fmt,
        language=material.language,
        seed=material.case_seed,
        extras=extras,
        styles=SONG_STYLES if extras else None,
    )
    if reference is None:
        reference = _reference(material, audio)
    return _built(made, distortion, None if audio else reference, **more)


# A short scene before the sponsor card, and commercial breaks


def _cold_open(
    scene_lines: int, card_ms: int, offset_ms: int, rough_reference: bool = False
) -> Builder:
    """A few lines, then something the other version does not have, then the rest.

    With `rough_reference` the reference times the lines of that first scene
    its own way, up to two seconds off. That is what the one real file did
    whose first scene stayed with the rest.
    """

    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        count = min(scene_lines, len(lines) // 5)
        at_ms = _before(lines, count)
        distortion = Distortion(offset_ms=offset_ms, breaks=[Break(at_ms=at_ms, delta_ms=card_ms)])
        reference = None
        if rough_reference and not audio:
            reference = loosen(material.reference, material.rng("reference"), rough_until_ms=at_ms)
        return _make(material, distortion, audio, reference=reference)

    return build


def _broadcast(material: Material, audio: bool) -> Built:
    """A recording from television: sponsor card after the first scene, two commercial breaks."""
    lines = material.lines
    scene = min(5, len(lines) // 5)
    breaks = [
        Break(at_ms=_before(lines, scene), delta_ms=10_000),
        Break(at_ms=_before(lines, _pause_near(lines, 0.45)), delta_ms=90_000),
        Break(at_ms=_before(lines, _pause_near(lines, 0.8)), delta_ms=60_000),
    ]
    return _make(material, Distortion(offset_ms=500, breaks=breaks), audio)


# Broadcast captions that sit a fraction of a second early for a while


def _drift(stretches: Sequence[tuple[float, float, int]], offset_ms: int = 0) -> Builder:
    """Stretches of the file, as shares of its lines, that are off by a little."""

    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        breaks = []
        for low, high, ms in stretches:
            first = max(1, round(low * len(lines)))
            after = min(len(lines) - 1, round(high * len(lines)))
            # An early stretch begins as a cut does, in the room before its first line.
            room = lines[first].start_ms - lines[first - 1].start_ms
            if room <= abs(ms) + 100:
                raise DistortionError("no room before the stretch")
            breaks.append(Break(at_ms=lines[first].start_ms - abs(ms) - 50, delta_ms=ms))
            breaks.append(Break(at_ms=lines[after].start_ms - 20, delta_ms=-ms))
        return _make(material, Distortion(offset_ms=offset_ms, breaks=breaks), audio)

    return build


# Several episodes in one file


def _pack(before: float, after: float, offset_ms: int = 0) -> Builder:
    """The seed as one episode of a pack, with shares of the other seed around it."""

    def build(material: Material, audio: bool) -> Built:
        own = material.lines
        other = sorted(material.other_target.lines, key=lambda line: line.start_ms)
        head = other[: round(before * len(other))]
        tail = other[len(other) - round(after * len(other)) :] if after else []
        lead = (max(line.end_ms for line in head) + 60_000) if head else 0
        extras = [
            Extra(
                line.start_ms - lead - offset_ms,
                line.end_ms - lead - offset_ms,
                line.text,
                "other_content",
                timed=False,
            )
            for line in head
        ]
        own_end = max(line.end_ms for line in own) + 60_000
        tail_from = tail[0].start_ms - 20_000 if tail else 0
        extras += [
            Extra(
                own_end + line.start_ms - tail_from,
                own_end + line.end_ms - tail_from,
                line.text,
                "other_content",
                timed=False,
            )
            for line in tail
        ]
        distortion = Distortion(offset_ms=lead + offset_ms)
        made = distort(
            material.target,
            distortion,
            language=material.language,
            seed=material.case_seed,
            extras=extras,
        )
        return _built(made, distortion, _reference(material, audio))

    return build


# A cut in the middle of a scene


def _scene_edits(edits: Sequence[tuple[float, int]], offset_ms: int = 0) -> Builder:
    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        breaks = [
            Break(at_ms=lines[_dense_near(lines, share)].start_ms - 30, delta_ms=ms)
            for share, ms in edits
        ]
        return _make(material, Distortion(offset_ms=offset_ms, breaks=breaks), audio)

    return build


# The next episode, sent by mistake


def _next_episode(shared_opening: bool) -> Builder:
    """Another film that shares a few lines with the reference, as two episodes do.

    With `shared_opening` the shared lines stand where the reference has
    them, like a recap that opens every episode. Without it they are short
    lines scattered over the file, like phrases a series keeps repeating.
    """

    def build(material: Material, audio: bool) -> Built:
        rng = material.rng("next-episode")
        own = material.lines
        other = sorted(material.other_target.lines, key=lambda line: line.start_ms)
        share = max(3, min(len(own), len(other)) // 9)
        ssa = pysubs2.SSAFile()
        if shared_opening:
            opening = own[:share]
            until = opening[-1].end_ms + 2_000
            rows = [(line.start_ms, line.end_ms, line.text) for line in opening]
            rows += [
                (line.start_ms, line.end_ms, line.text) for line in other if line.start_ms >= until
            ]
        else:
            phrases = sorted(own, key=lambda line: (len(line.text), line.start_ms))[:share]
            slots = set(rng.sample(range(len(other)), share))
            rows = []
            for index, line in enumerate(other):
                text = phrases[len(rows) % share].text if index in slots else line.text
                rows.append((line.start_ms, line.end_ms, text))
        for start, end, text in sorted(rows):
            ssa.events.append(pysubs2.SSAEvent(start=start, end=end, text=text))
        reference = None if audio else loosen(material.reference, material.rng("reference"))
        return Built(
            target=Subtitle(ssa, material.target.format),
            reference=reference,
            expected_outcome="rejected_no_match",
        )

    return build


# A track of signs and songs as the reference


def _signs_reference(plain: bool, offset_ms: int) -> Builder:
    """The target against a track that has no dialogue at all.

    Nothing in such a track says where a spoken line belongs, so the file has
    to come back as it was, or be refused. `plain` makes the track an SRT
    file of bare sign texts, which no style marks as signs.
    """

    def build(material: Material, audio: bool) -> Built:
        rng = material.rng("signs")
        lines = material.lines
        language = material.reference_language
        last = max(line.end_ms for line in lines)
        if plain:
            track = _script(signs(lines[0].start_ms, last, 8, rng, language, False), "srt")
            for event in track.ssa.events:
                event.text = re.sub(r"\{[^}]*\}", "", event.text)
        else:
            track = _script(typesetting(lines, rng, language, 1))
        distortion = Distortion(offset_ms=offset_ms)
        made = distort(
            material.target, distortion, language=material.language, seed=material.case_seed
        )
        return Built(
            target=made.subtitle,
            truth=made.truth,
            distortion=distortion,
            reference=track,
            expected_outcome="left_alone",
        )

    return build


# Several subtitle tracks in one video, of which one is the dialogue


def _signs_track(material: Material, extras: Sequence[Extra] | None = None) -> Subtitle:
    """A track of signs and songs, as a release for a dubbed version carries it."""
    if extras is None:
        extras = typesetting(
            material.lines, material.rng("signs-track"), material.reference_language, 1
        )
    return _script(extras)


def _plain_signs_track(material: Material) -> Subtitle:
    """Sign texts in an SRT file, which no style marks as signs."""
    lines = material.lines
    last = max(line.end_ms for line in lines)
    made = signs(
        lines[0].start_ms, last, 8, material.rng("plain-signs"), material.reference_language, False
    )
    track = _script(made, "srt")
    for event in track.ssa.events:
        event.text = re.sub(r"\{[^}]*\}", "", event.text)
    return track


def _full_track(material: Material, extras: Sequence[Extra] = ()) -> Subtitle:
    """The dialogue in the reference language, timed anew. With `extras` it is a
    typeset script that carries the songs and signs as well."""
    track = loosen(material.reference, material.rng("reference"))
    if not extras:
        return track
    return distort(track, Distortion(), fmt="ass", extras=extras, styles=SONG_STYLES).subtitle


def _several(
    tracks: Callable[[Material], list[Track]],
    distortion: Distortion | None = None,
    breaks: int = 0,
    *,
    fits: bool = True,
) -> Builder:
    """The target against the subtitle tracks of one video.

    With `fits`, one of the tracks is the dialogue of the film and the target
    has to be synced against it. Without, none is. Then the file has to be
    refused, or handed back as it was where the tracks are only signs. With
    the audio beside them it is synced on the audio.
    """

    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        chosen = distortion or Distortion()
        if breaks:
            chosen = chosen.model_copy(
                update={
                    "breaks": [
                        Break(
                            at_ms=_before(lines, _pause_near(lines, (number + 1) / (breaks + 1))),
                            delta_ms=(35_000, -15_000, 70_000)[number % 3],
                        )
                        for number in range(breaks)
                    ]
                }
            )
        # Made as a case against audio is, so that no single reference is built: the
        # tracks are the reference.
        built = _make(material, chosen, True)
        built.references = tracks(material)
        if fits or audio:
            if chosen.is_identity():
                built.expected_outcome = "already_in_sync"
            return built
        # Nothing says where a line belongs, so no plan can be the right one.
        built.ideal_plan = None
        roles = {track.role for track in built.references}
        built.expected_outcome = "left_alone" if roles == {"signs"} else "rejected_no_match"
        return built

    return build


def _release_pair(signs_first: bool, honest_labels: bool) -> Callable[[Material], list[Track]]:
    """The two tracks of a fansub release: the full script, and its signs and songs alone.

    Both carry the same songs and signs. With `honest_labels` the full track
    is the default one. Without, the track of signs is marked default and
    forced, as it is where the release was set up for the dubbed version, and
    the titles say nothing.
    """

    def tracks(material: Material) -> list[Track]:
        language = material.reference_language
        extras = typesetting(material.lines, material.rng("signs-track"), language, 1)
        full = Track(_full_track(material, extras), "full", language)
        only_signs = Track(_signs_track(material, extras), "signs", language)
        if honest_labels:
            full.title, full.default = "Full Subtitles", True
            only_signs.title, only_signs.forced = "Signs & Songs", True
        else:
            only_signs.default = only_signs.forced = True
        return [only_signs, full] if signs_first else [full, only_signs]

    return tracks


def _other_episode_track(material: Material) -> Track:
    """The dialogue of another episode, which a pack or a mislabelled file brings along."""
    if material.other_reference is None:
        track = loosen(material.other_target, material.rng("other-episode"))
        return Track(track, "other_episode", material.language)
    track = loosen(material.other_reference, material.rng("other-episode"))
    return Track(track, "other_episode", material.reference_language)


def _own_language_track(material: Material) -> Track:
    """The dialogue of this film in the language of the target, timed by someone else."""
    return Track(loosen(material.target, material.rng("own-language")), "full", material.language)


def _unlabelled(material: Material) -> list[Track]:
    """Three tracks about which the file says nothing, with the dialogue last."""
    return [
        Track(_plain_signs_track(material), "signs"),
        Track(_other_episode_track(material).subtitle, "other_episode"),
        Track(_full_track(material), "full"),
    ]


def _two_that_fit(material: Material) -> list[Track]:
    """The dialogue in two languages, and a track of signs. Either dialogue track serves."""
    language = material.reference_language
    return [
        Track(_full_track(material), "full", language, default=True),
        _own_language_track(material),
        Track(_signs_track(material), "signs", language, "Signs", forced=True),
    ]


def _same_track_twice(material: Material) -> list[Track]:
    """One dialogue track that the file holds twice, under two names, and a track of signs."""
    language = material.reference_language
    full = _full_track(material)
    return [
        Track(_signs_track(material), "signs", language, "Signs & Songs"),
        Track(full, "full", language, "Dialogue"),
        Track(full.copy(), "full", language, "Dialogue (SDH)"),
    ]


def _six_tracks(material: Material) -> list[Track]:
    """As many tracks as a job takes, of every kind, with the dialogue in the middle."""
    language = material.reference_language
    full = _full_track(material)
    return [
        Track(_signs_track(material), "signs", language, "Signs & Songs", True, True),
        _own_language_track(material),
        _other_episode_track(material),
        Track(full, "full", language, "Dialogue"),
        Track(full.copy(), "full", language, "Dialogue (copy)"),
        Track(_plain_signs_track(material), "signs", "und"),
    ]


def _only_signs(material: Material) -> list[Track]:
    """Two tracks, and neither holds a line of dialogue."""
    language = material.reference_language
    return [
        Track(_signs_track(material), "signs", language, "Signs & Songs", True, True),
        Track(_plain_signs_track(material), "signs", language, "Signs"),
    ]


def _wrong_episode_and_signs(material: Material) -> list[Track]:
    """The dialogue of another episode, marked as the default track, and a track of signs."""
    other = _other_episode_track(material)
    other.title, other.default = "Dialogue", True
    signs_only = Track(_signs_track(material), "signs", material.reference_language, "Signs")
    return [other, signs_only]


# Scripts in which the karaoke and the signs are most of the lines


def _karaoke(
    target_heavy: bool,
    reference_heavy: bool,
    offset_ms: int,
    breaks: int = 0,
    heavy: int = 2,
) -> Builder:
    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        cuts = [
            Break(
                at_ms=_before(lines, _pause_near(lines, (number + 1) / (breaks + 1))),
                delta_ms=(25_000, -12_000, 40_000)[number % 3],
            )
            for number in range(breaks)
        ]
        distortion = Distortion(offset_ms=offset_ms, breaks=cuts)
        reference = None
        if not audio:
            reference = loosen(material.reference, material.rng("reference"))
            if reference_heavy:
                reference = distort(
                    reference,
                    Distortion(),
                    fmt="ass",
                    extras=typesetting(
                        lines, material.rng("reference-fx"), material.reference_language, heavy
                    ),
                    styles=SONG_STYLES,
                ).subtitle
        if not target_heavy:
            return _make(material, distortion, audio, reference=reference)
        extras = typesetting(lines, material.rng("target-fx"), material.language, heavy)
        return _make(
            material,
            distortion,
            audio,
            base=_as_format(material.target, "ass"),
            fmt="ass",
            extras=extras,
            reference=reference,
        )

    return build


# Captions as television and streaming services write them


def _decorated(material: Material) -> tuple[Subtitle, list[Extra]]:
    """The target as a broadcast caption file: speaker names, marks, sounds and music."""
    rng = material.rng("captions")
    events = material.target.ssa.events
    texts = []
    for event in events:
        text = event.text
        roll = rng.random()
        if roll < 0.3:
            text = f"（{rng.choice(_SPEAKERS)}）{text}"
        elif roll < 0.4:
            text = f"≪{text}≫"
        elif roll < 0.5:
            text = f"{text}➡"
        texts.append(text)
    lines = material.lines
    extras = []
    gaps = [
        (earlier.end_ms, later.start_ms)
        for earlier, later in zip(lines, lines[1:], strict=False)
        if later.start_ms - earlier.end_ms >= 2_000
    ]
    rng.shuffle(gaps)
    for number, (start, end) in enumerate(gaps[: max(4, len(lines) // 3)]):
        length = min(2_500, end - start - 600)
        at = start + (end - start - length) // 2
        if number % 4 == 3:
            extras.append(Extra(at, at + length, rng.choice(_CAPTION_MUSIC), "karaoke"))
        else:
            extras.append(Extra(at, at + length, rng.choice(_CAPTION_SOUNDS), "sound_description"))
    return _retexted(material.target, texts), extras


def _captions(options: Options) -> Builder:
    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        base, extras = _decorated(material)
        distortion = Distortion(
            offset_ms=1_800,
            breaks=[Break(at_ms=_before(lines, min(5, len(lines) // 5)), delta_ms=10_000)],
        )
        made = distort(
            base, distortion, language=material.language, seed=material.case_seed, extras=extras
        )
        return _built(made, distortion, _reference(material, audio), options=options)

    return build


# Two languages in one file


def _pairs(material: Material) -> list[tuple[Line, Line]]:
    """Every line of the target with the reference line that starts closest to it."""
    others = material.reference.lines
    return [
        (line, min(others, key=lambda other: abs(other.start_ms - line.start_ms)))
        for line in material.target.lines
    ]


def _stacked(material: Material, audio: bool) -> Built:
    """Both languages in every line, one above the other."""
    base = _retexted(
        material.target, [f"{line.text}\\N{other.text}" for line, other in _pairs(material)]
    )
    return _make(material, Distortion(offset_ms=-3_100), audio, base=base)


def _twin_tracks(material: Material, audio: bool) -> Built:
    """Every line twice, once in each language, in styles of their own."""
    ssa = pysubs2.SSAFile()
    ssa.styles["JP"] = pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER)
    ssa.styles["EN"] = pysubs2.SSAStyle()
    for line, other in _pairs(material):
        for text, style in ((line.text, "JP"), (other.text, "EN")):
            ssa.events.append(
                pysubs2.SSAEvent(start=line.start_ms, end=line.end_ms, text=text, style=style)
            )
    lines = material.lines
    breaks = [
        Break(at_ms=_before(lines, _pause_near(lines, 0.35)), delta_ms=30_000),
        Break(at_ms=_before(lines, _pause_near(lines, 0.7)), delta_ms=-9_000),
    ]
    return _make(
        material, Distortion(offset_ms=1_400, breaks=breaks), audio, base=Subtitle(ssa, "ass")
    )


def _furigana(material: Material, audio: bool) -> Built:
    """A script with reading aids: small kana placed above some of the lines."""
    rng = material.rng("furigana")
    lines = material.lines
    extras = [
        Extra(
            line.start_ms,
            line.end_ms,
            f"{{\\an2\\pos({rng.randint(400, 880)},{rng.randint(580, 620)})\\fs18}}"
            f"{rng.choice(_FURIGANA)}",
            "sign",
            "Furigana",
        )
        for line in lines
        if rng.random() < 0.45
    ]
    distortion = Distortion(
        offset_ms=2_200,
        breaks=[Break(at_ms=_before(lines, _pause_near(lines, 0.5)), delta_ms=45_000)],
    )
    return _make(
        material,
        distortion,
        audio,
        base=_as_format(material.target, "ass"),
        fmt="ass",
        extras=extras,
    )


# Times as only players read them


def _odd_time(ms: int, rng: random.Random) -> str:
    """A time written the way a tool that cut a script into parts writes it."""
    hundredths = ms // 10 % 100
    seconds = ms // 1000
    kind = rng.randrange(3)
    if kind == 0:
        # Fewer minutes, and the rest in the seconds.
        minutes = max(0, seconds // 60 - rng.randint(1, 3))
        return f"0:{minutes}:{seconds - minutes * 60}.{hundredths:02d}"
    if kind == 1:
        # A minute or two too many, taken back in the seconds.
        minutes = seconds // 60 + rng.randint(1, 2)
        return f"0:{minutes}:{seconds - minutes * 60}.{hundredths:02d}"
    # Every part as short as it can be written.
    return f"{seconds // 3600}:{seconds // 60 % 60}:{seconds % 60}.{hundredths:02d}"


def _before_zero(ms: int) -> str:
    """A time that far before zero, with a sign on the minutes and on the seconds."""
    seconds = ms // 1000 + 1
    return f"0:-{seconds // 60}:-{seconds % 60}.{ms // 10 % 100:02d}"


_ASS_EVENT = re.compile(
    r"^(Dialogue: [^,]*,)(\d+):(\d+):(\d+)\.(\d+),(\d+):(\d+):(\d+)\.(\d+),(.*)$"
)


def _odd_times(material: Material, audio: bool) -> Built:
    """One part of a script that a tool cut out of a longer one.

    The lines of the part before it are still in the file, with times before
    zero that a player reads as zero. The times of its own lines are right,
    and written as such tools write them.
    """
    rng = material.rng("odd-times")
    distortion = Distortion(offset_ms=1_500)
    made = distort(
        _as_format(material.target, "ass"),
        distortion,
        fmt="ass",
        language=material.language,
        seed=material.case_seed,
    )

    def ms(hours: str, minutes: str, seconds: str, hundredths: str) -> int:
        return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(hundredths) * 10

    rows = []
    first = None
    for row in dumps(made.subtitle).splitlines():
        event = _ASS_EVENT.match(row)
        if event is not None:
            first = len(rows) if first is None else first
            start, end = ms(*event.group(2, 3, 4, 5)), ms(*event.group(6, 7, 8, 9))
            row = f"{event[1]}{_odd_time(start, rng)},{_odd_time(end, rng)},{event[10]}"
        rows.append(row)
    if first is None:
        raise DistortionError("the target has no lines")
    earlier = sorted(material.other_target.lines, key=lambda line: line.start_ms)
    length = max(line.end_ms for line in earlier) + 30_000
    before = [
        f"Dialogue: 0,{_before_zero(length - line.start_ms)},{_before_zero(length - line.end_ms)},"
        f"Default,,0,0,0,,{line.text}"
        for line in earlier
    ]
    truth = [
        TruthLine(id=index, kind="other_content", label="other_content")
        for index in range(len(before))
    ]
    truth += [line.model_copy(update={"id": line.id + len(before)}) for line in made.truth]
    return Built(
        target=made.subtitle,
        truth=truth,
        distortion=distortion,
        reference=_reference(material, audio),
        target_text="\n".join([*rows[:first], *before, *rows[first:]]) + "\n",
    )


# Plain distortions against a reference that was timed by someone else


def _loose(distortion: Distortion | None = None, breaks: int = 0) -> Builder:
    def build(material: Material, audio: bool) -> Built:
        lines = material.lines
        chosen = distortion or Distortion()
        if breaks:
            chosen = chosen.model_copy(
                update={
                    "breaks": [
                        Break(
                            at_ms=_before(lines, _pause_near(lines, (number + 1) / (breaks + 1))),
                            delta_ms=(35_000, -15_000, 70_000)[number % 3],
                        )
                        for number in range(breaks)
                    ]
                }
            )
        built = _make(material, chosen, audio)
        if chosen.is_identity():
            built.expected_outcome = "already_in_sync"
        return built

    return build


_CLEAN_UP = Options(remove_hearing_impaired=True, remove_karaoke=True)


def real_file_recipes() -> list[RealRecipe]:
    """Every case that replicates a problem of the real files, in the order of the README."""
    film = 24000 / 1001
    return [
        RealRecipe("in-sync", "loose_reference", _loose()),
        RealRecipe("early-1.5s", "loose_reference", _loose(Distortion(offset_ms=-1_500))),
        RealRecipe("late-8s", "loose_reference", _loose(Distortion(offset_ms=8_000))),
        RealRecipe("rate-25-to-23.976", "loose_reference", _loose(Distortion(rate=25 / film))),
        RealRecipe("blocks-2", "loose_reference", _loose(breaks=2)),
        RealRecipe("blocks-3", "loose_reference", _loose(Distortion(offset_ms=900), 3)),
        RealRecipe("sponsor-card-10s", "cold_open", _cold_open(4, 10_000, 1_200), audio=True),
        RealRecipe("scene-4s-early", "cold_open", _cold_open(6, 4_000, 6_000), audio=True),
        RealRecipe("two-lines-then-card", "cold_open", _cold_open(2, 10_000, 0), audio=True),
        RealRecipe(
            "scene-4s-early-rough-reference", "cold_open", _cold_open(6, 4_000, 6_000, True)
        ),
        RealRecipe("broadcast-with-breaks", "cold_open", _broadcast, audio=True),
        RealRecipe(
            "one-stretch-0.4s-early", "caption_drift", _drift([(0.35, 0.65, -400)]), audio=True
        ),
        RealRecipe(
            "two-stretches-off",
            "caption_drift",
            _drift([(0.15, 0.4, -350), (0.6, 0.85, -500)], offset_ms=2_500),
            audio=True,
        ),
        RealRecipe(
            "half-the-file-0.5s-early", "caption_drift", _drift([(0.5, 1.0, -500)]), audio=True
        ),
        RealRecipe("second-of-two", "pack", _pack(1.0, 0.0), audio=True),
        RealRecipe("first-of-two", "pack", _pack(0.0, 1.0, offset_ms=1_500), audio=True),
        RealRecipe("middle-of-three", "pack", _pack(0.5, 0.5), audio=True),
        RealRecipe("cut-3s", "mid_scene", _scene_edits([(0.5, -3_000)]), audio=True),
        RealRecipe("insert-5s", "mid_scene", _scene_edits([(0.4, 5_000)]), audio=True),
        RealRecipe(
            "two-edits",
            "mid_scene",
            _scene_edits([(0.3, 2_500), (0.7, -4_000)], offset_ms=-800),
            audio=True,
        ),
        RealRecipe("shared-opening", "next_episode", _next_episode(True), audio=True),
        RealRecipe("shared-phrases", "next_episode", _next_episode(False), audio=True),
        RealRecipe("signs-and-songs-late-8s", "signs_reference", _signs_reference(False, 8_000)),
        RealRecipe("signs-and-songs-in-sync", "signs_reference", _signs_reference(False, 0)),
        RealRecipe("plain-signs-late-8s", "signs_reference", _signs_reference(True, 8_000)),
        RealRecipe(
            "full-and-signs",
            "several_references",
            _several(_release_pair(False, True), Distortion(offset_ms=8_000)),
            both=True,
        ),
        RealRecipe(
            "signs-marked-default",
            "several_references",
            _several(_release_pair(True, False), Distortion(offset_ms=-1_500)),
        ),
        RealRecipe(
            "three-without-labels", "several_references", _several(_unlabelled, breaks=2), both=True
        ),
        RealRecipe(
            "two-languages-fit",
            "several_references",
            _several(_two_that_fit, Distortion(offset_ms=-2_700), 1),
        ),
        RealRecipe(
            "same-track-twice",
            "several_references",
            _several(_same_track_twice, Distortion(rate=25 / film)),
        ),
        RealRecipe(
            "six-tracks", "several_references", _several(_six_tracks, Distortion(offset_ms=900), 3)
        ),
        RealRecipe("in-sync", "several_references", _several(_release_pair(True, True))),
        RealRecipe(
            "only-signs",
            "several_references",
            _several(_only_signs, Distortion(offset_ms=8_000), fits=False),
            both=True,
        ),
        RealRecipe(
            "other-episode-and-signs",
            "several_references",
            _several(_wrong_episode_and_signs, Distortion(offset_ms=8_000), fits=False),
            both=True,
        ),
        RealRecipe("heavy-reference", "karaoke", _karaoke(False, True, -7_300)),
        RealRecipe("heavy-reference-blocks-2", "karaoke", _karaoke(False, True, 1_100, 2)),
        RealRecipe("heavy-target", "karaoke", _karaoke(True, False, 5_400, 1), audio=True),
        RealRecipe("heavy-both-blocks-2", "karaoke", _karaoke(True, True, -2_600, 2)),
        RealRecipe("huge-reference", "karaoke", _karaoke(False, True, 12_000, 1, heavy=10)),
        RealRecipe("kept", "captions", _captions(Options()), audio=True),
        RealRecipe("cleaned", "captions", _captions(_CLEAN_UP), audio=True),
        RealRecipe("stacked", "bilingual", _stacked, audio=True),
        RealRecipe("twin-tracks", "bilingual", _twin_tracks, audio=True),
        RealRecipe("furigana", "bilingual", _furigana, audio=True),
        RealRecipe("script-cut-into-parts", "odd_times", _odd_times, audio=True),
    ]
