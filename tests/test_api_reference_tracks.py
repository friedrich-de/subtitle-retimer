"""Several reference subtitles through the API: sent as repeated parts, chosen by the service."""

import json

from retimer import dumps, loads
from retimer.api.store import REFERENCE_SUBTITLE, TARGET, reference_file

from .api_helpers import AUDIO, make_service, starts_of
from .sync_helpers import SENTENCES, make_transcript
from .track_helpers import COUNT, TRUTH, full, other_episode, part, script, signs, target


def refused(response, status: int, code: str) -> bool:
    return response.status_code == status and response.json()["error"]["code"] == code


def data(subtitle, fmt: str | None = None) -> bytes:
    return dumps(subtitle, fmt).encode("utf-8")


TARGET_FILE = ("Episode 01.ja.srt", data(target()))
SIGNS_FILE = ("track 2.ass", data(signs()))
FULL_FILE = ("track 3.ass", data(full(), "ass"))
OTHER_FILE = ("track 4.srt", data(other_episode()))
LABELS = [
    {"language": "eng", "title": "Signs & Songs", "default": False, "forced": True},
    {"language": "eng", "title": "Full Subtitles", "default": True, "forced": False},
    {},
]


def post(service, references: list[tuple[str, bytes]], options: dict | None = None, audio=None):
    files = [("target", TARGET_FILE)]
    files += [("reference_subtitle", reference) for reference in references]
    if audio is not None:
        files.append(("reference_audio", ("episode.ogg", audio)))
    form = {"options": json.dumps(options)} if options is not None else None
    return service.client.post("/v1/jobs", files=files, data=form)


def finished(service, references, options: dict | None = None, audio=None) -> dict:
    response = post(service, references, options, audio)
    assert response.status_code == 202, response.text
    service.run()
    return service.client.get(response.json()["links"]["self"]).json()


def test_a_job_with_several_reference_subtitles_is_synced_against_the_one_that_fits(tmp_path):
    service = make_service(tmp_path)

    response = post(service, [SIGNS_FILE, FULL_FILE, OTHER_FILE], {"reference_tracks": LABELS})

    assert response.status_code == 202, response.text
    queued = response.json()
    request = queued["request"]
    # One reference subtitle is named as it always was. Several are a list.
    assert request["reference_subtitle"] is None
    assert [entry["index"] for entry in request["reference_subtitles"]] == [0, 1, 2]
    assert request["reference_subtitles"][0] == {
        "index": 0,
        "language": "eng",
        "title": "Signs & Songs",
        "default": False,
        "forced": True,
        "filename": "track 2.ass",
        "format": "ass",
        "bytes": len(SIGNS_FILE[1]),
        "lines": 9,
        "dialogue_lines": 0,
    }
    assert request["reference_subtitles"][2]["title"] is None
    assert "reference_tracks" not in request["options"]
    directory = service.store.directory(queued["id"])
    assert sorted(path.name for path in directory.iterdir()) == [
        reference_file(0),
        reference_file(1),
        reference_file(2),
        TARGET,
    ]

    service.run()
    job = service.client.get(queued["links"]["self"]).json()

    assert job["outcome"] == "synced"
    assert job["summary"]["warnings"] == []
    chosen = job["summary"]["reference"]
    assert chosen["chosen"] == 1
    assert '"Full Subtitles"' in chosen["reason"]
    assert [found["verdict"] for found in chosen["candidates"]] == [
        "too_little_dialogue",
        "chosen",
        "no_match",
    ]
    assert chosen["candidates"][1] == {
        "index": 1,
        "language": "eng",
        "title": "Full Subtitles",
        "default": True,
        "forced": False,
        "filename": "track 3.ass",
        "lines": COUNT,
        "dialogue_lines": COUNT,
        "matched_lines": COUNT,
        "comparable_lines": COUNT,
        "share": 1.0,
        "verdict": "chosen",
        "reason": f"The target's dialogue finds its counterpart in it: {COUNT} of {COUNT} lines.",
        "duplicate_of": None,
    }
    assert all(found["reason"] for found in chosen["candidates"])
    assert starts_of(service.client.get(job["links"]["result"]).text) == TRUTH
    report = service.client.get(job["links"]["report"]).json()
    assert report["reference_choice"]["chosen"] == 1
    assert report["reference_choice"]["candidates"][0]["left_out"] == {"sign": 5, "song": 4}


def test_one_reference_subtitle_is_a_job_as_it_always_was(tmp_path):
    service = make_service(tmp_path)

    job = finished(service, [FULL_FILE], {"reference_language": "en"})

    assert job["outcome"] == "synced"
    single = job["request"]["reference_subtitle"]
    assert job["request"]["reference_subtitles"] == [single]
    assert (single["filename"], single["format"], single["lines"]) == ("track 3.ass", "ass", COUNT)
    assert job["summary"]["reference"]["chosen"] == 0
    assert job["summary"]["reference"]["candidates"][0]["language"] == "en"
    assert len(job["summary"]["reference"]["candidates"]) == 1


def test_a_job_against_audio_alone_reports_no_choice(tmp_path):
    service = make_service(tmp_path)
    service.remember_transcript(make_transcript(TRUTH, SENTENCES[:COUNT]))

    job = finished(service, [], audio=AUDIO)

    assert job["outcome"] == "synced"
    assert job["summary"]["reference"] is None
    assert job["request"]["reference_subtitles"] == []


def test_when_no_track_fits_the_job_is_rejected_and_says_so(tmp_path):
    service = make_service(tmp_path)

    job = finished(service, [SIGNS_FILE, ("more signs.ass", data(signs()))])

    assert job["outcome"] == "rejected_no_match"
    assert job["summary"]["reference"]["chosen"] is None
    assert job["summary"]["reference"]["reason"] == (
        "None of the 2 reference subtitles fits the target."
    )
    assert "no audio to sync against" in job["summary"]["warnings"][0]
    assert job["summary"]["agent"] is None
    assert "result" not in job["links"]
    assert refused(service.client.get(f"/v1/jobs/{job['id']}/result"), 404, "no_result")


def test_when_no_track_fits_and_audio_came_along_the_audio_is_what_counts(tmp_path):
    service = make_service(tmp_path)
    service.remember_transcript(make_transcript(TRUTH, SENTENCES[:COUNT]))

    job = finished(service, [SIGNS_FILE, OTHER_FILE], audio=AUDIO)

    assert job["outcome"] == "synced"
    assert job["summary"]["reference"]["chosen"] is None
    assert [found["verdict"] for found in job["summary"]["reference"]["candidates"]] == [
        "too_little_dialogue",
        "no_match",
    ]
    assert job["summary"]["warnings"] == [
        "None of the 2 reference subtitles fits the target. The target was synced against "
        "the audio alone."
    ]
    report = service.client.get(job["links"]["report"]).json()
    assert report["reference"]["kind"] == "audio"


def test_the_matched_reference_is_the_chosen_track_in_its_own_format(tmp_path):
    service = make_service(tmp_path)
    shared = part(list(range(4, COUNT)))
    options = {"matched_reference": True}

    queued = post(service, [OTHER_FILE, ("Episode 01.en.ass", data(shared, "ass"))], options)
    assert queued.json()["request"]["matched_reference_format"] is None
    service.run()
    job = service.client.get(queued.json()["links"]["self"]).json()

    assert job["summary"]["reference"]["chosen"] == 1
    assert job["summary"]["matched_reference_lines"] == COUNT - 4
    matched = service.client.get(job["links"]["matched_reference"])
    assert matched.headers["content-type"] == "text/x-ssa; charset=utf-8"
    assert matched.headers["content-disposition"] == (
        "attachment; filename*=utf-8''Episode%2001.en.matched.ass"
    )
    assert [line.text for line in loads(matched.text).lines] == SENTENCES[4:COUNT]

    asked = finished(
        service, [OTHER_FILE, ("b.ass", data(shared, "ass"))], {**options, "output_format": "srt"}
    )
    again = service.client.get(asked["links"]["matched_reference"])
    assert again.headers["content-type"] == "application/x-subrip; charset=utf-8"

    none = finished(service, [SIGNS_FILE, OTHER_FILE], options)
    assert none["outcome"] == "rejected_no_match"
    assert refused(
        service.client.get(f"/v1/jobs/{none['id']}/matched_reference"), 404, "no_matched_reference"
    )


def test_a_track_that_cannot_be_read_does_not_sink_a_job_with_others(tmp_path):
    service = make_service(tmp_path)
    broken = ("picture track.sup", b"PG\x00\x01not a subtitle with text")

    response = post(service, [broken, FULL_FILE, ("empty.srt", b"")])

    assert response.status_code == 202, response.text
    entries = response.json()["request"]["reference_subtitles"]
    assert entries[0]["error"]["code"] == "invalid_reference_subtitle"
    assert entries[0]["error"]["message"].startswith("reference_subtitle[0]: ")
    assert entries[2]["error"]["message"] == "reference_subtitle[2] is empty."
    assert "format" not in entries[0] and entries[0]["filename"] == "picture track.sup"
    directory = service.store.directory(response.json()["id"])
    assert sorted(path.name for path in directory.iterdir()) == [reference_file(1), TARGET]

    service.run()
    job = service.client.get(response.json()["links"]["self"]).json()

    assert job["outcome"] == "synced"
    found = job["summary"]["reference"]
    assert found["chosen"] == 1
    assert [candidate["verdict"] for candidate in found["candidates"]] == [
        "unreadable",
        "chosen",
        "unreadable",
    ]
    assert found["candidates"][2]["reason"] == "reference_subtitle[2] is empty."


def test_tracks_that_all_cannot_be_read_are_refused_unless_audio_came_along(tmp_path):
    service = make_service(tmp_path)
    service.remember_transcript(make_transcript(TRUTH, SENTENCES[:COUNT]))
    broken = [("a.sup", b"PG\x00\x01no text"), ("b.srt", b"")]

    response = post(service, broken)
    assert refused(response, 422, "invalid_reference_subtitle")
    assert response.json()["error"]["message"].startswith("reference_subtitle[0]: ")
    assert list(service.store.jobs_dir.iterdir()) == []

    job = finished(service, broken, audio=AUDIO)
    assert job["outcome"] == "synced"
    assert job["summary"]["reference"]["chosen"] is None
    assert "the audio alone" in job["summary"]["warnings"][0]

    # One reference subtitle that cannot be read is refused, as it always was.
    alone = post(service, [("a.srt", b"")])
    assert refused(alone, 422, "invalid_reference_subtitle")
    assert alone.json()["error"]["message"] == "reference_subtitle is empty."


def test_a_track_over_the_size_limit_is_refused_whatever_came_with_it(tmp_path):
    service = make_service(tmp_path, max_subtitle_bytes=4_000)
    large = ("large.srt", data(full()) + b"\n" * 4_000)

    response = post(service, [FULL_FILE, large])

    assert refused(response, 413, "file_too_large")
    assert "reference_subtitle[1]" in response.json()["error"]["message"]
    assert list(service.store.jobs_dir.iterdir()) == []


def test_a_job_takes_a_limited_number_of_reference_subtitles(tmp_path):
    service = make_service(tmp_path)
    assert service.settings.max_reference_subtitles == 6

    seven = post(service, [(f"{index}.srt", data(full())) for index in range(7)])
    assert refused(seven, 422, "too_many_reference_subtitles")
    assert "up to 6" in seven.json()["error"]["message"]
    assert list(service.store.jobs_dir.iterdir()) == []

    six = finished(service, [(f"{index}.srt", data(full())) for index in range(6)])
    assert six["outcome"] == "synced"
    assert [found["verdict"] for found in six["summary"]["reference"]["candidates"]] == [
        "chosen",
        *["duplicate"] * 5,
    ]

    small = make_service(tmp_path / "small", max_reference_subtitles=2)
    assert refused(
        post(small, [FULL_FILE, SIGNS_FILE, OTHER_FILE]), 422, "too_many_reference_subtitles"
    )


def test_the_labels_have_to_be_one_for_each_track(tmp_path):
    service = make_service(tmp_path)

    short = post(service, [SIGNS_FILE, FULL_FILE], {"reference_tracks": [{}]})
    assert refused(short, 422, "reference_tracks_mismatch")
    assert "1 entries" in short.json()["error"]["message"]

    without = post(service, [], {"reference_tracks": [{}]}, audio=AUDIO)
    assert refused(without, 422, "reference_tracks_mismatch")

    unknown = post(service, [FULL_FILE], {"reference_tracks": [{"codec": "ass"}]})
    assert refused(unknown, 422, "invalid_options")
    wrong = post(service, [FULL_FILE], {"reference_tracks": [{"forced": "perhaps"}]})
    assert refused(wrong, 422, "invalid_options")
    assert list(service.store.jobs_dir.iterdir()) == []


def test_a_label_is_free_text_and_is_kept_to_one_short_printable_line(tmp_path):
    service = make_service(tmp_path)
    labels = [
        {
            "language": " EN-us ",
            "title": "Full\x00 Subtitles\r\n\x1b[31m(honorifics)   " + "x" * 400,
            "default": None,
            "forced": None,
        },
        {"language": "", "title": "   "},
    ]

    job = finished(service, [FULL_FILE, SIGNS_FILE], {"reference_tracks": labels})

    first, second = job["request"]["reference_subtitles"]
    assert first["language"] == "en-us"
    assert first["title"].startswith("Full Subtitles [31m(honorifics) xxx")
    assert len(first["title"]) == 200
    assert (first["default"], first["forced"]) == (False, False)
    assert (second["language"], second["title"]) == (None, None)
    assert job["summary"]["reference"]["candidates"][0]["title"] == first["title"]


def test_a_job_an_earlier_release_took_in_still_runs(tmp_path):
    service = make_service(tmp_path)
    queued = post(service, [FULL_FILE]).json()
    # As it was stored before a job could have several: one file, and no list.
    directory = service.store.directory(queued["id"])
    (directory / reference_file(0)).rename(directory / REFERENCE_SUBTITLE)
    request = {
        key: value for key, value in queued["request"].items() if key != "reference_subtitles"
    }
    with service.store._connect() as connection:
        connection.execute(
            "UPDATE jobs SET request = ? WHERE id = ?", (json.dumps(request), queued["id"])
        )

    service.run()
    job = service.client.get(queued["links"]["self"]).json()

    assert job["outcome"] == "synced"
    assert job["summary"]["reference"]["chosen"] == 0
    assert starts_of(service.client.get(job["links"]["result"]).text) == TRUTH


def test_the_features_of_a_server_are_told_to_those_with_a_key(tmp_path):
    service = make_service(tmp_path, max_reference_subtitles=4)

    assert refused(service.anonymous().get("/v1/features"), 401, "unauthorized")
    answer = service.client.get("/v1/features").json()

    assert set(answer["features"]) >= {"reference_tracks", "remove_signs_and_credits"}
    assert answer["limits"]["reference_subtitles"] == 4
    assert answer["limits"]["request_bytes"] == service.settings.max_body_bytes
    assert set(answer["limits"]) == {
        "reference_subtitles",
        "request_bytes",
        "subtitle_bytes",
        "subtitle_dialogue_lines",
        "audio_bytes",
        "audio_minutes",
        "image_subtitle_bytes",
    }
    # The health check stays as silent as it was.
    assert service.anonymous().get("/healthz").json() == {"status": "ok"}


def test_signs_and_credits_are_removed_when_asked(tmp_path):
    service = make_service(tmp_path)
    events = [
        (start + 3_500, "Default", text) for start, text in zip(TRUTH, SENTENCES, strict=False)
    ]
    events.insert(3, (20_000, "Signs", "{\\pos(320,60)}Harbor Office"))
    events.append((TRUTH[-1] + 9_000, "Default", "Subtitles by the Example Group"))
    files = [
        ("target", ("episode.ass", data(script(events)))),
        ("reference_subtitle", FULL_FILE),
    ]

    def run(options: dict) -> tuple[dict, list[str]]:
        response = service.client.post(
            "/v1/jobs", files=files, data={"options": json.dumps(options)}
        )
        assert response.status_code == 202, response.text
        service.run()
        job = service.client.get(response.json()["links"]["self"]).json()
        result = loads(service.client.get(job["links"]["result"]).text)
        return job, [line.text for line in result.lines]

    kept_job, kept = run({"drop_unmatched": False})
    job, texts = run({"remove_signs_and_credits": True, "drop_unmatched": False})

    assert kept_job["request"]["options"]["remove_signs_and_credits"] is False
    assert len(kept) == COUNT + 2
    assert job["request"]["options"]["remove_signs_and_credits"] is True
    assert texts == SENTENCES[:COUNT]
    plan = service.client.get(job["links"]["plan"]).json()
    removed = {op["reason"]: op["ids"] for op in plan["content_ops"]}
    assert removed == {"signs": [3], "credits": [COUNT + 1]}
