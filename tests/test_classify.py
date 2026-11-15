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
    styles = "\n".join(
        f"Style: {name},Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,20,20,30,1"
        for name in style_names
    )
    lines = "\n".join(
        f"Dialogue: 0,0:00:{index:02d}.00,0:00:{index:02d}.90,{style},,0,0,0,,{text}"
        for index, (style, text) in enumerate(events)
    )
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
