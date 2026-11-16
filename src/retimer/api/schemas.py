"""What a request may say, and what the service answers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from retimer.api.store import FINISHED, PLAN, QUEUED, REPORT, RESULT, RUNNING, Job

# A language as the speech model takes it: "ja", "en".
Language = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z]{2,3}$", to_lower=True, strip_whitespace=True)
]


class JobOptions(BaseModel):
    """The `options` of a job. A name that is not listed here is refused, not ignored."""

    model_config = ConfigDict(extra="forbid")

    remove_hearing_impaired: bool = False
    remove_karaoke: bool = False
    strip_formatting: bool = False
    remove_symbols: bool = False
    # Trim leading or trailing lines when the reference confirms the content is absent.
    drop_unmatched: bool = True
    # Hints. The language of the audio is detected when it is not given.
    audio_language: Language | None = None
    reference_language: Language | None = None
    # Defaults to the format of the target.
    output_format: Literal["srt", "ass"] | None = None
    # The model that settles what the routine steps leave open. "off" runs the
    # fixed pipeline. Defaults to what the server is set to.
    agent: Literal["haiku", "sonnet", "opus", "off"] | None = None


def _time(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_view(job: Job, files: set[str]) -> dict[str, Any]:
    """A job as a client sees it. `files` are the files of the job that exist."""
    base = f"/v1/jobs/{job.id}"
    links = {"self": base}
    if job.status == FINISHED:
        for name, key in ((RESULT, "result"), (PLAN, "plan"), (REPORT, "report")):
            if name in files:
                links[key] = f"{base}/{key}"
    stage = job.stage
    if job.status == QUEUED:
        stage = "queued"
    elif job.status == RUNNING:
        stage = stage or "starting"
    return {
        "id": job.id,
        "status": job.status,
        "stage": stage if job.status != FINISHED else None,
        "outcome": job.outcome,
        "created_at": _time(job.created_at),
        "started_at": _time(job.started_at),
        "finished_at": _time(job.finished_at),
        "expires_at": _time(job.expires_at),
        "request": job.request,
        "summary": job.summary,
        "error": job.error,
        "links": links,
    }
