"""The report of a run: tables in Markdown, from the manifest and the judgments.

The report is written into the run's directory, next to the data. It names
files of the set, so it is not for the repository.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

from retimer.realset.judge import INPUT, load_judgments, load_track_readings
from retimer.realset.manifest import Manifest, load_manifest
from retimer.realset.names import FILE_TYPES, is_broadcast
from retimer.realset.run import RUN_FILE, load_results
from retimer.realset.systems import SYSTEMS, load_tuning
from retimer.realset.windows import (
    DISAGREE_MS,
    MIN_PAIRS,
    OFF_MS,
    SOUND_OFF_MS,
    WINDOW_MS,
    disagreements,
    judge_sound,
)

REPORT_FILE = "report.md"
# The system the others are compared with.
OURS = "retimer"
# A difference between two systems larger than this is not a matter of taste
# but one of them being plainly wrong, which the window check shows by itself.
_SPOT_CHECK_MAX_MS = 3_000
_SPOT_CHECKS = 10


def _percent(part: int, whole: int) -> str:
    return "-" if not whole else f"{100 * part / whole:.0f}%"


def _table(header: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _code(code: object) -> str:
    """The commits the systems of a run were run with, shortest when they are all the same."""
    if not isinstance(code, dict):
        return f"`{code}`"
    commits = sorted({str(commit) for commit in code.values()})
    if len(commits) == 1:
        return f"`{commits[0]}`"
    return ", ".join(f"`{commit}` ({system})" for system, commit in sorted(code.items()))


def _clock(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def system_order(judgments: list[dict]) -> list[str]:
    """The systems that were judged: the input first, then in the kit's order, then the rest."""
    present = {entry["system"] for entry in judgments}
    known = [INPUT, *(system.name for system in SYSTEMS)]
    return [name for name in known if name in present] + sorted(present - set(known))


def is_right(entry: dict) -> bool:
    """Whether a system did what the pair expects.

    A pair that belongs together has to come out verified. One that does not
    has to be refused, or left as it was.
    """
    if entry["expected"] == "placed":
        return entry["verdict"] == "verified"
    return entry["verdict"] == "refused" or (
        entry["verdict"] not in ("failed",) and not entry.get("moved", False)
    )


def verified(entry: dict) -> bool:
    """Whether the window check verified an output."""
    return entry["verdict"] == "verified"


def confirmed(entry: dict) -> bool:
    """Whether the sound check confirmed an output."""
    return judge_sound(entry.get("sound")) == "confirmed"


def by_file_type(
    manifest: Manifest,
    judgments: list[dict],
    systems: list[str],
    held_out: set[str],
    passed: Callable[[dict], bool] = verified,
) -> list[list[object]]:
    """Share of main pairs that pass a check, per kind of target file and system."""
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    pairs = {pair.id: pair for pair in manifest.pairs}
    for entry in judgments:
        if entry["kind"] != "main" or entry["pair"] not in held_out:
            continue
        kind = manifest.target(pairs[entry["pair"]].target).type
        for key in (kind, "All"):
            counts[(key, entry["system"])][0] += passed(entry)
            counts[(key, entry["system"])][1] += 1
    rows = []
    for kind in (*FILE_TYPES, "All"):
        most = max((counts[(kind, system)][1] for system in systems), default=0)
        if most:
            rows.append([kind, most, *(_percent(*counts[(kind, system)]) for system in systems)])
    return rows


def verdict_counts(judgments: list[dict], systems: list[str], kind: str) -> list[list[object]]:
    """What became of the pairs of one kind, per system."""
    rows = []
    for system in systems:
        entries = [e for e in judgments if e["system"] == system and e["kind"] == kind]
        if not entries:
            continue
        verdicts = Counter(entry["verdict"] for entry in entries)
        seconds = [entry["seconds"] for entry in entries if entry.get("seconds") is not None]
        windows = sum(entry.get("judged_windows", 0) for entry in entries)
        off = sum(entry.get("off_windows", 0) for entry in entries)
        rows.append(
            [
                system,
                len(entries),
                _percent(verdicts["verified"], len(entries)),
                verdicts["verified"],
                verdicts["off"],
                verdicts["unjudged"],
                verdicts["refused"],
                verdicts["failed"],
                _percent(windows - off, windows),
                f"{statistics.median(seconds):.1f}" if seconds else "-",
            ]
        )
    return rows


def two_witnesses(judgments: list[dict], systems: list[str], pairs: set[str]) -> list[list]:
    """The window check and the sound check side by side, per system.

    Only outputs both checks could read are counted: those with a verdict of
    the window check and with audio to ask.
    """
    rows = []
    for system in systems:
        cells: Counter[tuple[str, str]] = Counter()
        for entry in judgments:
            sound = judge_sound(entry.get("sound"))
            if entry["system"] == system and entry["pair"] in pairs and sound is not None:
                cells[(entry["verdict"], sound)] += 1
        total = sum(cells.values())
        if total:
            rows.append(
                [
                    system,
                    total,
                    *(
                        cells[(verdict, sound)]
                        for verdict in ("verified", "off", "unjudged")
                        for sound in ("confirmed", "off", "unclear")
                    ),
                ]
            )
    return rows


def head_to_head(judgments: list[dict], systems: list[str], pairs: set[str]) -> list[list[object]]:
    """The retimer against every other system, on the same pairs."""
    verified: dict[str, dict[str, bool]] = defaultdict(dict)
    for entry in judgments:
        if entry["pair"] in pairs:
            verified[entry["system"]][entry["pair"]] = entry["verdict"] == "verified"
    rows = []
    for system in systems:
        if system in (OURS, INPUT):
            continue
        shared = set(verified[OURS]) & set(verified[system])
        both = sum(verified[OURS][p] and verified[system][p] for p in shared)
        ours = sum(verified[OURS][p] and not verified[system][p] for p in shared)
        theirs = sum(verified[system][p] and not verified[OURS][p] for p in shared)
        rows.append([system, len(shared), both, ours, theirs, len(shared) - both - ours - theirs])
    return rows


def _shifts(entry: dict) -> dict[int, int]:
    return {int(index): shift for index, shift in entry.get("shift_windows", [])}


def disagreement_rows(judgments: list[dict], systems: list[str], pairs: set[str]) -> list[list]:
    """How often the retimer and another system moved a window differently."""
    by_system: dict[str, dict[str, dict]] = defaultdict(dict)
    for entry in judgments:
        if entry["pair"] in pairs and "shift_windows" in entry:
            by_system[entry["system"]][entry["pair"]] = entry
    rows = []
    for system in systems:
        if system in (OURS, INPUT):
            continue
        shared = sorted(set(by_system[OURS]) & set(by_system[system]))
        windows = differing = files = 0
        who: Counter[str] = Counter()
        for pair in shared:
            ours, theirs = by_system[OURS][pair], by_system[system][pair]
            one, other = _shifts(ours), _shifts(theirs)
            windows += len(set(one) & set(other))
            found = disagreements(one, other)
            differing += len(found)
            if found:
                files += 1
                who[(ours["verdict"] == "verified", theirs["verdict"] == "verified")] += 1
        rows.append(
            [
                system,
                len(shared),
                files,
                _percent(differing, windows),
                who[(True, False)],
                who[(False, True)],
                who[(True, True)],
                who[(False, False)],
            ]
        )
    return rows


def refusal_rows(judgments: list[dict], systems: list[str], kind: str, note: str | None, notes):
    """What each system did with pairs it should not have moved, or with one role of track."""
    rows = []
    for system in systems:
        entries = [
            entry
            for entry in judgments
            if entry["system"] == system
            and entry["kind"] == kind
            and (note is None or notes.get(entry["pair"]) == note)
        ]
        if not entries:
            continue
        refused = sum(entry["verdict"] == "refused" for entry in entries)
        failed = sum(entry["verdict"] == "failed" for entry in entries)
        moved = sum(bool(entry.get("moved")) for entry in entries)
        verdicts = Counter(entry["verdict"] for entry in entries)
        rows.append(
            [
                system,
                len(entries),
                refused,
                len(entries) - refused - failed - moved,
                moved,
                failed,
                verdicts["verified"],
                verdicts["off"],
            ]
        )
    return rows


def _sound_at(entry: dict, index: int, shift: int) -> int | None:
    """What the sound check read where a window of the target ended up, when that was clear.

    The window check counts windows on the target's own clock and the sound
    check on the output's, so the window is looked up where its middle went.
    """
    sound = entry.get("sound")
    if sound is None:
        return None
    wanted = (index * WINDOW_MS + WINDOW_MS // 2 + shift) // WINDOW_MS
    for at, _, offset, _, clear in sound["windows"]:
        if at == wanted and clear:
            return offset
    return None


def spot_checks(manifest: Manifest, judgments: list[dict], other: str = "alass") -> list[dict]:
    """The clearest main pairs in which the retimer and another system disagree.

    Clear means: several windows, each by more than the two systems' usual
    scatter and by little enough that neither is plainly lost. Captions taken
    off a broadcast come first, since that is where the open question is,
    and a video is named once, with the file that differs in the most
    windows. Only windows that land on the video count: the later episodes
    of a file that holds several are nobody's to check here.
    Each window comes with the two shifts and with what the sound check read
    on each output there.
    """
    pairs = {pair.id: pair for pair in manifest.pairs if pair.kind == "main"}
    entries: dict[str, dict[str, dict]] = defaultdict(dict)
    for entry in judgments:
        if entry["pair"] in pairs and entry["system"] in (OURS, other):
            entries[entry["pair"]][entry["system"]] = entry
    found = []
    for pair_id, both in entries.items():
        if OURS not in both or other not in both:
            continue
        ours, theirs = both[OURS], both[other]
        if "shift_windows" not in ours or "shift_windows" not in theirs:
            continue
        pair = pairs[pair_id]
        video_ms = manifest.video(pair.video).duration_s * 1000
        differing = [
            (index, one, two, _sound_at(ours, index, one), _sound_at(theirs, index, two))
            for index, one, two in disagreements(_shifts(ours), _shifts(theirs))
            if abs(one - two) <= _SPOT_CHECK_MAX_MS
            and (index + 1) * WINDOW_MS + one > 0
            and (not video_ms or index * WINDOW_MS + one < video_ms)
        ]
        if not differing:
            continue
        target = manifest.target(pair.target)
        found.append(
            {
                "pair": pair_id,
                "target": target.name,
                "type": target.type,
                "video": manifest.video(pair.video).source,
                "windows": differing,
                "ours": ours["verdict"],
                "theirs": theirs["verdict"],
                "other": other,
            }
        )
    found.sort(
        key=lambda item: (
            not is_broadcast(item["type"]),
            -len(item["windows"]),
            -statistics.median(abs(one - two) for _, one, two, _, _ in item["windows"]),
            item["pair"],
        )
    )
    once: dict[str, dict] = {}
    for item in found:
        once.setdefault(item["video"], item)
    return list(once.values())[:_SPOT_CHECKS]


def _spot_check_text(item: dict) -> str:
    def heard(offset: int | None) -> str:
        return "sound not clear" if offset is None else f"sound {offset / 1000:+.2f} s"

    windows = "; ".join(
        f"{_clock(index * WINDOW_MS + one)} to {_clock((index + 1) * WINDOW_MS + one)}: "
        f"retimer {one / 1000:+.2f} s ({heard(ours)}), "
        f"{item['other']} {two / 1000:+.2f} s ({heard(theirs)})"
        for index, one, two, ours, theirs in item["windows"][:4]
    )
    return (
        f"- **{item['target']}** ({item['type']}) on `{item['video']}`. "
        f"Window check: retimer {item['ours']}, {item['other']} {item['theirs']}. "
        f"{len(item['windows'])} windows differ. {windows}. Pair `{item['pair']}`."
    )


def own_tracks(manifest: Manifest, readings: list[dict]) -> tuple[list[list[object]], list[str]]:
    """What the sound says about the videos' own tracks: a table by role, and the main tracks
    it finds off."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    off = []
    for reading in readings:
        verdict = judge_sound(reading["sound"])
        counts[reading["role"]][verdict] += 1
        if reading["role"] == "main" and verdict == "off":
            sound = reading["sound"]
            clear = [offset for _, _, offset, _, is_clear in sound["windows"] if is_clear]
            beyond = sum(1 for offset in clear if abs(offset) > SOUND_OFF_MS)
            whole = "not clear" if not sound["clear"] else f"{sound['offset_ms'] / 1000:+.2f} s"
            off.append(
                f"- `{manifest.video(reading['video']).source}`: whole file {whole}, "
                f"{beyond} of {len(clear)} clear windows further than "
                f"{SOUND_OFF_MS / 1000:.1f} s off"
            )
    rows = [
        [role, sum(found.values()), found["confirmed"], found["off"], found["unclear"]]
        for role, found in sorted(counts.items())
    ]
    return rows, sorted(off)


def render(
    manifest: Manifest,
    judgments: list[dict],
    results: list[dict],
    tuning: dict[str, dict],
    run_info: dict,
    name: str,
    track_readings: list[dict] | None = None,
) -> str:
    systems = system_order(judgments)
    main = {pair.id for pair in manifest.pairs if pair.kind == "main"}
    held_out = {pair.id for pair in manifest.pairs if pair.kind == "main" and not pair.tuning}
    notes = {pair.id: pair.note for pair in manifest.pairs}
    tracks = [track for video in manifest.videos for track in video.tracks]
    roles = Counter(track.role for track in tracks)
    kinds = Counter(pair.kind for pair in manifest.pairs)
    types = Counter(target.type for target in manifest.targets)
    verdict_header = [
        "System",
        "Pairs",
        "Share verified",
        "Verified",
        "Off",
        "Not judged",
        "Refused",
        "Failed",
        f"Windows within {OFF_MS / 1000:.2f} s",
        "Median seconds",
    ]
    refusal_header = [
        "System",
        "Pairs",
        "Refused",
        "Left alone",
        "Moved",
        "Failed",
        "of these: verified",
        "off",
    ]
    parts = [
        f"# Real-file run `{name}`",
        f"Started {run_info.get('started')}, code at {_code(run_info.get('code'))}. "
        f"The set was built {manifest.built}.",
        "## The set",
        f"{len(manifest.videos)} videos with {len(tracks)} text subtitle tracks "
        f"({', '.join(f'{count} {role}' for role, count in sorted(roles.items()))}), "
        f"{len(manifest.targets)} targets, {len(manifest.pairs)} pairs "
        f"({kinds['main']} main, {kinds['other_track']} with another track of the same video, "
        f"{kinds['wrong']} deliberately wrong). "
        f"{len(manifest.skipped)} listed files were left out.",
        _table(
            ["Kind of target file", "Targets"],
            [[kind, types[kind]] for kind in FILE_TYPES if types[kind]],
        ),
        "## How a file is judged",
        f"Lines of the target that clearly mean the same as a line of the reference track are "
        f"found from the text. An output is held against them in windows of "
        f"{WINDOW_MS // 60_000} minutes: a window with at least {MIN_PAIRS} such pairs is off "
        f"when their median error is above {OFF_MS / 1000:.2f} s. A file is verified when no "
        "judged window is off. The pairs are found with the retimer's own matching, so this "
        "check alone cannot clear the retimer.",
        "## Main pairs by kind of file: share verified",
        f"On the {len(held_out)} main pairs that no setting was chosen on. `input` is the "
        "target as it came.",
        _table(
            ["Kind of file", "Pairs", *systems],
            by_file_type(manifest, judgments, systems, held_out),
        ),
        "## Main pairs by kind of file: share the sound check confirms",
        f"The same pairs, asked of the video's audio instead of its subtitle track. Confirmed "
        f"means: the reading over the whole file is clear and within {SOUND_OFF_MS / 1000:.1f} s, "
        "and no window reads clear and further off. The sound check knows nothing of the "
        "retimer's matching. It does not rank two results that are both within its tolerance.",
        _table(
            ["Kind of file", "Pairs", *systems],
            by_file_type(manifest, judgments, systems, held_out, confirmed),
        ),
        "## The two checks side by side",
        "Main pairs, all of them. Columns: the window check's verdict, then the sound check's.",
        _table(
            [
                "System",
                "Outputs",
                *(
                    f"{verdict} / {sound}"
                    for verdict in ("verified", "off", "not judged")
                    for sound in ("confirmed", "off", "unclear")
                ),
            ],
            two_witnesses(judgments, systems, main),
        ),
        "## Main pairs: what became of them",
        f"All {len(main)} main pairs.",
        _table(verdict_header, verdict_counts(judgments, systems, "main")),
        "## Head to head with the retimer",
        "On the main pairs that no setting was chosen on, counting files verified.",
        _table(
            ["Against", "Pairs", "Both", "Only the retimer", "Only the other", "Neither"],
            head_to_head(judgments, systems, held_out),
        ),
        "## Where another system moved the lines differently",
        f"A window counts when the two shifts differ by more than {DISAGREE_MS / 1000:.1f} s. "
        "The last four columns say which of the two was verified in the files with such a window.",
        _table(
            [
                "Against",
                "Pairs with both outputs",
                "Files with a differing window",
                "Windows differing",
                "Only the retimer verified",
                "Only the other",
                "Both",
                "Neither",
            ],
            disagreement_rows(judgments, systems, held_out),
        ),
    ]
    for role in ("full", "foreign", "signs", "wrong_episode"):
        rows = refusal_rows(judgments, systems, "other_track", role, notes)
        if rows:
            parts += [f"## Other tracks of the same video: `{role}`", _table(refusal_header, rows)]
    for note in ("the next episode", "another series"):
        rows = refusal_rows(judgments, systems, "wrong", note, notes)
        if rows:
            parts += [f"## Deliberately wrong pairs: {note}", _table(refusal_header, rows)]
    outcomes = Counter(
        (record["kind"], record["outcome"]) for record in results if record["system"] == OURS
    )
    if outcomes:
        parts += [
            "## What the retimer reported",
            _table(
                ["Kind of pair", "Outcome", "Pairs"],
                [[kind, outcome, count] for (kind, outcome), count in sorted(outcomes.items())],
            ),
        ]
    if tuning:
        parts += [
            "## Settings chosen on the tuning pairs",
            _table(
                ["System", "Setting", "Arguments", "Share verified per setting tried"],
                [
                    [
                        system,
                        choice["setting"],
                        " ".join(choice["args"]) or "(defaults)",
                        ", ".join(f"{k} {100 * v:.0f}%" for k, v in choice["verified"].items()),
                    ]
                    for system, choice in tuning.items()
                ],
            ),
        ]
    rows, off = own_tracks(manifest, track_readings or [])
    if rows:
        parts += [
            "## The videos' own tracks against the sound",
            "The window check takes the main track of a video for the truth. This is the sound "
            "check's reading of those tracks themselves.",
            _table(["Role", "Tracks", "Confirmed", "Off", "Unclear"], rows),
        ]
        if off:
            parts.append("Main tracks the sound check finds off:\n\n" + "\n".join(off))
    checks = spot_checks(manifest, judgments)
    if checks:
        parts.append("## Candidates for a spot check: the retimer against alass")
        parts.append(
            "Times are on the video's clock. A shift is how far the system moved the lines of "
            "that stretch. The sound reading is how far the lines would still have to move to "
            "sit on the voices, positive when they come too early."
        )
        parts.append("\n".join(_spot_check_text(item) for item in checks))
    return "\n\n".join(parts) + "\n"


def write_report(root: Path, run_dir: Path) -> str:
    """Write the report of a run into its directory and return the text."""
    info_path = run_dir / RUN_FILE
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    text = render(
        load_manifest(root),
        load_judgments(run_dir),
        load_results(run_dir),
        load_tuning(root),
        info,
        run_dir.name,
        load_track_readings(run_dir),
    )
    (run_dir / REPORT_FILE).write_text(text, encoding="utf-8")
    return text
