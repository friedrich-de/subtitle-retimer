"""Starting the service: checks first, then the server, with logs that keep secrets out."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tempfile

from retimer.api.auth import KEY_PREFIX
from retimer.api.config import ApiSettings, ConfigError
from retimer.providers import OPENROUTER_KEY_ENV

log = logging.getLogger("retimer.api")

TRUSTED_PROXIES_ENV = "RETIMER_TRUSTED_PROXIES"
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
# What a key of this service, or one presented to it, looks like in a line of text.
_KEY_SHAPES = re.compile(rf"\b{KEY_PREFIX}[A-Za-z0-9_-]{{20,}}|(?i:bearer)\s+[^\s\"']{{8,}}")


def secrets_in_environment() -> list[str]:
    """The provider keys this process holds, to be kept out of everything it writes."""
    from retimer.agent import ANTHROPIC_KEY_ENV

    values = [os.environ.get(name, "") for name in (OPENROUTER_KEY_ENV, ANTHROPIC_KEY_ENV)]
    return [value for value in values if len(value) >= 8]


class RedactingFormatter(logging.Formatter):
    """Formats a record, then blanks every secret in it, tracebacks included.

    The code never logs a key. This is for what it does not control: the text
    of an error that came from a library or from a provider.
    """

    def __init__(self, fmt: str, secrets: list[str]) -> None:
        super().__init__(fmt)
        self._secrets = sorted(secrets, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "[redacted]")
        return _KEY_SHAPES.sub("[redacted]", text)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", secrets_in_environment()
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # The HTTP clients log the address of every provider call. That is noise here.
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def load_settings(*, dev: bool = False, need_keys: bool = True) -> ApiSettings:
    settings = ApiSettings.from_env(need_keys=need_keys)
    if dev:
        settings = settings.model_copy(update={"dev": True})
    return settings


def problems(settings: ApiSettings, host: str | None) -> list[str]:
    """What keeps the service from starting on this machine."""
    from retimer.agent import agent_available, agent_key_env, agent_provider

    found = []
    if host is not None and settings.dev and host not in _LOOPBACK:
        found.append(
            "a development server takes plain HTTP and shows the documentation, so it only "
            f"listens on this machine. {host} is not a loopback address."
        )
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            found.append(f"{tool} is not installed")
    if not os.environ.get(OPENROUTER_KEY_ENV):
        found.append(f"{OPENROUTER_KEY_ENV} is not set, so no job could run")
    try:
        agent_provider()
    except ValueError as error:
        found.append(str(error))
    else:
        if not agent_available():
            found.append(
                f"{agent_key_env()} is not set. A model is part of every deployment, "
                "so the service does not start without its key."
            )
    return found


def _prepare_directories(settings: ApiSettings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Uploads are spooled to the temporary directory while they arrive. On many
    # servers the default one is memory, so they get a place on the data disk.
    spool = settings.data_dir / "tmp"
    shutil.rmtree(spool, ignore_errors=True)
    spool.mkdir(mode=0o700)
    tempfile.tempdir = str(spool)


def serve(host: str, port: int, *, dev: bool = False, run_worker: bool = True) -> int:
    """Run the HTTP service until it is stopped. Returns the exit code."""
    import uvicorn

    from retimer.api.app import create_app

    configure_logging()
    try:
        settings = load_settings(dev=dev)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    found = problems(settings, host)
    if found:
        for problem in found:
            print(f"error: {problem}", file=sys.stderr)
        return 2
    if settings.dev:
        log.warning("development server: plain HTTP is accepted and /docs is open")
    _prepare_directories(settings)
    log.info(
        "starting on %s:%d with %d key(s): %s",
        host,
        port,
        len(settings.api_keys),
        ", ".join(key.label for key in settings.api_keys),
    )
    uvicorn.run(
        create_app(settings, run_worker=run_worker),
        host=host,
        port=port,
        # The address and scheme a proxy reports are believed only from these.
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get(TRUSTED_PROXIES_ENV, "127.0.0.1"),
        server_header=False,
        log_config=None,
        limit_concurrency=64,
        timeout_keep_alive=15,
    )
    return 0


def work() -> int:
    """Run the worker alone, for a server that was started with --no-worker."""
    from retimer.api.store import JobStore
    from retimer.api.worker import Services, Worker, WorkerBusy
    from retimer.cache import CACHE_DIR_ENV, Cache

    configure_logging()
    try:
        settings = load_settings(need_keys=False)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    found = problems(settings, None)
    if found:
        for problem in found:
            print(f"error: {problem}", file=sys.stderr)
        return 2
    store = JobStore(settings.data_dir, settings.retention_hours * 3600)
    cache = Cache(os.environ.get(CACHE_DIR_ENV) or settings.data_dir / "cache")
    worker = Worker(store, settings, Services(cache=cache))
    log.info("worker started on %s", settings.data_dir)
    try:
        worker.run_forever()
    except WorkerBusy as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0
