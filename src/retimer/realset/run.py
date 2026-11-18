"""Runs systems over the pairs of a set and keeps what each produced.

A run is a directory under `runs/` in the set. Every result is written as
soon as it is there, so a run that was cut off is continued by starting it
again: what is on record is not run a second time.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from retimer.bench.runners import RunOutput
from retimer.realset.manifest import RUNS_DIR, Manifest, Pair, load_manifest
from retimer.realset.speech import load_speech
from retimer.realset.systems import System, as_case, make_runner, resolve

RESULTS_FILE = "results.jsonl"
RUN_FILE = "run.json"
OUTPUTS_DIR = "outputs"

# Workers are started afresh and not forked: a fork of a process that has
# threads can hang, and the embedding step leaves threads behind.
_WORKERS = multiprocessing.get_context("spawn")
# One manifest and one runner per system in every worker process.
_STATE: dict = {}


def _work(root: str, system: System, pair: Pair, out_dir: str) -> dict:
    """Run one system on one pair, in a worker process, and return the record."""
    set_root = Path(root)
    if "manifest" not in _STATE:
        _STATE["manifest"] = load_manifest(set_root)
    runners = _STATE.setdefault("runners", {})
    if system not in runners:
        runners[system] = make_runner(system, set_root)
    work_dir = Path(out_dir) / OUTPUTS_DIR / system.name / pair.id
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        case = as_case(_STATE["manifest"], pair, set_root, system.reference)
        produced = runners[system].run(case, work_dir)
    except Exception as error:  # a system under test must not stop the run
        produced = RunOutput(outcome="failed", error=f"{type(error).__name__}: {error}"[-600:])
    seconds = produced.seconds or time.monotonic() - started
    output = produced.output_path
    return {
        "pair": pair.id,
        "system": system.name,
        "kind": pair.kind,
        "outcome": produced.outcome,
        "seconds": round(seconds, 3),
        "cost_usd": round(produced.cost_usd, 6),
        "error": produced.error,
        "output": None if output is None else Path(output).relative_to(out_dir).as_posix(),
        "kept_ids": produced.kept_ids,
        "details": produced.details,
    }


def load_results(run_dir: Path) -> list[dict]:
    """The records of a run. Of a pair that was run again, the last one counts."""
    path = run_dir / RESULTS_FILE
    if not path.exists():
        return []
    latest: dict[tuple[str, str], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            record = json.loads(line)
            latest[(record["pair"], record["system"])] = record
    return list(latest.values())


def code_version() -> str | None:
    """The commit of the code that runs, when it runs from a clone."""
    try:
        done = subprocess.run(
            ["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


def select_pairs(
    manifest: Manifest,
    *,
    kinds: list[str] | None = None,
    tuning: bool | None = None,
    only: str | None = None,
    limit: int | None = None,
) -> list[Pair]:
    pairs = [
        pair
        for pair in manifest.pairs
        if (kinds is None or pair.kind in kinds)
        and (tuning is None or pair.tuning == tuning)
        and (only is None or only in pair.id)
    ]
    return pairs if limit is None else pairs[:limit]


def run_systems(
    root: Path,
    name: str,
    system_names: list[str],
    *,
    pairs: list[Pair] | None = None,
    workers: int = 4,
    retry_failed: bool = False,
    again: bool = False,
    agent_limit_usd: float | None = None,
    say: Callable[[str], None] | None = None,
) -> Path:
    """Run the named systems over the pairs and return the directory of the run.

    What is on record is left alone, unless it failed and `retry_failed` is
    set, or `again` is set: then the named systems are run once more and the
    new record of a pair takes the place of the old one.

    The agent's model is billed. When what it cost in this run reaches
    `agent_limit_usd`, the agent's jobs that have not begun are dropped. They
    are not on record then, so the run can be continued with a higher limit.
    """
    say = say or (lambda message: None)
    manifest = load_manifest(root)
    pairs = manifest.pairs if pairs is None else pairs
    systems = resolve(system_names, root)
    agents = {system.name for system in systems if system.agent}
    if agents:
        from retimer.agent.loop import agent_key_env

        if not os.environ.get(agent_key_env()):
            raise ValueError(
                f"{', '.join(sorted(agents))} asks a model: "
                f"set {agent_key_env()} or give a key file"
            )
    run_dir = (root / RUNS_DIR / name).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    recorded = load_results(run_dir)
    on_record = {
        (record["pair"], record["system"])
        for record in recorded
        if not again and not (retry_failed and record["outcome"] == "failed")
    }
    agent_usd = sum(
        record.get("cost_usd") or 0.0 for record in recorded if record["system"] in agents
    )
    spent = agent_limit_usd is not None and agent_usd >= agent_limit_usd
    if agents and spent:
        say(
            f"the agent's model has cost {agent_usd:.2f} USD in this run, which is its limit: "
            "none of its jobs are run"
        )
    transcribed = load_speech(root).transcribed()

    def applies(system: System, pair: Pair) -> bool:
        """Against the audio, another track of the same video is the same job as its main pair."""
        if system.reference == "subtitle":
            return True
        if pair.kind == "other_track" or manifest.video(pair.video).audio is None:
            return False
        # The retimer reads a transcript of the audio, and only a sample has one.
        return not system.sampled or pair.video in transcribed

    jobs = [
        (system, pair)
        for system in systems
        for pair in pairs
        if (pair.id, system.name) not in on_record
        and applies(system, pair)
        and not (system.agent and spent)
    ]
    say(f"{len(jobs)} runs to do, {len(on_record)} on record")
    info_path = run_dir / RUN_FILE
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    info.setdefault("started", datetime.now(UTC).isoformat(timespec="seconds"))
    # The code a system was run with last. A run can be added to after the code moved on.
    info.setdefault("code", {}).update(dict.fromkeys((s.name for s in systems), code_version()))
    info.setdefault("systems", {}).update({system.name: list(system.args) for system in systems})
    for system in systems:
        version = getattr(make_runner(system, root), "version", None)
        if version is not None and system.tool not in info.setdefault("versions", {}):
            info["versions"][system.tool] = version()

    done = failed = dropped = 0
    started = time.monotonic()
    with (
        (run_dir / RESULTS_FILE).open("a", encoding="utf-8") as results,
        ProcessPoolExecutor(max_workers=max(1, workers), mp_context=_WORKERS) as pool,
    ):
        futures = {
            pool.submit(_work, str(root.resolve()), system, pair, str(run_dir)): system
            for system, pair in jobs
        }
        for future in as_completed(futures):
            if future.cancelled():
                dropped += 1
                continue
            record = future.result()
            results.write(json.dumps(record, ensure_ascii=False) + "\n")
            results.flush()
            done += 1
            failed += record["outcome"] == "failed"
            if futures[future].agent and agent_limit_usd is not None:
                before = agent_usd
                agent_usd += record["cost_usd"]
                if before < agent_limit_usd <= agent_usd:
                    say(
                        f"the agent's model has cost {agent_usd:.2f} USD, the limit of this "
                        "run: its jobs that have not begun are dropped"
                    )
                    for other, system in futures.items():
                        if system.agent:
                            other.cancel()
            if done % 50 == 0 or done + dropped == len(jobs):
                minutes = (time.monotonic() - started) / 60
                say(f"[{done}/{len(jobs)}] {failed} failed, {minutes:.1f} min")
    if dropped:
        say(f"{dropped} jobs of the agent were not run")
    info["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    info_path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return run_dir
