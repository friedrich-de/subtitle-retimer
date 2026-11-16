"""The service stays closed: what gets through the gate, and what it holds back."""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from retimer.api import ApiSettings, ConfigError, create_app
from retimer.api.auth import format_entry, new_key
from retimer.api.server import RedactingFormatter, problems

from .api_helpers import make_service, shifted

JOB = "0123456789abcdef0123456789abcdef"


def every_route(app) -> list[tuple[str, str]]:
    """Each method and path the application answers, with a job id filled in."""
    found = []
    for route in app.routes:
        assert isinstance(route, Route), f"a route of an unknown kind: {route!r}"
        for method in sorted(route.methods - {"HEAD"}):
            found.append((method, route.path.replace("{job_id}", JOB)))
    return found


def refused(response, status: int, code: str) -> bool:
    return response.status_code == status and response.json()["error"]["code"] == code


def test_every_route_but_the_health_check_needs_a_key(tmp_path):
    service = make_service(tmp_path)
    routes = every_route(service.app)
    stranger = service.anonymous()

    assert ("POST", "/v1/jobs") in routes and len(routes) >= 8
    for method, path in routes:
        response = stranger.request(method, path)
        if path == "/healthz":
            assert response.status_code == 200
            continue
        assert refused(response, 401, "unauthorized"), (method, path)
        assert response.headers["www-authenticate"] == "Bearer"


def test_the_same_goes_for_a_development_server_except_its_documentation(tmp_path):
    service = make_service(tmp_path, dev=True)
    stranger = service.anonymous()
    open_paths = {"/healthz", "/docs", "/openapi.json"}

    for method, path in every_route(service.app):
        response = stranger.request(method, path)
        if path in open_paths:
            assert response.status_code == 200, path
        else:
            assert refused(response, 401, "unauthorized"), (method, path)


def test_the_health_check_says_nothing_about_the_service(tmp_path):
    service = make_service(tmp_path)
    response = service.anonymous().get("/healthz")

    assert response.json() == {"status": "ok"}
    assert "server" not in response.headers


def test_what_does_not_exist_looks_the_same_as_what_is_closed(tmp_path):
    service = make_service(tmp_path)
    stranger = service.anonymous()

    for path in ("/", "/v1", "/v1/nothing", "/admin", "/.env", "/v1/jobs/../../etc/passwd"):
        assert refused(stranger.get(path), 401, "unauthorized"), path
    assert refused(service.client.get("/v1/nothing"), 404, "not_found")


def test_there_is_no_documentation_on_a_production_server(tmp_path):
    service = make_service(tmp_path)

    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert refused(service.anonymous().get(path), 401, "unauthorized"), path
        assert refused(service.client.get(path), 404, "not_found"), path


def test_a_development_server_has_documentation(tmp_path):
    service = make_service(tmp_path, dev=True)

    schema = service.anonymous().get("/openapi.json").json()

    assert "/v1/jobs" in schema["paths"]
    assert "multipart/form-data" in schema["paths"]["/v1/jobs"]["post"]["requestBody"]["content"]
    assert service.anonymous().get("/docs").status_code == 200


def test_a_wrong_key_is_refused(tmp_path):
    service = make_service(tmp_path)
    stranger = service.anonymous()
    other_key, _ = new_key("other")
    entry = service.settings.api_keys[0]

    for value in (other_key, service.key[:-1], service.key.upper(), entry.sha256, "", "x" * 500):
        response = stranger.get("/v1/jobs", headers={"Authorization": f"Bearer {value}"})
        assert refused(response, 401, "unauthorized"), value[:12]
    assert refused(
        stranger.get("/v1/jobs", headers={"Authorization": f"Basic {service.key}"}),
        401,
        "unauthorized",
    )


def test_the_key_is_accepted_in_either_header(tmp_path):
    service = make_service(tmp_path)
    stranger = service.anonymous()

    assert stranger.get("/v1/jobs", headers={"X-API-Key": service.key}).status_code == 200
    assert stranger.get("/v1/jobs", headers=service.auth()).status_code == 200


def test_a_key_in_the_address_is_not_accepted(tmp_path):
    service = make_service(tmp_path)
    stranger = service.anonymous()

    for name in ("api_key", "key", "token", "access_token"):
        assert refused(stranger.get(f"/v1/jobs?{name}={service.key}"), 401, "unauthorized")


def test_too_many_wrong_keys_make_an_address_wait(tmp_path):
    service = make_service(tmp_path, auth_failures_per_minute=3)
    guesser = service.anonymous(client=("203.0.113.9", 4000))
    bystander = service.anonymous(client=("203.0.113.10", 4000))
    wrong = {"Authorization": "Bearer rt_wrong"}

    for _ in range(3):
        assert refused(guesser.get("/v1/jobs", headers=wrong), 401, "unauthorized")
    blocked = guesser.get("/v1/jobs", headers=wrong)

    assert refused(blocked, 429, "too_many_failures")
    assert 1 <= int(blocked.headers["retry-after"]) <= 61
    # While it waits, the address gets no answer about any key, the right one included.
    assert refused(guesser.get("/v1/jobs", headers=service.auth()), 429, "too_many_failures")
    assert bystander.get("/v1/jobs", headers=service.auth()).status_code == 200


def test_plain_http_is_refused_before_the_key_is_looked_at(tmp_path):
    service = make_service(tmp_path)
    plain = TestClient(service.app, base_url="http://testserver")

    assert refused(plain.get("/v1/jobs", headers=service.auth()), 403, "https_required")
    assert refused(plain.get("/v1/jobs"), 403, "https_required")
    # The health check is asked from the machine itself, without a proxy in between.
    assert plain.get("/healthz").status_code == 200


def test_a_development_server_takes_plain_http(tmp_path):
    service = make_service(tmp_path, dev=True)
    plain = TestClient(service.app, base_url="http://testserver")

    assert plain.get("/v1/jobs", headers=service.auth()).status_code == 200
    assert "strict-transport-security" not in plain.get("/healthz").headers


def test_every_answer_carries_the_security_headers(tmp_path):
    service = make_service(tmp_path)
    answers = [
        service.client.get("/v1/jobs"),
        service.anonymous().get("/v1/jobs"),
        service.anonymous().get("/healthz"),
        service.client.get("/v1/nothing"),
    ]

    for response in answers:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["strict-transport-security"].startswith("max-age=")
        assert response.headers["content-security-policy"] == "default-src 'none'"


def test_requests_per_key_are_limited(tmp_path):
    service = make_service(tmp_path, labels=("al", "phone"), requests_per_minute=5)

    for _ in range(5):
        assert service.client.get("/v1/jobs").status_code == 200
    limited = service.client.get("/v1/jobs")

    assert refused(limited, 429, "rate_limited")
    assert int(limited.headers["retry-after"]) >= 1
    assert service.anonymous().get("/v1/jobs", headers=service.auth("phone")).status_code == 200
    assert service.anonymous().get("/healthz").status_code == 200


def test_a_declared_body_over_the_limit_is_refused_unread(tmp_path):
    service = make_service(tmp_path, max_audio_bytes=4_096, max_subtitle_bytes=2_048)
    limit = service.settings.max_body_bytes
    read = []

    def body():
        read.append(1)
        yield b"x" * 10

    response = service.client.post(
        "/v1/jobs",
        content=body(),
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(limit + 1),
        },
    )

    assert refused(response, 413, "body_too_large")
    assert read == []


def test_a_body_that_declares_no_length_is_cut_off_at_the_limit(tmp_path):
    service = make_service(tmp_path, max_audio_bytes=4_096, max_subtitle_bytes=2_048)

    def body():
        yield b'--x\r\nContent-Disposition: form-data; name="target"; filename="a.srt"\r\n\r\n'
        for _ in range(1_000):
            yield b"x" * 1_024

    response = service.client.post(
        "/v1/jobs", content=body(), headers={"Content-Type": "multipart/form-data; boundary=x"}
    )

    assert "content-length" not in response.request.headers
    assert refused(response, 413, "body_too_large")
    assert list(service.store.jobs_dir.iterdir()) == []


def test_an_upload_that_stops_arriving_is_not_waited_for(tmp_path):
    service = make_service(tmp_path, upload_timeout_s=0.2)
    sent = []
    head = b'--x\r\nContent-Disposition: form-data; name="target"; filename="a.srt"\r\n\r\n'
    chunks = [{"type": "http.request", "body": head, "more_body": True}]

    async def receive():
        if chunks:
            return chunks.pop()
        await asyncio.sleep(60)  # The client holds the connection and sends nothing more.

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/v1/jobs",
        "raw_path": b"/v1/jobs",
        "query_string": b"",
        "root_path": "",
        "client": ("203.0.113.9", 4_000),
        "server": ("testserver", 443),
        "headers": [
            (b"authorization", f"Bearer {service.key}".encode()),
            (b"content-type", b"multipart/form-data; boundary=x"),
        ],
    }
    started = time.monotonic()

    asyncio.run(service.app(scope, receive, send))

    assert time.monotonic() - started < 10
    assert sent[0]["status"] == 408
    assert json.loads(sent[1]["body"])["error"]["code"] == "upload_too_slow"
    assert list(service.store.jobs_dir.iterdir()) == []
    # The place the key held for the upload is free again.
    target, reference = shifted()
    assert service.submit({"target": target, "reference_subtitle": reference}).status_code == 202


def test_a_request_that_is_no_upload_may_not_carry_a_body(tmp_path):
    service = make_service(tmp_path)

    response = service.client.request("GET", "/v1/jobs", content=b"x" * 10_000)

    assert refused(response, 413, "body_too_large")


def test_a_file_over_its_own_limit_is_refused(tmp_path):
    service = make_service(tmp_path, max_subtitle_bytes=1_024)
    target, reference = shifted()

    response = service.submit({"target": target + b"\n" * 2_000, "reference_subtitle": reference})

    assert refused(response, 413, "file_too_large")
    assert "target" in response.json()["error"]["message"]
    assert list(service.store.jobs_dir.iterdir()) == []


def test_submissions_per_hour_are_limited(tmp_path):
    service = make_service(tmp_path, jobs_per_hour=2)
    target, reference = shifted()
    files = {"target": target, "reference_subtitle": reference}

    assert service.submit(files).status_code == 202
    assert service.submit(files).status_code == 202
    limited = service.submit(files)

    assert refused(limited, 429, "rate_limited")
    assert int(limited.headers["retry-after"]) > 60


def test_a_key_can_only_have_so_many_jobs_under_way(tmp_path):
    service = make_service(tmp_path, labels=("al", "phone"), max_active_jobs=2)
    target, reference = shifted()
    files = {"target": target, "reference_subtitle": reference}

    assert service.submit(files).status_code == 202
    assert service.submit(files).status_code == 202
    assert refused(service.submit(files), 429, "too_many_jobs")
    assert service.submit(files, headers=service.auth("phone")).status_code == 202

    service.run()
    assert service.submit(files).status_code == 202


def test_a_day_has_a_budget(tmp_path):
    service = make_service(tmp_path, daily_budget_usd=0.002)
    target, reference = shifted()
    files = {"target": target, "reference_subtitle": reference}

    # The stand-in for the embedding service charges 0.001 USD a call.
    assert service.finished(files)["summary"]["cost_usd"] == pytest.approx(0.002)
    over = service.submit(files)

    assert refused(over, 429, "budget_exhausted")
    assert int(over.headers["retry-after"]) >= 1
    assert service.client.get("/v1/jobs").status_code == 200


def test_uploads_stop_when_the_disk_is_nearly_full(tmp_path):
    service = make_service(tmp_path, min_free_bytes=10**18)
    target, reference = shifted()

    response = service.submit({"target": target, "reference_subtitle": reference})

    assert refused(response, 507, "storage_full")


def test_one_key_does_not_see_the_jobs_of_another(tmp_path):
    service = make_service(tmp_path, labels=("al", "phone"))
    target, reference = shifted()
    job = service.finished({"target": target, "reference_subtitle": reference})
    other = service.anonymous(headers=service.auth("phone"))

    for path in ("", "/result", "/plan", "/report"):
        assert service.client.get(job["links"]["self"] + path).status_code == 200
        assert refused(other.get(job["links"]["self"] + path), 404, "not_found")
    assert refused(other.delete(job["links"]["self"]), 404, "not_found")
    assert other.get("/v1/jobs").json() == {"jobs": []}
    assert service.client.get(job["links"]["self"]).status_code == 200


def test_a_job_id_is_never_used_as_a_path(tmp_path):
    service = make_service(tmp_path)
    (tmp_path / "data" / "secret.txt").write_text("not for anyone")

    for job_id in ("..", "%2e%2e", "..%2fsecret.txt", "x" * 32, "A" * 32, "0" * 31, "0" * 33):
        for suffix in ("", "/result", "/plan", "/report"):
            response = service.client.get(f"/v1/jobs/{job_id}{suffix}")
            assert response.status_code == 404, (job_id, suffix)
            assert "not for anyone" not in response.text


def test_a_failure_inside_the_server_tells_the_client_nothing(tmp_path, monkeypatch, caplog):
    service = make_service(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("database at /srv/retimer/data is on fire, key sk-or-123")

    monkeypatch.setattr(service.store, "list", broken)
    client = TestClient(
        service.app,
        base_url="https://testserver",
        headers=service.auth(),
        raise_server_exceptions=False,
    )
    with caplog.at_level(logging.ERROR, logger="retimer.api"):
        response = client.get("/v1/jobs")

    assert refused(response, 500, "internal_error")
    assert "fire" not in response.text and "/srv" not in response.text
    assert "on fire" in caplog.text


def test_there_are_no_websockets(tmp_path):
    service = make_service(tmp_path)

    with pytest.raises(WebSocketDisconnect), service.client.websocket_connect("/v1/jobs"):
        pass


def test_the_service_does_not_start_without_a_key(tmp_path):
    with pytest.raises(ConfigError, match="does not start without an API key"):
        create_app(ApiSettings(data_dir=tmp_path), run_worker=False)
    with pytest.raises(ConfigError, match="RETIMER_API_KEYS"):
        ApiSettings.from_env({})


def test_settings_come_from_the_environment(tmp_path):
    key, entry = new_key("al")
    settings = ApiSettings.from_env(
        {
            "RETIMER_API_KEYS": format_entry(entry),
            "RETIMER_DATA_DIR": str(tmp_path),
            "RETIMER_MAX_AUDIO_MB": "50",
            "RETIMER_DAILY_BUDGET_USD": "1.5",
            "RETIMER_RETENTION_HOURS": "6",
            "RETIMER_AGENT": "off",
        }
    )

    assert settings.api_keys == [entry]
    assert settings.data_dir == tmp_path
    assert settings.max_audio_bytes == 50 * 1024 * 1024
    assert (settings.daily_budget_usd, settings.retention_hours, settings.agent) == (1.5, 6, "off")
    assert settings.dev is False


def test_a_setting_that_makes_no_sense_stops_the_start(tmp_path):
    key, entry = new_key("al")
    base = {"RETIMER_API_KEYS": format_entry(entry)}

    for name, value in (
        ("RETIMER_MAX_AUDIO_MB", "lots"),
        ("RETIMER_REQUESTS_PER_MINUTE", "0"),
        ("RETIMER_DAILY_BUDGET_USD", "-1"),
        ("RETIMER_AGENT", "gpt"),
        ("RETIMER_DEV", "maybe"),
    ):
        with pytest.raises(ConfigError):
            ApiSettings.from_env({**base, name: value})
    with pytest.raises(ConfigError) as refused_key:
        ApiSettings.from_env({"RETIMER_API_KEYS": key})
    assert key not in str(refused_key.value)


def test_a_development_server_only_listens_on_its_own_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("retimer.api.server.shutil.which", lambda tool: f"/usr/bin/{tool}")
    _, entry = new_key("al")
    dev = ApiSettings(data_dir=tmp_path, api_keys=[entry], dev=True)
    production = dev.model_copy(update={"dev": False})

    assert problems(dev, "127.0.0.1") == []
    assert "loopback" in problems(dev, "0.0.0.0")[0]
    assert problems(production, "0.0.0.0") == []


def test_the_server_does_not_start_without_what_jobs_need(tmp_path, monkeypatch):
    _, entry = new_key("al")
    settings = ApiSettings(data_dir=tmp_path, api_keys=[entry])
    monkeypatch.setattr("retimer.api.server.shutil.which", lambda tool: None)

    found = problems(settings, "127.0.0.1")

    assert any("ffmpeg" in problem for problem in found)
    assert any("OPENROUTER_API_KEY" in problem for problem in found)


def test_secrets_are_blanked_in_the_log(tmp_path):
    provider_key = "sk-or-v1-0123456789abcdef"
    client_key, _ = new_key("al")
    formatter = RedactingFormatter("%(levelname)s %(message)s", [provider_key])

    def line(message: str, *args, exc_info=None) -> str:
        record = logging.LogRecord(
            "retimer.api", logging.ERROR, __file__, 1, message, args, exc_info
        )
        return formatter.format(record)

    assert provider_key not in line("provider said: bad key %s", provider_key)
    assert "[redacted]" in line("provider said: bad key %s", provider_key)
    assert client_key not in line(f"header was Authorization: Bearer {client_key}")
    assert client_key not in line(f"x-api-key: {client_key}")
    assert "Bearer [redacted]" not in line("no secret here") and "no secret here" in line(
        "no secret here"
    )
    try:
        raise RuntimeError(
            f"request failed with headers {{'Authorization': 'Bearer {provider_key}'}}"
        )
    except RuntimeError:
        import sys

        text = line("job failed", exc_info=sys.exc_info())
    assert provider_key not in text and "Traceback" in text


def test_the_command_makes_a_key_and_its_entry(capsys):
    from retimer.api.auth import parse_keys, verify
    from retimer.cli import main

    assert main(["api-key", "--label", "al"]) == 0
    printed = capsys.readouterr().out.split()
    key = next(word for word in printed if word.startswith("rt_"))
    entry = next(word for word in printed if word.startswith("al:"))

    assert verify(key, parse_keys(entry)).label == "al"
    assert main(["api-key", "--label", "Not Valid"]) == 1


def test_errors_are_json_with_a_code_and_a_message(tmp_path):
    service = make_service(tmp_path)

    for response in (
        service.anonymous().get("/v1/jobs"),
        service.client.get(f"/v1/jobs/{JOB}"),
        service.client.put("/v1/jobs"),
        service.client.get("/v1/jobs?limit=many"),
        service.client.post(
            "/v1/jobs", content=b"{}", headers={"Content-Type": "application/json"}
        ),
    ):
        body = json.loads(response.text)
        assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
        assert response.status_code >= 400


def test_the_command_refuses_to_serve_what_it_should_not(tmp_path, monkeypatch, capsys):
    from retimer.cli import main

    monkeypatch.setenv("RETIMER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RETIMER_API_KEYS", raising=False)

    assert main(["serve", "--port", "0"]) == 2
    assert "does not start without an API key" in capsys.readouterr().err

    key, entry = new_key("al")
    monkeypatch.setenv("RETIMER_API_KEYS", format_entry(entry))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("retimer.api.server.shutil.which", lambda tool: f"/usr/bin/{tool}")

    assert main(["serve", "--dev", "--host", "0.0.0.0", "--port", "0"]) == 2
    assert "loopback" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


@contextlib.contextmanager
def running_server(tmp_path, **environment):
    """The real server in a process of its own, as it is started in production."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    key, entry = new_key("al")
    env = {
        **os.environ,
        "RETIMER_API_KEYS": format_entry(entry),
        "RETIMER_DATA_DIR": str(tmp_path / f"data-{port}"),
        "OPENROUTER_API_KEY": "sk-or-not-a-real-key",
        **environment,
    }
    env.pop("RETIMER_DEV", None)
    log = (tmp_path / f"server-{port}.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "retimer.cli", "serve", "--port", str(port)],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if client.get("/healthz").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert process.poll() is None, "the server did not start"
            assert time.monotonic() < deadline, "the server did not come up in time"
            time.sleep(0.1)
        yield client, key
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=30)
        log.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_the_real_server_believes_only_its_proxy_about_https(tmp_path):
    proxied = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.7"}

    # The proxy on the same machine is believed by default.
    with running_server(tmp_path) as (client, key):
        auth = {"Authorization": f"Bearer {key}"}
        health = client.get("/healthz")
        assert health.json() == {"status": "ok"}
        assert "server" not in health.headers
        assert refused(client.get("/v1/jobs", headers=auth), 403, "https_required")
        assert client.get("/v1/jobs", headers={**auth, **proxied}).json() == {"jobs": []}
        assert refused(client.get("/v1/jobs", headers=proxied), 401, "unauthorized")
        assert refused(client.get("/docs", headers={**auth, **proxied}), 404, "not_found")

    # A sender that is not the proxy can claim HTTPS as much as it likes.
    with running_server(tmp_path, RETIMER_TRUSTED_PROXIES="192.0.2.1") as (client, key):
        auth = {"Authorization": f"Bearer {key}"}
        assert refused(client.get("/v1/jobs", headers={**auth, **proxied}), 403, "https_required")
