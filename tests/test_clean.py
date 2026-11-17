import pytest

from retimer.clean import (
    normalize_for_match,
    remove_hearing_impaired,
    remove_symbols,
    strip_formatting,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[door creaks]", ""),
        ("(sighs) I knew it.\\NNobody came.", "I knew it.\\NNobody came."),
        ("- [laughs]\\N- Stop it.", "- Stop it."),
        ("JOHN: Not again.", "Not again."),
        ("- MAN 2: Over here!", "- Over here!"),
        ("（笑い）そうですね。", "そうですね。"),
        ("{\\pos(320,240)}Hello (quietly)", "{\\pos(320,240)}Hello"),
        ("{\\i1}[music]{\\i0}", ""),
        ("Nothing to remove.", "Nothing to remove."),
        ("Note: it works.", "Note: it works."),
        ("...", "..."),
        ("12:30 is too late.", "12:30 is too late."),
    ],
)
def test_remove_hearing_impaired(text, expected):
    assert remove_hearing_impaired(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("♪ Somewhere far away ♪", "Somewhere far away"),
        ("♪ ♪", ""),
        ("# Humming a tune #", "Humming a tune"),
        ("Call #5 now.", "Call #5 now."),
        ("{\\an8}♫ La la", "{\\an8}La la"),
        ("No symbols here.", "No symbols here."),
        # Japanese broadcast captions: a sentence runs on, a voice comes from a device.
        ("＜彼らは 誰よりも深く→", "＜彼らは 誰よりも深く"),
        ("四方の門は閉じ　王冠より出で➡️", "四方の門は閉じ　王冠より出で"),
        ("📺昨晩　衝撃は➨", "昨晩　衝撃は"),
        ("📱おかけになった電話は", "おかけになった電話は"),
        ("⚟少女よ　聞こえているかい？", "少女よ　聞こえているかい？"),
        ("☎️", ""),
        ("🎵～", ""),
        ("キラッ☆\\N好き♡", "キラッ\\N好き"),
        # What is part of the text stays.
        ("藤丸～！ 久利須＝矜治", "藤丸～！ 久利須＝矜治"),
        ("≪芸術だなぁ｡≫ ＜きっと＞ 3×4", "≪芸術だなぁ｡≫ ＜きっと＞ 3×4"),
        ("Time alter ── double accel", "Time alter ── double accel"),
    ],
)
def test_remove_symbols(text, expected):
    assert remove_symbols(text) == expected


def test_strip_formatting():
    assert strip_formatting("{\\i1}Hi{\\i0}\\Nthere") == "Hi\\Nthere"
    assert strip_formatting("{\\p1}m 0 0 l 10 0 10 10{\\p0}") == ""
    assert strip_formatting("Plain") == "Plain"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- [phone rings]\\N- Hello?", "Hello?"),
        ("{\\i1}It's me.{\\i0}", "It's me."),
        ("MARA: We should go.\\N♪", "We should go."),
        ("- Yes.\\N- No.", "Yes. No."),
        ("{\\p1}m 0 0 l 10 0{\\p0}", ""),
        ("[sobbing]", ""),
        ("（狡噛）📱もしもし➡", "もしもし"),
    ],
)
def test_normalize_for_match(text, expected):
    assert normalize_for_match(text) == expected
