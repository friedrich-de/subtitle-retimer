from retimer import inspect_subtitle, loads, sample_lines


def test_report_for_ass(ass):
    report = inspect_subtitle(ass)
    assert report.format == "ass"
    assert report.line_count == 13
    assert report.comment_count == 1
    assert report.labels == {
        "comment": 1,
        "song": 3,
        "dialogue": 5,
        "sound_description": 1,
        "sign": 2,
        "credit": 1,
    }
    assert report.tags.karaoke_lines == 2
    assert report.tags.drawing_lines == 1
    assert report.tags.positioned_lines == 2
    assert report.tags.lines_with_override_tags == 6
    assert report.hearing_impaired.full_lines == 1
    assert report.hearing_impaired.lines_with_removable_parts == 2
    assert report.lines_with_music_symbols == 1
    assert report.language_guess is None
    assert report.scripts == {"latin": 1.0}


def test_styles_are_listed_largest_first(ass):
    styles = inspect_subtitle(ass).styles
    assert [style.name for style in styles] == ["Default", "OP-Romaji", "Signs"]
    assert styles[0].lines == 8
    assert styles[0].sample == "Good morning."
    assert styles[1].labels == {"song": 2}


def test_timing_stats(ass):
    timing = inspect_subtitle(ass).timing
    assert timing.first_start_ms == 1_000
    assert timing.last_end_ms == 1_354_000
    # The two signs sit on top of a dialogue line and of each other.
    assert timing.overlapping_lines == 2
    assert timing.max_simultaneous == 3
    assert timing.out_of_order == 0
    assert timing.zero_or_negative_duration == 0


def test_timing_oddities_are_counted():
    subtitle = loads(
        "1\n00:00:05,000 --> 00:00:06,000\nSecond\n\n"
        "2\n00:00:01,000 --> 00:00:01,000\nFirst\n\n"
        "3\n00:00:07,000 --> 00:00:40,000\nVery long\n\n"
        "4\n00:00:41,000 --> 00:00:42,000\nVery long\n"
    )
    report = inspect_subtitle(subtitle)
    assert report.timing.out_of_order == 1
    assert report.timing.zero_or_negative_duration == 1
    assert report.timing.longer_than_20s == 1
    assert report.consecutive_duplicates == 1


def test_japanese_is_recognized_by_script():
    subtitle = loads("1\n00:00:01,000 --> 00:00:02,000\nおはようございます。今日は晴れです。\n")
    report = inspect_subtitle(subtitle)
    assert report.language_guess == "ja"
    assert set(report.scripts) == {"kana", "han"}


def test_sample_lines_filters(ass):
    assert [line.id for line in sample_lines(ass, label="song")] == [1, 2, 10]
    assert [line.id for line in sample_lines(ass, style="Signs")] == [7, 8]
    assert [line.id for line in sample_lines(ass, pattern="morning")] == [3]
    assert [line.id for line in sample_lines(ass, from_ms=100_000, to_ms=110_000)] == [9, 10, 11]
    assert [line.id for line in sample_lines(ass, max_confidence=0.79)] == []


def test_sample_lines_spreads_over_the_matches(ass):
    assert [line.id for line in sample_lines(ass, limit=3)] == [0, 6, 12]
    assert [line.id for line in sample_lines(ass, limit=1)] == [0]
