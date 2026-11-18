"""Judges what the systems produced, every system by the same measures.

For each pair the lines of the target that clearly mean the same as a line of
the reference track are found once, from the text alone. Each system's output
is then held against them: how far do those lines start from their
counterparts, window by window? Next to it stands what the system did to the
file, as shifts per window, so that systems can be compared with each other
where the text has nothing to say.

Finding the counterparts uses the retimer's own matching, so this check alone
cannot clear the retimer. It is one witness of three. The second is the sound
check of the benchmark, which asks the audio of the video whether the lines
start where somebody starts to speak, and knows nothing of any subtitle track.
The third are the places where the systems disagree with each other.
"""

from __future__ import annotations

import json
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from retimer.audio import AudioError
from retimer.bench import soundcheck
from retimer.bench.metrics import match_output_ids
from retimer.classify import classify_lines
from retimer.embed import Embedder
from retimer.io import SubtitleError, load
from retimer.match import match_lines
from retimer.model import Subtitle
from retimer.realset.embedding import CachedOnly, CompactCache
from retimer.realset.manifest import CACHE_DIR, Manifest, Pair, load_manifest, plain_path
from retimer.realset.run import load_results
from retimer.realset.windows import (
    UNMOVED_MS,
    judge_windows,
    line_shifts,
    segments,
    window_errors,
    window_shifts,
)
from retimer.reference import timeline_from_subtitle
from retimer.views import build_match_view

JUDGMENTS_FILE = "judgments.jsonl"
TRACKS_FILE = "tracks.jsonl"
# How far a pair of lines has to stand out from all others to count as
# clearly meaning the same. The pipeline takes anchors from 2.0 on.
CLEAR_SCORE = 3.0
# The target as it came, judged like a system: how far off the files are to begin with.
INPUT = "input"
_MAX_SEGMENTS = 40

_WORKERS = multiprocessing.get_context("spawn")
_STATE: dict = {}


def clear_pairs(target: Subtitle, reference: Subtitle, embedder: Embedder) -> list[tuple[int, int]]:
    """The lines of the target with a reference line that clearly means the same.

    Each as the id of the target's line and the start of its counterpart.
    The clock plays no part in finding them, only the text and the order of
    the lines.
    """
    lines = build_match_view(target, classify_lines(target)).lines
    timeline = timeline_from_subtitle(reference, None, classify_lines(reference))
    matching = match_lines(lines, timeline, embedder)
    return [
        (anchor.target_id, anchor.ref_start_ms)
        for anchor in matching.anchors
        if anchor.score >= CLEAR_SCORE
    ]


def starts_by_id(target: Subtitle, output: Subtitle, kept_ids: list[int] | None) -> dict[int, int]:
    """Where every line of the target starts in an output, for the lines that are in it."""
    ids = (
        kept_ids if kept_ids and len(kept_ids) == len(output) else match_output_ids(target, output)
    )
    return {
        line_id: line.start_ms
        for line_id, line in zip(ids, output.lines, strict=True)
        if line_id is not None
    }


def sound_reading(presence: np.ndarray, subtitle: Subtitle) -> dict:
    """What the sound check says about a file, from a voice curve worked out once.

    The same reading as `soundcheck.check_sound` gives. That one works the
    curve out anew for every file, and here one video's audio is asked about
    the outputs of every system. Each window comes as its index, its number
    of lines, the offset, the strength and whether the offset is clear.
    """
    audio_ms = len(presence) * soundcheck.STEP_MS
    spans = [span for span in soundcheck.line_spans(subtitle) if 0 <= span[0] < audio_ms]
    whole = np.zeros(2 * soundcheck._span_steps(soundcheck.REACH_MS) + 1)
    windows = []
    for index, from_ms in enumerate(range(0, max(audio_ms, 1), soundcheck.WINDOW_MS)):
        inside = [span for span in spans if from_ms <= span[0] < from_ms + soundcheck.WINDOW_MS]
        if not inside:
            continue
        curve = soundcheck.line_responses(inside, presence).sum(axis=0)
        whole += curve
        if len(inside) >= soundcheck.MIN_LINES:
            offset, strength = soundcheck.read_offset(curve)
            clear = strength >= soundcheck.CLEAR_STRENGTH
            windows.append([index, len(inside), offset, strength, clear])
        else:
            windows.append([index, len(inside), None, None, False])
    reading: dict = {"lines": len(spans), "offset_ms": None, "strength": None, "clear": False}
    if len(spans) >= soundcheck.MIN_LINES:
        reading["offset_ms"], reading["strength"] = soundcheck.read_offset(whole)
        reading["clear"] = reading["strength"] >= soundcheck.CLEAR_STRENGTH
    return {**reading, "windows": windows}


def judge_output(
    evidence: list[tuple[int, int]], original: dict[int, int], starts: dict[int, int]
) -> dict:
    """The measures of one output: errors against the evidence, and what was moved."""
    windows = window_errors(evidence, starts)
    verdict, judged, off = judge_windows(windows)
    shifts = line_shifts(original, starts)
    parts = segments(original, starts)
    return {
        "verdict": verdict,
        "judged_windows": judged,
        "off_windows": off,
        "windows": windows,
        "moved": bool(shifts) and max(abs(shift) for shift in shifts.values()) > UNMOVED_MS,
        "shift_windows": sorted(window_shifts(original, starts).items()),
        "segments": len(parts),
        "largest_segments": sorted(parts, key=lambda part: -part[3])[:_MAX_SEGMENTS],
        "lines": len(starts),
        "lines_lost": len(original) - len(shifts),
    }


def _judge_pair(
    root: Path, run: Path, pair: Pair, records: list[dict], presence: np.ndarray | None
) -> list[dict]:
    """Every judgment of one pair: of the target as it came, and of each system's output."""
    manifest: Manifest = _STATE["manifest"]
    base = {"pair": pair.id, "kind": pair.kind, "expected": pair.expected}
    try:
        target = load(root / manifest.target(pair.target).file)
        reference = load(plain_path(root, manifest.track(pair.track).file))
        evidence = clear_pairs(target, reference, _STATE["embedder"])
    except Exception as error:  # one unreadable file must not stop the judging
        return [{**base, "system": INPUT, "verdict": "failed", "error": str(error)[-300:]}]
    original = {line.id: line.start_ms for line in target.lines}

    def measured(subtitle: Subtitle, starts: dict[int, int]) -> dict:
        found = judge_output(evidence, original, starts)
        if presence is not None:
            found["sound"] = sound_reading(presence, subtitle)
        return found

    judged = [{**base, "system": INPUT, "pairs": len(evidence), **measured(target, original)}]
    for record in records:
        entry = {
            **base,
            "system": record["system"],
            "outcome": record["outcome"],
            "seconds": record["seconds"],
            "pairs": len(evidence),
        }
        if record["output"] is None:
            refused = record["outcome"] == "rejected_no_match"
            entry.update(verdict="refused" if refused else "failed", error=record["error"])
        else:
            try:
                output = load(plain_path(run, record["output"]))
                entry.update(measured(output, starts_by_id(target, output, record.get("kept_ids"))))
            except (SubtitleError, OSError) as error:
                entry.update(verdict="failed", error=f"unreadable output: {error}")
        judged.append(entry)
    return judged


def _judge_video(
    root: str, run_dir: str, video_id: str, work: list[tuple[Pair, list[dict]]]
) -> tuple[list[dict], list[dict]]:
    """Every judgment of the pairs of one video, in a worker process.

    The pairs of a video share its audio, so its voice curve is worked out
    once. The video's own full tracks are asked of the sound as well: the
    window check takes the main track for the truth, and this says whether
    it is one.
    """
    set_root = Path(root)
    if "manifest" not in _STATE:
        _STATE["manifest"] = load_manifest(set_root)
        _STATE["embedder"] = Embedder(client=CachedOnly(), cache=CompactCache(set_root / CACHE_DIR))
    video = _STATE["manifest"].video(video_id)
    presence = None
    if video.audio is not None:
        try:
            presence = soundcheck.voice_presence(
                soundcheck.decode_audio(set_root / video.audio.file)
            )
        except (AudioError, OSError):
            presence = None
    judged: list[dict] = []
    for pair, records in work:
        judged += _judge_pair(set_root, Path(run_dir), pair, records, presence)
    tracks: list[dict] = []
    for track in video.tracks if presence is not None else []:
        if track.role == "signs":
            continue
        try:
            reading = sound_reading(presence, load(plain_path(set_root, track.file)))
        except (SubtitleError, OSError):
            continue
        tracks.append({"video": video_id, "track": track.id, "role": track.role, "sound": reading})
    return judged, tracks


def _write(path: Path, entries: list[dict]) -> None:
    text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    path.write_text(text, encoding="utf-8")


def judge_run(
    root: Path, run_dir: Path, *, workers: int = 4, say: Callable[[str], None] | None = None
) -> list[dict]:
    """Judge every output of a run and write the judgments next to its results.

    What the sound says about the videos' own tracks is written next to them.
    """
    say = say or (lambda message: None)
    manifest = load_manifest(root)
    by_pair: dict[str, list[dict]] = {}
    for record in load_results(run_dir):
        by_pair.setdefault(record["pair"], []).append(record)
    by_video: dict[str, list[tuple[Pair, list[dict]]]] = {}
    for pair in manifest.pairs:
        if pair.id in by_pair:
            by_video.setdefault(pair.video, []).append((pair, by_pair[pair.id]))
    judgments: list[dict] = []
    tracks: list[dict] = []
    with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=_WORKERS) as pool:
        futures = [
            pool.submit(_judge_video, str(root.resolve()), str(run_dir.resolve()), video_id, work)
            for video_id, work in by_video.items()
        ]
        for done, future in enumerate(futures, start=1):
            judged, read = future.result()
            judgments += judged
            tracks += read
            if done % 50 == 0 or done == len(futures):
                say(f"judged the pairs of {done}/{len(futures)} videos")
    _write(run_dir / JUDGMENTS_FILE, judgments)
    _write(run_dir / TRACKS_FILE, tracks)
    return judgments


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_judgments(run_dir: Path) -> list[dict]:
    return _read(run_dir / JUDGMENTS_FILE)


def load_track_readings(run_dir: Path) -> list[dict]:
    """What the sound check read on the videos' own tracks, as `judge_run` wrote it."""
    return _read(run_dir / TRACKS_FILE)
