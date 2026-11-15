"""Runs every system over every case and stores the results."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from retimer.bench.cases import LoadedCase
from retimer.bench.metrics import evaluate
from retimer.bench.report import build_summary, render_markdown
from retimer.bench.runners import Runner, RunOutput
from retimer.io import SubtitleError, load

RESULTS_FILE = "results.jsonl"
SUMMARY_JSON = "summary.json"
SUMMARY_MD = "summary.md"
RUN_FILE = "run.json"


def run_one(case: LoadedCase, runner: Runner, work_dir: Path, repeat: int = 0) -> dict:
    """Run one system on one case and return the result record."""
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        produced = runner.run(case, work_dir)
    except Exception as error:  # a system under test must not stop the benchmark
        produced = RunOutput(outcome="failed", error=f"{type(error).__name__}: {error}")
    if not produced.seconds:
        produced.seconds = time.monotonic() - started

    target = load(case.target_path)
    output = None
    if produced.output_path is not None:
        try:
            output = load(produced.output_path)
        except (OSError, SubtitleError) as error:
            produced.outcome = "failed"
            produced.error = f"unreadable output: {error}"
    spec = case.case
    metrics = evaluate(
        spec.truth,
        target,
        output,
        outcome=produced.outcome,
        expected_outcome=spec.expected_outcome,
        output_ids=produced.kept_ids if output is not None else None,
        remove_kinds=spec.remove_kinds(),
        drop_unmatched=spec.options.drop_unmatched,
    )
    return {
        "case": spec.id,
        "seed": spec.seed,
        "family": spec.family,
        "recipe": spec.recipe,
        "reference": spec.reference.kind,
        "target_language": spec.target_language,
        "reference_language": spec.reference.subtitle_language or spec.reference.audio_language,
        "system": runner.name,
        "repeat": repeat,
        "outcome": metrics.outcome,
        "seconds": round(produced.seconds, 3),
        "cost_usd": produced.cost_usd,
        "error": produced.error,
        "metrics": metrics.model_dump(mode="json"),
        "details": produced.details,
    }


def run_benchmark(
    cases: Sequence[LoadedCase],
    runners: Sequence[Runner],
    out_dir: Path,
    *,
    repeats: int = 1,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the systems over the cases, write the result files and return the summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda message: None)

    skipped: dict[str, str] = {}
    active: list[Runner] = []
    for runner in runners:
        reason = runner.unavailable()
        if reason:
            skipped[runner.name] = reason
            say(f"skipping {runner.name}: {reason}")
        else:
            active.append(runner)

    jobs = [
        (case, runner, repeat)
        for runner in active
        for case in cases
        if runner.supports(case)
        for repeat in range(repeats)
    ]
    results: list[dict] = []
    lock = threading.Lock()
    results_path = out_dir / RESULTS_FILE
    results_path.write_text("", encoding="utf-8")

    def work(job: tuple[LoadedCase, Runner, int]) -> None:
        case, runner, repeat = job
        work_dir = out_dir / "outputs" / runner.name / case.case.id
        if repeats > 1:
            work_dir = work_dir / f"repeat-{repeat}"
        record = run_one(case, runner, work_dir, repeat)
        with lock:
            results.append(record)
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            metrics = record["metrics"]
            status = "ok " if metrics["success"] else "bad"
            median = metrics["median_error_ms"]
            shown = "" if median is None else f" median {median:.0f} ms"
            say(
                f"[{len(results)}/{len(jobs)}] {status} {runner.name:<16} {case.case.id}"
                f"  {record['outcome']}{shown}"
            )

    started = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, jobs))

    results.sort(key=lambda r: (r["system"], r["case"], r["repeat"]))
    results_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in results),
        encoding="utf-8",
    )
    summary = build_summary(results)
    summary["skipped_systems"] = skipped
    run_info = {
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "cases": len(cases),
        "repeats": repeats,
        "systems": {runner.name: getattr(runner, "version", lambda: None)() for runner in active},
        "skipped_systems": skipped,
    }
    (out_dir / RUN_FILE).write_text(json.dumps(run_info, indent=1), encoding="utf-8")
    (out_dir / SUMMARY_JSON).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out_dir / SUMMARY_MD).write_text(render_markdown(summary, run_info), encoding="utf-8")
    return summary


def load_results(directory: Path) -> list[dict]:
    path = directory / RESULTS_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
