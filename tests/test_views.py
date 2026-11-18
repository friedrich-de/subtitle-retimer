from retimer import Options, build_views, classify_lines, loads
from retimer.plan import RemoveLines, StripText
from retimer.views import build_match_view


def ops_of(subtitle, **options):
    return build_views(subtitle, Options(**options)).content_ops


def test_no_options_means_no_content_ops(ass):
    assert ops_of(ass) == []


def test_remove_karaoke(ass):
    assert ops_of(ass, remove_karaoke=True) == [RemoveLines(reason="karaoke", ids=[1, 2, 10])]


def test_remove_hearing_impaired(ass):
    assert ops_of(ass, remove_hearing_impaired=True) == [
        RemoveLines(reason="hearing_impaired", ids=[5]),
        StripText(what="hearing_impaired", ids=[6, 9]),
    ]


def test_remove_signs_and_credits(ass):
    assert ops_of(ass, remove_signs_and_credits=True) == [
        RemoveLines(reason="signs", ids=[7, 8]),
        RemoveLines(reason="credits", ids=[12]),
    ]
    # A drawing is a sign, and is removed as one whatever else is asked.
    assert ops_of(ass, remove_signs_and_credits=True, strip_formatting=True) == [
        RemoveLines(reason="signs", ids=[7, 8]),
        RemoveLines(reason="credits", ids=[12]),
        StripText(what="formatting", ids=[1, 2, 4, 11]),
    ]


def test_signs_that_carry_no_mark_of_one_stay():
    # In a plain SRT nothing tells a sign from a line of dialogue.
    subtitle = loads(
        "1\n00:00:01,000 --> 00:00:03,000\nTokyo Station\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nWhere are we going?\n"
    )
    assert ops_of(subtitle, remove_signs_and_credits=True) == []


def test_remove_symbols_drops_lines_that_hold_nothing_else(ass, srt):
    assert ops_of(ass, remove_symbols=True) == [RemoveLines(reason="symbols", ids=[10])]
    assert ops_of(srt, remove_symbols=True) == [StripText(what="symbols", ids=[2, 5])]


def test_strip_formatting_removes_drawings(ass):
    assert ops_of(ass, strip_formatting=True) == [
        RemoveLines(reason="drawing", ids=[8]),
        StripText(what="formatting", ids=[1, 2, 4, 7, 11]),
    ]


def test_a_line_emptied_by_two_options_is_removed_once():
    subtitle = loads(
        "1\n00:00:01,000 --> 00:00:02,000\n(hums) ♪\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nHi (waves) ♪\n"
    )
    assert ops_of(subtitle, remove_hearing_impaired=True, remove_symbols=True) == [
        RemoveLines(reason="hearing_impaired", ids=[0]),
        StripText(what="hearing_impaired", ids=[1]),
        StripText(what="symbols", ids=[1]),
    ]


def test_match_view_is_dialogue_reduced_to_spoken_words(ass):
    view = build_views(ass, Options()).match_view
    assert [(line.id, line.text) for line in view.lines] == [
        (3, "Good morning."),
        (4, "Is anyone there?"),
        (6, "I knew it. Nobody came."),
        (9, "We should go."),
        (11, "Wait for me!"),
    ]
    assert view.lines[0].start_ms == 90_000
    assert view.excluded == {
        "comment": 1,
        "song": 3,
        "sound_description": 1,
        "sign": 2,
        "credit": 1,
    }


def test_match_view_does_not_depend_on_the_options(ass):
    plain = build_views(ass, Options()).match_view
    cleaned = build_views(ass, Options(remove_karaoke=True, strip_formatting=True)).match_view
    assert plain == cleaned


def _events(rows):
    lines = "\n".join(
        f"Dialogue: {layer},0:00:{start / 1000:05.2f},0:00:{end / 1000:05.2f},"
        f"Default,,0,0,0,,{text}"
        for layer, start, end, text in rows
    )
    return loads(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize\nStyle: Default,Arial,40\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"{lines}\n"
    )


def test_layers_and_frames_of_one_line_are_one_line_in_the_match_view():
    subtitle = _events(
        [
            (0, 1_000, 3_000, "{\\blur4}Where are you going?"),
            (1, 1_000, 3_000, "{\\bord0}Where are you going?"),
            (0, 4_000, 4_040, "Why do I fight?"),
            (0, 4_040, 4_080, "{\\i1}Why do I fight?"),
            (0, 4_080, 6_000, "Why do I fight?"),
            (0, 7_000, 8_000, "No!"),
            (0, 9_000, 10_000, "No!"),
        ]
    )

    view = build_match_view(subtitle, classify_lines(subtitle))

    assert [(line.id, line.start_ms, line.end_ms, line.text) for line in view.lines] == [
        (0, 1_000, 3_000, "Where are you going?"),
        (2, 4_000, 6_000, "Why do I fight?"),
        (5, 7_000, 8_000, "No!"),
        (6, 9_000, 10_000, "No!"),
    ]
    assert view.excluded == {"repeat": 3}
