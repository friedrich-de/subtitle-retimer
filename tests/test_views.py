from retimer import Options, build_views, loads
from retimer.plan import RemoveLines, StripText


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
