"""Jobs through the API: submit, run, and fetch what was produced."""

import json
import logging
import shutil
import subprocess
import time

import pytest

from retimer import dumps, loads
from retimer.api.store import REFERENCE_AUDIO, RESULT, TARGET
from retimer.api.worker import Worker, WorkerBusy
from retimer.audio import ALLOWED_CONTAINERS, AudioError, probe_audio
from retimer.cache import Cache
from retimer.pipeline import prepare
from retimer.providers import ProviderError

from .agent_helpers import FakeModel, pair, submit, three_parts
from .api_helpers import AUDIO, make_service, shifted, srt, starts_of
from .sync_helpers import SENTENCES, FakeClient, make_subtitle, make_transcript, spread

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def refused(response, status: int, code: str) -> bool:
    return response.status_code == status and response.json()["error"]["code"] == code


def test_a_job_against_a_reference_subtitle(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted(by_ms=3_500)

    response = service.client.post(
        "/v1/jobs",
        files={
            "target": ("Episode 01.ja.srt", target),
            "reference_subtitle": ("Episode 01.en.srt", reference),
        },
    )

    assert response.status_code == 202
    queued = response.json()
    assert response.headers["location"] == f"/v1/jobs/{queued['id']}"
    assert (queued["status"], queued["stage"], queued["outcome"]) == ("queued", "queued", None)
    assert queued["links"] == {"self": f"/v1/jobs/{queued['id']}"}
    assert queued["request"]["target"] == {
        "filename": "Episode 01.ja.srt",
        "format": "srt",
        "bytes": len(target),
        "lines": 30,
        "dialogue_lines": 30,
    }
    assert refused(service.client.get(queued["links"]["self"] + "/result"), 409, "not_finished")

    service.run()
    job = service.client.get(queued["links"]["self"]).json()

    assert (job["status"], job["stage"], job["outcome"]) == ("finished", None, "synced")
    assert job["error"] is None
    assert job["finished_at"] and job["expires_at"] > job["finished_at"]
    summary = job["summary"]
    assert summary["confidence"] >= 0.9
    assert summary["fit"] == "constant"
    assert [block["shift_ms"] for block in summary["blocks"]] == [-3_500]
    assert (summary["target_lines"], summary["delivered_lines"], summary["trimmed_lines"]) == (
        30,
        30,
        0,
    )
    assert summary["cost_usd"] > 0
    assert set(job["links"]) == {"self", "result", "plan", "report"}

    result = service.client.get(job["links"]["result"])
    assert result.headers["content-type"] == "application/x-subrip; charset=utf-8"
    assert (
        result.headers["content-disposition"] == "attachment; filename*=utf-8''Episode%2001.ja.srt"
    )
    assert starts_of(result.text) == spread(30)

    plan = service.client.get(job["links"]["plan"]).json()
    assert plan["outcome"] == "synced"
    assert plan["timing_ops"] == [{"op": "shift", "ms": -3_500}]

    report = service.client.get(job["links"]["report"]).json()
    assert report["outcome"] == "synced"
    assert report["reference"]["kind"] == "subtitle"
    assert report["anchors"] == 30


def test_a_job_against_audio_and_the_audio_is_deleted_when_it_is_done(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    service.remember_transcript(make_transcript(truth, duration_ms=truth[-1] + 60_000))
    target = srt([start + 8_000 for start in truth])

    response = service.submit({"target": target, "reference_audio": AUDIO})
    job_id = response.json()["id"]

    assert response.json()["request"]["reference_audio"] == {
        "filename": "reference_audio.bin",
        "container": "ogg",
        "codec": "opus",
        "bytes": len(AUDIO),
        "duration_ms": 180_000,
    }
    assert service.store.file(job_id, REFERENCE_AUDIO).read_bytes() == AUDIO

    service.run()
    job = service.client.get(f"/v1/jobs/{job_id}").json()

    assert job["outcome"] == "synced"
    assert starts_of(service.client.get(job["links"]["result"]).text) == truth
    assert service.client.get(job["links"]["report"]).json()["reference"]["kind"] == "audio"
    assert service.store.file(job_id, REFERENCE_AUDIO) is None
    assert service.store.file(job_id, TARGET) is not None
    assert service.provider.transcriptions == 0


def test_a_job_against_both(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    service.remember_transcript(make_transcript(truth))
    target = srt([start + 2_500 for start in truth])

    job = service.finished(
        {"target": target, "reference_audio": AUDIO, "reference_subtitle": srt(truth)}
    )

    assert job["outcome"] == "synced"
    assert service.client.get(job["links"]["report"]).json()["reference"]["kind"] == "both"


def test_the_options_decide_what_is_delivered(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    texts = [f"[sighs] {text}" if index == 3 else text for index, text in enumerate(SENTENCES[:20])]
    target = srt([start + 2_000 for start in truth], texts)
    options = {"remove_hearing_impaired": True, "output_format": "ass"}

    job = service.finished(
        {"target": target, "reference_subtitle": srt(truth)}, json.dumps(options)
    )

    assert job["request"]["options"]["remove_hearing_impaired"] is True
    assert job["request"]["output_format"] == "ass"
    result = service.client.get(job["links"]["result"])
    assert result.headers["content-type"] == "text/x-ssa; charset=utf-8"
    assert result.headers["content-disposition"] == 'attachment; filename="target.ass"'
    delivered = loads(result.text)
    assert delivered.format == "ass"
    assert [line.start_ms for line in delivered.lines] == truth
    assert delivered.lines[3].text == SENTENCES[3]
    plan = service.client.get(job["links"]["plan"]).json()
    assert {"op": "strip_text", "what": "hearing_impaired", "ids": [3]} in plan["content_ops"]


def test_a_file_in_sync_is_said_to_be(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)

    job = service.finished({"target": srt(truth), "reference_subtitle": srt(truth)})

    assert job["outcome"] == "already_in_sync"
    assert starts_of(service.client.get(job["links"]["result"]).text) == truth


def test_other_content_is_rejected_and_no_file_is_made(tmp_path):
    service = make_service(tmp_path)
    target = srt(spread(14), SENTENCES[:14])
    reference = srt(spread(14), SENTENCES[15:29])

    job = service.finished({"target": target, "reference_subtitle": reference})

    assert (job["status"], job["outcome"]) == ("finished", "rejected_no_match")
    assert set(job["links"]) == {"self", "plan", "report"}
    assert job["summary"]["delivered_lines"] == 0
    assert refused(service.client.get(job["links"]["self"] + "/result"), 404, "no_result")
    assert service.client.get(job["links"]["plan"]).json()["outcome"] == "rejected_no_match"
    assert service.store.file(job["id"], RESULT) is None


def test_the_agent_settles_what_the_routine_steps_leave_open(tmp_path):
    model = FakeModel([pair((12, 12), (13, 13), (14, 14))], [submit(note="Paired three lines.")])
    service = make_service(tmp_path, model_client=model)
    target, truth = three_parts()

    job = service.finished({"target": dumps(target).encode(), "reference_subtitle": srt(truth)})

    assert job["outcome"] == "synced"
    assert job["request"]["agent"] == "haiku"
    agent = job["summary"]["agent"]
    assert (agent["model"], agent["consulted"], agent["finished"], agent["turns"]) == (
        "claude-haiku-5-5",
        True,
        "submitted",
        2,
    )
    assert starts_of(service.client.get(job["links"]["result"]).text) == truth
    assert len(model.requests) == 2
    report = service.client.get(job["links"]["report"]).json()
    assert report["agent"]["pairs"] == {"12": 12, "13": 13, "14": 14}
    assert report["cost_usd"]["agent"] > 0


def test_a_job_can_name_its_model_or_do_without(tmp_path):
    model = FakeModel([pair((12, 12), (13, 13), (14, 14))], [submit()])
    service = make_service(tmp_path, model_client=model)
    target, truth = three_parts()
    files = {"target": dumps(target).encode(), "reference_subtitle": srt(truth)}

    without = service.finished(files, '{"agent": "off"}')
    with_opus = service.finished(files, '{"agent": "opus"}')

    assert without["request"]["agent"] == "off"
    assert without["summary"]["agent"] is None
    assert with_opus["summary"]["agent"]["model"] == "claude-opus-5-5"
    # The job named the model, and OpenRouter was asked for it under its name there.
    assert [request["model"] for request in model.requests] == ["anthropic/claude-opus-5.5"] * 2


def test_the_agent_gets_a_tighter_budget_than_the_command_line_gives_it(tmp_path):
    model = FakeModel(*[[pair((12, 12))] for _ in range(8)])
    service = make_service(tmp_path, model_client=model, agent_max_cost_usd=0.0001)
    target, truth = three_parts()

    job = service.finished({"target": dumps(target).encode(), "reference_subtitle": srt(truth)})

    assert job["summary"]["agent"]["finished"] == "budget"
    assert job["summary"]["agent"]["turns"] == 2
    assert job["outcome"] == "partial"


def test_without_a_key_for_the_agent_jobs_run_the_fixed_pipeline(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    files = {"target": target, "reference_subtitle": reference}

    job = service.finished(files)

    assert job["request"]["agent"] == "off"
    assert job["outcome"] == "synced"
    assert refused(service.submit(files, '{"agent": "haiku"}'), 422, "agent_unavailable")
    assert service.submit(files, '{"agent": "off"}').status_code == 202


def test_a_server_can_be_set_to_run_without_the_agent(tmp_path):
    service = make_service(tmp_path, model_client=FakeModel(), agent="off")
    target, truth = three_parts()

    job = service.finished({"target": dumps(target).encode(), "reference_subtitle": srt(truth)})

    assert job["request"]["agent"] == "off"
    assert job["summary"]["agent"] is None


@pytest.mark.parametrize(
    ("options", "problem"),
    [
        ('{"remove_karaoke": "sometimes"}', "remove_karaoke"),
        ('{"speed": 2}', "speed"),
        ('{"output_format": "vtt"}', "output_format"),
        ('{"agent": "gpt-9"}', "agent"),
        ('{"agent": "claude-opus-5-5"}', "agent"),
        ('{"audio_language": "ja; drop table"}', "audio_language"),
        ("[1, 2]", "options"),
        ("not json", "options"),
    ],
)
def test_options_that_are_not_understood_are_refused(tmp_path, options, problem):
    service = make_service(tmp_path)
    target, reference = shifted()

    response = service.submit({"target": target, "reference_subtitle": reference}, options)

    assert refused(response, 422, "invalid_options")
    assert problem in response.json()["error"]["message"]
    assert list(service.store.jobs_dir.iterdir()) == []


def test_language_hints_reach_the_job(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()

    response = service.submit(
        {"target": target, "reference_subtitle": reference},
        '{"audio_language": " JA ", "reference_language": "en"}',
    )

    options = response.json()["request"]["options"]
    assert (options["audio_language"], options["reference_language"]) == ("ja", "en")


def test_a_submission_has_to_have_the_right_parts(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    post = service.client.post

    assert refused(
        post("/v1/jobs", files={"reference_subtitle": ("a.srt", reference)}), 422, "missing_target"
    )
    assert refused(post("/v1/jobs", files={"target": ("a.srt", target)}), 422, "missing_reference")
    assert refused(
        post("/v1/jobs", files={"target": ("a.srt", target)}, data={"reference_subtitle": "text"}),
        422,
        "not_a_file",
    )
    assert refused(
        post(
            "/v1/jobs",
            files={"target": ("a.srt", target), "reference_subtitle": ("b.srt", reference)},
            data={"callback_url": "https://example.com"},
        ),
        422,
        "unknown_field",
    )
    assert refused(
        post(
            "/v1/jobs",
            files=[
                ("target", ("a.srt", target)),
                ("target", ("b.srt", target)),
                ("reference_subtitle", ("c.srt", reference)),
            ],
        ),
        422,
        "repeated_field",
    )
    assert refused(post("/v1/jobs", json={"target": "text"}), 415, "unsupported_media_type")
    assert refused(
        post(
            "/v1/jobs", content=b"junk", headers={"Content-Type": "multipart/form-data; boundary=x"}
        ),
        400,
        "bad_request",
    )
    assert list(service.store.jobs_dir.iterdir()) == []
    assert service.client.get("/v1/jobs").json() == {"jobs": []}


def test_too_many_parts_are_refused(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    files = [("target", ("a.srt", target)), ("reference_subtitle", ("b.srt", reference))]
    files += [("reference_audio", (f"{index}.ogg", AUDIO)) for index in range(3)]

    response = service.client.post("/v1/jobs", files=files)

    assert refused(response, 400, "bad_request")
    assert "Too many files" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("content", "said"),
    [
        (b"this is not a subtitle at all", "not a recognizable subtitle format"),
        (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", "unsupported subtitle format"),
        (b"\xff\xfe" + bytes(range(256)) * 9 + b"\x00\xd8", "could not be"),
        (b"", "empty"),
    ],
)
def test_a_target_that_is_no_subtitle_is_refused_at_once(tmp_path, content, said):
    service = make_service(tmp_path)
    _, reference = shifted()

    response = service.submit({"target": content, "reference_subtitle": reference})

    assert refused(response, 422, "invalid_target")
    assert said in response.json()["error"]["message"]
    assert list(service.store.jobs_dir.iterdir()) == []


def test_a_reference_that_is_no_subtitle_is_refused_at_once(tmp_path):
    service = make_service(tmp_path)
    target, _ = shifted()

    response = service.submit({"target": target, "reference_subtitle": b"\x00\x01\x02 nothing"})

    assert refused(response, 422, "invalid_reference_subtitle")
    assert list(service.store.jobs_dir.iterdir()) == []


def test_a_subtitle_larger_than_a_film_needs_is_refused(tmp_path):
    service = make_service(tmp_path, max_subtitle_lines=40, max_dialogue_lines=25)
    _, reference = shifted(20)
    long_reference = srt(spread(30))

    many_lines = service.submit(
        {
            "target": srt(spread(41, step_ms=3_000), ["hello there"] * 41),
            "reference_subtitle": reference,
        }
    )
    much_dialogue = service.submit(
        {"target": srt(spread(20)), "reference_subtitle": long_reference}
    )

    assert refused(many_lines, 422, "too_many_lines")
    assert "41 lines" in many_lines.json()["error"]["message"]
    assert refused(much_dialogue, 422, "too_many_lines")
    assert "reference_subtitle has 30 lines of dialogue" in much_dialogue.json()["error"]["message"]


def test_a_line_no_viewer_could_read_is_refused(tmp_path):
    service = make_service(tmp_path)
    _, reference = shifted(3)
    cut_wrong = b"1\n00:00:01,000 --> 00:00:02,000\n" + b"(" * 300_000 + b"\n"
    started = time.monotonic()

    response = service.submit({"target": cut_wrong, "reference_subtitle": reference})

    assert refused(response, 422, "invalid_target")
    assert "300000 characters" in response.json()["error"]["message"]
    assert time.monotonic() - started < 5


def test_a_long_drawing_is_not_a_long_line(tmp_path):
    service = make_service(tmp_path)
    truth = spread(20)
    target = make_subtitle([start + 2_000 for start in truth])
    target.ssa.events[5].text = "{\\p1}" + "m 0 0 l 100 0 100 100 0 100 " * 2_000 + "{\\p0}"
    options = '{"remove_hearing_impaired": true, "remove_symbols": true, "output_format": "ass"}'

    job = service.finished(
        {"target": dumps(target, "ass").encode(), "reference_subtitle": srt(truth)}, options
    )

    assert job["outcome"] == "synced"
    assert job["summary"]["delivered_lines"] == 20


def test_hostile_text_is_cleaned_in_time_proportional_to_its_length():
    from retimer.clean import normalize_for_match, remove_hearing_impaired, remove_symbols

    count = 200_000
    lines = [
        "[" * count,
        "(" * count,
        "（あ" * (count // 2),
        "[a " * (count // 3),
        "a" + "\u3000" * count + "b",
        "x\\N" + " " * count + "b",
        "{}" * (count // 2) + "x",
        "# " * (count // 2) + "x",
        "a" + "\u3000" * count + "{\\i1}x",
    ]
    started = time.monotonic()

    for line in lines:
        remove_hearing_impaired(line)
        remove_symbols(line)
        normalize_for_match(line)

    # Each of these took minutes before the patterns were made to read a line once.
    assert time.monotonic() - started < 5


def test_audio_that_cannot_be_read_is_refused_at_once(tmp_path):
    def probe(path):
        raise AudioError("ffprobe failed: /srv/data/jobs/x/reference_audio: Invalid data")

    service = make_service(tmp_path, probe=probe)
    target, _ = shifted()

    response = service.submit({"target": target, "reference_audio": b"not audio"})

    assert refused(response, 422, "invalid_reference_audio")
    message = response.json()["error"]["message"]
    assert all(container in message for container in ALLOWED_CONTAINERS)
    assert "/srv" not in message and "ffprobe" not in message
    assert list(service.store.jobs_dir.iterdir()) == []


def test_audio_longer_than_allowed_is_refused(tmp_path):
    service = make_service(tmp_path, max_audio_minutes=2)
    target, _ = shifted()

    response = service.submit({"target": target, "reference_audio": AUDIO})

    assert refused(response, 422, "audio_too_long")
    assert "3 minutes" in response.json()["error"]["message"]


def test_audio_over_its_size_limit_is_refused(tmp_path):
    service = make_service(tmp_path, max_audio_bytes=1_000)
    target, _ = shifted()

    response = service.submit({"target": target, "reference_audio": b"x" * 1_001})

    assert refused(response, 413, "file_too_large")
    assert "reference_audio" in response.json()["error"]["message"]


def test_the_name_a_client_gives_a_file_is_only_ever_a_name(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()

    response = service.client.post(
        "/v1/jobs",
        files={
            "target": ("../../../etc/cron.d/x\x00y\n.srt", target),
            "reference_subtitle": ("C:\\Users\\me\\ref.srt", reference),
        },
    )
    job = response.json()
    service.run()

    # The client library escapes the control characters before they are sent.
    assert job["request"]["target"]["filename"] == "x%00y%0A.srt"
    assert job["request"]["reference_subtitle"]["filename"] == "ref.srt"
    assert sorted(path.name for path in service.store.directory(job["id"]).iterdir()) == [
        "plan.json",
        "reference_subtitle",
        "report.json",
        "result",
        "target",
    ]
    result = service.client.get(f"/v1/jobs/{job['id']}/result")
    assert result.headers["content-disposition"] == "attachment; filename*=utf-8''x%2500y%250A.srt"
    assert not (tmp_path / "etc").exists()


def test_a_file_name_is_reduced_to_a_name():
    from retimer.api.app import _disposition, _safe_name

    assert _safe_name("../../etc/passwd", "target") == "passwd"
    assert _safe_name("C:\\Users\\me\\ep 1.srt", "target") == "ep 1.srt"
    assert _safe_name("a\x00b\r\nSet-Cookie: x.srt", "target") == "abSet-Cookie: x.srt"
    assert _safe_name("..", "target") == "target"
    assert _safe_name("", "target") == "target"
    assert _safe_name(None, "target") == "target"
    assert len(_safe_name("x" * 500 + ".srt", "target")) == 120
    # A name can never break out of the header it is sent back in.
    assert _disposition('a"b.srt') == "attachment; filename*=utf-8''a%22b.srt"
    assert "\n" not in _disposition("a\nb.srt")


def test_a_japanese_file_name_survives(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()

    response = service.client.post(
        "/v1/jobs",
        files={"target": ("第01話.ja.ass", target), "reference_subtitle": ("ref.srt", reference)},
    )
    service.run()
    result = service.client.get(f"/v1/jobs/{response.json()['id']}/result")

    assert response.json()["request"]["target"]["filename"] == "第01話.ja.ass"
    assert result.headers["content-disposition"] == (
        "attachment; filename*=utf-8''%E7%AC%AC01%E8%A9%B1.ja.srt"
    )


class FailingProvider(FakeClient):
    def embed(self, texts, *, model):
        raise ProviderError("/embeddings failed: HTTP 402: account acct_123 is out of credit")


def test_a_provider_failure_closes_the_job_without_details(tmp_path, caplog):
    service = make_service(tmp_path)
    service.worker.services.openrouter = FailingProvider()
    target, reference = shifted()

    with caplog.at_level(logging.ERROR, logger="retimer.api"):
        job = service.finished({"target": target, "reference_subtitle": reference})

    assert (job["status"], job["outcome"]) == ("finished", "failed")
    assert job["error"]["code"] == "provider_error"
    assert "acct_123" not in json.dumps(job)
    assert set(job["links"]) == {"self"}
    for name in ("result", "plan", "report"):
        assert service.client.get(f"{job['links']['self']}/{name}").status_code == 404
    # The cause is in the server's log, under the id of the job.
    assert "acct_123" in caplog.text and job["id"] in caplog.text


def test_a_failed_job_with_audio_counts_against_the_budget(tmp_path):
    service = make_service(tmp_path)
    service.worker.services.openrouter = FailingProvider()
    service.remember_transcript(make_transcript(spread(20)))
    target, _ = shifted(20)

    job = service.finished({"target": target, "reference_audio": AUDIO})

    assert job["outcome"] == "failed"
    # Three minutes of audio, at the price of transcribing them.
    assert service.store.spent_today() == pytest.approx(180 * 0.0000111)


def test_any_other_failure_closes_the_job_too(tmp_path, monkeypatch, caplog):
    service = make_service(tmp_path)
    target, reference = shifted()

    def broken(*args, **kwargs):
        raise ZeroDivisionError("a bug in /home/app/src/retimer/fit.py")

    monkeypatch.setattr("retimer.pipeline.match_lines", broken)
    with caplog.at_level(logging.ERROR, logger="retimer.api"):
        job = service.finished({"target": target, "reference_subtitle": reference})

    assert job["outcome"] == "failed"
    assert job["error"] == {
        "code": "internal_error",
        "message": "The job failed for a reason on the server's side.",
    }
    assert "ZeroDivisionError" in caplog.text
    # The worker goes on with the next job.
    monkeypatch.undo()
    assert (
        service.finished({"target": target, "reference_subtitle": reference})["outcome"] == "synced"
    )


def test_the_stage_of_a_running_job_is_reported(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    truth = spread(20)
    service.remember_transcript(make_transcript(truth))
    seen = []
    set_stage = service.store.set_stage

    def record(job_id, stage):
        set_stage(job_id, stage)
        job = service.client.get(f"/v1/jobs/{job_id}").json()
        seen.append((job["status"], job["stage"]))

    monkeypatch.setattr(service.store, "set_stage", record)
    job = service.finished(
        {"target": srt([start + 2_000 for start in truth]), "reference_audio": AUDIO}
    )

    assert seen == [
        ("running", "transcribing"),
        ("running", "matching"),
        ("running", "deciding"),
        ("running", "writing"),
    ]
    assert (job["status"], job["stage"]) == ("finished", None)


def test_the_routine_steps_say_which_one_begins(tmp_path):
    steps = []

    prepare(
        make_subtitle([start + 2_000 for start in spread(20)]),
        reference=make_subtitle(spread(20)),
        client=FakeClient(),
        cache=Cache(tmp_path / "cache"),
        progress=steps.append,
    )

    assert steps == ["matching"]


def test_jobs_are_listed_newest_first(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    files = {"target": target, "reference_subtitle": reference}
    first = service.finished(files)["id"]
    time.sleep(0.01)
    second = service.submit(files).json()["id"]

    listed = service.client.get("/v1/jobs").json()["jobs"]

    assert [job["id"] for job in listed] == [second, first]
    assert [job["status"] for job in listed] == ["queued", "finished"]
    assert len(service.client.get("/v1/jobs?limit=1").json()["jobs"]) == 1


def test_a_job_can_be_deleted_with_everything_it_holds(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    job = service.finished({"target": target, "reference_subtitle": reference})
    directory = service.store.directory(job["id"])
    assert directory.exists()

    response = service.client.delete(job["links"]["self"])

    assert response.status_code == 204
    assert not directory.exists()
    assert refused(service.client.get(job["links"]["self"]), 404, "not_found")
    assert refused(service.client.get(job["links"]["result"]), 404, "not_found")
    assert refused(service.client.delete(job["links"]["self"]), 404, "not_found")


def test_a_running_job_cannot_be_deleted(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    job_id = service.submit({"target": target, "reference_subtitle": reference}).json()["id"]
    service.store.claim()

    assert refused(service.client.delete(f"/v1/jobs/{job_id}"), 409, "job_running")
    assert refused(service.client.get(f"/v1/jobs/{job_id}/plan"), 409, "not_finished")


def test_a_job_and_its_files_are_gone_after_the_retention_period(tmp_path, monkeypatch):
    service = make_service(tmp_path, retention_hours=6)
    target, reference = shifted()
    job = service.finished({"target": target, "reference_subtitle": reference})
    directory = service.store.directory(job["id"])
    finished_at = time.time()

    monkeypatch.setattr(service.store, "_clock", lambda: finished_at + 5 * 3600)
    service.worker.housekeeping()
    assert service.client.get(job["links"]["result"]).status_code == 200

    monkeypatch.setattr(service.store, "_clock", lambda: finished_at + 6 * 3600 + 60)
    service.worker.housekeeping()

    assert refused(service.client.get(job["links"]["self"]), 404, "not_found")
    assert refused(service.client.get(job["links"]["result"]), 404, "not_found")
    assert not directory.exists()


def test_the_worker_runs_jobs_in_the_background_and_recovers_what_was_interrupted(tmp_path):
    service = make_service(tmp_path)
    target, reference = shifted()
    files = {"target": target, "reference_subtitle": reference}
    interrupted = service.submit(files).json()["id"]
    service.store.claim()  # as if the service had stopped in the middle of this job
    waiting = service.submit(files).json()["id"]

    service.worker.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs = [
                service.client.get(f"/v1/jobs/{job_id}").json() for job_id in (interrupted, waiting)
            ]
            if all(job["status"] == "finished" for job in jobs):
                break
            time.sleep(0.05)
    finally:
        service.worker.stop()

    assert [job["outcome"] for job in jobs] == ["synced", "synced"]


def test_only_one_worker_runs_on_a_data_directory(tmp_path):
    service = make_service(tmp_path)
    second = Worker(service.store, service.settings, service.worker.services)

    service.worker.start()
    try:
        with pytest.raises(WorkerBusy, match="--no-worker"):
            second.start()
    finally:
        service.worker.stop()

    second.start()
    second.stop()


def test_the_application_starts_and_stops_its_worker(tmp_path):
    from starlette.testclient import TestClient

    from retimer.api import create_app
    from retimer.api.worker import Services

    service = make_service(tmp_path)
    target, reference = shifted()
    services = Services(cache=service.cache, openrouter=FakeClient())
    app = create_app(service.settings, services=services, probe=None)

    with TestClient(app, base_url="https://testserver", headers=service.auth()) as client:
        response = client.post(
            "/v1/jobs",
            files={"target": ("a.srt", target), "reference_subtitle": ("b.srt", reference)},
        )
        link = response.json()["links"]["self"]
        deadline = time.monotonic() + 20
        while client.get(link).json()["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.get(link).json()["outcome"] == "synced"
    assert all(not thread.is_alive() for thread in app.state.worker._threads)


def speech_file(path, seconds: float = 2.0, *arguments: str):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=300:duration={seconds}",
        ]
        + list(arguments)
        + [str(path)],
        check=True,
    )
    return path


@needs_ffmpeg
def test_real_audio_is_recognized_whatever_it_is_called(tmp_path):
    cases = {
        "a.ogg": ("ogg", "opus", ["-c:a", "libopus", "-b:a", "24k"]),
        "b.mp3": ("mp3", "mp3", []),
        "c.m4a": ("mov,mp4,m4a,3gp,3g2,mj2", "aac", []),
        "d.mka": ("matroska,webm", "opus", ["-c:a", "libopus"]),
        "e.flac": ("flac", "flac", []),
        "f.wav": ("wav", "pcm_s16le", []),
    }
    for name, (container, codec, arguments) in cases.items():
        made = speech_file(tmp_path / name, 2.0, *arguments)
        # The server stores an upload under a name of its own, without an extension.
        stored = made.rename(tmp_path / "reference_audio")
        info = probe_audio(stored)
        assert (info.container, info.codec) == (container, codec), name
        assert 1_900 <= info.duration_ms <= 2_200, name


@needs_ffmpeg
def test_a_file_that_names_other_files_is_not_opened(tmp_path):
    secret = tmp_path / "secret.wav"
    speech_file(secret)
    playlist = tmp_path / "playlist"
    playlist.write_text(f"ffconcat version 1.0\nfile '{secret}'\n")
    hls = tmp_path / "stream.m3u8"
    hls.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\n{secret}\n#EXT-X-ENDLIST\n")
    remote = tmp_path / "remote"
    remote.write_text("ffconcat version 1.0\nfile 'http://127.0.0.1:9/audio.ogg'\n")
    text = tmp_path / "text"
    text.write_text("just some words, no audio at all\n" * 50)

    for path in (playlist, hls, remote, text):
        with pytest.raises(AudioError):
            probe_audio(path)


@needs_ffmpeg
def test_a_job_with_real_audio_checks_and_cuts_it(tmp_path):
    from .api_helpers import fake_probe  # noqa: F401  (the real probe is used here)

    service = make_service(tmp_path, probe=probe_audio, max_audio_minutes=1)
    target, _ = shifted()
    audio = speech_file(tmp_path / "speech.ogg", 3.0, "-c:a", "libopus", "-b:a", "24k").read_bytes()
    playlist = b"ffconcat version 1.0\nfile '/etc/hostname'\n"
    long_audio = speech_file(
        tmp_path / "long.ogg", 90.0, "-c:a", "libopus", "-b:a", "16k"
    ).read_bytes()

    accepted = service.submit({"target": target, "reference_audio": audio})
    hostile = service.submit({"target": target, "reference_audio": playlist})
    too_long = service.submit({"target": target, "reference_audio": long_audio})

    assert accepted.status_code == 202
    described = accepted.json()["request"]["reference_audio"]
    assert (described["container"], described["codec"]) == ("ogg", "opus")
    assert 2_900 <= described["duration_ms"] <= 3_200
    assert refused(hostile, 422, "invalid_reference_audio")
    assert refused(too_long, 422, "audio_too_long")
    assert len(list(service.store.jobs_dir.iterdir())) == 1


def test_a_running_service_is_a_benchmark_system(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from retimer.api import create_app
    from retimer.api.worker import Services
    from retimer.bench.cases import Case, LoadedCase, Reference, generate_cases, load_cases
    from retimer.bench.harness import run_one
    from retimer.bench.runners import ApiRunner, builtin_runners

    from .bench_helpers import write_seeds

    assert builtin_runners()["api"].unavailable() == "RETIMER_API_URL and RETIMER_API_KEY not set"
    monkeypatch.setenv("RETIMER_API_URL", "https://retimer.example")
    assert builtin_runners()["api"].unavailable() == "RETIMER_API_KEY not set"

    seeds = write_seeds(tmp_path / "seeds")
    generate_cases(seeds, tmp_path / "media", tmp_path / "cases")
    service = make_service(tmp_path)
    services = Services(cache=service.cache, openrouter=FakeClient())
    app = create_app(service.settings, services=services)

    with TestClient(app, base_url="https://testserver", headers=service.auth()) as client:
        runner = ApiRunner(client=client, poll_s=0.02)
        assert runner.unavailable() is None
        for name in ("late-8s", "blocks-2", "rate-25-to-23.976", "in-sync", "noise-removed"):
            (case,) = load_cases(tmp_path / "cases", f"alpha.en.subtitle-ja.{name}")
            record = run_one(case, runner, tmp_path / "work" / name)
            assert record["error"] is None, name
            assert record["metrics"]["success"], name
            assert record["outcome"] == case.case.expected_outcome
            assert record["details"]["plan"]["outcome"] == record["outcome"]
            # The file a client gets is the plan run on the target, nothing else.
            assert record["details"]["file_matches_plan"] is True, name
            assert record["cost_usd"] >= 0
        # Another film as the reference: the service returns a verdict and no file.
        other = tmp_path / "other"
        other.mkdir()
        (other / "target.srt").write_bytes(srt(spread(14), SENTENCES[:14]))
        (other / "reference.srt").write_bytes(srt(spread(14), SENTENCES[15:29]))
        wrong = LoadedCase(
            directory=other,
            case=Case(
                id="other-film",
                seed="alpha",
                family="wrong_content",
                recipe="wrong-content",
                target="target.srt",
                target_language="en",
                reference=Reference(kind="subtitle", subtitle="reference.srt"),
                expected_outcome="rejected_no_match",
            ),
        )
        rejected = run_one(wrong, runner, tmp_path / "work" / "wrong")
        assert rejected["error"] is None
        assert rejected["outcome"] == "rejected_no_match"
        assert rejected["metrics"]["success"]
        # Every job is deleted again when its result is fetched.
        assert client.get("/v1/jobs").json() == {"jobs": []}


def test_the_benchmark_waits_when_the_service_says_so(tmp_path):
    import httpx

    from retimer.bench.cases import generate_cases, load_cases
    from retimer.bench.harness import run_one
    from retimer.bench.runners import ApiRunner

    from .bench_helpers import write_seeds

    generate_cases(write_seeds(tmp_path / "seeds"), tmp_path / "media", tmp_path / "cases")
    (case,) = load_cases(tmp_path / "cases", "alpha.en.subtitle-ja.late-8s")
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.method)
        if len(asked) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {}})
        return httpx.Response(503, text="down for maintenance")

    client = httpx.Client(transport=httpx.MockTransport(answer), base_url="https://retimer.example")
    record = run_one(case, ApiRunner(client=client), tmp_path / "work")

    assert asked == ["POST", "POST"]
    assert record["outcome"] == "failed"
    assert "503" in record["error"]


def test_a_script_full_of_typesetting_is_taken_for_its_dialogue(tmp_path):
    # Fansub scripts redraw a sign for every frame, also in the style of the
    # dialogue, and give the styles of a song names of their own. Such a file
    # has a few hundred spoken lines among many thousand.
    service = make_service(tmp_path, max_dialogue_lines=40)
    target, reference = shifted(30)
    script = dumps(loads(reference.decode("utf-8")), "ass")

    def stamp(ms: int) -> str:
        return f"0:{ms // 60000:02d}:{ms % 60000 / 1000:05.2f}"

    frames = "".join(
        f"Dialogue: 0,{stamp(10_000 + 40 * index)},{stamp(10_040 + 40 * index)},Default,,0,0,0,"
        f"fx,{{\\pos(960,1024)\\clip(0,{index},1920,{index + 1})}}Why do I fight?\n"
        for index in range(500)
    )
    song = "".join(
        f"Dialogue: 0,{stamp(60_000 + 100 * index)},{stamp(60_500 + 100 * index)},"
        f"Wonder Stella {script_name},,0,0,0,fx,{{\\pos({index},40)}}a\n"
        for index in range(300)
        for script_name in ("Romaji", "English")
    )

    job = service.finished(
        {"target": target, "reference_subtitle": (script + frames + song).encode("utf-8")}
    )

    assert job["request"]["reference_subtitle"]["lines"] == 30 + 500 + 600
    assert job["request"]["reference_subtitle"]["dialogue_lines"] == 30
    assert job["outcome"] == "synced"
    assert [block["shift_ms"] for block in job["summary"]["blocks"]] == [-3_500]


def test_a_reference_without_dialogue_is_named_as_the_reason(tmp_path):
    service = make_service(tmp_path)
    target, _ = shifted(20)
    signs = "".join(
        f"Dialogue: 0,0:00:{index:02d}.00,0:00:{index:02d}.90,Signs,,0,0,0,,"
        f"{{\\pos(100,{index})}}Bakery {index}\n"
        for index in range(1, 30)
    )
    script = (
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize\nStyle: Signs,Arial,40\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    job = service.finished(
        {"target": target, "reference_subtitle": (script + signs).encode("utf-8")},
        '{"agent": "off"}',
    )

    assert job["outcome"] == "partial"
    assert job["request"]["reference_subtitle"]["dialogue_lines"] == 0
    assert "The reference subtitle has no dialogue" in job["summary"]["warnings"][0]
    assert "Timing is unchanged" in job["summary"]["warnings"][-1]
