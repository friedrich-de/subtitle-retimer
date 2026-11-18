"""The systems that are run over a real-file set.

They are the benchmark's runners, given real pairs. Each tool is run with its
defaults, and once more with settings chosen on a fixed tenth of the main
pairs, so that a tool is not judged by defaults that happen not to suit this
kind of file. Against a subtitle track every system gets the same file.

Against the audio the tools read the sound itself. The retimer reads a
transcript of it, which a speech model at a provider writes. That is billed,
so it is done beforehand for a sample of the videos (`retimer.realset.speech`)
and the retimer runs against the audio of those videos only.

The agent is the one system that is billed while it runs: a model is asked
about what the fixed pipeline leaves open. It is run when it is named. So is
`retimer-questions`, which writes down what that model would be asked and asks
nobody: it shows for nothing on which files a change to the questions takes
effect.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from retimer.bench.cases import Case, LoadedCase, Reference
from retimer.bench.runners import (
    AgentRunner,
    AlassRunner,
    FfsubsyncRunner,
    PipelineRunner,
    QuestionsRunner,
    Runner,
    RunOutput,
)
from retimer.io import SubtitleError, load, save
from retimer.realset.embedding import CachedOnly, CompactCache
from retimer.realset.manifest import (
    CACHE_DIR,
    TARGETS_DIR,
    TUNING_FILE,
    Manifest,
    Pair,
    plain_path,
)
from retimer.text import LINE_BREAK

Tool = Literal["retimer", "alass", "ffsubsync"]
ReferenceKind = Literal["subtitle", "audio"]

# The longest a tool may take on one pair.
TIMEOUT_S = 600
# The language the speech model is told. The set takes the Japanese audio
# track of every video, and a client that reads the track's tag names it.
AUDIO_LANGUAGE = "ja"


@dataclass(frozen=True)
class System:
    name: str
    tool: Tool
    reference: ReferenceKind
    args: tuple[str, ...] = ()
    # The settings come from the set's tuning file.
    tuned: bool = False
    # A model at a provider decides what the fixed pipeline leaves open.
    agent: bool = False
    # What that model would be asked is written down, and nobody is asked.
    dry: bool = False

    @property
    def sampled(self) -> bool:
        """Whether the system only runs on the videos whose audio was transcribed."""
        return self.tool == "retimer" and self.reference == "audio"


SYSTEMS = (
    System("retimer", "retimer", "subtitle"),
    System("retimer-agent", "retimer", "subtitle", agent=True),
    System("retimer-questions", "retimer", "subtitle", dry=True),
    System("alass", "alass", "subtitle"),
    System("alass-tuned", "alass", "subtitle", tuned=True),
    System("ffsubsync", "ffsubsync", "subtitle"),
    System("ffsubsync-tuned", "ffsubsync", "subtitle", tuned=True),
    System("retimer-audio", "retimer", "audio"),
    System("retimer-agent-audio", "retimer", "audio", agent=True),
    System("alass-audio", "alass", "audio"),
    System("alass-audio-tuned", "alass", "audio", tuned=True),
    System("ffsubsync-audio", "ffsubsync", "audio"),
    System("ffsubsync-audio-tuned", "ffsubsync", "audio", tuned=True),
)
# What a run takes when no system is named: everything that is not billed while it runs.
DEFAULT_SYSTEMS = tuple(system.name for system in SYSTEMS if not system.agent and not system.dry)

# The settings that are tried for each tool. The first of each is the default.
_ALASS = {
    "default": (),
    "split-penalty-3": ("--split-penalty", "3"),
    "split-penalty-5": ("--split-penalty", "5"),
    "split-penalty-10": ("--split-penalty", "10"),
    "split-penalty-20": ("--split-penalty", "20"),
    "no-split": ("--no-split",),
    "no-rate-guess": ("--disable-fps-guessing",),
    "no-rate-guess-split-penalty-10": ("--disable-fps-guessing", "--split-penalty", "10"),
    "no-rate-guess-split-penalty-20": ("--disable-fps-guessing", "--split-penalty", "20"),
    "no-rate-guess-no-split": ("--disable-fps-guessing", "--no-split"),
    "no-speed-up": ("--speed-optimization", "0"),
}
_FFSUBSYNC = {
    "default": (),
    "split": ("--split-penalty",),
    "split-10": ("--split-penalty", "10"),
    "split-20": ("--split-penalty", "20"),
    "no-rate-fix": ("--no-fix-framerate",),
    "no-rate-fix-split": ("--no-fix-framerate", "--split-penalty"),
    "no-rate-fix-split-10": ("--no-fix-framerate", "--split-penalty", "10"),
    "no-rate-fix-split-20": ("--no-fix-framerate", "--split-penalty", "20"),
    "rate-search": ("--gss",),
}
_FFSUBSYNC_AUDIO = {
    **_FFSUBSYNC,
    "auditok": ("--vad", "auditok"),
    "auditok-no-rate-fix": ("--vad", "auditok", "--no-fix-framerate"),
    "auditok-no-rate-fix-split-10": (
        "--vad",
        "auditok",
        "--no-fix-framerate",
        "--split-penalty",
        "10",
    ),
}
CANDIDATES: dict[tuple[Tool, ReferenceKind], dict[str, tuple[str, ...]]] = {
    ("alass", "subtitle"): _ALASS,
    ("alass", "audio"): _ALASS,
    ("ffsubsync", "subtitle"): _FFSUBSYNC,
    ("ffsubsync", "audio"): _FFSUBSYNC_AUDIO,
}


def candidate_systems() -> list[System]:
    """One system per setting that is tried, named `tool~setting` or `tool-audio~setting`."""
    systems = []
    for (tool, reference), settings in CANDIDATES.items():
        base = tool if reference == "subtitle" else f"{tool}-audio"
        systems += [
            System(f"{base}~{name}", tool, reference, args) for name, args in settings.items()
        ]
    return systems


def load_tuning(root: Path) -> dict[str, dict]:
    """The chosen settings by system name, as `realset tune` wrote them."""
    path = root / TUNING_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def resolve(names: list[str], root: Path) -> list[System]:
    """The systems of these names, with the settings of the tuned ones filled in."""
    known = {system.name: system for system in (*SYSTEMS, *candidate_systems())}
    tuning = load_tuning(root)
    resolved = []
    for name in names:
        if name not in known:
            raise ValueError(f"unknown system: {name}")
        system = known[name]
        if system.tuned:
            if name not in tuning:
                raise ValueError(f"{name} has no settings yet: run `retimer realset tune` first")
            system = System(name, system.tool, system.reference, tuple(tuning[name]["args"]), True)
        resolved.append(system)
    return resolved


# The fields of an ASS header that say how a script is to be shown.
_HEADER_FIELDS = {
    "title",
    "scripttype",
    "wrapstyle",
    "playresx",
    "playresy",
    "layoutresx",
    "layoutresy",
    "scaledborderandshadow",
    "ycbcr matrix",
    "collisions",
    "playdepth",
    "timer",
    "kerning",
    "language",
}


def _resave(source: Path, destination: Path) -> bool:
    """Write a subtitle again the way an editor would. False when it cannot be read at all."""
    try:
        subtitle = load(source)
    except (SubtitleError, OSError):
        return False
    # An empty row inside a line is what strict readers stumble over most.
    for event in subtitle.ssa.events:
        rows = [row for row in LINE_BREAK.split(event.text) if row.strip()]
        event.text = "\\N".join(rows)
    # A muxer can write the fonts into the header of a track, thousands of
    # lines of them. A reader that tells the format from the beginning of the
    # file then never gets to see what format it is.
    for key in [key for key in subtitle.ssa.info if key.strip().lower() not in _HEADER_FIELDS]:
        del subtitle.ssa.info[key]
    save(subtitle, destination)
    return True


class SecondTry:
    """A tool that gets re-saved copies of the files when it fails on them as they are.

    Real files are written loosely: a blank line inside a cue, a time no
    editor would write, fonts in the header of a track. A tool that reads
    strictly gives up on them. Someone who knows the tool would open the file
    in an editor and save it again, so the tuned run of a tool does the same
    with the target and with a reference subtitle. The run with the defaults
    does not, and shows what the tool does with the files as they are.
    """

    def __init__(self, runner: AlassRunner | FfsubsyncRunner) -> None:
        self.runner = runner
        self.name = runner.name

    def unavailable(self) -> str | None:
        return self.runner.unavailable()

    def supports(self, case: LoadedCase) -> bool:
        return self.runner.supports(case)

    def version(self) -> str | None:
        return self.runner.version()

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        first = self.runner.run(case, work_dir)
        if first.outcome != "failed" or "timed out" in (first.error or ""):
            return first
        copy_dir = work_dir / "resaved"
        copy_dir.mkdir(exist_ok=True)
        try:
            if not _resave(case.target_path, copy_dir / case.case.target):
                return first
            spec = case.case
            reference = case.reference_subtitle_path
            if reference is not None:
                copy = copy_dir / f"reference{reference.suffix}"
                if _resave(reference, copy):
                    described = spec.reference.model_copy(update={"subtitle": str(copy)})
                    spec = spec.model_copy(update={"reference": described})
            again = self.runner.run(LoadedCase(case=spec, directory=copy_dir), work_dir)
        finally:
            shutil.rmtree(copy_dir, ignore_errors=True)
        again.seconds += first.seconds
        again.details = {**again.details, "resaved_input": True, "first_error": first.error}
        return again


def make_runner(system: System, root: Path) -> Runner:
    if system.tool == "retimer":
        # Embeddings and transcripts come from the set's cache and from nowhere
        # else. The agent's model is not behind this client: it is asked with
        # the key in the environment.
        kind = QuestionsRunner if system.dry else AgentRunner if system.agent else PipelineRunner
        runner = kind(system.name, client=CachedOnly(), cache=CompactCache(root / CACHE_DIR))
        runner.names_audio_language = True
        return runner
    tool = AlassRunner if system.tool == "alass" else FfsubsyncRunner
    runner = tool(system.name, system.args, TIMEOUT_S)
    return SecondTry(runner) if system.tuned else runner


def as_case(manifest: Manifest, pair: Pair, root: Path, reference: ReferenceKind) -> LoadedCase:
    """A pair as the benchmark's runners take a case: a target and one reference."""
    target = manifest.target(pair.target)
    if reference == "audio":
        audio = manifest.video(pair.video).audio
        if audio is None:
            raise ValueError(f"the video of {pair.id} has no audio in the set")
        described = Reference(
            kind="audio", audio=str(root / audio.file), audio_language=AUDIO_LANGUAGE
        )
    else:
        track = plain_path(root, manifest.track(pair.track).file)
        described = Reference(kind="subtitle", subtitle=str(track))
    case = Case(
        id=pair.id,
        seed=pair.video,
        # A real pair is not distorted, and nothing is known about its lines.
        family="none",
        recipe=pair.kind,
        target=Path(target.file).name,
        target_language="ja",
        reference=described,
        expected_outcome=pair.expected,
    )
    return LoadedCase(case=case, directory=root / TARGETS_DIR)
