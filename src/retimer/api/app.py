"""The HTTP API: submit a job, ask how it is doing, fetch what it produced.

Every request passes the gate before anything else looks at it. The gate lets
the health check through and nothing else without a valid API key, so a route
cannot be left open by forgetting to protect it. It also holds the limits that
have to work before a body is read: request rates and the size of an upload.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from retimer.api.auth import bearer_key, verify
from retimer.api.config import ApiSettings
from retimer.api.limits import SlidingWindow, Slots, address_name
from retimer.api.schemas import JobOptions, job_view
from retimer.api.store import (
    FINISHED,
    PLAN,
    REFERENCE_AUDIO,
    REFERENCE_SUBTITLE,
    REPORT,
    RESULT,
    RUNNING,
    TARGET,
    Job,
    JobStore,
)
from retimer.api.worker import Services, Worker, agent_available
from retimer.audio import ALLOWED_CONTAINERS, AudioError, AudioInfo, ToolMissing, probe_audio
from retimer.cache import CACHE_DIR_ENV, Cache
from retimer.classify import classify_lines
from retimer.io import SubtitleError, load
from retimer.text import is_drawing, strip_tags
from retimer.views import build_match_view

log = logging.getLogger("retimer.api")

HEALTH_PATH = "/healthz"
JOBS_PATH = "/v1/jobs"
# A request that is not an upload has no use for a body.
_SMALL_BODY = 4 * 1024
_MEDIA_TYPES = {"srt": "application/x-subrip", "ass": "text/x-ssa"}
_STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    415: "unsupported_media_type",
}
_FORM_FILES = (TARGET, REFERENCE_SUBTITLE, REFERENCE_AUDIO)


class ApiError(Exception):
    """A refusal with a status, a code a client can act on, and a sentence for a person."""

    def __init__(
        self, status: int, code: str, message: str, *, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s


class BodyTooLarge(Exception):
    pass


class UploadTooSlow(Exception):
    pass


def error_response(
    status: int, code: str, message: str, *, retry_after_s: float | None = None
) -> JSONResponse:
    headers = {}
    if retry_after_s is not None:
        headers["Retry-After"] = str(max(1, round(retry_after_s + 0.5)))
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


class Gate:
    """Stands in front of the application and decides what gets through."""

    def __init__(self, app: ASGIApp, settings: ApiSettings, open_paths: frozenset[str]) -> None:
        self.app = app
        self.settings = settings
        self.open_paths = open_paths
        self.requests = SlidingWindow(settings.requests_per_minute, 60.0)
        self.failures = SlidingWindow(settings.auth_failures_per_minute, 60.0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # The service has no other kind of connection.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        send = self._with_headers(scope, send)
        refusal = self._check(scope)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self.app(scope, self._limited(scope, receive), send)

    def _check(self, scope: Scope) -> JSONResponse | None:
        """Why the request is refused, or None when it may pass."""
        path, method = scope["path"], scope["method"]
        if method == "GET" and path in self.open_paths:
            scope["retimer.body_limit"] = 0
            return None
        if not self.settings.dev and scope.get("scheme") != "https":
            return error_response(403, "https_required", "This service is reached over HTTPS only.")

        client = scope.get("client")
        address = address_name(client[0] if client else None)
        wait = self.failures.wait_s(address)
        if wait:
            return error_response(
                429,
                "too_many_failures",
                "Too many requests without a valid API key came from this address.",
                retry_after_s=wait,
            )
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope["headers"]
        }
        key = verify(bearer_key(headers), self.settings.api_keys)
        if key is None:
            self.failures.hit(address)
            return error_response(
                401,
                "unauthorized",
                "A valid API key is required: Authorization: Bearer <key>.",
            )
        wait = self.requests.hit(key.label)
        if wait:
            return error_response(
                429, "rate_limited", "Too many requests. Try again shortly.", retry_after_s=wait
            )

        upload = method == "POST" and path == JOBS_PATH
        limit = self.settings.max_body_bytes if upload else _SMALL_BODY
        length = headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                return error_response(400, "bad_request", "Content-Length is not a number.")
            if int(length) > limit:
                return error_response(413, "body_too_large", _too_large(limit))
        scope["retimer.body_limit"] = limit
        scope.setdefault("state", {})["owner"] = key.label
        return None

    def _limited(self, scope: Scope, receive: Receive) -> Receive:
        """`receive`, but it stops at the size limit and at the time an upload may take.

        The declared length was checked already. This counts what really
        arrives, for a body that declares no length or a wrong one.
        """
        limit = scope["retimer.body_limit"]
        deadline = time.monotonic() + self.settings.upload_timeout_s
        received = 0
        complete = False

        async def limited() -> Message:
            nonlocal received, complete
            if complete:
                return await receive()
            # A client that stops sending is not waited for beyond the deadline.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UploadTooSlow()
            try:
                message = await asyncio.wait_for(receive(), remaining)
            except TimeoutError:
                raise UploadTooSlow() from None
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(limit)
                complete = not message.get("more_body", False)
            else:
                complete = True
            return message

        return limited

    def _with_headers(self, scope: Scope, send: Send) -> Send:
        """`send`, with the headers every answer of the service carries."""
        extra = [
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
        ]
        if not self.settings.dev:
            extra.append((b"strict-transport-security", b"max-age=31536000"))
            extra.append((b"content-security-policy", b"default-src 'none'"))

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        *(pair for pair in extra if pair[0] not in present),
                    ],
                }
            await send(message)

        return with_headers


def _too_large(limit: int) -> str:
    if limit < 1024 * 1024:
        return "The request is larger than this service takes."
    return (
        f"The request is larger than the {_megabytes(limit)} this service takes. "
        "Audio as mono Opus at 24 kbps comes to about 11 MB an hour."
    )


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def _safe_name(filename: str | None, fallback: str) -> str:
    """A client's file name, reduced to a name: no directories, no control characters."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable()).strip().strip(".")
    return name[:120] or fallback


def _disposition(filename: str) -> str:
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


@dataclass
class _Parts:
    target: UploadFile
    reference_subtitle: UploadFile | None
    reference_audio: UploadFile | None
    options: JobOptions


def _read_form(form: FormData) -> _Parts:
    """Check the shape of a submission: which parts are there, and of which kind."""
    known = {*_FORM_FILES, "options"}
    for name in form:
        if name not in known:
            raise ApiError(
                422,
                "unknown_field",
                f"The form has a part this service does not know: {name[:40]!r}. "
                f"It takes {', '.join(sorted(known))}.",
            )
        if len(form.getlist(name)) > 1:
            raise ApiError(422, "repeated_field", f"The form has more than one part named {name}.")
    files: dict[str, UploadFile | None] = {}
    for name in _FORM_FILES:
        part = form.get(name)
        if part is not None and not isinstance(part, UploadFile):
            raise ApiError(422, "not_a_file", f"{name} has to be sent as a file.")
        files[name] = part
    target = files[TARGET]
    if target is None:
        raise ApiError(
            422, "missing_target", "The form has no target: the subtitle to synchronize."
        )
    if files[REFERENCE_SUBTITLE] is None and files[REFERENCE_AUDIO] is None:
        raise ApiError(
            422,
            "missing_reference",
            "A job needs a reference: reference_audio, reference_subtitle, or both.",
        )
    raw = form.get("options")
    if isinstance(raw, UploadFile):
        raise ApiError(422, "invalid_options", "options has to be a JSON object in a text field.")
    try:
        options = JobOptions.model_validate_json(raw) if raw else JobOptions()
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'options'}: {issue['msg']}"
            for issue in error.errors(include_input=False, include_url=False)[:5]
        )
        raise ApiError(422, "invalid_options", f"options is not valid. {problems}") from error
    return _Parts(
        target=target,
        reference_subtitle=files[REFERENCE_SUBTITLE],
        reference_audio=files[REFERENCE_AUDIO],
        options=options,
    )


class _Intake:
    """Takes a submission in: stores the uploads, checks them, and queues the job."""

    def __init__(self, settings: ApiSettings, store: JobStore, probe, agent_ready: bool) -> None:
        self.settings = settings
        self.store = store
        self.probe = probe
        self.agent_ready = agent_ready

    def _save(self, upload: UploadFile, path: Path, limit: int, field: str) -> int:
        if upload.size is not None and upload.size > limit:
            raise ApiError(
                413, "file_too_large", f"{field} is larger than the {_megabytes(limit)} allowed."
            )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            upload.file.seek(0)
            shutil.copyfileobj(upload.file, handle, 1024 * 1024)
            size = handle.tell()
        if size > limit:
            raise ApiError(
                413, "file_too_large", f"{field} is larger than the {_megabytes(limit)} allowed."
            )
        if size == 0:
            raise ApiError(422, f"invalid_{field}", f"{field} is empty.")
        return size

    def _subtitle(self, upload: UploadFile, path: Path, field: str) -> dict[str, Any]:
        size = self._save(upload, path, self.settings.max_subtitle_bytes, field)
        try:
            subtitle = load(path)
        except SubtitleError as error:
            raise ApiError(422, f"invalid_{field}", f"{field}: {error}.") from error
        except Exception as error:
            # A file that trips the parser is a file that cannot be read.
            log.info("%s could not be parsed: %r", field, error)
            raise ApiError(
                422, f"invalid_{field}", f"{field}: the file could not be read as a subtitle."
            ) from error
        if len(subtitle) > self.settings.max_subtitle_lines:
            raise ApiError(
                422,
                "too_many_lines",
                f"{field} has {len(subtitle)} lines. "
                f"The limit is {self.settings.max_subtitle_lines}.",
            )
        for line in subtitle.lines:
            if is_drawing(line.text):
                continue
            length = len(strip_tags(line.text))
            if length > self.settings.max_line_chars:
                raise ApiError(
                    422,
                    f"invalid_{field}",
                    f"{field}: the line at {line.start_ms / 1000:.0f} s has {length} characters "
                    f"of text. The limit is {self.settings.max_line_chars}. Is the file cut "
                    "into lines correctly?",
                )
        dialogue = len(build_match_view(subtitle, classify_lines(subtitle)).lines)
        if dialogue > self.settings.max_dialogue_lines:
            raise ApiError(
                422,
                "too_many_lines",
                f"{field} has {dialogue} lines of dialogue. "
                f"The limit is {self.settings.max_dialogue_lines}.",
            )
        return {
            "filename": _safe_name(upload.filename, field),
            "format": subtitle.format,
            "bytes": size,
            "lines": len(subtitle),
            "dialogue_lines": dialogue,
        }

    def _audio(self, upload: UploadFile, path: Path) -> dict[str, Any]:
        field = REFERENCE_AUDIO
        size = self._save(upload, path, self.settings.max_audio_bytes, field)
        try:
            info: AudioInfo = self.probe(path)
        except ToolMissing:
            raise
        except AudioError as error:
            log.info("%s was refused: %s", field, error)
            raise ApiError(
                422,
                f"invalid_{field}",
                f"{field} is not audio this service can read. It takes one audio track in "
                f"one of these containers: {', '.join(ALLOWED_CONTAINERS)}.",
            ) from error
        minutes = info.duration_ms / 60_000
        if minutes > self.settings.max_audio_minutes:
            raise ApiError(
                422,
                "audio_too_long",
                f"{field} is {minutes:.0f} minutes long. "
                f"The limit is {self.settings.max_audio_minutes}.",
            )
        return {
            "filename": _safe_name(upload.filename, field),
            "container": info.container,
            "codec": info.codec,
            "bytes": size,
            "duration_ms": info.duration_ms,
        }

    def accept(self, owner: str, parts: _Parts) -> Job:
        options = parts.options
        agent = options.agent or self.settings.agent
        if agent != "off" and not self.agent_ready:
            if options.agent is not None:
                raise ApiError(
                    422,
                    "agent_unavailable",
                    "This server has no key for the agent. Leave agent out or send off.",
                )
            agent = "off"
        job_id, directory = self.store.new_directory()
        try:
            request: dict[str, Any] = {
                "target": self._subtitle(parts.target, directory / TARGET, TARGET),
                "reference_subtitle": None,
                "reference_audio": None,
            }
            if parts.reference_subtitle is not None:
                request["reference_subtitle"] = self._subtitle(
                    parts.reference_subtitle, directory / REFERENCE_SUBTITLE, REFERENCE_SUBTITLE
                )
            if parts.reference_audio is not None:
                request["reference_audio"] = self._audio(
                    parts.reference_audio, directory / REFERENCE_AUDIO
                )
            request["options"] = options.model_dump(exclude={"agent", "output_format"})
            request["agent"] = agent
            request["output_format"] = options.output_format or request["target"]["format"]
            job = self.store.add(job_id, owner, request)
        except BaseException:
            self.store.discard_directory(job_id)
            raise
        audio = request["reference_audio"]
        log.info(
            "job %s accepted from %s: target %d lines, reference subtitle %s, audio %s, agent %s",
            job.id,
            owner,
            request["target"]["lines"],
            "yes" if request["reference_subtitle"] else "no",
            f"{audio['duration_ms'] / 60_000:.1f} min" if audio else "no",
            agent,
        )
        return job


def _until_midnight_utc() -> float:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


_SUBMIT_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["target"],
                    "properties": {
                        "target": {
                            "type": "string",
                            "format": "binary",
                            "description": "The subtitle to synchronize. SRT or ASS.",
                        },
                        "reference_audio": {
                            "type": "string",
                            "format": "binary",
                            "description": "Audio of the video. Mono Opus at 24 kbps is enough.",
                        },
                        "reference_subtitle": {
                            "type": "string",
                            "format": "binary",
                            "description": "A subtitle that is in sync, in any language.",
                        },
                        "options": {
                            "type": "string",
                            "description": "A JSON object, see JobOptions.",
                        },
                    },
                }
            }
        },
    }
}


def create_app(
    settings: ApiSettings,
    *,
    services: Services | None = None,
    run_worker: bool = True,
    probe=probe_audio,
) -> FastAPI:
    """Build the service. `services` and `probe` are where tests put stand-ins."""
    settings.check()
    store = JobStore(settings.data_dir, settings.retention_hours * 3600)
    if services is None:
        cache_dir = os.environ.get(CACHE_DIR_ENV) or settings.data_dir / "cache"
        services = Services(cache=Cache(cache_dir))
    worker = Worker(store, settings, services)
    agent_ready = services.model_client is not None or agent_available()
    intake = _Intake(settings, store, probe, agent_ready)
    submissions = SlidingWindow(settings.jobs_per_hour, 3600.0)
    uploads = Slots()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_worker:
            worker.start()
        try:
            yield
        finally:
            if run_worker:
                await run_in_threadpool(worker.stop)

    app = FastAPI(
        title="Subtitle Retimer",
        version=metadata.version("retimer"),
        description="Synchronizes a subtitle to audio, to a subtitle that is in sync, or to both.",
        lifespan=lifespan,
        # The interactive documentation and the schema exist on a development server only.
        docs_url="/docs" if settings.dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.dev else None,
        redirect_slashes=False,
        # Nothing about a request leaves the server, whatever the environment is set to.
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
        # Only so that the documentation knows about the key. The gate does the checking.
        dependencies=[Depends(HTTPBearer(auto_error=False, description="The API key."))],
    )
    open_paths = {HEALTH_PATH}
    if settings.dev:
        open_paths |= {"/docs", "/openapi.json"}
    app.add_middleware(Gate, settings=settings, open_paths=frozenset(open_paths))
    app.state.settings = settings
    app.state.store = store
    app.state.worker = worker

    # Errors

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(
            error.status, error.code, error.message, retry_after_s=error.retry_after_s
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(error.status_code, "error")
        message = error.detail if isinstance(error.detail, str) else "The request was refused."
        return error_response(error.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()[:5]
        )
        return error_response(422, "invalid_request", problems)

    @app.exception_handler(BodyTooLarge)
    async def body_too_large(request: Request, error: BodyTooLarge) -> JSONResponse:
        return error_response(413, "body_too_large", _too_large(error.args[0]))

    @app.exception_handler(UploadTooSlow)
    async def upload_too_slow(request: Request, error: UploadTooSlow) -> JSONResponse:
        return error_response(408, "upload_too_slow", "The upload took too long.")

    @app.exception_handler(ClientDisconnect)
    async def client_gone(request: Request, error: ClientDisconnect) -> Response:
        return Response(status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.error("unexpected error on %s %s", request.method, request.url.path, exc_info=error)
        return error_response(500, "internal_error", "The server could not handle the request.")

    # Lookups

    def owner_of(request: Request) -> str:
        # Set by the gate. Without it there is no request to serve.
        return request.state.owner

    def find(request: Request, job_id: str) -> Job:
        job = store.get(job_id, owner_of(request))
        if job is None:
            raise ApiError(
                404, "not_found", "There is no such job. Jobs are deleted when they expire."
            )
        return job

    def finished(request: Request, job_id: str) -> Job:
        job = find(request, job_id)
        if job.status != FINISHED:
            raise ApiError(
                409, "not_finished", f"The job is {job.status}. Ask again when it is finished."
            )
        return job

    def produced(job: Job) -> set[str]:
        return {name for name in (RESULT, PLAN, REPORT) if store.file(job.id, name) is not None}

    def document(job: Job, name: str) -> Response:
        path = store.file(job.id, name)
        if path is None:
            raise ApiError(
                404, "not_available", f"The job ended as {job.outcome} and has no {name}."
            )
        return Response(path.read_bytes(), media_type="application/json")

    # Routes

    @app.get(HEALTH_PATH, summary="Whether the service is up")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def admit(owner: str) -> None:
        """Refuse a submission before its body is read, where that can be known already."""
        if shutil.disk_usage(settings.data_dir).free < settings.min_free_bytes:
            log.error("uploads are refused: the disk is nearly full")
            raise ApiError(507, "storage_full", "The server cannot take uploads right now.")
        if store.spent_today() >= settings.daily_budget_usd:
            raise ApiError(
                429,
                "budget_exhausted",
                "The jobs of today have used up the server's daily budget.",
                retry_after_s=_until_midnight_utc(),
            )
        if not uploads.take(owner, settings.max_active_jobs, already=store.active(owner)):
            raise ApiError(
                429,
                "too_many_jobs",
                f"{settings.max_active_jobs} jobs of this key are queued or running. "
                "Wait for one to finish.",
                retry_after_s=30,
            )

    @app.post(
        JOBS_PATH,
        status_code=202,
        summary="Submit a job",
        openapi_extra=_SUBMIT_SCHEMA,
    )
    async def submit_job(request: Request) -> JSONResponse:
        owner = owner_of(request)
        content_type = request.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
            raise ApiError(415, "unsupported_media_type", "Send the job as multipart/form-data.")
        await run_in_threadpool(admit, owner)
        try:
            wait = submissions.hit(owner)
            if wait:
                raise ApiError(
                    429,
                    "rate_limited",
                    f"This key has submitted {settings.jobs_per_hour} jobs within an hour.",
                    retry_after_s=wait,
                )
            async with request.form(max_files=3, max_fields=4, max_part_size=64 * 1024) as form:
                parts = _read_form(form)
                job = await run_in_threadpool(intake.accept, owner, parts)
        finally:
            uploads.release(owner)
        worker.notify()
        return JSONResponse(
            job_view(job, set()), status_code=202, headers={"Location": f"{JOBS_PATH}/{job.id}"}
        )

    @app.get(JOBS_PATH, summary="The jobs of this key, newest first")
    def list_jobs(request: Request, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        return {
            "jobs": [job_view(job, produced(job)) for job in store.list(owner_of(request), limit)]
        }

    @app.get(JOBS_PATH + "/{job_id}", summary="Status, stage and outcome of a job")
    def get_job(request: Request, job_id: str) -> dict[str, Any]:
        job = find(request, job_id)
        return job_view(job, produced(job))

    @app.get(JOBS_PATH + "/{job_id}/result", summary="The retimed subtitle")
    def get_result(request: Request, job_id: str) -> Response:
        job = finished(request, job_id)
        path = store.file(job.id, RESULT)
        if path is None:
            raise ApiError(404, "no_result", f"The job ended as {job.outcome} and has no file.")
        fmt = job.request["output_format"]
        stem = job.request["target"]["filename"].rsplit(".", 1)[0] or "subtitle"
        return Response(
            path.read_bytes(),
            media_type=f"{_MEDIA_TYPES[fmt]}; charset=utf-8",
            headers={"Content-Disposition": _disposition(f"{stem}.{fmt}")},
        )

    @app.get(JOBS_PATH + "/{job_id}/plan", summary="The operation plan")
    def get_plan(request: Request, job_id: str) -> Response:
        return document(finished(request, job_id), PLAN)

    @app.get(JOBS_PATH + "/{job_id}/report", summary="Diagnosis, decisions, scores and warnings")
    def get_report(request: Request, job_id: str) -> Response:
        return document(finished(request, job_id), REPORT)

    @app.delete(JOBS_PATH + "/{job_id}", status_code=204, summary="Delete a job and its files")
    def delete_job(request: Request, job_id: str) -> Response:
        done = store.delete(job_id, owner_of(request))
        if done is None:
            raise ApiError(404, "not_found", "There is no such job.")
        if done == RUNNING:
            raise ApiError(409, "job_running", "The job is running. Delete it when it is finished.")
        return Response(status_code=204)

    return app
