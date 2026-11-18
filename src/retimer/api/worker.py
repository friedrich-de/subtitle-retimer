"""The worker: takes queued jobs one by one and runs them.

A job is the same work as `retimer sync`: the routine steps, then the agent
where something is open. A job whose model cannot be asked fails and can be
sent again. What a job produces goes into its directory, and its row says how
it ended.
The worker also cleans up: expired jobs, stray uploads, and old cache entries.
"""

from __future__ import annotations

import json
import logging
import mmap
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retimer.api.config import ApiSettings
from retimer.api.store import (
    IMAGE_SUBTITLE,
    MATCHED_REFERENCE,
    PLAN,
    REFERENCE_AUDIO,
    REFERENCE_SUBTITLE,
    REPORT,
    RESULT,
    TARGET,
    Job,
    JobStore,
    reference_file,
)
from retimer.audio import AudioError
from retimer.cache import Cache
from retimer.choice import Candidate, ReferenceChoice, TrackLabel
from retimer.io import SubtitleError, dumps, load
from retimer.providers import ModelUnavailable, OpenRouter, ProviderError
from retimer.views import Options

log = logging.getLogger("retimer.api")

_HOUSEKEEPING_EVERY_S = 300.0
# What a second of audio costs to transcribe, for jobs that fail before they
# can say what they spent. From the speech provider's price of 2026-11-16.
_TRANSCRIPTION_USD_PER_S = 0.0000111

# What a client is told when a job fails. The cause in full goes to the log.
FAILURES = {
    "invalid_subtitle": "A subtitle of this job could not be read.",
    "invalid_audio": "The audio of this job could not be read.",
    "invalid_image_subtitle": "The image subtitle of this job could not be read.",
    "no_text": "No text could be read from the pictures of this image subtitle.",
    "ocr_budget_exceeded": "Reading this image subtitle cost more than one job may spend.",
    "provider_error": "A hosted service the job depends on failed. A new job may succeed later.",
    "model_unavailable": "The model that settles the job could not be asked. Send the job again.",
    "internal_error": "The job failed for a reason on the server's side.",
}


class WorkerBusy(RuntimeError):
    """Another worker already runs on this data directory."""


@dataclass
class Services:
    """What a job reaches the outside world through. Tests put stand-ins here."""

    cache: Cache
    openrouter: OpenRouter | None = None
    model_client: Any | None = None


def agent_available() -> bool:
    from retimer import agent

    return agent.agent_available()


def _candidates(request: dict[str, Any], directory: Path) -> list[Candidate]:
    """The reference subtitles of a job, in the order they were sent."""
    entries = request.get("reference_subtitles")
    if entries is None:
        # A job that an earlier release took in: one reference subtitle at most.
        path = directory / REFERENCE_SUBTITLE
        if not path.is_file():
            return []
        entry = request.get("reference_subtitle") or {}
        return [Candidate(subtitle=load(path), filename=entry.get("filename"))]
    candidates = []
    for entry in entries:
        label = TrackLabel(
            language=entry.get("language"),
            title=entry.get("title"),
            default=bool(entry.get("default")),
            forced=bool(entry.get("forced")),
        )
        if "error" in entry:
            # It could not be read when the job came in, and its entry says why.
            candidates.append(
                Candidate(
                    subtitle=None,
                    label=label,
                    filename=entry.get("filename"),
                    problem=entry["error"]["message"],
                )
            )
            continue
        candidates.append(
            Candidate(
                subtitle=load(directory / reference_file(entry["index"])),
                label=label,
                filename=entry.get("filename"),
            )
        )
    return candidates


def matched_reference_source(
    request: dict[str, Any], reference: dict[str, Any] | None
) -> dict[str, Any]:
    """What a job's request says about the reference subtitle its matched reference is made of.

    `reference` is the choice between the reference subtitles as the job's
    summary has it. The matched reference comes from the chosen one.
    """
    entries = request.get("reference_subtitles") or []
    chosen = (reference or {}).get("chosen")
    if chosen is not None and 0 <= chosen < len(entries):
        return entries[chosen]
    return request.get("reference_subtitle") or (entries[0] if entries else {})


def _reference_summary(choice: ReferenceChoice | None) -> dict[str, Any] | None:
    """The choice between the reference subtitles, as the summary of a job shows it."""
    if choice is None:
        return None
    return choice.model_dump(exclude={"candidates": {"__all__": {"left_out"}}})


def _summary(result, seconds: float) -> dict[str, Any]:
    report = result.report
    summary: dict[str, Any] = {
        "confidence": result.plan.confidence,
        "fit": report.chosen,
        "rate": report.rate_name,
        "blocks": [
            {
                "from_ms": block.from_ms,
                "to_ms": block.to_ms,
                "shift_ms": block.shift_ms,
                "lines": block.lines,
                "confidence": block.confidence,
            }
            for block in report.blocks
        ],
        # Which reference subtitle the job was synced against, and what was
        # found out about each. None for a job without a reference subtitle.
        "reference": _reference_summary(report.reference_choice),
        "target_lines": report.target_lines,
        "delivered_lines": len(result.subtitle) if result.subtitle is not None else 0,
        "trimmed_lines": len(report.trimmed),
        # Lines outside the part the reference covers, in a file that holds
        # more than that, such as several episodes.
        "cut_lines": len(report.cut),
        # The lines of the matched reference, when one was asked for and made.
        "matched_reference_lines": len(result.reference) if result.reference is not None else None,
        # How many pairs of lines the report lists for it.
        "matched_reference_pairs": len(report.matched_reference.pairs)
        if result.reference is not None and report.matched_reference is not None
        else None,
        "warnings": report.warnings,
        "agent": None,
        "cost_usd": round(report.total_cost_usd, 6),
        "seconds": round(seconds, 2),
    }
    if report.agent:
        summary["agent"] = {
            key: report.agent.get(key)
            for key in ("model", "consulted", "questions", "finished", "turns", "cost_usd")
        }
    return summary


def run_ocr_job(job: Job, store: JobStore, settings: ApiSettings, services: Services) -> str:
    """Read the image subtitle of a job into a subtitle with text. Nothing is raised."""
    started = time.monotonic()
    directory = store.directory(job.id)
    request = job.request
    # Imported here, so that a server which runs no jobs itself starts without numpy.
    from retimer.ocr import OcrBudgetExceeded, OcrError, read_image_subtitle
    from retimer.pgs import PgsError, read_pgs

    try:
        store.set_stage(job.id, "decoding")
        with (
            (directory / IMAGE_SUBTITLE).open("rb") as handle,
            mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data,
        ):
            events = read_pgs(data)
        if not events:
            raise PgsError("the stream shows no subtitle")
        store.set_stage(job.id, "reading")
        subtitle, stats = read_image_subtitle(
            events,
            model=request["model"],
            language=request["options"].get("language"),
            client=services.openrouter,
            cache=services.cache,
            max_cost_usd=settings.ocr_max_cost_usd,
        )
        store.set_stage(job.id, "writing")
        text = dumps(subtitle, request["output_format"])
        (directory / RESULT).write_text(text, encoding="utf-8")
        warnings = []
        if stats.unread_pictures:
            warnings.append(
                f"{stats.unread_pictures} pictures could not be read and were left out."
            )
        empty = len(events) - len(subtitle)
        if empty:
            warnings.append(f"{empty} subtitles showed no text and were left out.")
        report = {
            "outcome": "transcribed",
            "screen": list(events[0].screen),
            "first_ms": events[0].start_ms,
            "last_ms": events[-1].end_ms,
            "lines": len(subtitle),
            "reading": stats.model_dump(),
            "warnings": warnings,
        }
        (directory / REPORT).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except Exception as error:
        code = (
            "invalid_image_subtitle"
            if isinstance(error, PgsError)
            else "ocr_budget_exceeded"
            if isinstance(error, OcrBudgetExceeded)
            else "no_text"
            if isinstance(error, OcrError)
            else "provider_error"
            if isinstance(error, ProviderError)
            else "internal_error"
        )
        log.exception("job %s failed (%s)", job.id, code)
        for name in (RESULT, REPORT):
            (directory / name).unlink(missing_ok=True)
        store.finish(
            job.id,
            "failed",
            error={"code": code, "message": FAILURES[code]},
            cost_usd=float(getattr(error, "cost_usd", 0.0) or 0.0),
        )
        return "failed"

    seconds = time.monotonic() - started
    summary = {
        "lines": len(subtitle),
        "events": stats.events,
        "pictures": stats.pictures,
        "distinct_pictures": stats.distinct_pictures,
        "from_cache": stats.from_cache,
        "unread_pictures": stats.unread_pictures,
        "model": stats.model,
        "warnings": warnings,
        "cost_usd": round(stats.cost_usd, 6),
        "seconds": round(seconds, 2),
    }
    store.finish(job.id, "transcribed", summary=summary, cost_usd=stats.cost_usd)
    log.info(
        "job %s finished: transcribed %d lines in %.1f s, %.4f USD",
        job.id,
        len(subtitle),
        seconds,
        stats.cost_usd,
    )
    return "transcribed"


def run_job(job: Job, store: JobStore, settings: ApiSettings, services: Services) -> str:
    """Run one claimed job to its end and record the outcome, which is returned.

    Nothing is raised: whatever goes wrong closes the job as failed.
    """
    if job.request.get("kind") == "ocr":
        return run_ocr_job(job, store, settings, services)
    started = time.monotonic()
    directory = store.directory(job.id)
    request = job.request
    try:
        # Imported here, so that a server which runs no jobs itself starts without numpy.
        from retimer.pipeline import prepare, propose, with_matched_reference

        options = request["options"]
        target = load(directory / TARGET)
        audio_path = directory / REFERENCE_AUDIO
        prepared = prepare(
            target,
            audio=audio_path if audio_path.is_file() else None,
            references=_candidates(request, directory),
            options=Options(
                remove_hearing_impaired=options["remove_hearing_impaired"],
                remove_karaoke=options["remove_karaoke"],
                remove_signs_and_credits=options.get("remove_signs_and_credits", False),
                strip_formatting=options["strip_formatting"],
                remove_symbols=options["remove_symbols"],
                drop_unmatched=options["drop_unmatched"],
            ),
            audio_language=options.get("audio_language"),
            reference_language=options.get("reference_language"),
            client=services.openrouter,
            cache=services.cache,
            progress=lambda step: store.set_stage(job.id, step),
        )
        store.set_stage(job.id, "deciding")
        # With no reference to sync against there is nothing for a model to settle.
        if prepared.refused:
            result = propose(prepared).result
        else:
            from retimer.agent import AgentSettings, Workbench, run_agent

            # A job stored by a release that still had "off" runs with the server's model.
            model = request["agent"] if request["agent"] != "off" else settings.agent
            agent = AgentSettings(model=model, max_cost_usd=settings.agent_max_cost_usd)
            result = run_agent(Workbench(prepared), agent, services.model_client)
        if options.get("matched_reference"):
            try:
                result = with_matched_reference(prepared, result)
            except Exception:
                # The retimed target is what the job is for. It is delivered
                # also when the second file could not be made.
                log.exception("job %s: the matched reference failed", job.id)
                report = result.report.model_copy(deep=True)
                report.warnings = [
                    *report.warnings,
                    "No matched reference was made. It failed for a reason on the server's side.",
                ]
                result = result.model_copy(update={"report": report})

        store.set_stage(job.id, "writing")
        (directory / PLAN).write_text(result.plan.to_json() + "\n", encoding="utf-8")
        (directory / REPORT).write_text(
            result.report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        if result.subtitle is not None:
            text = dumps(result.subtitle, request["output_format"])
            (directory / RESULT).write_text(text, encoding="utf-8")
        if result.reference is not None:
            source = matched_reference_source(
                request, _reference_summary(result.report.reference_choice)
            )
            fmt = request.get("matched_reference_format") or source["format"]
            (directory / MATCHED_REFERENCE).write_text(
                dumps(result.reference, fmt), encoding="utf-8"
            )
    except Exception as error:
        code = (
            "invalid_subtitle"
            if isinstance(error, SubtitleError)
            else "invalid_audio"
            if isinstance(error, AudioError)
            else "model_unavailable"
            if isinstance(error, ModelUnavailable)
            else "provider_error"
            if isinstance(error, ProviderError)
            else "internal_error"
        )
        log.exception("job %s failed (%s)", job.id, code)
        for name in (RESULT, MATCHED_REFERENCE, PLAN, REPORT):
            (directory / name).unlink(missing_ok=True)
        # A failed job cannot say what it spent. Its audio may have been
        # transcribed, so that much is held against the day's budget: jobs
        # that fail must not be a way around it.
        audio = request.get("reference_audio") or {}
        spent = audio.get("duration_ms", 0) / 1000 * _TRANSCRIPTION_USD_PER_S
        spent += float(getattr(error, "cost_usd", 0.0) or 0.0)
        store.finish(
            job.id,
            "failed",
            error={"code": code, "message": FAILURES[code]},
            cost_usd=round(spent, 6),
        )
        return "failed"

    outcome = result.plan.outcome
    summary = _summary(result, time.monotonic() - started)
    store.finish(job.id, outcome, summary=summary, cost_usd=result.report.total_cost_usd)
    log.info(
        "job %s finished: %s in %.1f s, %.4f USD",
        job.id,
        outcome,
        summary["seconds"],
        summary["cost_usd"],
    )
    return outcome


class _Lock:
    """Held by the one worker of a data directory. The system frees it when the process ends."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None

    def acquire(self) -> None:
        try:
            import fcntl
        except ImportError:  # Not a POSIX system: no lock, and one worker by convention.
            return
        handle = self._path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise WorkerBusy(
                f"another worker already runs on {self._path.parent}. "
                "A server next to it is started with --no-worker."
            ) from error
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class Worker:
    """Runs queued jobs in the background until it is stopped."""

    def __init__(
        self,
        store: JobStore,
        settings: ApiSettings,
        services: Services,
        *,
        poll_s: float = 1.0,
    ) -> None:
        self.store = store
        self.settings = settings
        self.services = services
        self._poll_s = poll_s
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = _Lock(store.data_dir / "worker.lock")
        self._last_housekeeping = 0.0
        self._housekeeping_lock = threading.Lock()

    def run_once(self) -> bool:
        """Run the oldest queued job, if there is one. Returns whether a job was run."""
        job = self.store.claim()
        if job is None:
            return False
        log.info("job %s started (attempt %d)", job.id, job.attempts)
        run_job(job, self.store, self.settings, self.services)
        return True

    def housekeeping(self) -> None:
        """Delete what has expired: jobs, stray uploads and old cache entries."""
        deleted = self.store.sweep()
        pruned = self.services.cache.prune(self.settings.cache_days * 86400)
        if deleted or pruned:
            log.info(
                "cleanup: %d expired jobs deleted, %d cache entries forgotten", deleted, pruned
            )

    def notify(self) -> None:
        """Say that a job was queued, so that it need not wait for the next look."""
        self._wake.set()

    def start(self) -> None:
        """Take the lock, deal with jobs an earlier run left behind, and start working."""
        self._lock.acquire()
        again, closed = self.store.recover()
        if again or closed:
            log.warning("recovered jobs: %d queued again, %d closed as failed", again, closed)
        self._stop.clear()
        for index in range(self.settings.workers):
            thread = threading.Thread(
                target=self._loop, name=f"retimer-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout_s: float = 30.0) -> None:
        """Stop taking jobs and wait for the running ones, up to `timeout_s`.

        A job that is still running then is started over by the next worker.
        """
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + timeout_s
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self._threads = []
        self._lock.release()

    def run_forever(self) -> None:
        """Work in this thread until `stop` is called from another one or the process ends."""
        self._lock.acquire()
        try:
            again, closed = self.store.recover()
            if again or closed:
                log.warning("recovered jobs: %d queued again, %d closed as failed", again, closed)
            self._stop.clear()
            self._loop()
        finally:
            self._lock.release()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._housekeeping_lock:
                    due = time.monotonic() - self._last_housekeeping >= _HOUSEKEEPING_EVERY_S
                    if due or not self._last_housekeeping:
                        self._last_housekeeping = time.monotonic()
                        self.housekeeping()
                if self.run_once():
                    continue
            except Exception:
                log.exception("the worker hit an error and goes on")
            self._wake.wait(self._poll_s)
            self._wake.clear()
