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
    from retimer.pipeline import sync

    if not args.audio and not args.reference:
        print("error: give --audio, --reference, or both", file=sys.stderr)
        return 1
    try:
        result = sync(
            load(args.file),
            audio=Path(args.audio) if args.audio else None,
            reference=load(args.reference) if args.reference else None,
            options=_options(args),
            audio_language=args.audio_language,
        )
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
        "warnings": report.warnings,
        "cost_usd": round(report.total_cost_usd, 5),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return EXIT_REJECTED if result.subtitle is None else 0


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
    syncer.add_argument("--reference", help="a subtitle that is in sync, in any language")
    syncer.add_argument("--audio-language", help="language spoken in the audio, if known")
    _add_content_options(syncer)
    syncer.add_argument(
        "--keep-unmatched",
        action="store_true",
        help="keep leading and trailing lines that the reference has no counterpart for",
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

    add_bench_commands(commands)
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
