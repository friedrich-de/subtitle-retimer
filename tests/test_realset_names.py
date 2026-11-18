import pytest

from retimer.realset.names import file_type, is_broadcast, parse_numbering, split_extension


@pytest.mark.parametrize(
    ("name", "season", "episodes"),
    [
        ("Some.Show.S02E07.A.Title.WEBRip.Service.ja[cc].srt", 2, (7,)),
        ("[Group] Some Show - 14 [BD 1920x1080 x264 AAC].ass", None, (14,)),
        ("[Group] Some Show - 14v2 [BD][h264-720p AC3][0FD05253].ass", None, (14,)),
        ("Some Show -03- 720p BD Dual Audio HEVC [Group].ja.ass", None, (3,)),
        ("[Group] Some Show E05 [BD][1080p][HEVC 10bit x265].srt", None, (5,)),
        ("Some Show - E001-004 [TV].srt", None, (1, 2, 3, 4)),
        ("ある物語 第012話.srt", None, (12,)),
        ("[Group][Some Show 2][03][720P][JPN].ass", None, (3,)),
        ("[Group] Some Show 2 05_ep27.ass", None, (5,)),
        ("[Group] Some Show 07.ass", None, (7,)),
        ("Some Show 2 - E09 [TV].srt", None, (9,)),
        ("Some.Other.Show.003.[x264.720p.10bit.AAC][BD].srt", None, (3,)),
        ("Some Show E11.ass", None, (11,)),
        ("[Group] Some Show - 13 END (STATION 1440x1080 MPEG2 AAC).srt", None, (13,)),
        ("[Group] Some Show S3 - 01.ja-en.ass", None, (1,)),
        ("[Group] A Film [BD 1080p x265 Ma10p FLAC].jpn.ass", None, ()),
        ("映画の題.WEBRip.Service.ja[cc].srt", None, ()),
        ("[Group] Show - S01SP01", 1, (1,)),
    ],
)
def test_the_episode_is_read_off_the_name(name, season, episodes):
    numbering = parse_numbering(name)
    assert (numbering.season, numbering.episodes) == (season, episodes)


def test_files_of_one_release_share_a_template():
    first = parse_numbering("Show.S01E01.The.Beginning.WEBRip.Service.ja[cc].srt")
    second = parse_numbering("Show.S01E02.What.Came.After.WEBRip.Service.ja[cc].srt")
    other_season = parse_numbering("Show.S02E01.Back.Again.WEBRip.Service.ja[cc].srt")
    assert first.template == second.template
    assert first.template != other_season.template

    checked = parse_numbering("[Group] Show - 01 [BD][720p][02A0491D].ass")
    revised = parse_numbering("[Group] Show - 24v2 [BD][720p][544CD7E0].ass")
    assert checked.template == revised.template
    other_release = parse_numbering("[Group] Show - 01 [BD][1080p][02A0491D].ass")
    assert checked.template != other_release.template


def test_a_number_inside_brackets_is_not_an_episode():
    assert parse_numbering("[Group] A Film [1080p][x265][10bit].ass").episodes == ()
    assert parse_numbering("A Film (2017) [BD].srt").episodes == ()


def test_split_extension_knows_only_subtitles():
    assert split_extension("a.b.SRT") == ("a.b", "srt")
    assert split_extension("show.sup.7z") == ("show.sup.7z", "")


@pytest.mark.parametrize(
    ("name", "episodes", "kind"),
    [
        ("Show - E001-004 [TV].srt", 4, "TV pack"),
        ("[Group] Show - 01 [BD 1920x1080].Jap&furigana.ass", 1, "furigana"),
        ("[Group] Show - 01 [720p][JPN, ENG].ass", 1, "bilingual"),
        ("Show -01- 720p [Group].ja-en.ass", 1, "bilingual"),
        ("[Group] Show - 01 [BD][CHS, JPN].ass", 1, "bilingual"),
        ("Show.S01E01.Title.WEBRip.Netflix.ja[cc].srt", 1, "Netflix"),
        ("Show.S01E01.Title.WEBRip.Amazon.ja-jp[sdh].srt", 1, "Amazon"),
        ("[Group] Show - 01 (AT-X 1440x1080 MPEG2 AAC).ass", 1, "AT-X"),
        ("[WOWOWrip] A Film.ja.srt", 1, "WOWOW"),
        ("Show - E01 [TV].srt", 1, "TV (station not named)"),
        ("Show.S01E01.WEBRip.ja[cc].srt", 1, "web captions (service not named)"),
        ("[Group] Show - 01 [WebRip][1080p][JPN].ass", 1, "fansub ASS"),
        ("[Group] Show - 01 [720p][JPN].ass", 1, "fansub ASS"),
        ("ある物語 第001話.srt", 1, "fansub SRT"),
    ],
)
def test_file_types(name, episodes, kind):
    assert file_type(name, episodes) == kind


def test_two_languages_in_the_lines_make_a_file_bilingual():
    assert file_type("[Group] Show - 01.ass", 1, two_scripts=True) == "bilingual"
    # What the name says about the source still comes second.
    assert file_type("Show.S01E01.WEBRip.Netflix.ja[cc].srt", 1, two_scripts=True) == "bilingual"


def test_broadcast_kinds():
    assert is_broadcast("AT-X") and is_broadcast("TV pack")
    assert not is_broadcast("Netflix") and not is_broadcast("fansub ASS")
