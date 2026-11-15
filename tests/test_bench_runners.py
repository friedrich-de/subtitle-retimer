import stat
import sys

import pytest

from retimer import load
from retimer.bench.cases import generate_cases, load_case
from retimer.bench.harness import load_results, run_benchmark, run_one
from retimer.bench.runners import (
    AlassRunner,
    FfsubsyncRunner,
    InputRunner,
    OracleRunner,
    builtin_runners,
)

from .bench_helpers import write_seeds

# A stand-in tool: reads `REF -i IN -o OUT [--fail] [--shift MS]` and shifts the file.
FAKE_TOOL = """#!{python}
import sys
from retimer import Plan, apply_plan, load, save

args = sys.argv[1:]
if "--version" in args:
    print("fake 1.0")
    raise SystemExit(0)
if "--fail" in args:
    print("could not sync", file=sys.stderr)
    raise SystemExit(3)
source = args[args.index("-i") + 1]
output = args[args.index("-o") + 1]
ms = int(args[args.index("--shift") + 1]) if "--shift" in args else 0
plan = Plan.model_validate({{"timing_ops": [{{"op": "shift", "ms": ms}}]}})
save(apply_plan(load(source), plan).subtitle, output)
"""


@pytest.fixture
def cases(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    return tmp_path / "cases"


@pytest.fixture
def fake_tool(tmp_path, monkeypatch):
    path = tmp_path / "fake-ffsubsync"
    path.write_text(FAKE_TOOL.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("RETIMER_FFSUBSYNC", str(path))
    return path


def test_the_oracle_solves_every_generated_case(cases, tmp_path):
    summary = run_benchmark(
        [load_case(path.parent) for path in sorted(cases.glob("*/case.json"))],
        [OracleRunner(), InputRunner()],
        tmp_path / "results",
        workers=2,
    )
    rows = {row["system"]: row for row in summary["overall"]}
    assert rows["oracle"]["file_success"] == 1.0
    assert rows["oracle"]["decision_accuracy"] == 1.0
    assert rows["oracle"]["median_error_ms"] == 0
    assert rows["input"]["file_success"] < 0.2
    assert {row["group"] for row in summary["by_family"]} >= {"constant", "blocks", "noise"}
    assert (tmp_path / "results" / "summary.md").read_text().startswith("# Benchmark results")
    assert len(load_results(tmp_path / "results")) == 2 * len(list(cases.glob("*/case.json")))


def test_a_command_tool_is_run_and_scored(cases, tmp_path, fake_tool):
    case = load_case(cases / "alpha.en.subtitle-ja.late-8s")
    runner = FfsubsyncRunner("fake", ("--shift", "-8000"))
    assert runner.unavailable() is None
    assert runner.version() == "fake 1.0"
    result = run_one(case, runner, tmp_path / "work")
    assert result["outcome"] == "synced"
    assert result["metrics"]["success"]
    assert result["error"] is None
    assert load(tmp_path / "work" / "target.srt").lines[0].start_ms == 20_000


def test_a_tool_that_leaves_the_file_alone_is_seen_as_such(cases, tmp_path, fake_tool):
    case = load_case(cases / "alpha.en.subtitle-ja.in-sync")
    result = run_one(case, FfsubsyncRunner("fake"), tmp_path / "work")
    assert result["outcome"] == "already_in_sync"
    assert result["metrics"]["decision_correct"]


def test_a_failing_tool_is_recorded_not_raised(cases, tmp_path, fake_tool):
    case = load_case(cases / "alpha.en.subtitle-ja.late-8s")
    result = run_one(case, FfsubsyncRunner("fake", ("--fail",)), tmp_path / "work")
    assert result["outcome"] == "failed"
    assert "exit code 3" in result["error"] and "could not sync" in result["error"]
    assert not result["metrics"]["success"]


def test_a_missing_tool_is_skipped_with_a_reason(cases, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("RETIMER_ALASS", raising=False)
    runner = AlassRunner()
    assert "not installed" in runner.unavailable()
    summary = run_benchmark(
        [load_case(cases / "alpha.en.subtitle-ja.late-8s")], [runner], tmp_path / "results"
    )
    assert summary["overall"] == []
    assert "alass" in summary["skipped_systems"]


def test_repeats_are_stored_and_summarized(cases, tmp_path, fake_tool):
    summary = run_benchmark(
        [load_case(cases / "alpha.en.subtitle-ja.late-8s")],
        [FfsubsyncRunner("fake", ("--shift", "-8000"))],
        tmp_path / "results",
        repeats=3,
    )
    assert summary["overall"][0]["runs"] == 3
    assert summary["stability"][0]["cases_repeated"] == 1
    assert summary["stability"][0]["outcome_agreement"] == 1.0


def test_commands_follow_each_tools_argument_order(tmp_path, monkeypatch):
    monkeypatch.setenv("RETIMER_FFSUBSYNC", "/opt/ffs")
    monkeypatch.setenv("RETIMER_ALASS", "/opt/alass")
    ref, target, out = tmp_path / "ref.ogg", tmp_path / "in.srt", tmp_path / "out.srt"
    assert FfsubsyncRunner("x", ("--split-penalty",)).command(ref, target, out) == [
        "/opt/ffs",
        str(ref),
        "-i",
        str(target),
        "-o",
        str(out),
        "--split-penalty",
    ]
    assert AlassRunner().command(ref, target, out) == [
        "/opt/alass",
        str(ref),
        str(target),
        str(out),
    ]


@pytest.mark.parametrize("name", ["ffsubsync", "alass"])
def test_the_real_tools_fix_a_constant_offset_when_installed(name, cases, tmp_path):
    runner = builtin_runners()[name]
    if runner.unavailable():
        pytest.skip(f"{name} is not installed")
    case = load_case(cases / "alpha.en.subtitle-ja.late-8s")
    result = run_one(case, runner, tmp_path / "work")
    assert result["error"] is None
    assert result["metrics"]["success"]
