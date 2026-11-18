"""Command line access to the subtitle core, the fixed pipeline and the benchmark."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from retimer.audio import AudioError
from retimer.bench.cli import add_bench_commands
from retimer.classify import classify_lines
from retimer.engine import PlanError, apply_plan
from retimer.inspection import inspect_subtitle, sample_lines
from retimer.io import SUPPORTED_FORMATS, SubtitleError, load, save
from retimer.plan import Plan
from retimer.providers import ProviderError
from retimer.realset.cli import add_realset_commands
from retimer.views import Options, build_views

# The reference is other content, so there is no output file.
EXIT_REJECTED = 3


def _cmd_inspect(args: argparse.Namespace) -> int:
    print(inspect_subtitle(load(args.file)).model_dump_json(indent=2))
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    subtitle = load(args.file)
    lines = sample_lines(
        subtitle,
        style=args.style,
        label=args.label,
        pattern=args.pattern,
        max_confidence=args.max_confidence,
        limit=args.limit,
    )
    for line in lines:
        print(f"{line.id:>5}  {line.start_ms:>9}  {line.label:<17}  {line.style:<16}  {line.text}")
    return 0


def _options(args: argparse.Namespace) -> Options:
    return Options(
        remove_hearing_impaired=args.remove_hearing_impaired,
        remove_karaoke=args.remove_karaoke,
        remove_signs_and_credits=args.remove_signs_and_credits,
        strip_formatting=args.strip_formatting,
        remove_symbols=args.remove_symbols,
        drop_unmatched=not getattr(args, "keep_unmatched", False),
    )


def _cmd_clean(args: argparse.Namespace) -> int:
    subtitle = load(args.file)
    views = build_views(subtitle, _options(args), classify_lines(subtitle))
    plan = Plan(outcome="already_in_sync", content_ops=views.content_ops)
    return _apply(subtitle, plan, args)


def _cmd_sync(args: argparse.Namespace) -> int:
    # Imported here: the other commands do not need numpy and start faster without it.
    from retimer.choice import MAX_REFERENCES, Candidate
    from retimer.pipeline import sync

    paths = args.reference or []
    if not args.audio and not paths:
        print("error: give --audio, --reference, or both", file=sys.stderr)
        return 1
    if args.matched_reference and not paths:
        print("error: --matched-reference needs --reference", file=sys.stderr)
        return 1
    if len(paths) > MAX_REFERENCES:
        print(f"error: --reference can be given up to {MAX_REFERENCES} times", file=sys.stderr)
        return 1
    job = {
        "audio": Path(args.audio) if args.audio else None,
        # Several are candidates: the one that fits the target best is taken.
        "references": [Candidate(subtitle=load(path), filename=Path(path).name) for path in paths],
        "options": _options(args),
        "audio_language": args.audio_language,
        "matched_reference": bool(args.matched_reference),
    }
    try:
        if args.agent:
            from retimer.agent import (
                AgentSettings,
                agent_available,
                agent_key_env,
                sync_with_agent,
            )

            try:
                needed = agent_key_env()
            except ValueError as error:
                print(f"error: {error}", file=sys.stderr)
                return 1
            if not agent_available():
                print(f"error: --agent needs {needed} to be set", file=sys.stderr)
                return 1
            settings = AgentSettings(
                model=args.agent,
                effort=args.agent_effort,
                consult="always" if args.agent_always else "auto",
            )
            result = sync_with_agent(load(args.file), agent=settings, **job)
        else:
            result = sync(load(args.file), **job)
    except (ProviderError, AudioError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    report = result.report
    if args.plan:
        Path(args.plan).write_text(result.plan.to_json() + "\n", encoding="utf-8")
    if args.report:
        Path(args.report).write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if result.subtitle is not None:
        save(result.subtitle, args.output, args.format)
    if result.reference is not None:
        save(result.reference, args.matched_reference, args.format)
    summary = {
        "outcome": report.outcome,
        "confidence": result.plan.confidence,
        "fit": report.chosen,
        "rate": report.rate_name,
        "blocks": [
            {"from_ms": block.from_ms, "shift_ms": block.shift_ms, "confidence": block.confidence}
            for block in report.blocks
        ],
        "trimmed_lines": len(report.trimmed),
        "cut_lines": len(report.cut),
        "warnings": report.warnings,
        "cost_usd": round(report.total_cost_usd, 5),
    }
    if report.reference_choice is not None and len(report.reference_choice.candidates) > 1:
        choice = report.reference_choice
        summary["reference"] = {
            "chosen": choice.candidates[choice.chosen].filename
            if choice.chosen is not None
            else None,
            "reason": choice.reason,
            "candidates": [
                {"file": found.filename, "verdict": found.verdict, "reason": found.reason}
                for found in choice.candidates
            ],
        }
    if report.matched_reference is not None:
        # The pairs themselves are in the report. Here they are only counted.
        summary["matched_reference"] = {
            **report.matched_reference.model_dump(exclude_none=True, exclude={"pairs"}),
            "pairs": len(report.matched_reference.pairs),
        }
    if report.agent:
        summary["agent"] = {
            key: report.agent[key]
            for key in ("model", "consulted", "questions", "finished", "turns", "cost_usd")
        }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return EXIT_REJECTED if result.subtitle is None else 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from retimer.api.server import serve

    return serve(args.host, args.port, dev=args.dev, run_worker=not args.no_worker)


def _cmd_worker(args: argparse.Namespace) -> int:
    from retimer.api.server import work

    return work()


def _cmd_api_key(args: argparse.Namespace) -> int:
    from retimer.api.auth import format_entry, new_key
    from retimer.api.config import API_KEYS_ENV

    try:
        key, entry = new_key(args.label)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("API key, for the client. It is shown once and stored nowhere:")
    print(f"  {key}")
    print(f"Entry for {API_KEYS_ENV} on the server. Several entries are separated by commas:")
    print(f"  {format_entry(entry)}")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    plan = Plan.model_validate_json(Path(args.plan).read_text(encoding="utf-8"))
    return _apply(load(args.file), plan, args)


def _apply(subtitle, plan: Plan, args: argparse.Namespace) -> int:
    try:
        result = apply_plan(subtitle, plan)
    except PlanError as error:
        for issue in error.issues:
            print(f"{issue.code}: {issue.message} {issue.ids or ''}".rstrip(), file=sys.stderr)
        return 2
    save(result.subtitle, args.output, args.format)
    print(json.dumps(dataclasses.asdict(result.summary), indent=2))
    return 0


def _add_content_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--remove-hearing-impaired", action="store_true")
    parser.add_argument("--remove-karaoke", action="store_true")
    parser.add_argument(
        "--remove-signs-and-credits",
        action="store_true",
        help="drop what is shown on screen and not spoken: signs, credits, advertisements",
    )
    parser.add_argument("--strip-formatting", action="store_true")
    parser.add_argument("--remove-symbols", action="store_true")


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output", required=True, help="where to write the result")
    parser.add_argument(
        "--format", choices=SUPPORTED_FORMATS, help="output format, default: same as the input"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retimer", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="print the inspection report as JSON")
    inspect.add_argument("file")
    inspect.set_defaults(run=_cmd_inspect)

    sample = commands.add_parser("sample", help="print lines that pass the filters")
    sample.add_argument("file")
    sample.add_argument("--style")
    sample.add_argument("--label")
    sample.add_argument("--pattern", help="regular expression on the text without tags")
    sample.add_argument("--max-confidence", type=float, help="only lines the rules are unsure of")
    sample.add_argument("--limit", type=int, default=20)
    sample.set_defaults(run=_cmd_sample)

    cleaner = commands.add_parser("clean", help="apply content options, leave the timing alone")
    cleaner.add_argument("file")
    _add_content_options(cleaner)
    _add_output(cleaner)
    cleaner.set_defaults(run=_cmd_clean)

    syncer = commands.add_parser(
        "sync", help="sync a subtitle to audio, a reference subtitle, or both"
    )
    syncer.add_argument("file", help="the subtitle to retime")
    syncer.add_argument("--audio", help="audio of the video the subtitle should fit")
    syncer.add_argument(
        "--reference",
        action="append",
        metavar="FILE",
        help="a subtitle that is in sync, in any language; given several times, such as "
        "once for every subtitle track of a video, the one that fits the target best is taken",
    )
    syncer.add_argument("--audio-language", help="language spoken in the audio, if known")
    _add_content_options(syncer)
    syncer.add_argument(
        "--keep-unmatched",
        action="store_true",
        help="keep leading and trailing lines that the reference has no counterpart for",
    )
    syncer.add_argument(
        "--agent",
        nargs="?",
        const="haiku",
        metavar="MODEL",
        help="let a model settle what the routine steps leave open: haiku (the default), "
        "sonnet, opus, or a full model id",
    )
    syncer.add_argument(
        "--agent-effort",
        default="medium",
        choices=("low", "medium", "high", "xhigh", "max"),
        help="how much the model may think, default: medium",
    )
    syncer.add_argument(
        "--agent-always",
        action="store_true",
        help="ask the model about every file, not only where something is open",
    )
    syncer.add_argument(
        "--matched-reference",
        metavar="PATH",
        help="also write the reference subtitle here, reduced to the dialogue lines the "
        "synced file has a counterpart for; needs --reference",
    )
    syncer.add_argument("--plan", help="also write the plan here, as JSON")
    syncer.add_argument("--report", help="also write the full report here, as JSON")
    _add_output(syncer)
    syncer.set_defaults(run=_cmd_sync)

    apply = commands.add_parser("apply", help="run a plan file on a subtitle")
    apply.add_argument("file")
    apply.add_argument("plan", help="path to a plan in JSON")
    _add_output(apply)
    apply.set_defaults(run=_cmd_apply)

    server = commands.add_parser("serve", help="run the HTTP API")
    server.add_argument("--host", default="127.0.0.1", help="default: this machine only")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument(
        "--dev",
        action="store_true",
        help="development server: plain HTTP and /docs, on this machine only",
    )
    server.add_argument(
        "--no-worker",
        action="store_true",
        help="only take requests; jobs are run by a separate `retimer worker`",
    )
    server.set_defaults(run=_cmd_serve)

    worker = commands.add_parser("worker", help="run queued jobs, next to `serve --no-worker`")
    worker.set_defaults(run=_cmd_worker)

    api_key = commands.add_parser("api-key", help="make an API key and its entry for the server")
    api_key.add_argument("--label", default="default", help="a name for the key in the logs")
    api_key.set_defaults(run=_cmd_api_key)

    add_bench_commands(commands)
    add_realset_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except (SubtitleError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
