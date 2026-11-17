import pytest

from retimer import Options, Plan, PlanError, apply_plan, build_views, dumps, loads, validate_plan
from retimer.plan import Shift, parse_timestamp


def times(subtitle):
    return {line.id: (line.start_ms, line.end_ms) for line in subtitle.lines}


def codes(subtitle, plan):
    return [issue.code for issue in validate_plan(subtitle, plan)]


def plan_of(*timing_ops, content_ops=()):
    return Plan.model_validate({"timing_ops": list(timing_ops), "content_ops": list(content_ops)})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("end", None),
        ("start", None),
        (1500, 1500),
        ("00:11:42.300", 702_300),
        ("0:00:01,5", 1_500),
        ("01:02", 62_000),
        ("1:00:00.000", 3_600_000),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


def test_plan_from_the_design_document_parses():
    plan = Plan.model_validate(
        {
            "outcome": "synced",
            "content_ops": [
                {"op": "remove_lines", "reason": "karaoke", "ids": [1, 2]},
                {"op": "strip_text", "what": "hearing_impaired", "ids": [6]},
            ],
            "timing_ops": [
                {"op": "scale", "factor": 1.04271, "reason": "25 fps source"},
                {"op": "shift", "from": "00:00:00.000", "to": "00:11:42.300", "ms": 1210},
                {"op": "shift", "from": "00:11:42.300", "to": "end", "ms": 14830},
            ],
            "confidence": 0.97,
        }
    )
    assert plan.timing_ops[1].to_ms == 702_300
    assert plan.timing_ops[2].to_ms is None
    assert Plan.model_validate_json(plan.to_json()) == plan


def test_constant_shift_moves_every_line_and_keeps_durations(srt):
    result = apply_plan(srt, plan_of({"op": "shift", "ms": 2500}))
    before, after = times(srt), times(result.subtitle)
    assert all(after[i] == (start + 2500, end + 2500) for i, (start, end) in before.items())
    assert result.summary.moved == 6
    assert result.summary.lines_out == 6


def test_the_input_is_left_untouched(srt):
    before = dumps(srt)
    apply_plan(srt, plan_of({"op": "shift", "ms": 2500}))
    assert dumps(srt) == before


def test_block_shifts_select_lines_by_original_start(srt):
    result = apply_plan(
        srt,
        plan_of(
            {"op": "shift", "from": "start", "to": "00:00:07.500", "ms": 1000},
            {"op": "shift", "from": "00:00:07.500", "to": "end", "ms": 9000},
        ),
    )
    after = times(result.subtitle)
    assert after[2] == (6_500, 8_000)
    assert after[3] == (16_500, 18_000)
    assert after[5] == (22_000, 24_000)


def test_scale_multiplies_times(srt):
    result = apply_plan(srt, plan_of({"op": "scale", "factor": 1.04271}))
    assert times(result.subtitle)[5] == (13_555, 15_641)


def test_scale_runs_before_shift_whatever_the_order(srt):
    scale = {"op": "scale", "factor": 1.04271}
    shift = {"op": "shift", "from": "00:00:10.000", "to": "end", "ms": 500}
    first = apply_plan(srt, plan_of(scale, shift))
    second = apply_plan(srt, plan_of(shift, scale))
    assert times(first.subtitle) == times(second.subtitle)
    # Line 4 starts at 10.000 in the original, so the range holds it after scaling too.
    assert times(first.subtitle)[4] == (
        round(10_000 * 1.04271) + 500,
        round(12_000 * 1.04271) + 500,
    )
    assert times(first.subtitle)[3] == (round(7_500 * 1.04271), round(9_000 * 1.04271))


def test_content_ops_run_on_the_original(ass):
    views = build_views(
        ass, Options(remove_hearing_impaired=True, remove_karaoke=True, strip_formatting=True)
    )
    result = apply_plan(ass, Plan(content_ops=views.content_ops))
    texts = {line.id: line.text for line in result.subtitle.lines}
    assert sorted(texts) == [0, 3, 4, 6, 7, 9, 11, 12]
    assert texts[4] == "Is anyone there?"
    assert texts[6] == "I knew it.\\NNobody came."
    assert texts[7] == "Central Station"
    assert texts[9] == "We should go."
    assert result.summary.removed == {"karaoke": 3, "hearing_impaired": 1, "drawing": 1}
    assert result.summary.stripped == {"hearing_impaired": 2, "formatting": 3}
    assert result.summary.lines_in == 13
    assert result.summary.lines_out == 8


def test_strip_without_ids_covers_the_file_and_drops_what_it_empties(srt):
    plan = plan_of(content_ops=[{"op": "strip_text", "what": "hearing_impaired"}])
    result = apply_plan(srt, plan)
    assert [line.id for line in result.subtitle.lines] == [0, 1, 2, 4, 5]
    assert result.subtitle.lines[0].text == "- Hello?"
    assert result.summary.emptied_by_strip == 1


def test_result_can_be_written_in_either_format(ass):
    result = apply_plan(ass, plan_of({"op": "shift", "ms": 1000}))
    assert loads(dumps(result.subtitle, "srt")).format == "srt"
    assert "0:01:31.00,0:01:33.50,Default" in dumps(result.subtitle)


def test_invalid_structure_is_reported(srt):
    assert codes(srt, plan_of({"op": "scale", "factor": 2.0})) == ["scale_out_of_range"]
    assert codes(
        srt, plan_of({"op": "scale", "factor": 1.04}, {"op": "scale", "factor": 0.96})
    ) == ["multiple_scales"]
    assert codes(
        srt,
        plan_of(
            {"op": "shift", "to": "00:00:08.000", "ms": 100},
            {"op": "shift", "from": "00:00:07.000", "ms": 200},
        ),
    ) == ["overlapping_ranges"]
    assert codes(srt, plan_of({"op": "shift", "from": 5000, "to": 5000, "ms": 1})) == [
        "empty_range"
    ]
    assert codes(
        srt, plan_of(content_ops=[{"op": "remove_lines", "reason": "x", "ids": [99]}])
    ) == ["unknown_ids"]


def test_negative_times_are_refused_until_the_lines_are_removed(srt):
    shift = {"op": "shift", "ms": -2000}
    issues = validate_plan(srt, plan_of(shift))
    assert [(issue.code, issue.ids) for issue in issues] == [("negative_time", [0])]
    removal = {"op": "remove_lines", "reason": "unmatched_leading", "ids": [0]}
    assert codes(srt, plan_of(shift, content_ops=[removal])) == []


def test_colliding_blocks_are_refused(srt):
    plan = plan_of(
        {"op": "shift", "to": "00:00:07.500", "ms": 5000},
        {"op": "shift", "from": "00:00:07.500", "ms": 0},
    )
    issues = validate_plan(srt, plan)
    # Lines 3 and 4 would start before line 2, which now starts at 10.500.
    assert [(issue.code, issue.ids) for issue in issues] == [("reordered", [3, 4])]
    with pytest.raises(PlanError) as error:
        apply_plan(srt, plan)
    assert error.value.issues == issues


def test_strip_leaves_comment_events_alone(ass):
    plan = plan_of(content_ops=[{"op": "strip_text", "what": "formatting"}])
    result = apply_plan(ass, plan)
    assert result.subtitle.lines[0].is_comment
    assert result.subtitle.lines[0].text == "Translation notes go here"
    # The drawing has nothing left once its tags are gone, so it is dropped.
    assert 8 not in [line.id for line in result.subtitle.lines]


def test_a_comment_at_the_start_does_not_stand_in_the_way_of_moving_earlier(ass):
    # Editors keep notes in comment events at time zero. Nobody sees them.
    assert ass.lines[0].is_comment and ass.lines[0].start_ms == 0
    plan = Plan(timing_ops=[Shift(ms=-500)])

    assert validate_plan(ass, plan) == []
    result = apply_plan(ass, plan).subtitle

    assert (result.lines[0].start_ms, result.lines[0].end_ms) == (0, 0)
    assert result.lines[1].start_ms == 500
