"""Command line access to the subtitle core, for trying the tools on real files."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from retimer.classify import classify_lines
from retimer.engine import PlanError, apply_plan
from retimer.inspection import inspect_subtitle, sample_lines
from retimer.io import SUPPORTED_FORMATS, SubtitleError, load, save
from retimer.plan import Plan
from retimer.views import Options, build_views


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


def _cmd_clean(args: argparse.Namespace) -> int:
    subtitle = load(args.file)
    options = Options(
        remove_hearing_impaired=args.remove_hearing_impaired,
        remove_karaoke=args.remove_karaoke,
        strip_formatting=args.strip_formatting,
        remove_symbols=args.remove_symbols,
    )
    views = build_views(subtitle, options, classify_lines(subtitle))
    plan = Plan(outcome="already_in_sync", content_ops=views.content_ops)
    return _apply(subtitle, plan, args)


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
    cleaner.add_argument("--remove-hearing-impaired", action="store_true")
    cleaner.add_argument("--remove-karaoke", action="store_true")
    cleaner.add_argument("--strip-formatting", action="store_true")
    cleaner.add_argument("--remove-symbols", action="store_true")
    _add_output(cleaner)
    cleaner.set_defaults(run=_cmd_clean)

    apply = commands.add_parser("apply", help="run a plan file on a subtitle")
    apply.add_argument("file")
    apply.add_argument("plan", help="path to a plan in JSON")
    _add_output(apply)
    apply.set_defaults(run=_cmd_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except SubtitleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
