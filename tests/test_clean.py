import pytest

from retimer.clean import (
    normalize_for_match,
    reads_as_japanese,
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
        # A long description runs over the line break.
        ("（スピーカーから流れる\\N「FIRE BURNING WOMAN」）", ""),
        ("（潜在犯たちの\\Nざわめき）\\N逃げろ！", "逃げろ！"),
        ("Wait. [door\\Nslams] Who is it?", "Wait. Who is it?"),
        # A bracket that was never closed does not reach for another's closer.
        ("(I mean it\\N- (laughs) Sure", "(I mean it\\N- Sure"),
        # Doubled parentheses mark a thought in Japanese captions. It is spoken.
        (
            "((フィーアのこと捕まえられたら\\Nお嫁さんになってあげるよ))",
            "((フィーアのこと捕まえられたら\\Nお嫁さんになってあげるよ))",
        ),
        ("（（そうだったのか））", "（（そうだったのか））"),
        ("((ﾌｨﾘｱ:ｻｼﾞｮｳ｡\\N痛っ｡", "((ｻｼﾞｮｳ｡\\N痛っ｡"),
        ("(切嗣)((殺し屋だった))", "((殺し屋だった))"),
        # Half-width and full-width partners are mixed in files typed by hand.
        ("貴様は我（おれ）を見るに能(あた）わぬ", "貴様は我を見るに能わぬ"),
        # Brackets nest: a speaker's name carries its reading.
        ("(刀鬼(とうき))鞘姫(さやひめ)", "鞘姫"),
        ("（ＮＡ(ナレーション):月見(るなみ)ヤチヨ）今は昔", "今は昔"),
        ("((衛宮(えみや)だ))", "((衛宮だ))"),
        ("〔エラー音〕\\N(仁菜) あっ…", "あっ…"),
        # In Japanese captions a name follows the bracket of a thought or a voice.
        ("《ハルリ：この女は…　違う》", "《この女は…　違う》"),
        ("＜ボンドルド：闇すらも及ばぬ深淵に", "＜闇すらも及ばぬ深淵に"),
        ("((ＭＥＭちょ：かなちゃんは　きっと", "((かなちゃんは　きっと"),
        ("{\\an8}｟ギルベルト：ヴァイオレット。", "{\\an8}｟ヴァイオレット。"),
        ("《鏑木{\\fscx50}：{\\fscx100}ほら", "《{\\fscx50}{\\fscx100}ほら"),
        ("《{\\fscx50}ハルリ：{\\fscx100}この女は", "《{\\fscx50}{\\fscx100}この女は"),
        ("《20：00　発進》", "《20：00　発進》"),
        ("注：これは本物です", "注：これは本物です"),
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
        ("＜彼らは 誰よりも深く→", "彼らは 誰よりも深く"),
        ("四方の門は閉じ　王冠より出で➡️", "四方の門は閉じ　王冠より出で"),
        ("📺昨晩　衝撃は➨", "昨晩　衝撃は"),
        ("📱おかけになった電話は", "おかけになった電話は"),
        ("⚟少女よ　聞こえているかい？", "少女よ　聞こえているかい？"),
        ("☎️", ""),
        ("🎵～", ""),
        ("キラッ☆\\N好き♡", "キラッ\\N好き"),
        # The brackets of a thought, a narration or a voice off screen. A
        # stretch runs over several subtitles, so each half goes on its own.
        ("≪芸術だなぁ｡≫ ＜きっと＞ 3×4", "芸術だなぁ｡ きっと 3×4"),
        ("《その言葉のとおり", "その言葉のとおり"),
        ("どうせ死ぬんだ》", "どうせ死ぬんだ"),
        ("《天使》", "天使"),
        ("（ポチタ）《ウウ～…》", "（ポチタ）ウウ～…"),
        ("＜　だが　これにより➡", "だが　これにより"),
        ("｟📺ソンナ顔シテテ　楽シイノ？", "ソンナ顔シテテ　楽シイノ？"),
        (
            "⸨外法による理不尽は➨\\N〈アシㇼパが帰ってきた〉",
            "外法による理不尽は\\Nアシㇼパが帰ってきた",
        ),
        ("》", ""),
        ("((あとはひたすら追かける))", "あとはひたすら追かける"),
        ("（（そうだったのか", "そうだったのか"),
        ("(切嗣)((殺し屋だった))", "(切嗣)殺し屋だった"),
        # What is part of the text stays.
        ("藤丸～！ 久利須＝矜治", "藤丸～！ 久利須＝矜治"),
        ("Time alter ── double accel", "Time alter ── double accel"),
        ("≪「黒閃」！≫ 『鬼滅の刃』", "「黒閃」！ 『鬼滅の刃』"),
        ("〝聖域 〞から出られた", "〝聖域 〞から出られた"),
        # Doubled parentheses that close a reading and a name are not a thought.
        ("(百目(ひゃくめ))かつて", "(百目(ひゃくめ))かつて"),
    ],
)
def test_remove_symbols(text, expected):
    assert remove_symbols(text) == expected


def test_chinese_and_korean_titles_keep_their_brackets():
    text = "此处出自《神曲》地狱篇 ((注))"
    assert remove_symbols(text, japanese=False) == text
    assert remove_symbols("♪ 《神曲》", japanese=False) == "《神曲》"


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["《その言葉のとおり", "{\\an8}地下４階。"], True),
        (["ﾌｨﾘｱ:ｻｼﾞｮｳ｡"], True),
        # A Chinese file that names a Japanese album once.
        (["此处出自《神曲》地狱篇第三曲"] * 10 + ["《サーフ ブンガク カマクラ》专辑"], False),
        (["《미생》은 웹툰이다"], False),
        (["Somewhere far away"], False),
        (["♪ ♪", ""], False),
    ],
)
def test_reads_as_japanese(texts, expected):
    assert reads_as_japanese(texts) is expected


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
