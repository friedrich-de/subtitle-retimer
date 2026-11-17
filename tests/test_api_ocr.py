"""Reading an image subtitle through the API: POST /v1/ocr, then the job like any other."""

import gzip
import json

import py7zr

from retimer import loads
from retimer.api.app import _too_large

from .api_helpers import make_service, shifted
from .pgs_helpers import FakeReader, simple_stream

STREAM = simple_stream(3)
MARGIN = 16


def reading_service(tmp_path, texts=None, **overrides):
    service = make_service(tmp_path, **overrides)
    service.provider = FakeReader(texts)
    service.worker.services.openrouter = service.provider
    return service


def submit(service, content=STREAM, name="Episode 01.sup", options=None, **kwargs):
    data = {"options": json.dumps(options)} if options is not None else None
    return service.client.post("/v1/ocr", files={"subtitle": (name, content)}, data=data, **kwargs)


def finished(service, **kwargs) -> dict:
    response = submit(service, **kwargs)
    assert response.status_code == 202, response.text
    service.run()
    return service.client.get(response.json()["links"]["self"]).json()


def test_an_image_subtitle_comes_back_as_text_with_its_times(tmp_path):
    service = reading_service(tmp_path, {20 + MARGIN: "一行目\n二行目", 24 + MARGIN: "(物音)"})

    response = submit(service, options={"language": "ja"})

    assert response.status_code == 202
    queued = response.json()
    assert queued["status"] == "queued"
    assert response.headers["location"] == f"/v1/jobs/{queued['id']}"
    assert queued["request"]["kind"] == "ocr"
    assert queued["request"]["subtitle"] == {
        "filename": "Episode 01.sup",
        "bytes": len(STREAM),
        "packing": "sup",
        "stream_bytes": len(STREAM),
    }
    assert queued["request"]["model"] == "haiku"

    service.run()
    job = service.client.get(queued["links"]["self"]).json()
    assert (job["status"], job["outcome"], job["error"]) == ("finished", "transcribed", None)
    assert job["summary"]["lines"] == 3
    assert job["summary"]["pictures"] == 3
    assert job["summary"]["model"] == "haiku"
    assert job["summary"]["cost_usd"] == 0.002
    assert set(job["links"]) == {"self", "result", "report"}

    result = service.client.get(job["links"]["result"])
    assert result.headers["content-type"] == "application/x-subrip; charset=utf-8"
    assert "Episode%2001.srt" in result.headers["content-disposition"]
    lines = loads(result.text).lines
    assert [(line.start_ms, line.end_ms) for line in lines] == [
        (10_000, 12_500),
        (14_000, 16_500),
        (18_000, 20_500),
    ]
    assert [line.text for line in lines][:2] == ["一行目\\N二行目", "(物音)"]
    report = service.client.get(job["links"]["report"]).json()
    assert report["reading"]["distinct_pictures"] == 3
    assert report["screen"] == [1920, 1080]
    assert "The subtitles are in Japanese." in service.provider.requests[0]["instructions"]


def test_an_archive_is_taken_as_a_site_serves_it(tmp_path):
    service = reading_service(tmp_path)
    packed = tmp_path / "packed.7z"
    with py7zr.SevenZipFile(packed, "w") as archive:
        archive.writestr(STREAM, "Show 01 BDSUP.sup")

    job = finished(service, content=packed.read_bytes(), name="Show 01 BDSUP.7z")

    assert job["outcome"] == "transcribed"
    assert job["request"]["subtitle"]["packing"] == "7z"
    assert job["request"]["subtitle"]["stream_bytes"] == len(STREAM)
    result = service.client.get(job["links"]["result"])
    assert "Show%2001%20BDSUP.srt" in result.headers["content-disposition"]

    job = finished(service, content=gzip.compress(STREAM), name="Show 01.sup.gz")
    assert job["request"]["subtitle"]["packing"] == "gzip"
    result = service.client.get(job["links"]["result"])
    assert "Show%2001.srt" in result.headers["content-disposition"]


def test_the_options_choose_the_model_and_the_format(tmp_path):
    service = reading_service(tmp_path)

    job = finished(service, options={"model": "sonnet", "output_format": "ass"})

    assert job["summary"]["model"] == "sonnet"
    assert service.provider.requests[0]["model"] == "anthropic/claude-sonnet-5.5"
    result = service.client.get(job["links"]["result"])
    assert result.headers["content-type"] == "text/x-ssa; charset=utf-8"
    assert loads(result.text).format == "ass"


def test_the_server_decides_the_model_when_the_request_names_none(tmp_path):
    service = reading_service(tmp_path, ocr_model="gemini-flash")

    job = finished(service)

    assert job["summary"]["model"] == "gemini-flash"


def test_the_stream_is_deleted_when_the_job_is_done(tmp_path):
    service = reading_service(tmp_path)
    response = submit(service)
    directory = service.store.directory(response.json()["id"])
    assert sorted(path.name for path in directory.iterdir()) == ["image_subtitle"]

    service.run()

    assert sorted(path.name for path in directory.iterdir()) == ["report.json", "result"]


def test_what_is_not_an_image_subtitle_is_refused_when_it_is_submitted(tmp_path):
    service = reading_service(tmp_path)
    target, _ = shifted()

    response = submit(service, content=target, name="episode.srt")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_subtitle"
    assert "neither a PGS stream" in response.json()["error"]["message"]
    assert service.store.list("al") == []
    assert list(service.store.jobs_dir.iterdir()) == []


def test_a_request_has_to_have_the_right_shape(tmp_path):
    service = reading_service(tmp_path)

    missing = service.client.post("/v1/ocr", files={"other": ("a.sup", STREAM)})
    assert (missing.status_code, missing.json()["error"]["code"]) == (422, "unknown_field")
    empty = service.client.post("/v1/ocr", files={"subtitle": ("a.sup", b"")})
    assert (empty.status_code, empty.json()["error"]["code"]) == (422, "invalid_subtitle")
    as_text = service.client.post(
        "/v1/ocr", data={"subtitle": "text"}, files={"options": ("o.json", b"{}")}
    )
    assert as_text.status_code == 422
    no_file = service.client.post("/v1/ocr", files={"options": (None, "{}")})
    assert (no_file.status_code, no_file.json()["error"]["code"]) == (422, "missing_subtitle")
    not_a_form = service.client.post("/v1/ocr", content=STREAM)
    assert not_a_form.status_code == 415
    for options in ({"model": "gpt"}, {"speed": "fast"}, {"language": "japanese!"}):
        refused = submit(service, options=options)
        assert (refused.status_code, refused.json()["error"]["code"]) == (422, "invalid_options")
    assert service.store.list("al") == []


def test_reading_needs_a_key_like_everything_else(tmp_path):
    service = reading_service(tmp_path)

    response = service.anonymous().post("/v1/ocr", files={"subtitle": ("a.sup", STREAM)})

    assert response.status_code == 401
    assert service.provider.requests == []


def test_an_upload_and_what_it_unpacks_to_have_limits(tmp_path):
    service = reading_service(
        tmp_path, max_image_subtitle_bytes=4_096, max_image_stream_bytes=20_000
    )
    bomb = gzip.compress(STREAM + b"\x00" * 100_000)
    assert len(bomb) < 4_096

    unpacked = submit(service, content=bomb, name="a.sup.gz")
    assert (unpacked.status_code, unpacked.json()["error"]["code"]) == (413, "file_too_large")
    assert "once it is unpacked" in unpacked.json()["error"]["message"]

    large = submit(service, content=simple_stream(1) + b"\x00" * 80_000)
    assert large.status_code == 413
    assert large.json()["error"]["code"] == "body_too_large"
    assert "packs to a few megabytes" in _too_large(90 * 1024 * 1024, "/v1/ocr")
    assert list(service.store.jobs_dir.iterdir()) == []


def test_the_request_limit_of_the_proxy_holds_for_image_subtitles_too(tmp_path):
    service = reading_service(
        tmp_path, max_request_bytes=3_000, max_subtitle_bytes=2_000, max_audio_bytes=2_000
    )

    response = submit(service, content=simple_stream(40))

    assert response.status_code == 413


def test_what_reading_costs_counts_against_the_budget_of_the_day(tmp_path):
    service = reading_service(tmp_path, daily_budget_usd=0.003)

    assert finished(service)["summary"]["cost_usd"] == 0.002
    assert finished(service, content=simple_stream(4))["outcome"] == "transcribed"

    refused = submit(service, content=simple_stream(5))
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "budget_exhausted")


def test_a_file_that_costs_too_much_to_read_fails_and_is_still_counted(tmp_path):
    service = reading_service(tmp_path, ocr_max_cost_usd=0.001)
    service.provider.cost_usd = 0.01

    job = finished(service, content=simple_stream(40))

    assert (job["outcome"], job["error"]["code"]) == ("failed", "ocr_budget_exceeded")
    assert "result" not in job["links"]
    assert service.store.spent_today() >= 0.01


def test_pictures_without_text_fail_the_job_with_a_reason(tmp_path):
    service = reading_service(tmp_path, {20 + MARGIN: "", 24 + MARGIN: "", 28 + MARGIN: ""})

    job = finished(service)

    assert (job["outcome"], job["error"]["code"]) == ("failed", "no_text")


def test_a_stream_that_breaks_off_wrongly_fails_the_job(tmp_path):
    service = reading_service(tmp_path)

    job = finished(service, content=STREAM + b"this is no segment at all")

    assert (job["outcome"], job["error"]["code"]) == ("failed", "invalid_image_subtitle")
    assert service.provider.requests == []


def test_jobs_of_both_kinds_are_listed_and_deleted_alike(tmp_path):
    service = reading_service(tmp_path)
    target, reference = shifted()
    service.submit({"target": target, "reference_subtitle": reference})
    reading = finished(service)

    listed = service.client.get("/v1/jobs").json()["jobs"]

    assert sorted(job["request"].get("kind", "sync") for job in listed) == ["ocr", "sync"]
    assert service.client.delete(reading["links"]["self"]).status_code == 204
    assert service.client.get(reading["links"]["self"]).status_code == 404
