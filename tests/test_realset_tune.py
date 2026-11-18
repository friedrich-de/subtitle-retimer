from retimer.realset.systems import CANDIDATES, candidate_systems
from retimer.realset.tune import choose


def entries(system, verdicts, windows=(4, 0)):
    return [
        {
            "system": system,
            "verdict": verdict,
            "judged_windows": windows[0],
            "off_windows": windows[1],
        }
        for verdict in verdicts
    ]


def test_the_setting_that_verifies_most_files_is_chosen():
    judgments = (
        entries("alass~default", ["verified", "off", "off", "off"])
        + entries("alass~split-penalty-10", ["verified", "verified", "verified", "off"])
        + entries("alass~no-split", ["verified", "verified", "off", "off"])
        + entries("ffsubsync~default", ["verified", "verified"])
        + entries("ffsubsync~split", ["verified", "verified"])
        + entries("ffsubsync-audio~default", ["off", "off"], windows=(4, 3))
        + entries("ffsubsync-audio~auditok", ["off", "off"], windows=(4, 1))
        + entries("alass", ["verified"])
        + entries("input", ["verified"])
    )
    chosen = choose(judgments)
    assert chosen["alass-tuned"]["setting"] == "split-penalty-10"
    assert chosen["alass-tuned"]["args"] == ["--split-penalty", "10"]
    assert chosen["alass-tuned"]["verified"] == {
        "default": 0.25,
        "split-penalty-10": 0.75,
        "no-split": 0.5,
    }
    assert chosen["alass-tuned"]["pairs"] == 4
    # Between equals the default stays.
    assert chosen["ffsubsync-tuned"]["setting"] == "default"
    # With no file verified anywhere, the windows in tolerance decide.
    assert chosen["ffsubsync-audio-tuned"]["setting"] == "auditok"
    # Nothing was tried for alass against the audio, so nothing is chosen.
    assert "alass-audio-tuned" not in chosen


def test_every_tool_is_tried_with_its_defaults_first():
    for settings in CANDIDATES.values():
        assert next(iter(settings.items())) == ("default", ())
    names = [system.name for system in candidate_systems()]
    assert len(names) == len(set(names))
    assert "alass~default" in names and "ffsubsync-audio~default" in names
