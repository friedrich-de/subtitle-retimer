"""A made-up series as seed material: the lines are written by a model, the times by rule.

Real episodes cannot go into the repository, and the two open films have a
few dozen lines each and nothing that follows them. A series written for the
benchmark has what the films lack: episodes of a usual length and density,
a next episode with the same people and the same opening words, and Japanese
and English that cut their sentences differently.

The model writes a scene as a list of beats. A beat is one thing one
character says, as the Japanese captions show it and as an English subtitle
shows it. Either side may show it in one line or in two, so the files do not
pair line for line. The times come from `lay_out`: how long a line takes to
say follows from its sounds, and the pauses, the songs and the breaks are
drawn from a seeded generator. Both languages are timed against the same
speech, the English a little looser, as a second translator would.

There is no sound. A series only gives cases against a reference subtitle.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import pysubs2
from pydantic import BaseModel, Field, ValidationError

from retimer.agent.loop import (
    DEFAULT_MODEL,
    OPENROUTER_MODELS,
    OPENROUTER_UPSTREAM,
    PRICES,
    agent_provider,
    make_client,
)
from retimer.bench.cases import SEEDS_FILE, Seed, SeedSet, load_seeds
from retimer.io import dumps
from retimer.model import Subtitle

SCRIPT_FILE = "series.json"
ATTRIBUTION = (
    "Made up for this benchmark: the lines were written by a language model and timed by rule. "
    "Not taken from any show."
)

Part = Literal["cold_open", "a", "b", "preview"]
# The scenes of an episode, and how many beats each is asked to have.
PARTS: tuple[tuple[Part, int, int], ...] = (
    ("cold_open", 1, 20),
    ("a", 5, 30),
    ("b", 5, 30),
    ("preview", 1, 7),
)

# An episode as it is broadcast: a scene before the opening song, two halves
# with a short card between them, the ending song, and a look at next week.
_LEAD_MS = 3_500
_OPENING_MS = 90_000
_EYECATCH_MS = 10_000
_ENDING_MS = 90_000
_SCENE_GAP_MS = (2_500, 9_000)
_SILENT_STRETCH_MS = (6_000, 24_000)
_SILENT_STRETCHES = (0, 1)
# How fast people talk: milliseconds for one mora, drawn for every beat.
_MORA_MS = (125, 16, 95, 170)
_MIN_SPEECH_MS = 420
_MIN_PIECE_MS = 800
# The pause after a beat: share of the beats, shortest and longest pause.
_PAUSES = ((0.5, 40, 300), (0.33, 300, 1_100), (0.17, 1_100, 4_000))
_LINGER_MS = {"ja": (100, 350), "en": (250, 700)}
_START_SCATTER_MS = {"ja": 0, "en": 80}
_FRAME_MS = 42
_ROW_LIMIT = {"ja": 17, "en": 42}

_SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
_PAUSE_MARKS = {"、": 160, "。": 260, "…": 320, "！": 140, "？": 140, "―": 200, "　": 120}


class Beat(BaseModel):
    """One thing one character says, in both languages. Either side has one line or more."""

    ja: list[str] = Field(min_length=1)
    en: list[str] = Field(min_length=1)


class Scene(BaseModel):
    part: Part
    summary: str
    beats: list[Beat] = Field(default_factory=list)


class Episode(BaseModel):
    number: int
    title_ja: str
    title_en: str
    scenes: list[Scene]


class Character(BaseModel):
    name_ja: str
    name_en: str
    role: str


class Series(BaseModel):
    """The whole script: who is in it, what every episode repeats, and the episodes."""

    slug: str
    title_ja: str
    title_en: str
    premise: str
    characters: list[Character]
    narration: list[Beat]
    sign_off: Beat
    episodes: list[Episode]


class SeriesError(RuntimeError):
    pass


# The times


def morae(text: str) -> int:
    """Roughly how many sounds a Japanese line has. A kanji counts as two."""
    count = 0
    for letter in text:
        if "\u4e00" <= letter <= "\u9fff" or letter == "\u3005":
            count += 2
        elif "\u3041" <= letter <= "\u30fa" or letter == "\u30fc":
            count += letter not in _SMALL_KANA
        elif letter.isalnum():
            count += 1
    return count


def _speech_ms(beat: Beat, rng: random.Random) -> int:
    mean, spread, least, most = _MORA_MS
    pace = min(most, max(least, rng.gauss(mean, spread)))
    said = "".join(beat.ja)
    pauses = sum(_PAUSE_MARKS.get(letter, 0) for letter in said.rstrip("。！？…"))
    pieces = max(len(beat.ja), len(beat.en))
    return max(_MIN_SPEECH_MS, _MIN_PIECE_MS * pieces, round(morae(said) * pace) + min(pauses, 900))


def _pause_ms(rng: random.Random) -> int:
    draw = rng.random()
    for share, least, most in _PAUSES:
        if draw < share:
            return rng.randint(least, most)
        draw -= share
    return rng.randint(*_PAUSES[-1][1:])


class Spoken(BaseModel):
    """A beat on the clock: when its speech starts and ends."""

    start_ms: int
    end_ms: int
    beat: Beat


def lay_out(series: Series, episode: Episode, seed: int = 0) -> list[Spoken]:
    """Put every beat of an episode on the clock.

    The narration that opens every episode gets the same times in all of
    them, as the same footage would.
    """
    spoken: list[Spoken] = []
    clock = _LEAD_MS

    def say(beats: list[Beat], rng: random.Random, stretches: int = 0) -> None:
        nonlocal clock
        quiet = set(rng.sample(range(1, len(beats)), min(stretches, max(0, len(beats) - 1))))
        for index, beat in enumerate(beats):
            if index in quiet:
                clock += rng.randint(*_SILENT_STRETCH_MS)
            length = _speech_ms(beat, rng)
            spoken.append(Spoken(start_ms=clock, end_ms=clock + length, beat=beat))
            clock += length + _pause_ms(rng)

    say(series.narration, random.Random(f"{series.slug}:narration"))
    rng = random.Random(f"{series.slug}:{episode.number}:{seed}")
    part: Part | None = None
    for scene in episode.scenes:
        if not scene.beats:
            continue
        if part is None or scene.part == part:
            clock += rng.randint(*_SCENE_GAP_MS)
        elif scene.part == "a":
            clock += _OPENING_MS + rng.randint(*_SCENE_GAP_MS)
        elif scene.part == "b":
            clock += _EYECATCH_MS + rng.randint(*_SCENE_GAP_MS)
        else:
            clock += _ENDING_MS + rng.randint(*_SCENE_GAP_MS)
        part = scene.part
        stretches = 0 if scene.part == "preview" else rng.randint(*_SILENT_STRETCHES)
        say(scene.beats, rng, stretches)
    say([series.sign_off], rng)
    return spoken


def _wrap(text: str, language: str) -> str:
    """Break a long line into two rows, where a reader would."""
    text = " ".join(text.split()) if language == "en" else text.strip()
    if len(text) <= _ROW_LIMIT[language]:
        return text
    middle = len(text) / 2
    if language == "en":
        places = [index for index, letter in enumerate(text) if letter == " "]
    else:
        places = [index + 1 for index, letter in enumerate(text[:-1]) if letter in "、。！？…　 "]
    if not places:
        if language == "en":
            return text
        places = [round(middle)]
    place = min(places, key=lambda index: abs(index - middle))
    return f"{text[:place].rstrip()}\\N{text[place:].lstrip()}"


def captions(spoken: list[Spoken], language: Literal["ja", "en"], seed: int = 0) -> Subtitle:
    """The subtitle file of one language for beats that are on the clock.

    A beat that a language shows in two lines shares its time between them
    by their length. The English starts are scattered a little and its lines
    stay up longer, as subtitles do next to captions.
    """
    rng = random.Random(f"captions:{language}:{seed}")
    scatter = _START_SCATTER_MS[language]
    ssa = pysubs2.SSAFile()
    floor = 0
    for index, item in enumerate(spoken):
        texts = [text for text in getattr(item.beat, language) if text.strip()]
        if not texts:
            continue
        limit = spoken[index + 1].start_ms - _FRAME_MS if index + 1 < len(spoken) else None
        weights = [max(1, morae(text) if language == "ja" else len(text)) for text in texts]
        span = item.end_ms - item.start_ms
        start = max(floor, item.start_ms + (rng.randint(-scatter, scatter) if scatter else 0))
        passed = 0
        for position, (text, weight) in enumerate(zip(texts, weights, strict=True)):
            passed += weight
            last = position == len(texts) - 1
            end = item.start_ms + round(span * passed / sum(weights))
            if last:
                end += rng.randint(*_LINGER_MS[language])
                if limit is not None:
                    end = max(item.end_ms, min(end, limit))
            end = max(end, start + 300)
            ssa.events.append(pysubs2.SSAEvent(start=start, end=end, text=_wrap(text, language)))
            start = end
        floor = start
    return Subtitle(ssa, "srt")


# The writing

_SYSTEM = (
    "You write scripts for a made-up TV anime that exists only as test material for subtitle "
    "tools. The scripts are published, so everything in them is your own invention: no existing "
    "show, character, person, brand or song lyric, and nothing that reads like a retelling of an "
    "existing story. Suitable for all ages."
)

_OUTLINE = """Invent the series and outline {episodes} consecutive episodes of it.

Return one JSON object and nothing else, in this shape:

{{
  "slug": "the title in lowercase Latin letters with hyphens, three words at most",
  "title_ja": "...",
  "title_en": "...",
  "premise": "three or four sentences in English",
  "characters": [
    {{"name_ja": "...", "name_en": "...",
      "role": "who they are and how they talk, one sentence in English"}}
  ],
  "narration": [
    {{"ja": ["..."], "en": ["..."]}}, {{"ja": ["..."], "en": ["..."]}},
    {{"ja": ["..."], "en": ["..."]}}
  ],
  "sign_off": {{"ja": ["..."], "en": ["..."]}},
  "episodes": [
    {{"number": 1, "title_ja": "...", "title_en": "...",
      "scenes": [{{"part": "cold_open", "summary": "..."}}]}}
  ]
}}

- Five to seven characters.
- "narration" is exactly three lines that a narrator speaks at the start of every episode, word
  for word the same each time. "sign_off" is the one line that closes every preview of the next
  episode. Each is given in Japanese and in English.
- Every episode has these scenes in this order: {layout}. A "summary" says in two English
  sentences what happens and who talks. In the "preview" scene one character teases the episode
  that follows.
- The episodes tell one continuous story. Give the people reasons to talk: plans, arguments,
  explanations, jokes."""

_SCENE = """This is the series.

<series>
{series}
</series>

This is episode {number}, "{title}", scene by scene.

<episode>
{outline}
</episode>
{previous}
Write scene {index}: {summary}

Write it as {count} beats or a few more. A beat is one thing one character says. Give every beat \
as the Japanese caption file shows it and as the subtitle of an English translation shows it:

JA: <the Japanese>
EN: <the English>

- Japanese: natural spoken lines, each character in their own voice. No speaker names, no stage \
directions, no sound descriptions, no brackets around a line. A caption has 30 characters at most.
- English: a natural subtitle, not word for word. A subtitle has 80 characters at most.
- Vary the length as real dialogue does. At least a quarter of the beats are short reactions of \
one to six characters, such as えっ？, うん, はい, 待って！ or なるほど, and some are long \
explanations.
- In two beats of every ten the two languages are cut differently. Write " || " where one \
caption or subtitle ends and the next begins. Use both ways about equally: one Japanese caption \
against two English subtitles, and two Japanese captions against one English subtitle.

JA: まだ決めてない。 || でも、必ず残す。
EN: I haven't decided yet, but we're keeping it no matter what.

JA: 東の空、尾根から二十度ほど上。
EN: East, about twenty degrees above the ridge. || It seems to be getting brighter.
- Dialogue only: no songs, no text on screen.{extra}
- Answer with the beats and nothing else."""

_BEAT = re.compile(r"^\s*(JA|EN)\s*[:：]\s*(.+?)\s*$")


def parse_beats(text: str) -> list[Beat]:
    """Read the beats of a scene as the model writes them. A line without its pair is dropped."""
    beats: list[Beat] = []
    waiting: list[str] | None = None
    for row in text.splitlines():
        found = _BEAT.match(row)
        if found is None:
            continue
        pieces = [piece.strip() for piece in found.group(2).split("||") if piece.strip()]
        if found.group(1) == "JA":
            waiting = pieces or None
        elif waiting and pieces:
            beats.append(Beat(ja=waiting, en=pieces))
            waiting = None
    return beats


def _json_object(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SeriesError("the model did not answer with a JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise SeriesError(f"the model's JSON does not parse: {error}") from error


class Writer:
    """Asks the model, and keeps count of what it cost."""

    def __init__(self, client: Any | None = None, model: str = DEFAULT_MODEL) -> None:
        self.client = client or make_client()
        self.model = model
        self.provider = agent_provider()
        self.tokens = {"input": 0, "output": 0}

    @property
    def cost_usd(self) -> float:
        prices = PRICES.get(self.model, (0.0, 0.0, 0.0, 0.0))
        return (self.tokens["input"] * prices[0] + self.tokens["output"] * prices[1]) / 1_000_000

    def ask(self, prompt: str, max_tokens: int = 16_000) -> str:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.provider == "openrouter":
            params["model"] = OPENROUTER_MODELS.get(self.model, self.model)
            params["extra_body"] = {"provider": OPENROUTER_UPSTREAM}
        with self.client.messages.stream(**params) as stream:
            message = stream.get_final_message()
        self.tokens["input"] += message.usage.input_tokens
        self.tokens["output"] += message.usage.output_tokens
        if message.stop_reason not in ("end_turn", "stop_sequence"):
            raise SeriesError(f"the model stopped early: {message.stop_reason}")
        return "".join(block.text for block in message.content if block.type == "text")


def _layout_text() -> str:
    names = {"cold_open": "cold_open", "a": "a", "b": "b", "preview": "preview"}
    return ", ".join(
        f'{count} "{names[part]}" scene{"s" if count > 1 else ""}' for part, count, _ in PARTS
    )


def _apart(beats: list[Beat]) -> list[Beat]:
    """Narration given as one beat of several lines, as one beat a line."""
    if len(beats) == 1 and len(beats[0].ja) == len(beats[0].en) > 1:
        return [Beat(ja=[ja], en=[en]) for ja, en in zip(beats[0].ja, beats[0].en, strict=True)]
    return beats


def _check_outline(series: Series, episodes: int) -> None:
    wanted = [part for part, count, _ in PARTS for _ in range(count)]
    if len(series.episodes) != episodes:
        raise SeriesError(f"{len(series.episodes)} episodes instead of {episodes}")
    for episode in series.episodes:
        if [scene.part for scene in episode.scenes] != wanted:
            raise SeriesError(f"episode {episode.number} does not have the scenes asked for")
    if len(series.narration) != 3:
        raise SeriesError("the narration does not have three lines")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", series.slug):
        raise SeriesError(f"the slug is not a plain name: {series.slug!r}")


def write_outline(writer: Writer, episodes: int = 3, attempts: int = 3) -> Series:
    """Have the model invent the series and outline its episodes."""
    prompt = _OUTLINE.format(episodes=episodes, layout=_layout_text())
    problem: Exception | None = None
    for _ in range(attempts):
        try:
            series = Series.model_validate(_json_object(writer.ask(prompt)))
            series.narration = _apart(series.narration)
            _check_outline(series, episodes)
            return series
        except (SeriesError, ValidationError) as error:
            problem = error
    raise SeriesError(f"no usable outline after {attempts} tries: {problem}")


def _beat_text(beat: Beat) -> str:
    return f"JA: {' || '.join(beat.ja)}\nEN: {' || '.join(beat.en)}"


def _series_text(series: Series) -> str:
    people = "\n".join(
        f"- {person.name_ja} ({person.name_en}): {person.role}" for person in series.characters
    )
    return f"{series.title_ja} ({series.title_en})\n{series.premise}\n\nThe characters:\n{people}"


def write_scene(writer: Writer, series: Series, episode: Episode, index: int) -> list[Beat]:
    """Have the model write one scene. It sees the outline and how the scene before it ended."""
    scene = episode.scenes[index]
    count = next(beats for part, _, beats in PARTS if part == scene.part)
    earlier = [beat for before in episode.scenes[:index] for beat in before.beats][-8:]
    previous = ""
    if earlier:
        told = "\n\n".join(_beat_text(beat) for beat in earlier)
        previous = (
            f"\nThe scene before this one ended like this.\n\n<previous>\n{told}\n</previous>\n"
        )
    extra = ""
    if scene.part == "preview":
        extra = (
            "\n- This is the preview of the next episode: one or two voices over pictures of it. "
            f"Do not write the closing line, which is always: {series.sign_off.ja[0]}"
        )
    prompt = _SCENE.format(
        series=_series_text(series),
        number=episode.number,
        title=episode.title_en,
        outline="\n".join(
            f"{position + 1}. {item.summary}" for position, item in enumerate(episode.scenes)
        ),
        previous=previous,
        index=index + 1,
        summary=scene.summary,
        count=count,
        extra=extra,
    )
    best: list[Beat] = []
    for _ in range(3):
        beats = parse_beats(writer.ask(prompt))
        if len(beats) > len(best):
            best = beats
        if len(best) >= 0.8 * count:
            break
    if len(best) < 0.5 * count:
        raise SeriesError(
            f"episode {episode.number} scene {index + 1}: {len(best)} beats instead of {count}"
        )
    return best


def write_series(
    writer: Writer, episodes: int = 3, progress: Callable[[str], None] | None = None
) -> Series:
    """Have the model write the whole script. Episodes are written side by side."""
    say = progress or (lambda message: None)
    series = write_outline(writer, episodes)
    say(f"{series.title_en} ({series.slug}): {episodes} episodes outlined")

    def write(episode: Episode) -> None:
        for index, scene in enumerate(episode.scenes):
            scene.beats = write_scene(writer, series, episode, index)
            say(f"episode {episode.number} scene {index + 1}: {len(scene.beats)} beats")

    with ThreadPoolExecutor(max_workers=episodes) as pool:
        list(pool.map(write, series.episodes))
    return series


# The files


def seed_id(series: Series, episode: Episode) -> str:
    return f"{series.slug}-{episode.number:02d}"


def save_series(series: Series, seeds_dir: Path, seed: int = 0) -> list[Seed]:
    """Write the script and the subtitles of every episode, and list them as seeds."""
    directory = seeds_dir / series.slug
    directory.mkdir(parents=True, exist_ok=True)
    script = json.dumps(series.model_dump(mode="json"), ensure_ascii=False, indent=1)
    (directory / SCRIPT_FILE).write_text(script + "\n", encoding="utf-8")
    seeds = []
    for episode in series.episodes:
        spoken = lay_out(series, episode, seed)
        files = {}
        # English first: it is the reference the Japanese captions are synced to.
        for language in ("en", "ja"):
            name = f"{series.slug}/{episode.number:02d}/{language}.srt"
            path = seeds_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dumps(captions(spoken, language, seed)), encoding="utf-8")
            files[language] = name
        seeds.append(
            Seed(
                id=seed_id(series, episode),
                title=f"{series.title_en}, episode {episode.number}: {episode.title_en}",
                subtitles=files,
                series=series.slug,
                attribution=ATTRIBUTION,
            )
        )
    known = load_seeds(seeds_dir)
    ours = {item.id for item in seeds}
    kept = [item for item in known.seeds if item.id not in ours and item.series != series.slug]
    listed = SeedSet(seeds=kept + seeds).model_dump(mode="json", exclude_none=True)
    text = json.dumps(listed, ensure_ascii=False, indent=2)
    (seeds_dir / SEEDS_FILE).write_text(text + "\n", encoding="utf-8")
    return seeds


def load_series(path: Path) -> Series:
    return Series.model_validate_json(path.read_text(encoding="utf-8"))
