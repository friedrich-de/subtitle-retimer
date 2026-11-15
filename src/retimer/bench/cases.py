"""Benchmark cases: what a case is, how seed material becomes cases, and where they live.

A case is a directory with a `case.json` and a target subtitle. The same layout
serves generated cases and hand-checked ones, so a naturally mistimed file can
be added by writing its directory by hand.
"""

from __future__ import annotations

import json
import os
import random
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from retimer.bench.distort import Break, Distortion, DistortionError, Noise, TruthLine, distort
from retimer.io import dumps, load
from retimer.model import Subtitle
from retimer.plan import Plan
from retimer.views import Options

Family = Literal["constant", "rate", "blocks", "mixed", "noise", "none", "wrong_content"]
ReferenceKind = Literal["audio", "subtitle", "both"]

CASE_FILE = "case.json"
SEEDS_FILE = "seeds.json"

FPS_FILM = 24000 / 1001
# How close to zero an early shift may push the first line.
_MIN_FIRST_START_MS = 500
_MIN_BLOCK_LINES = 3
_MIN_BREAK_GAP_MS = 400
_SEGMENT_MS = (10_000, 180_000)
_NOISE_KINDS_BY_OPTION = {
    "remove_hearing_impaired": "sound_description",
    "remove_karaoke": "karaoke",
}


class SeedAudio(BaseModel):
    file: str
    language: str
    source_url: str | None = None


class Seed(BaseModel):
    """In-sync material for one film or episode: subtitles by language, and maybe audio."""

    id: str
    title: str
    subtitles: dict[str, str]
    audio: SeedAudio | None = None
    license: str | None = None
    attribution: str | None = None


class SeedSet(BaseModel):
    seeds: list[Seed]


class Reference(BaseModel):
    kind: ReferenceKind
    audio: str | None = None
    audio_language: str | None = None
    subtitle: str | None = None
    subtitle_language: str | None = None


class Case(BaseModel):
    id: str
    seed: str
    family: Family
    recipe: str
    target: str
    target_language: str
    reference: Reference
    options: Options = Field(default_factory=Options)
    expected_outcome: str
    distortion: Distortion | None = None
    truth: list[TruthLine] = Field(default_factory=list)
    ideal_plan: Plan | None = None
    notes: str | None = None

    def remove_kinds(self) -> set[str]:
        """Noise kinds that the case's options ask to remove from the output."""
        return {
            kind for option, kind in _NOISE_KINDS_BY_OPTION.items() if getattr(self.options, option)
        }


class LoadedCase(BaseModel):
    """A case with its directory, so that its files can be found."""

    case: Case
    directory: Path

    @property
    def target_path(self) -> Path:
        return self.directory / self.case.target

    def resolve(self, relative: str | None) -> Path | None:
        return None if relative is None else (self.directory / relative).resolve()

    @property
    def audio_path(self) -> Path | None:
        return self.resolve(self.case.reference.audio)

    @property
    def reference_subtitle_path(self) -> Path | None:
        return self.resolve(self.case.reference.subtitle)


class Recipe(BaseModel):
    """A named distortion. Break positions are drawn per case from a seeded generator."""

    name: str
    family: Family
    offset_ms: int = 0
    rate: float = 1.0
    breaks: int = 0
    break_kinds: tuple[Literal["insert", "cut"], ...] = ("insert", "cut")
    noise: Noise = Field(default_factory=Noise)
    options: Options = Field(default_factory=Options)
    target_format: str | None = None


def seed_recipes() -> list[Recipe]:
    """The distortions of the design document, in a size that suits a few seed files."""
    all_noise = Noise(sound_descriptions=6, credits=2, karaoke_lines=6, trailing_lines=3)
    clean_up = Options(remove_hearing_impaired=True, remove_karaoke=True)
    return [
        Recipe(name="in-sync", family="none"),
        Recipe(name="late-0.2s", family="constant", offset_ms=200),
        Recipe(name="early-1.5s", family="constant", offset_ms=-1_500),
        Recipe(name="late-8s", family="constant", offset_ms=8_000),
        Recipe(name="early-20s", family="constant", offset_ms=-20_000),
        Recipe(name="late-60s", family="constant", offset_ms=60_000),
        Recipe(name="rate-23.976-to-25", family="rate", rate=FPS_FILM / 25),
        Recipe(name="rate-25-to-23.976", family="rate", rate=25 / FPS_FILM),
        Recipe(name="rate-24-to-25", family="rate", rate=24 / 25),
        Recipe(name="blocks-1-insert", family="blocks", breaks=1, break_kinds=("insert",)),
        Recipe(name="blocks-1-cut", family="blocks", breaks=1, break_kinds=("cut",)),
        Recipe(name="blocks-2", family="blocks", breaks=2),
        Recipe(name="blocks-3", family="blocks", breaks=3),
        Recipe(name="blocks-5", family="blocks", breaks=5),
        Recipe(
            name="mixed-rate-offset-2-blocks",
            family="mixed",
            offset_ms=3_200,
            rate=25 / FPS_FILM,
            breaks=2,
        ),
        Recipe(name="mixed-offset-3-blocks", family="mixed", offset_ms=-1_800, breaks=3),
        Recipe(name="noise-kept", family="noise", offset_ms=4_300, noise=all_noise),
        Recipe(
            name="noise-removed",
            family="noise",
            offset_ms=-2_700,
            noise=all_noise,
            options=clean_up,
            target_format="ass",
        ),
        Recipe(
            name="noise-trailing-only",
            family="noise",
            offset_ms=1_100,
            noise=Noise(trailing_lines=4),
        ),
    ]


def _stable_seed(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def choose_breaks(
    base: Subtitle, count: int, kinds: tuple[str, ...], rng: random.Random
) -> list[Break]:
    """Draw break points in gaps between lines, leaving every block a few lines.

    A cut never removes more lines than it leaves in the block before the next
    break, so short files get shorter cuts than the nominal range.
    """
    lines = sorted(base.lines, key=lambda line: line.start_ms)
    count = min(count, len(lines) // (2 * _MIN_BLOCK_LINES))
    if count <= 0:
        return []
    # Candidate positions: after line i, when the next line leaves a gap.
    candidates = [
        index
        for index in range(_MIN_BLOCK_LINES, len(lines) - _MIN_BLOCK_LINES + 1)
        if lines[index].start_ms - max(line.end_ms for line in lines[:index]) >= _MIN_BREAK_GAP_MS
    ]
    chosen: list[int] = []
    for index in rng.sample(candidates, len(candidates)):
        if all(abs(index - other) >= 2 * _MIN_BLOCK_LINES for other in chosen):
            chosen.append(index)
        if len(chosen) == count:
            break
    chosen.sort()

    breaks = []
    for position, index in enumerate(chosen):
        previous_end = max(line.end_ms for line in lines[:index])
        at_ms = (previous_end + lines[index].start_ms) // 2
        kind = rng.choice(kinds)
        size = rng.randint(*_SEGMENT_MS)
        if kind == "cut":
            # Keep at least a few lines between the end of the cut and the next break.
            stop = chosen[position + 1] if position + 1 < len(chosen) else len(lines)
            last_removable = stop - _MIN_BLOCK_LINES - 1
            if last_removable < index:
                kind = "insert"
            else:
                limit = lines[last_removable + 1].start_ms - at_ms - 1
                size = min(size, limit)
                if size < _SEGMENT_MS[0] // 2:
                    kind = "insert"
                    size = rng.randint(*_SEGMENT_MS)
        breaks.append(Break(at_ms=at_ms, delta_ms=size if kind == "insert" else -size))
    return breaks


def build_distortion(base: Subtitle, recipe: Recipe, rng: random.Random) -> Distortion:
    breaks = choose_breaks(base, recipe.breaks, recipe.break_kinds, rng) if recipe.breaks else []
    offset = recipe.offset_ms
    if offset < 0:
        # An early shift may not push the first line before zero.
        first = min(line.start_ms for line in base.lines)
        offset = max(offset, -(first - _MIN_FIRST_START_MS))
    return Distortion(offset_ms=offset, rate=recipe.rate, breaks=breaks, noise=recipe.noise)


def load_seeds(directory: Path) -> SeedSet:
    return SeedSet.model_validate_json((directory / SEEDS_FILE).read_text(encoding="utf-8"))


def _relative(path: Path, start: Path) -> str:
    return os.path.relpath(path.resolve(), start.resolve())


def _write_case(directory: Path, case: Case, target: Subtitle) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / case.target).write_text(dumps(target), encoding="utf-8")
    (directory / CASE_FILE).write_text(
        json.dumps(case.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _pairings(seed: Seed, has_audio: bool) -> list[tuple[str, ReferenceKind, str | None]]:
    """Target language, reference kind and reference subtitle language for one seed.

    Every language is synced against the audio, and against a subtitle in
    another language. The first language also gets the combined reference.
    """
    languages = list(seed.subtitles)
    pairings: list[tuple[str, ReferenceKind, str | None]] = []
    for position, language in enumerate(languages):
        other = next((item for item in languages if item != language), None)
        if has_audio:
            pairings.append((language, "audio", None))
        if other is not None:
            pairings.append((language, "subtitle", other))
            if has_audio and position == 0:
                pairings.append((language, "both", other))
    return pairings


def generate_cases(
    seeds_dir: Path,
    media_dir: Path,
    out_dir: Path,
    recipes: list[Recipe] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Write the case set for every seed and return the case ids.

    Cases that need audio are skipped for seeds whose audio has not been fetched.
    """
    say = progress or (lambda message: None)
    recipes = recipes if recipes is not None else seed_recipes()
    seeds = load_seeds(seeds_dir).seeds
    written: list[str] = []

    def audio_of(seed: Seed) -> Path | None:
        if seed.audio is None:
            return None
        path = media_dir / seed.audio.file
        return path if path.exists() else None

    for position, seed in enumerate(seeds):
        audio = audio_of(seed)
        if seed.audio is not None and audio is None:
            say(f"{seed.id}: no audio at {media_dir / seed.audio.file}, audio cases are left out")
        for language, kind, other in _pairings(seed, audio is not None):
            base = load(seeds_dir / seed.subtitles[language])
            for recipe in recipes:
                case_id = f"{seed.id}.{language}.{kind}{'-' + other if other else ''}.{recipe.name}"
                directory = out_dir / case_id
                fmt = recipe.target_format or base.format
                try:
                    distortion = build_distortion(
                        base, recipe, random.Random(_stable_seed(case_id))
                    )
                    result = distort(
                        base, distortion, fmt=fmt, language=language, seed=_stable_seed(case_id)
                    )
                except DistortionError as error:
                    say(f"skipped {case_id}: {error}")
                    continue
                reference = Reference(
                    kind=kind,
                    audio=_relative(audio, directory) if kind != "subtitle" and audio else None,
                    audio_language=seed.audio.language
                    if kind != "subtitle" and seed.audio
                    else None,
                    subtitle=(
                        _relative(seeds_dir / seed.subtitles[other], directory) if other else None
                    ),
                    subtitle_language=other,
                )
                case = Case(
                    id=case_id,
                    seed=seed.id,
                    family=recipe.family,
                    recipe=recipe.name,
                    target=f"target.{fmt}",
                    target_language=language,
                    reference=reference,
                    options=recipe.options,
                    expected_outcome="already_in_sync" if recipe.family == "none" else "synced",
                    distortion=distortion,
                    truth=result.truth,
                    ideal_plan=result.ideal_plan,
                )
                _write_case(directory, case, result.subtitle)
                written.append(case_id)

        # Wrong content: this seed's subtitle against the next seed's reference.
        if len(seeds) < 2:
            continue
        other_seed = seeds[(position + 1) % len(seeds)]
        other_audio = audio_of(other_seed)
        language = next(iter(seed.subtitles))
        base = load(seeds_dir / seed.subtitles[language])
        for kind in ("audio", "subtitle"):
            other_language = next(iter(other_seed.subtitles))
            if kind == "audio" and other_audio is None:
                continue
            case_id = f"{seed.id}.{language}.{kind}.wrong-content-{other_seed.id}"
            directory = out_dir / case_id
            reference = Reference(
                kind=kind,
                audio=_relative(other_audio, directory) if kind == "audio" else None,
                audio_language=other_seed.audio.language if kind == "audio" else None,
                subtitle=(
                    _relative(seeds_dir / other_seed.subtitles[other_language], directory)
                    if kind == "subtitle"
                    else None
                ),
                subtitle_language=other_language if kind == "subtitle" else None,
            )
            case = Case(
                id=case_id,
                seed=seed.id,
                family="wrong_content",
                recipe=f"wrong-content-{other_seed.id}",
                target=f"target.{base.format}",
                target_language=language,
                reference=reference,
                expected_outcome="rejected_no_match",
            )
            _write_case(directory, case, base)
            written.append(case_id)
    return written


def load_case(directory: Path) -> LoadedCase:
    case = Case.model_validate_json((directory / CASE_FILE).read_text(encoding="utf-8"))
    return LoadedCase(case=case, directory=directory)


def load_cases(directory: Path, pattern: str | None = None) -> list[LoadedCase]:
    """Every case under `directory`, in name order. `pattern` keeps ids that contain it."""
    cases = [
        load_case(path.parent)
        for path in sorted(directory.glob(f"*/{CASE_FILE}"))
        if pattern is None or pattern in path.parent.name
    ]
    return cases
