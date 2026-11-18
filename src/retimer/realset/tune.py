"""Chooses the settings of the tools on a fixed tenth of the main pairs.

A tool's defaults are not made for every kind of file. So each tool gets a
second run with the setting that verified the most files on the tuning pairs,
and the report then counts both runs on the other nine tenths only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from retimer.realset.judge import judge_run
from retimer.realset.manifest import TUNING_FILE, load_manifest
from retimer.realset.run import run_systems, select_pairs
from retimer.realset.systems import CANDIDATES, SYSTEMS, candidate_systems

TUNING_RUN = "tuning"


def choose(judgments: list[dict]) -> dict[str, dict]:
    """The best setting of every tuned system, from the judgments of the candidates.

    Best is the largest share of verified files. Between equals the one with
    more windows in tolerance wins, and then the one that is tried first,
    which makes a tie go to the default.
    """
    verified: dict[str, int] = defaultdict(int)
    windows_ok: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    for entry in judgments:
        if "~" not in entry["system"]:
            continue
        count[entry["system"]] += 1
        verified[entry["system"]] += entry["verdict"] == "verified"
        windows_ok[entry["system"]] += entry.get("judged_windows", 0) - entry.get("off_windows", 0)
    chosen = {}
    for system in SYSTEMS:
        if not system.tuned:
            continue
        base = system.tool if system.reference == "subtitle" else f"{system.tool}-audio"
        settings = CANDIDATES[(system.tool, system.reference)]
        tried = [name for name in settings if count.get(f"{base}~{name}")]
        if not tried:
            continue
        best = max(
            tried,
            key=lambda name: (
                verified[f"{base}~{name}"] / count[f"{base}~{name}"],
                windows_ok[f"{base}~{name}"],
                -tried.index(name),
            ),
        )
        chosen[system.name] = {
            "setting": best,
            "args": list(settings[best]),
            "pairs": count[f"{base}~{best}"],
            "verified": {
                name: round(verified[f"{base}~{name}"] / count[f"{base}~{name}"], 4)
                for name in tried
            },
        }
    return chosen


def tune(root: Path, *, workers: int = 4, say: Callable[[str], None] | None = None) -> dict:
    """Run every candidate setting on the tuning pairs, choose, and write the choice to the set."""
    pairs = select_pairs(load_manifest(root), kinds=["main"], tuning=True)
    names = [system.name for system in candidate_systems()]
    run_dir = run_systems(root, TUNING_RUN, names, pairs=pairs, workers=workers, say=say)
    chosen = choose(judge_run(root, run_dir, workers=workers, say=say))
    (root / TUNING_FILE).write_text(json.dumps(chosen, indent=1), encoding="utf-8")
    return chosen
