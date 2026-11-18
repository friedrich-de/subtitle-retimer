"""The `retimer bench` commands."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from retimer.audio import AudioError
from retimer.bench.cases import CASE_FILE, generate_cases, load_cases
from retimer.bench.harness import SUMMARY_MD, load_results, run_benchmark
from retimer.bench.report import build_summary, render_markdown
from retimer.bench.runners import DEFAULT_SYSTEMS, Runner, builtin_runners
from retimer.bench.seeds import SeedError, fetch_seed_audio
from retimer.bench.series import (
    SCRIPT_FILE,
    Series,
    SeriesError,
    Writer,
    lay_out,
    load_series,
    save_series,
    write_series,
)
from retimer.bench.soundcheck import (
    REACH_MS,
    WINDOW_MS,
    SoundCheckError,
    check_files,
    render_text,
)
from retimer.io import SubtitleError, load

DEFAULT_SEEDS = Path("bench/seeds")
DEFAULT_MEDIA = Path("data/bench/media")
DEFAULT_CASES = Path("data/bench/cases")
DEFAULT_RESULTS = Path("data/bench/results")


def all_runners() -> dict[str, Runner]:
    """Every system the benchmark can run. Later milestones add theirs here."""
    return builtin_runners()


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        fetch_seed_audio(args.seeds, args.media, _say)
    except (SeedError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _generate(args: argparse.Namespace) -> int:
    written = generate_cases(args.seeds, args.media, args.cases, progress=_say)
    _say(f"wrote {len(written)} cases to {args.cases}")
    return len(written)


def _cmd_generate(args: argparse.Namespace) -> int:
    return 0 if _generate(args) else 1


def _cmd_systems(args: argparse.Namespace) -> int:
    for name, runner in all_runners().items():
        reason = runner.unavailable()
        print(f"{name:<18} {'ready' if reason is None else reason}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not any(args.cases.glob(f"*/{CASE_FILE}")):
        _generate(args)
    cases = load_cases(args.cases, args.only, args.family.split(",") if args.family else None)
    if not cases:
        print("error: no cases to run", file=sys.stderr)
        return 1
    known = all_runners()
    names = args.systems.split(",") if args.systems else list(DEFAULT_SYSTEMS)
    unknown = [name for name in names if name not in known]
    if unknown:
        print(f"error: unknown systems: {', '.join(unknown)}", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = args.out or DEFAULT_RESULTS / (f"{stamp}-{args.label}" if args.label else stamp)
    run_benchmark(
        cases,
        [known[name] for name in names],
        out,
        repeats=args.repeats,
        workers=args.workers,
        progress=_say,
    )
    print((out / SUMMARY_MD).read_text(encoding="utf-8"))
    _say(f"results are in {out}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    print(render_markdown(build_summary(load_results(args.results))))
    return 0


def _cmd_soundcheck(args: argparse.Namespace) -> int:
    try:
        check = check_files(
            args.audio, load(args.subtitle), round(args.window * 1000), round(args.reach * 1000)
        )
    except (AudioError, SoundCheckError, SubtitleError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(check.model_dump_json(indent=2) if args.json else render_text(check))
    return 0


def _saved_series(series: Series, args: argparse.Namespace) -> None:
    seeds = save_series(series, args.seeds, args.seed)
    for seed, episode in zip(seeds, series.episodes, strict=True):
        spoken = lay_out(series, episode, args.seed)
        _say(f"{seed.id}: {len(spoken)} beats, {spoken[-1].end_ms / 60_000:.1f} minutes")


def _cmd_write_series(args: argparse.Namespace) -> int:
    try:
        writer = Writer()
        series = write_series(writer, args.episodes, _say)
    except (SeriesError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _saved_series(series, args)
    _say(
        f"wrote {args.seeds / series.slug} for ${writer.cost_usd:.3f} "
        f"({writer.tokens['input']} tokens in, {writer.tokens['output']} out)"
    )
    return 0


def _cmd_time_series(args: argparse.Namespace) -> int:
    try:
        series = load_series(args.seeds / args.series / SCRIPT_FILE)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _saved_series(series, args)
    return 0


def add_bench_commands(commands: argparse._SubParsersAction) -> None:
    bench = commands.add_parser("bench", help="generate benchmark cases and run systems on them")
    actions = bench.add_subparsers(dest="bench_command", required=True)

    def add_dirs(parser: argparse.ArgumentParser, cases: bool = True) -> None:
        parser.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS, help="seed material")
        parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA, help="seed audio files")
        if cases:
            parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="case set")

    fetch = actions.add_parser("fetch-seeds", help="download the audio of the seed material")
    add_dirs(fetch, cases=False)
    fetch.set_defaults(run=_cmd_fetch)

    generate = actions.add_parser("generate", help="write the case set from the seed material")
    add_dirs(generate)
    generate.set_defaults(run=_cmd_generate)

    written = actions.add_parser(
        "write-series", help="have the model write a made-up series as seed material"
    )
    written.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS, help="seed material")
    written.add_argument("--episodes", type=int, default=3)
    written.add_argument("--seed", type=int, default=0, help="what the times are drawn from")
    written.set_defaults(run=_cmd_write_series)

    timed = actions.add_parser("time-series", help="time the script of a made-up series again")
    timed.add_argument("series", help="its directory name under the seed material")
    timed.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS, help="seed material")
    timed.add_argument("--seed", type=int, default=0, help="what the times are drawn from")
    timed.set_defaults(run=_cmd_time_series)

    systems = actions.add_parser("systems", help="list the systems and whether they can run here")
    systems.set_defaults(run=_cmd_systems)

    run = actions.add_parser("run", help="run systems over the cases and store the results")
    add_dirs(run)
    run.add_argument("--systems", help=f"comma separated, default: {','.join(DEFAULT_SYSTEMS)}")
    run.add_argument("--only", help="run only the cases whose id contains this text")
    run.add_argument("--family", help="run only these families of cases, comma separated")
    run.add_argument("--repeats", type=int, default=1, help="runs per case, for stability")
    run.add_argument("--workers", type=int, default=2, help="cases run at the same time")
    run.add_argument("--label", help="a name added to the results directory")
    run.add_argument("--out", type=Path, help="results directory, default: a new timestamped one")
    run.set_defaults(run=_cmd_run)

    report = actions.add_parser("report", help="print the summary of a stored run")
    report.add_argument("results", type=Path)
    report.set_defaults(run=_cmd_report)

    soundcheck = actions.add_parser(
        "soundcheck", help="check where the lines of a subtitle start against the sound itself"
    )
    soundcheck.add_argument("audio", type=Path, help="audio or video file")
    soundcheck.add_argument("subtitle", type=Path)
    soundcheck.add_argument(
        "--window", type=float, default=WINDOW_MS / 1000, help="seconds per stretch"
    )
    soundcheck.add_argument(
        "--reach", type=float, default=REACH_MS / 1000, help="seconds searched either way"
    )
    soundcheck.add_argument("--json", action="store_true", help="print the check as JSON")
    soundcheck.set_defaults(run=_cmd_soundcheck)
