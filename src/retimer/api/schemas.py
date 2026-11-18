"""What a request may say, and what the service answers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

from retimer.api.store import (
    FINISHED,
    MATCHED_REFERENCE,
    PLAN,
    QUEUED,
    REPORT,
    RESULT,
    RUNNING,
    Job,
)

# A language as the speech model takes it: "ja", "en".
Language = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z]{2,3}$", to_lower=True, strip_whitespace=True)
]


# What the service can do beyond its first release, for a client that also
# talks to servers of an earlier one. The health check lists them.
FEATURES = ("matched_reference", "ocr", "reference_tracks", "remove_signs_and_credits")

_TITLE_CHARS = 200
_LANGUAGE_CHARS = 35


def _one_line(value: object) -> object:
    """A label as a container gives it: any text. Kept to one printable line."""
    if not isinstance(value, str):
        return value
    return " ".join("".join(char if char.isprintable() else " " for char in value).split())


def _title(value: object) -> object:
    text = _one_line(value)
    return (text[:_TITLE_CHARS] or None) if isinstance(text, str) else text


def _track_language(value: object) -> object:
    text = _one_line(value)
    return (text[:_LANGUAGE_CHARS].lower() or None) if isinstance(text, str) else text


class ReferenceTrack(BaseModel):
    """What a video file says about one of its subtitle tracks. Every field is optional.

    These are hints. Which track a job is synced against is decided by what
    the tracks contain, and the labels only decide between tracks that fit
    equally well. They come back in the report next to what was found.
    """

    model_config = ConfigDict(extra="forbid")

    # As the container has it: "en", "eng", "pt-BR", "und". Not checked, only kept short.
    language: Annotated[str | None, BeforeValidator(_track_language)] = None
    # Free text, such as "Signs & Songs".
    title: Annotated[str | None, BeforeValidator(_title)] = None
    default: bool | None = None
    forced: bool | None = None


class JobOptions(BaseModel):
    """The `options` of a job. A name that is not listed here is refused, not ignored."""

    model_config = ConfigDict(extra="forbid")

    remove_hearing_impaired: bool = False
    remove_karaoke: bool = False
    # Drop what is shown on screen and not spoken: signs, and the credits and
    # advertisements of whoever made or passed on the file.
    remove_signs_and_credits: bool = False
    strip_formatting: bool = False
    remove_symbols: bool = False
    # Trim leading or trailing lines when the reference confirms the content is absent.
    drop_unmatched: bool = True
    # Hints. The language of the audio is detected when it is not given.
    audio_language: Language | None = None
    reference_language: Language | None = None
    # One entry for every reference_subtitle of the job, in the order they
    # were sent: what the video file says about that track.
    reference_tracks: list[ReferenceTrack] | None = None
    # Also hand back the reference subtitle, reduced to the dialogue lines the
    # delivered target has a counterpart for. Needs a reference subtitle.
    matched_reference: bool = False
    # Defaults to the format of the target, and for the matched reference to
    # the format of the reference subtitle.
    output_format: Literal["srt", "ass"] | None = None
    # The model that settles what the routine steps leave open. "off" runs the
    # fixed pipeline. Defaults to what the server is set to.
    agent: Literal["haiku", "sonnet", "opus", "off"] | None = None


class OcrOptions(BaseModel):
    """The `options` of a request to read an image subtitle."""

    model_config = ConfigDict(extra="forbid")

    # The language the subtitles are written in, such as "ja". It is a hint
    # for the model that reads the pictures; without it the model sees for itself.
    language: Language | None = None
    # The model that reads the pictures. Defaults to what the server is set to.
    model: Literal["haiku", "sonnet", "gemini-flash", "gemini-flash-lite", "qwen-flash"] | None = (
        None
    )
    output_format: Literal["srt", "ass"] = "srt"


def _time(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_view(job: Job, files: set[str]) -> dict[str, Any]:
    """A job as a client sees it. `files` are the files of the job that exist."""
    base = f"/v1/jobs/{job.id}"
    links = {"self": base}
    if job.status == FINISHED:
        for name, key in (
            (RESULT, "result"),
            (MATCHED_REFERENCE, "matched_reference"),
            (PLAN, "plan"),
            (REPORT, "report"),
        ):
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
