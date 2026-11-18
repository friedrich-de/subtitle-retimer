import json

from retimer.cli import main

from .bench_helpers import write_seeds


def test_run_generates_cases_runs_systems_and_stores_results(tmp_path, capsys):
    seeds = write_seeds(tmp_path / "seeds")
    args = ["--seeds", str(seeds), "--media", str(tmp_path / "media")]
    cases = ["--cases", str(tmp_path / "cases")]
    out = tmp_path / "results"
    code = main(
        ["bench", "run", *args, *cases, "--systems", "input,oracle", "--only", "alpha.en"]
        + ["--out", str(out)]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "| oracle |" in printed and "## By distortion" in printed
    summary = json.loads((out / "summary.json").read_text())
    assert {row["system"] for row in summary["overall"]} == {"input", "oracle"}
    assert (out / "outputs" / "oracle" / "alpha.en.subtitle-ja.late-8s" / "target.srt").exists()

    assert main(["bench", "report", str(out)]) == 0
    assert "| oracle |" in capsys.readouterr().out


def test_unknown_systems_and_empty_case_sets_are_errors(tmp_path, capsys):
    seeds = write_seeds(tmp_path / "seeds")
    base = ["--seeds", str(seeds), "--media", str(tmp_path / "m"), "--cases", str(tmp_path / "c")]
    assert main(["bench", "run", *base, "--systems", "nonsense"]) == 1
    assert "unknown systems" in capsys.readouterr().err
    assert main(["bench", "run", *base, "--only", "no-such-case"]) == 1


def test_generate_and_systems_commands(tmp_path, capsys):
    seeds = write_seeds(tmp_path / "seeds")
    code = main(
        ["bench", "generate", "--seeds", str(seeds), "--media", str(tmp_path / "m")]
        + ["--cases", str(tmp_path / "cases")]
    )
    assert code == 0
    assert (tmp_path / "cases" / "alpha.en.subtitle-ja.in-sync" / "case.json").exists()
    assert main(["bench", "systems"]) == 0
    listed = capsys.readouterr().out
    assert "oracle" in listed and "alass" in listed


def test_run_can_be_kept_to_some_families(tmp_path, capsys):
    seeds = write_seeds(tmp_path / "seeds")
    base = ["--seeds", str(seeds), "--media", str(tmp_path / "m"), "--cases", str(tmp_path / "c")]
    out = tmp_path / "results"
    code = main(
        ["bench", "run", *base, "--systems", "oracle,input", "--out", str(out)]
        + ["--family", "next_episode,signs_reference"]
    )
    assert code == 0
    summary = json.loads((out / "summary.json").read_text())
    assert {row["group"] for row in summary["by_family"]} == {"next_episode", "signs_reference"}
    rows = {(row["system"], row["group"]): row for row in summary["by_family"]}
    # The oracle refuses the next episode. Handing the file back as it came is
    # right for a reference of signs, and wrong for another episode.
    assert rows[("oracle", "next_episode")]["decision_accuracy"] == 1.0
    assert rows[("input", "signs_reference")]["decision_accuracy"] == 1.0
    assert rows[("input", "next_episode")]["decision_accuracy"] == 0.0
    assert "| oracle | next_episode |" in capsys.readouterr().out
    assert main(["bench", "run", *base, "--family", "no-such-family"]) == 1
