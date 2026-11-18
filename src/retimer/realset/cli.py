"""The `retimer realset` commands."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from retimer.realset.jimaku import KEY_ENV
from retimer.realset.manifest import CACHE_DIR, RUNS_DIR

DEFAULT_RUN = "main"


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _cmd_build(args: argparse.Namespace) -> int:
    from retimer.realset.build import build

    key = os.environ.get(KEY_ENV) or None
    if args.jimaku_key_file:
        key = args.jimaku_key_file.read_text(encoding="utf-8").strip()
    build(
        args.videos,
        args.root,
        jimaku_key=key,
        overrides_path=args.overrides,
        workers=args.workers,
        refresh=args.refresh,
        say=_say,
    )
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    from retimer.realset.embedding import CompactCache, fill_cache
    from retimer.realset.manifest import load_manifest, plain_path

    manifest = load_manifest(args.root)
    targets = {manifest.target(pair.target).file for pair in manifest.pairs}
    tracks = {manifest.track(pair.track).file for pair in manifest.pairs}
    fill_cache(
        [args.root / file for file in sorted(targets)],
        [plain_path(args.root, file) for file in sorted(tracks)],
        CompactCache(args.root / CACHE_DIR),
        say=_say,
    )
    return 0


def _pairs(args: argparse.Namespace):
    from retimer.realset.manifest import load_manifest
    from retimer.realset.run import select_pairs

    return select_pairs(
        load_manifest(args.root),
        kinds=args.kinds.split(",") if args.kinds else None,
        only=args.only,
        limit=args.limit,
    )


def _cmd_run(args: argparse.Namespace) -> int:
    from retimer.realset.run import run_systems
    from retimer.realset.systems import SYSTEMS

    names = args.systems.split(",") if args.systems else [system.name for system in SYSTEMS]
    try:
        run_dir = run_systems(
            args.root,
            args.name,
            names,
            pairs=_pairs(args),
            workers=args.workers,
            retry_failed=args.retry_failed,
            again=args.again,
            say=_say,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _say(f"results are in {run_dir}")
    return 0


def _cmd_tune(args: argparse.Namespace) -> int:
    from retimer.realset.tune import tune

    for name, choice in tune(args.root, workers=args.workers, say=_say).items():
        print(f"{name}: {choice['setting']} ({' '.join(choice['args']) or 'the defaults'})")
    return 0


def _cmd_judge(args: argparse.Namespace) -> int:
    from retimer.realset.judge import judge_run

    judge_run(args.root, args.root / RUNS_DIR / args.name, workers=args.workers, say=_say)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from retimer.realset.report import write_report

    print(write_report(args.root, args.root / RUNS_DIR / args.name))
    return 0


def _cmd_tidy(args: argparse.Namespace) -> int:
    from retimer.realset.manifest import tidy

    _say(f"packed {tidy(args.root)} outputs")
    return 0


def add_realset_commands(commands: argparse._SubParsersAction) -> None:
    realset = commands.add_parser(
        "realset", help="build a set of real files, run systems over it, judge and report"
    )
    actions = realset.add_subparsers(dest="realset_command", required=True)

    def add(name: str, run, help_text: str, workers: bool = True) -> argparse.ArgumentParser:
        parser = actions.add_parser(name, help=help_text)
        parser.add_argument("root", type=Path, help="the directory of the set")
        if workers:
            parser.add_argument("--workers", type=int, default=8, help="work done side by side")
        parser.set_defaults(run=run)
        return parser

    build = add("build", _cmd_build, "build the set, or bring it up to date")
    build.add_argument("--videos", type=Path, required=True, help="videos to take tracks from")
    build.add_argument(
        "--jimaku-key-file", type=Path, help=f"a file with the Jimaku key (or set {KEY_ENV})"
    )
    build.add_argument("--overrides", type=Path, help="default: overrides.json in the set")
    build.add_argument("--refresh", action="store_true", help="ask Jimaku for its listing again")

    add("embed", _cmd_embed, "embed every text of the set with the local model", workers=False)

    run = add("run", _cmd_run, "run systems over the pairs and keep what they produce")
    run.add_argument("--systems", help="comma separated, default: all")
    run.add_argument("--run", dest="name", default=DEFAULT_RUN, help="the name of the run")
    run.add_argument("--kinds", help="only pairs of these kinds: main, other_track, wrong")
    run.add_argument("--only", help="only pairs whose id contains this text")
    run.add_argument("--limit", type=int, help="only the first this-many pairs")
    run.add_argument("--retry-failed", action="store_true", help="run again what failed")
    run.add_argument(
        "--again", action="store_true", help="run the named systems again, over what is on record"
    )

    add("tune", _cmd_tune, "choose the tools' settings on the tuning pairs")

    judge = add("judge", _cmd_judge, "judge every output of a run")
    judge.add_argument("--run", dest="name", default=DEFAULT_RUN, help="the name of the run")

    report = add("report", _cmd_report, "write and print the report of a run", workers=False)
    report.add_argument("--run", dest="name", default=DEFAULT_RUN, help="the name of the run")

    add("tidy", _cmd_tidy, "pack the outputs of the runs, delete unpacked copies", workers=False)
