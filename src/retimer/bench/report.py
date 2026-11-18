"""Turns result records into the summary tables."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from retimer.bench.metrics import (
    LOOSE_TOLERANCE_MS,
    SUCCESS_SHARE,
    SUCCESS_TOLERANCE_MS,
    TOLERANCES_MS,
    UNMOVED_MS,
    Summary,
    agent_use,
    stability,
    summarize,
)

_FAMILY_ORDER = (
    "none",
    "constant",
    "rate",
    "blocks",
    "mixed",
    "noise",
    "wrong_content",
    "loose_reference",
    "cold_open",
    "caption_drift",
    "pack",
    "mid_scene",
    "next_episode",
    "signs_reference",
    "karaoke",
    "captions",
    "bilingual",
    "odd_times",
)
_REFERENCE_ORDER = ("audio", "subtitle", "both")


def _grouped(results: Sequence[dict], key: str) -> dict[str, dict[str, list[dict]]]:
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        groups[result["system"]][result[key]].append(result)
    return groups


def build_summary(results: Sequence[dict]) -> dict:
    """Overall, per family and per reference kind, for every system in the results."""
    systems = sorted({result["system"] for result in results})
    by_system = {name: [r for r in results if r["system"] == name] for name in systems}
    families = _grouped(results, "family")
    references = _grouped(results, "reference")
    # Stability is a figure for the systems that were run more than once per case.
    repeated = [name for name in systems if any(result["repeat"] > 0 for result in by_system[name])]
    agents = [agent_use(by_system[name], name) for name in systems]
    return {
        "overall": [summarize(by_system[name], name, "all").model_dump() for name in systems],
        "by_family": [
            summarize(families[name][family], name, family).model_dump()
            for name in systems
            for family in _FAMILY_ORDER
            if family in families[name]
        ],
        "by_reference": [
            summarize(references[name][kind], name, kind).model_dump()
            for name in systems
            for kind in _REFERENCE_ORDER
            if kind in references[name]
        ],
        "stability": [stability(by_system[name], name).model_dump() for name in repeated],
        "agent": [use.model_dump() for use in agents if use is not None],
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _number(value: float | None, digits: int) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    return [_row(header), _row(["---"] * len(header)), *(_row(row) for row in rows), ""]


def _overall_rows(rows: Sequence[Summary], with_group: bool) -> list[list[str]]:
    table = []
    for row in rows:
        cells = [row.system]
        if with_group:
            cells.append(row.group)
        cells += [
            str(row.runs),
            _pct(row.file_success),
            _pct(row.file_success_loose),
            _ms(row.median_error_ms),
            _ms(row.p90_error_ms),
            *(_pct(row.within.get(str(tolerance))) for tolerance in TOLERANCES_MS),
            _pct(row.decision_accuracy),
            str(row.failed),
            "n/a" if row.mean_seconds is None else f"{row.mean_seconds:.1f}",
            f"{row.total_cost_usd:.4f}",
        ]
        table.append(cells)
    return table


def render_markdown(summary: dict, run_info: dict | None = None) -> str:
    header = [
        "Runs",
        "File success",
        f"At {LOOSE_TOLERANCE_MS} ms",
        "Median error (ms)",
        "90th pct error (ms)",
        *(f"Within {tolerance} ms" for tolerance in TOLERANCES_MS),
        "Decisions right",
        "Failed",
        "Mean time (s)",
        "Cost (USD)",
    ]
    lines = ["# Benchmark results", ""]
    if run_info:
        versions = ", ".join(
            f"{name} ({version})" if version else name
            for name, version in run_info.get("systems", {}).items()
        )
        lines += [
            f"Run started {run_info.get('started')}, {run_info.get('cases')} cases, "
            f"{run_info.get('repeats')} run(s) per case. Systems: {versions}.",
            "",
        ]
    for name, reason in summary.get("skipped_systems", {}).items():
        lines += [f"Skipped {name}: {reason}.", ""]
    lines += [
        f"File success is the share of files with at least {SUCCESS_SHARE:.0%} of dialogue lines "
        f"within {SUCCESS_TOLERANCE_MS} ms of their true start, and the column after it is the "
        f"same with {LOOSE_TOLERANCE_MS} ms. Error columns are medians over "
        "files. The tolerance columns pool all dialogue lines. A tool that reports no outcome "
        f"counts as having left a file alone when no line moved more than {UNMOVED_MS} ms.",
        "",
        "## Overall",
        "",
    ]
    overall = [Summary.model_validate(row) for row in summary["overall"]]
    lines += _table(["System", *header], _overall_rows(overall, with_group=False))

    for title, key, label in (
        ("By distortion", "by_family", "Distortion"),
        ("By reference", "by_reference", "Reference"),
    ):
        rows = [Summary.model_validate(row) for row in summary[key]]
        if rows:
            lines += [f"## {title}", ""]
            lines += _table(["System", label, *header], _overall_rows(rows, with_group=True))

    if summary.get("agent"):
        lines += [
            "## What the model was asked",
            "",
            "An agent system calls its model only for files whose draft plan leaves something "
            "open. Turns, time and cost are means over the runs in which it was called. A plan "
            "is kept back when the model's changes put fewer certain lines in place than the "
            "plan it started from.",
            "",
        ]
        lines += _table(
            [
                "System",
                "Model",
                "Runs",
                "Model asked",
                "Turns when asked",
                "Seconds when asked",
                "Cost when asked (USD)",
                "Cost per run (USD)",
                "Plans changed",
                "Kept back",
                "Not finished",
            ],
            [
                [
                    row["system"],
                    row["model"] or "n/a",
                    str(row["runs"]),
                    f"{row['asked']} ({_pct(row['asked'] / row['runs'] if row['runs'] else None)})",
                    _number(row["turns_per_asked"], 1),
                    _number(row["seconds_per_asked"], 1),
                    _number(row["cost_per_asked_usd"], 4),
                    _number(row["cost_per_run_usd"], 5),
                    str(row["changed"]),
                    str(row["kept_back"]),
                    ", ".join(f"{count} {how}" for how, count in sorted(row["unfinished"].items()))
                    or "0",
                ]
                for row in summary["agent"]
            ],
        )

    if summary.get("stability"):
        lines += ["## Stability over repeated runs", ""]
        lines += _table(
            [
                "System",
                "Cases repeated",
                "Same outcome",
                "Same success",
                "Mean spread of median error (ms)",
                "Largest spread (ms)",
            ],
            [
                [
                    row["system"],
                    str(row["cases_repeated"]),
                    _pct(row["outcome_agreement"]),
                    _pct(row["success_agreement"]),
                    _ms(row["mean_spread_ms"]),
                    _ms(row["max_spread_ms"]),
                ]
                for row in summary["stability"]
            ],
        )
    return "\n".join(lines)
