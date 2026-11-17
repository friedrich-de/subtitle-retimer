"""What the service is run with. Everything comes from the environment.

The defaults are the production ones: every limit is on, the interactive
documentation is off, and a request that did not arrive over HTTPS is refused.
`dev` relaxes the last two for a server on the same machine.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field

from retimer.api.auth import ApiKey, parse_keys

MB = 1024 * 1024

DATA_DIR_ENV = "RETIMER_DATA_DIR"
API_KEYS_ENV = "RETIMER_API_KEYS"
DEV_ENV = "RETIMER_DEV"

AGENT_CHOICES = ("haiku", "sonnet", "opus", "off")


class ConfigError(ValueError):
    """The service is set up in a way it will not start with."""


class ApiSettings(BaseModel):
    data_dir: Path = Path("data")
    # Who may call the service. Only the hashes of the keys are kept.
    api_keys: list[ApiKey] = Field(default_factory=list)
    # A server for development on this machine: plain HTTP and the interactive
    # documentation. Never for a server that others can reach.
    dev: bool = False

    # Uploads. A subtitle of a film is well under a megabyte, and a film's
    # audio as the client should send it (mono Opus, 24 kbps) about 22 MB.
    max_subtitle_bytes: int = 10 * MB
    max_audio_bytes: int = 90 * MB
    max_audio_minutes: int = 240
    # One request as a whole: the files and the form around them. The proxy in
    # front has a limit of its own, and what it turns away gets its answer, not
    # this service's. Cloudflare stops at 100 MB on its free plan, so the
    # service stops a little earlier and the client gets an error it can read.
    max_request_bytes: int = 95 * MB
    # Lines of a subtitle, comments and signs included, and the lines of it
    # that are compared with the reference. Matching holds a table of every
    # line against every event in memory.
    max_subtitle_lines: int = 50_000
    max_dialogue_lines: int = 4_000
    # The text of one line, tags and drawings aside. Nothing a viewer could
    # read is this long, and a file that is cut wrong can be.
    max_line_chars: int = 2_000
    # A body that takes longer than this to arrive is dropped.
    upload_timeout_s: float = 900.0
    # Below this much free disk no upload is taken.
    min_free_bytes: int = 512 * MB

    # Requests per key, and what a key may have under way at once.
    requests_per_minute: int = 120
    jobs_per_hour: int = 60
    max_active_jobs: int = 10
    # Wrong keys per address before it has to wait.
    auth_failures_per_minute: int = 10
    # What all jobs of one day (UTC) may cost at the providers together.
    daily_budget_usd: float = 5.0

    # A job and its files are deleted this long after it finished. The audio
    # goes as soon as the job is done.
    retention_hours: float = 24.0
    # Transcripts and embeddings are forgotten after this many days.
    cache_days: float = 30.0

    # Jobs that run side by side.
    workers: int = 1
    # The model that settles what the routine steps leave open, when a request
    # names none. "off" runs the fixed pipeline.
    agent: str = "haiku"
    agent_max_cost_usd: float = 0.25

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def database(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    @property
    def max_body_bytes(self) -> int:
        """The most a request may carry: one audio file, two subtitles and the form around them.

        Never more than `max_request_bytes`, whatever the files may be on their own.
        """
        files = self.max_audio_bytes + 2 * self.max_subtitle_bytes + 64 * 1024
        return min(files, self.max_request_bytes)

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, need_keys: bool = True
    ) -> ApiSettings:
        """Read the settings. `need_keys` is off for the worker, which serves no requests."""
        env = os.environ if env is None else env
        values: dict[str, object] = {}

        def take(name: str, field: str, convert=str) -> None:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return
            try:
                values[field] = convert(raw.strip())
            except ValueError as error:
                raise ConfigError(f"{name} is not valid: {error}") from error

        def megabytes(raw: str) -> int:
            return round(float(raw) * MB)

        def flag(raw: str) -> bool:
            if raw.lower() in ("1", "true", "yes", "on"):
                return True
            if raw.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError("expected 1 or 0")

        def agent(raw: str) -> str:
            if raw.lower() not in AGENT_CHOICES:
                raise ValueError(f"expected one of {', '.join(AGENT_CHOICES)}")
            return raw.lower()

        take(DATA_DIR_ENV, "data_dir", Path)
        take(API_KEYS_ENV, "api_keys", parse_keys)
        take(DEV_ENV, "dev", flag)
        take("RETIMER_MAX_SUBTITLE_MB", "max_subtitle_bytes", megabytes)
        take("RETIMER_MAX_AUDIO_MB", "max_audio_bytes", megabytes)
        take("RETIMER_MAX_REQUEST_MB", "max_request_bytes", megabytes)
        take("RETIMER_MAX_AUDIO_MINUTES", "max_audio_minutes", int)
        take("RETIMER_MAX_DIALOGUE_LINES", "max_dialogue_lines", int)
        take("RETIMER_REQUESTS_PER_MINUTE", "requests_per_minute", int)
        take("RETIMER_JOBS_PER_HOUR", "jobs_per_hour", int)
        take("RETIMER_MAX_ACTIVE_JOBS", "max_active_jobs", int)
        take("RETIMER_DAILY_BUDGET_USD", "daily_budget_usd", float)
        take("RETIMER_RETENTION_HOURS", "retention_hours", float)
        take("RETIMER_CACHE_DAYS", "cache_days", float)
        take("RETIMER_WORKERS", "workers", int)
        take("RETIMER_AGENT", "agent", agent)
        take("RETIMER_AGENT_MAX_COST_USD", "agent_max_cost_usd", float)
        settings = cls(**values)
        settings.check(need_keys=need_keys)
        return settings

    def check(self, *, need_keys: bool = True) -> None:
        """Refuse a setup that would leave the service open or unable to work."""
        if need_keys and not self.api_keys:
            raise ConfigError(
                f"{API_KEYS_ENV} is not set. The service does not start without an API key. "
                "Make one with: retimer api-key"
            )
        positive = (
            "max_subtitle_bytes",
            "max_audio_bytes",
            "max_request_bytes",
            "max_audio_minutes",
            "max_subtitle_lines",
            "max_dialogue_lines",
            "max_line_chars",
            "requests_per_minute",
            "jobs_per_hour",
            "max_active_jobs",
            "auth_failures_per_minute",
            "retention_hours",
            "cache_days",
            "workers",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} must be greater than zero")
        for name in ("max_audio_bytes", "max_subtitle_bytes"):
            if getattr(self, name) > self.max_request_bytes:
                raise ConfigError(
                    f"{name} is larger than max_request_bytes, so no request could carry such "
                    "a file. Raise RETIMER_MAX_REQUEST_MB, or lower the limit of the file."
                )
        if self.daily_budget_usd < 0 or self.agent_max_cost_usd < 0:
            raise ConfigError("a budget cannot be negative")
        if self.agent not in AGENT_CHOICES:
            raise ConfigError(f"agent must be one of {', '.join(AGENT_CHOICES)}")
