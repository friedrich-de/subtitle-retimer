from retimer import classify_lines, loads


def labels_of(subtitle):
    return [label.label for label in classify_lines(subtitle)]


def test_ass_labels(ass):
    assert labels_of(ass) == [
        "comment",
        "song",
        "song",
        "dialogue",
        "dialogue",
        "sound_description",
        "dialogue",
        "sign",
        "sign",
        "dialogue",
        "song",
        "dialogue",
        "credit",
    ]


def test_srt_labels(srt):
    assert labels_of(srt) == [
        "dialogue",
        "dialogue",
        "song",
        "sound_description",
        "dialogue",
        "song",
    ]


def test_reasons_and_confidence_are_reported(ass):
    labels = classify_lines(ass)
    assert labels[1].reasons == ("karaoke timing tags",)
    assert labels[8].reasons == ("vector drawing",)
    assert labels[3].confidence == 0.9


def _ass_with(style_names, events):
    """A file with these styles and events: (style, text), or (style, text, effect, shown ms)."""
    styles = "\n".join(
        f"Style: {name},Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,20,20,30,1"
        for name in style_names
    )
    rows = []
    for index, event in enumerate(events):
        style, text, effect, shown = (*event, "", 900)[:4]
        end = index * 1000 + shown
        rows.append(
            f"Dialogue: 0,0:{index // 60:02d}:{index % 60:02d}.00,"
            f"0:{end // 60000:02d}:{end % 60000 / 1000:05.2f},{style},,0,0,0,{effect},{text}"
        )
    lines = "\n".join(rows)
    return loads(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{styles}\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"{lines}\n"
    )


def test_style_names_do_not_match_inside_words():
    # "Top" contains "op", "Narrated" ends in "ed", "Subtitle" contains "title".
    subtitle = _ass_with(
        ["Top", "Narrated", "Subtitle"],
        [("Top", "Up here."), ("Narrated", "Long ago."), ("Subtitle", "Hello.")],
    )
    assert labels_of(subtitle) == ["dialogue", "dialogue", "dialogue"]


def test_song_and_sign_styles_by_name():
    subtitle = _ass_with(
        ["Main", "ED_English", "TS", "Episode Title"],
        [
            ("Main", "Hello."),
            ("Main", "Goodbye."),
            ("ED_English", "The night is long"),
            ("TS", "Bakery"),
            ("Episode Title", "The Beginning"),
        ],
    )
    assert labels_of(subtitle) == ["dialogue", "dialogue", "song", "sign", "sign"]


def test_positioned_line_in_a_dialogue_style_stays_dialogue_with_low_confidence():
    subtitle = _ass_with(
        ["Main", "Extra"],
        [
            ("Main", "Hello."),
            ("Main", "{\\pos(640,60)}Moved out of the way."),
            ("Main", "Goodbye."),
            ("Main", "See you."),
            ("Extra", "{\\pos(100,100)}Shop sign"),
        ],
    )
    labels = classify_lines(subtitle)
    assert (labels[1].label, labels[1].confidence) == ("dialogue", 0.6)
    assert (labels[4].label, labels[4].confidence) == ("sign", 0.75)


def test_the_styles_of_a_song_are_known_by_their_family():
    # Fansub scripts name the styles of a song after the song, or after "op" and
    # "ed" with what is written in them, and give every layer a style of its own.
    names = [
        "Default",
        "Wonder Stella Romaji",
        "Wonder Stella English",
        "edrom-L0",
        "edeng-L1",
        "edengglow",
        "optrans",
        "edtrans2",
        "Ep0 INS",
    ]
    subtitle = _ass_with(
        names,
        [("Default", "Hello."), ("Default", "Goodbye.")]
        + [(name, "Under the endless sky") for name in names[1:]],
    )

    assert labels_of(subtitle) == ["dialogue", "dialogue"] + ["song"] * 8
    reasons = classify_lines(subtitle)[3].reasons
    assert reasons == ("style 'Wonder Stella English' belongs to a song",)


def test_a_dialogue_style_is_not_drawn_into_a_family():
    subtitle = _ass_with(
        ["Default", "Default Romaji", "Alt", "Notes English"],
        [
            ("Default", "Hello."),
            ("Default Romaji", "sore wa"),
            ("Alt", "Over here."),
            ("Notes English", "A kind of rice cake."),
        ],
    )

    assert labels_of(subtitle) == ["dialogue", "song", "dialogue", "sign"]


def test_a_style_whose_lines_are_mostly_timed_as_karaoke_is_a_song():
    subtitle = _ass_with(
        ["Default", "Blue"],
        [
            ("Default", "Hello."),
            ("Blue", "{\\k20}ha{\\k30}shi{\\k25}re"),
            ("Blue", "{\\k20}to{\\k30}o{\\k25}ku"),
            ("Blue", "Run far away"),
        ],
    )

    assert labels_of(subtitle) == ["dialogue", "song", "song", "song"]


def test_typesetting_drawn_frame_by_frame_is_no_dialogue_whatever_its_style():
    frames = [
        ("Default", "{\\clip(0,907,1920,908)\\pos(960,1024)}Why do I fight?", "fx", 40)
        for _ in range(40)
    ]
    subtitle = _ass_with(
        ["Default"],
        [("Default", "Hello."), ("Default", "{\\pos(960,1025)}Verschmelzen!"), *frames],
    )

    labels = classify_lines(subtitle)

    assert [label.label for label in labels[:2]] == ["dialogue", "dialogue"]
    assert {label.label for label in labels[2:]} == {"sign"}
    assert labels[2].reasons == ("positioned on screen", "shown for a moment only")


def test_a_line_that_an_effect_template_generated_is_no_dialogue():
    subtitle = _ass_with(
        ["Default"],
        [
            ("Default", "Hello."),
            ("Default", "{\\pos(960,1025)\\blur2}a", "fx", 900),
            ("Default", "No position, so it is kept.", "fx", 900),
        ],
    )

    assert labels_of(subtitle) == ["dialogue", "sign", "dialogue"]


def test_a_style_full_of_typesetting_does_not_become_the_dialogue_style_by_its_size():
    # A style that carries a third of the lines counts as dialogue. Only plain
    # lines count towards that: positioned text can be most of a file.
    signs = [("Mio", "{\\pos(100,100)}Bakery") for _ in range(30)]
    subtitle = _ass_with(
        ["Kei", "Mio"], [("Kei", "Hello there."), ("Kei", "Goodbye for now."), *signs]
    )

    labels = classify_lines(subtitle)

    assert [label.label for label in labels[:2]] == ["dialogue", "dialogue"]
    assert labels[0].reasons == ("dialogue style",)
    assert {label.label for label in labels[2:]} == {"sign"}
