"""The matched reference through the API: asked for with an option, fetched like the result."""

import json
import logging

from retimer import dumps, loads
from retimer.api.store import MATCHED_REFERENCE, PLAN, REPORT, RESULT

from .agent_helpers import FakeModel, pair, submit, three_parts
from .api_helpers import AUDIO, make_service, srt, starts_of
from .sync_helpers import SENTENCES, make_transcript, spread

ASKED = json.dumps({"matched_reference": True})


def refused(response, status: int, code: str) -> bool:
    return response.status_code == status and response.json()["error"]["code"] == code


def pair_of_files(missing: tuple[int, ...] = (6, 7)) -> tuple[bytes, bytes, list[int]]:
    """A late target that lacks some lines, its reference, and where the shared lines start."""
    truth = spread(24)
    said = [index for index in range(24) if index not in missing]
    target = srt([truth[index] + 3_500 for index in said], [SENTENCES[index] for index in said])
    return target, srt(truth), [truth[index] for index in said]


def test_a_job_hands_back_the_reference_it_was_asked_for(tmp_path):
    service = make_service(tmp_path)
    target, reference, shared = pair_of_files()

    response = service.client.post(
        "/v1/jobs",
        files={
            "target": ("Episode 01.ja.srt", target),
            "reference_subtitle": ("Episode 01.en.srt", reference),
        },
        data={"options": ASKED},
    )
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["request"]["options"]["matched_reference"] is True
    assert queued["request"]["matched_reference_format"] == "srt"
    assert "matched_reference" not in queued["links"]
    service.run()

    job = service.client.get(queued["links"]["self"]).json()
    assert job["outcome"] == "synced"
    assert set(job["links"]) == {"self", "result", "matched_reference", "plan", "report"}
    assert job["links"]["matched_reference"] == f"/v1/jobs/{job['id']}/matched_reference"
    assert job["summary"]["matched_reference_lines"] == 22
    assert job["summary"]["warnings"] == []

    matched = service.client.get(job["links"]["matched_reference"])
    assert matched.status_code == 200
    assert matched.headers["content-type"] == "application/x-subrip; charset=utf-8"
    assert matched.headers["content-disposition"] == (
        "attachment; filename*=utf-8''Episode%2001.en.matched.srt"
    )
    assert starts_of(matched.text) == shared
    assert starts_of(service.client.get(job["links"]["result"]).text) == shared

    report = service.client.get(job["links"]["report"]).json()["matched_reference"]
    assert (report["delivered"], report["lines"]) == (True, 22)
    assert report["left_out"] == {"no_counterpart": 2}


def test_a_job_that_was_not_asked_has_none(tmp_path):
    service = make_service(tmp_path)
    target, reference, _ = pair_of_files()

    job = service.finished({"target": target, "reference_subtitle": reference})

    assert job["request"]["options"]["matched_reference"] is False
    assert "matched_reference_format" not in job["request"]
    assert set(job["links"]) == {"self", "result", "plan", "report"}
    assert job["summary"]["matched_reference_lines"] is None
    assert refused(
        service.client.get(job["links"]["self"] + "/matched_reference"), 404, "not_requested"
    )
    assert service.client.get(job["links"]["report"]).json()["matched_reference"] is None
    assert service.store.file(job["id"], MATCHED_REFERENCE) is None


def test_audio_alone_cannot_be_asked_for_one(tmp_path):
    service = make_service(tmp_path)
    target, _, _ = pair_of_files()

    response = service.submit({"target": target, "reference_audio": AUDIO}, ASKED)

    assert refused(response, 422, "missing_reference_subtitle")
    assert "reference_subtitle" in response.json()["error"]["message"]
    assert service.client.get("/v1/jobs").json()["jobs"] == []


def test_with_audio_and_a_subtitle_the_reference_comes_back_on_the_audio(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    service.remember_transcript(make_transcript(truth))
    files = {
        "target": srt([start + 9_000 for start in truth]),
        "reference_audio": AUDIO,
        "reference_subtitle": srt([start + 4_000 for start in truth]),
    }

    job = service.finished(files, ASKED)

    assert job["outcome"] == "synced"
    assert starts_of(service.client.get(job["links"]["result"]).text) == truth
    assert starts_of(service.client.get(job["links"]["matched_reference"]).text) == truth
    report = service.client.get(job["links"]["report"]).json()["matched_reference"]
    assert report["moved_onto_audio"] is True


def test_the_matched_reference_keeps_its_format_unless_one_is_asked_for(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    target = srt([start + 2_000 for start in truth])
    script = loads(srt(truth).decode())
    script.format = "ass"
    reference = dumps(script).encode()

    kept = service.finished({"target": target, "reference_subtitle": reference}, ASKED)
    asked = service.finished(
        {"target": target, "reference_subtitle": reference},
        json.dumps({"matched_reference": True, "output_format": "srt"}),
    )

    assert (kept["request"]["output_format"], kept["request"]["matched_reference_format"]) == (
        "srt",
        "ass",
    )
    matched = service.client.get(kept["links"]["matched_reference"])
    assert matched.headers["content-type"] == "text/x-ssa; charset=utf-8"
    assert matched.headers["content-disposition"] == (
        'attachment; filename="reference_subtitle.matched.ass"'
    )
    assert loads(matched.text).format == "ass"
    assert asked["request"]["matched_reference_format"] == "srt"
    assert loads(service.client.get(asked["links"]["matched_reference"]).text).format == "srt"


def test_the_content_options_reach_the_matched_reference(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    texts = [f"[sighs] {text}" if index == 3 else text for index, text in enumerate(SENTENCES[:20])]
    options = {"matched_reference": True, "remove_hearing_impaired": True}

    job = service.finished(
        {
            "target": srt([start + 2_000 for start in truth]),
            "reference_subtitle": srt(truth, texts),
        },
        json.dumps(options),
    )

    matched = loads(service.client.get(job["links"]["matched_reference"]).text)
    assert [line.text for line in matched.lines] == SENTENCES[:20]


def test_other_content_has_no_matched_reference_and_the_report_says_why(tmp_path):
    service = make_service(tmp_path)
    target = srt(spread(14), SENTENCES[:14])
    reference = srt(spread(14), SENTENCES[15:29])

    job = service.finished({"target": target, "reference_subtitle": reference}, ASKED)

    assert job["outcome"] == "rejected_no_match"
    assert set(job["links"]) == {"self", "plan", "report"}
    assert job["summary"]["matched_reference_lines"] is None
    assert job["summary"]["warnings"][-1].startswith("No matched reference was made.")
    assert refused(
        service.client.get(job["links"]["self"] + "/matched_reference"),
        404,
        "no_matched_reference",
    )
    report = service.client.get(job["links"]["report"]).json()["matched_reference"]
    assert report["delivered"] is False
    assert report["note"] == "The reference is other content than the target."


def test_a_job_the_agent_settled_hands_one_back_too(tmp_path):
    model = FakeModel([pair((12, 12), (13, 13), (14, 14))], [submit(note="Paired three lines.")])
    service = make_service(tmp_path, model_client=model)
    target, truth = three_parts()

    job = service.finished(
        {"target": dumps(target).encode(), "reference_subtitle": srt(truth)}, ASKED
    )

    assert job["outcome"] == "synced"
    assert job["summary"]["agent"]["consulted"] is True
    assert starts_of(service.client.get(job["links"]["result"]).text) == truth
    # The three lines the agent paired are in place, so their counterparts are found.
    assert starts_of(service.client.get(job["links"]["matched_reference"]).text) == truth
    assert job["summary"]["matched_reference_lines"] == 30


def test_a_matched_reference_is_not_ready_before_the_job_is(tmp_path):
    service = make_service(tmp_path)
    target, reference, _ = pair_of_files()

    queued = service.submit({"target": target, "reference_subtitle": reference}, ASKED).json()

    assert refused(
        service.client.get(queued["links"]["self"] + "/matched_reference"), 409, "not_finished"
    )


def test_a_matched_reference_is_only_for_the_key_that_asked(tmp_path):
    service = make_service(tmp_path, labels=("al", "other"))
    target, reference, _ = pair_of_files()
    job = service.finished({"target": target, "reference_subtitle": reference}, ASKED)

    response = service.client.get(job["links"]["matched_reference"], headers=service.auth("other"))

    assert refused(response, 404, "not_found")
    assert service.anonymous().get(job["links"]["matched_reference"]).status_code == 401


def test_the_target_is_delivered_also_when_the_matched_reference_fails(
    tmp_path, monkeypatch, caplog
):
    service = make_service(tmp_path)
    target, reference, shared = pair_of_files()

    def broken(*args, **kwargs):
        raise RuntimeError("the disk is on fire")

    monkeypatch.setattr("retimer.pipeline.match_reference", broken)
    with caplog.at_level(logging.ERROR, logger="retimer.api"):
        job = service.finished({"target": target, "reference_subtitle": reference}, ASKED)

    assert (job["outcome"], job["error"]) == ("synced", None)
    assert set(job["links"]) == {"self", "result", "plan", "report"}
    assert starts_of(service.client.get(job["links"]["result"]).text) == shared
    assert job["summary"]["matched_reference_lines"] is None
    assert job["summary"]["warnings"] == [
        "No matched reference was made. It failed for a reason on the server's side."
    ]
    assert "the disk is on fire" not in json.dumps(job)
    assert "the matched reference failed" in caplog.text
    assert refused(
        service.client.get(job["links"]["self"] + "/matched_reference"),
        404,
        "no_matched_reference",
    )


def test_a_job_that_fails_while_writing_leaves_no_file_behind(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    target, reference, _ = pair_of_files()
    written = []

    def second_fails(subtitle, fmt):
        written.append(fmt)
        if len(written) == 2:
            raise OSError("no space left on device")
        return dumps(subtitle, fmt)

    monkeypatch.setattr("retimer.api.worker.dumps", second_fails)
    job = service.finished({"target": target, "reference_subtitle": reference}, ASKED)

    assert written == ["srt", "srt"]
    assert (job["outcome"], job["error"]["code"]) == ("failed", "internal_error")
    assert set(job["links"]) == {"self"}
    for name in (RESULT, MATCHED_REFERENCE, PLAN, REPORT):
        assert service.store.file(job["id"], name) is None


def test_a_job_deleted_takes_its_matched_reference_along(tmp_path):
    service = make_service(tmp_path)
    target, reference, _ = pair_of_files()
    job = service.finished({"target": target, "reference_subtitle": reference}, ASKED)
    assert service.store.file(job["id"], MATCHED_REFERENCE) is not None

    assert service.client.delete(job["links"]["self"]).status_code == 204

    assert not service.store.directory(job["id"]).exists()
