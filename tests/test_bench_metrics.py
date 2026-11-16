import pytest

from retimer import Plan, apply_plan, loads
from retimer.bench.distort import Distortion, Noise, distort
from retimer.bench.metrics import (
    agent_use,
    evaluate,
    infer_outcome,
    match_output_ids,
    percentile,
    stability,
    summarize,
)
from retimer.bench.report import build_summary, render_markdown

from .bench_helpers import make_base


def shifted(subtitle, ms, **range_):
    plan = Plan.model_validate({"timing_ops": [{"op": "shift", "ms": ms, **range_}]})
    return apply_plan(subtitle, plan).subtitle


def test_percentile_interpolates():
    assert percentile([10], 0.9) == 10
    assert percentile([0, 100], 0.5) == 50
    assert percentile(list(range(11)), 0.9) == 9
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_a_perfect_sync_scores_full_marks():
    base = make_base()
    case = distort(base, Distortion(offset_ms=3_000))
    output = shifted(case.subtitle, -3_000)
    metrics = evaluate(case.truth, case.subtitle, output, outcome=None, expected_outcome="synced")
    assert metrics.outcome == "synced"
    assert metrics.lines_scored == 40
    assert metrics.median_error_ms == 0
    assert metrics.within == {"100": 1.0, "250": 1.0, "500": 1.0}
    assert metrics.success
    assert metrics.decision_correct is None


def test_errors_are_measured_against_the_true_start():
    base = make_base()
    case = distort(base, Distortion(offset_ms=3_000))
    # Ten of forty lines are left 300 ms late.
    output = shifted(shifted(case.subtitle, -3_000), 300, **{"from": 140_000})
    metrics = evaluate(case.truth, case.subtitle, output, outcome=None, expected_outcome="synced")
    assert metrics.median_error_ms == 0
    assert metrics.p90_error_ms == 300
    assert metrics.max_error_ms == 300
    assert metrics.within == {"100": 0.75, "250": 0.75, "500": 1.0}
    assert not metrics.success


def test_the_untouched_input_fails_a_shifted_case():
    case = distort(make_base(), Distortion(offset_ms=3_000))
    metrics = evaluate(
        case.truth, case.subtitle, case.subtitle, outcome=None, expected_outcome="synced"
    )
    assert metrics.outcome == "already_in_sync"
    assert metrics.median_error_ms == 3_000
    assert metrics.median_signed_error_ms == 3_000
    assert not metrics.success


def test_removed_dialogue_counts_as_missing():
    case = distort(make_base(), Distortion())
    plan = Plan.model_validate(
        {"content_ops": [{"op": "remove_lines", "reason": "test", "ids": [0, 1, 2, 3]}]}
    )
    output = apply_plan(case.subtitle, plan).subtitle
    metrics = evaluate(
        case.truth,
        case.subtitle,
        output,
        outcome="already_in_sync",
        expected_outcome="already_in_sync",
        output_ids=output.ids,
    )
    assert metrics.lines_missing == 4
    assert metrics.within["250"] == 0.9
    assert not metrics.success
    assert metrics.decision_correct


def test_no_output_fails_the_file():
    case = distort(make_base(), Distortion(offset_ms=500))
    metrics = evaluate(case.truth, case.subtitle, None, outcome=None, expected_outcome="synced")
    assert metrics.outcome == "failed"
    assert metrics.lines_missing == 40
    assert not metrics.success


def test_wrong_content_is_judged_by_the_decision_alone():
    base = make_base()
    rejected = evaluate(
        [], base, None, outcome="rejected_no_match", expected_outcome="rejected_no_match"
    )
    assert rejected.success and rejected.decision_correct
    synced = evaluate([], base, base, outcome=None, expected_outcome="rejected_no_match")
    assert not synced.success and synced.decision_correct is False


def test_in_sync_cases_expect_the_file_to_be_left_alone():
    case = distort(make_base(), Distortion())
    alone = evaluate(
        case.truth,
        case.subtitle,
        shifted(case.subtitle, 40),
        outcome=None,
        expected_outcome="already_in_sync",
    )
    assert alone.decision_correct and alone.success
    moved = evaluate(
        case.truth,
        case.subtitle,
        shifted(case.subtitle, 180),
        outcome=None,
        expected_outcome="already_in_sync",
    )
    assert moved.decision_correct is False and moved.success


def test_trailing_lines_and_options_decide_the_content():
    noise = Noise(sound_descriptions=3, karaoke_lines=4, trailing_lines=3)
    case = distort(make_base(pause_every=8), Distortion(noise=noise), seed=1)
    kept_all = evaluate(
        case.truth, case.subtitle, case.subtitle, outcome=None, expected_outcome="synced"
    )
    assert (kept_all.absent_lines, kept_all.absent_removed) == (3, 0)
    assert kept_all.decision_correct is False

    trimmed = apply_plan(case.subtitle, case.ideal_plan).subtitle
    good = evaluate(
        case.truth,
        case.subtitle,
        trimmed,
        outcome="synced",
        expected_outcome="synced",
        output_ids=trimmed.ids,
    )
    assert good.absent_removed == 3 and good.decision_correct and good.content_ok

    # With the options asking for clean output, the song and the sounds must go too.
    strict = evaluate(
        case.truth,
        case.subtitle,
        trimmed,
        outcome="synced",
        expected_outcome="synced",
        output_ids=trimmed.ids,
        remove_kinds={"karaoke", "sound_description"},
    )
    assert strict.content_ok is False
    assert strict.within_all_lines["250"] == 1.0

    kept_on_request = evaluate(
        case.truth,
        case.subtitle,
        case.subtitle,
        outcome=None,
        expected_outcome="synced",
        drop_unmatched=False,
    )
    assert kept_on_request.decision_correct is None and kept_on_request.content_ok


def test_output_lines_are_matched_by_text_when_the_tool_reorders_them():
    target = loads(
        "1\n00:00:05,000 --> 00:00:06,000\nYes.\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nFirst\n\n"
        "3\n00:00:08,000 --> 00:00:09,000\nYes.\n"
    )
    # The tool sorted the file by time, shifted it, and dropped nothing.
    output = loads(
        "1\n00:00:02,000 --> 00:00:03,000\nFirst\n\n"
        "2\n00:00:06,000 --> 00:00:07,000\nYes.\n\n"
        "3\n00:00:09,000 --> 00:00:10,000\nYes.\n\n"
        "4\n00:00:11,000 --> 00:00:12,000\nAdded by the tool\n"
    )
    ids = match_output_ids(target, output)
    assert ids == [1, 0, 2, None]
    assert infer_outcome(target, output, ids) == "synced"


def record(case, system, metrics, repeat=0, seconds=1.0, cost=0.0):
    return {
        "case": case,
        "system": system,
        "repeat": repeat,
        "seconds": seconds,
        "cost_usd": cost,
        "metrics": metrics.model_dump(),
    }


def test_summary_and_stability():
    case = distort(make_base(), Distortion(offset_ms=3_000))
    good = evaluate(
        case.truth,
        case.subtitle,
        shifted(case.subtitle, -3_000),
        outcome=None,
        expected_outcome="synced",
    )
    bad = evaluate(
        case.truth, case.subtitle, case.subtitle, outcome=None, expected_outcome="synced"
    )
    wrong = evaluate(
        [], case.subtitle, None, outcome="synced", expected_outcome="rejected_no_match"
    )
    results = [
        record("a", "tool", good, seconds=2.0, cost=0.01),
        record("a", "tool", bad, repeat=1, seconds=4.0, cost=0.03),
        record("b", "tool", good),
        record("c", "tool", wrong),
    ]
    summary = summarize(results, "tool", "all")
    assert summary.runs == 4
    assert summary.file_success == pytest.approx(2 / 3)
    assert summary.median_error_ms == 0
    assert summary.within["250"] == pytest.approx(2 / 3)
    assert (summary.decision_cases, summary.decision_accuracy) == (1, 0.0)
    assert summary.mean_seconds == 2.0
    assert summary.total_cost_usd == pytest.approx(0.04)

    spread = stability(results, "tool")
    assert spread.cases_repeated == 1
    assert spread.outcome_agreement == 0.0
    assert spread.max_spread_ms == 3_000


def test_the_summary_says_what_the_model_was_asked():
    case = distort(make_base(), Distortion(offset_ms=3_000))
    good = evaluate(
        case.truth,
        case.subtitle,
        shifted(case.subtitle, -3_000),
        outcome=None,
        expected_outcome="synced",
    )

    def traced(name, **trace):
        base = {"family": "constant", "reference": "subtitle"}
        agent = {"model": "claude-haiku-5-5", "consulted": False, "finished": "not_needed"}
        report = {"agent": {**agent, **trace}}
        return {**record(name, "agent-haiku", good), **base, "details": {"report": report}}

    asked = {"consulted": True, "finished": "submitted", "accepted": True}
    kept_back = {**asked, "changed": True, "accepted": False}
    out_of_budget = {**asked, "finished": "budget"}
    results = [
        traced("a"),
        traced("b", **asked, turns=2, seconds=4.0, cost_usd=0.001, changed=True),
        traced("c", **kept_back, turns=4, seconds=8.0, cost_usd=0.003),
        traced("d", **out_of_budget, turns=8, seconds=9.0, cost_usd=0.002),
    ]

    use = agent_use(results, "agent-haiku")
    assert (use.model, use.runs, use.asked) == ("claude-haiku-5-5", 4, 3)
    assert use.turns_per_asked == pytest.approx(14 / 3)
    assert use.seconds_per_asked == pytest.approx(7.0)
    assert use.cost_per_asked_usd == pytest.approx(0.002)
    assert use.cost_per_run_usd == pytest.approx(0.0015)
    assert (use.changed, use.kept_back) == (2, 1)
    assert use.unfinished == {"budget": 1}

    # A system without a model has no such figures, and no section in the summary.
    tool = [{**record("a", "tool", good), "family": "constant", "reference": "subtitle"}]
    assert agent_use(tool, "tool") is None
    assert "## What the model was asked" not in render_markdown(build_summary(tool))

    summary = build_summary(results + tool)
    assert [row["system"] for row in summary["agent"]] == ["agent-haiku"]
    assert summary["stability"] == []
    again = {**traced("b", **asked, turns=3, seconds=5.0, cost_usd=0.001), "repeat": 1}
    assert [row["system"] for row in build_summary([*results, again, *tool])["stability"]] == [
        "agent-haiku"
    ]
    text = render_markdown(summary)
    assert "## What the model was asked" in text
    assert "| agent-haiku | claude-haiku-5-5 | 4 | 3 (75%) | 4.7 | 7.0 | 0.0020 | 0.00150 " in text
    assert "| 2 | 1 | 1 budget |" in text
