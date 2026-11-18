import json

from retimer import load
from retimer.cli import main

from .conftest import DATA


def test_inspect_prints_a_json_report(capsys):
    assert main(["inspect", str(DATA / "sample.ass")]) == 0
    assert json.loads(capsys.readouterr().out)["line_count"] == 13


def test_clean_writes_the_cleaned_file(tmp_path, capsys):
    out = tmp_path / "out.srt"
    code = main(["clean", str(DATA / "sample.srt"), "--remove-hearing-impaired", "-o", str(out)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["removed"] == {"hearing_impaired": 1}
    assert [line.text for line in load(out).lines][0] == "- Hello?"


def test_clean_removes_signs_and_credits(tmp_path, capsys):
    out = tmp_path / "out.ass"
    code = main(["clean", str(DATA / "sample.ass"), "--remove-signs-and-credits", "-o", str(out)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["removed"] == {"signs": 2, "credits": 1}
    texts = [line.text for line in load(out).lines]
    assert not any("Central Station" in text or "Subtitles by" in text for text in texts)
    assert "Good morning." in texts


def test_apply_runs_a_plan_file_and_converts_the_format(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"timing_ops": [{"op": "shift", "ms": 1500}]}))
    out = tmp_path / "out.ass"
    code = main(["apply", str(DATA / "sample.srt"), str(plan), "-o", str(out), "--format", "ass"])
    assert code == 0
    result = load(out)
    assert result.format == "ass"
    assert result.lines[0].start_ms == 2_500


def test_apply_reports_plan_issues(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"timing_ops": [{"op": "shift", "ms": -5000}]}))
    code = main(["apply", str(DATA / "sample.srt"), str(plan), "-o", str(tmp_path / "x.srt")])
    assert code == 2
    assert "negative_time" in capsys.readouterr().err


def test_unreadable_input_is_an_error(tmp_path, capsys):
    bad = tmp_path / "bad.srt"
    bad.write_text("not a subtitle")
    assert main(["inspect", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err
