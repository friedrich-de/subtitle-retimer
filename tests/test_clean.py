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
    ],
)
def test_normalize_for_match(text, expected):
    assert normalize_for_match(text) == expected
