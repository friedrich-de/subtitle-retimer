"""The systems under test. Each runner syncs one case and says what it did.

External tools are run as commands, exactly as a user would run them. They take
one reference, so a case with both audio and a reference subtitle gives them the
audio, and a case with several reference subtitles gives them the one a client
would send that sends a single track. The project's own pipeline is called
directly and gets every track.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import BaseModel, Field

from retimer.audio import AudioError
from retimer.bench.cases import LoadedCase
from retimer.bench.metrics import LEFT_ALONE, NO_TIMING, REFUSED
from retimer.cache import Cache
from retimer.engine import PlanError, apply_plan
from retimer.io import dumps, load, save
from retimer.plan import Plan
from retimer.providers import OPENROUTER_KEY_ENV, OpenRouter, ProviderError

if TYPE_CHECKING:
    from retimer.pipeline import SyncResult

# The names are repeated here so that listing the systems does not load the
# agent, and with it the provider's library.
AGENT_PROVIDER_ENV = "RETIMER_AGENT_PROVIDER"
ANTHROPIC_KEY_ENV = "ANTHROPIC_RETIMER_API_KEY"

# Where a running service is, and the key to it, for the system that goes through HTTP.
API_URL_ENV = "RETIMER_API_URL"
API_KEY_ENV = "RETIMER_API_KEY"

DEFAULT_TIMEOUT_S = 600
_ERROR_CHARS = 600


class RunOutput(BaseModel):
    """What a system produced for one case.

    `outcome` is None for tools that do not decide one. `kept_ids` names the
    target line behind every output line, for systems that know it.
    """

    outcome: str | None = None
    output_path: Path | None = None
    kept_ids: list[int] | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None
    details: dict = Field(default_factory=dict)


class Runner(Protocol):
    name: str

    def unavailable(self) -> str | None:
        """Why this system cannot run here, or None when it can."""

    def supports(self, case: LoadedCase) -> bool: ...

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput: ...


class InputRunner:
    """Returns the target untouched. Shows how far off the cases are to begin with."""

    name = "input"

    def unavailable(self) -> str | None:
        return None

    def supports(self, case: LoadedCase) -> bool:
        return True

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        output = work_dir / case.case.target
        shutil.copyfile(case.target_path, output)
        return RunOutput(output_path=output)


class OracleRunner:
    """Applies the ideal plan stored with a generated case. Checks the harness itself."""

    name = "oracle"

    def unavailable(self) -> str | None:
        return None

    def supports(self, case: LoadedCase) -> bool:
        return case.case.ideal_plan is not None or case.case.expected_outcome in NO_TIMING

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        if case.case.expected_outcome == REFUSED:
            return RunOutput(outcome=REFUSED)
        if case.case.expected_outcome == LEFT_ALONE:
            output = work_dir / case.case.target
            shutil.copyfile(case.target_path, output)
            return RunOutput(outcome="partial", output_path=output)
        plan = case.case.ideal_plan
        try:
            result = apply_plan(load(case.target_path), plan)
        except PlanError as error:
            return RunOutput(outcome="failed", error=str(error))
        output = work_dir / case.case.target
        save(result.subtitle, output)
        return RunOutput(
            outcome=plan.outcome,
            output_path=output,
            kept_ids=result.subtitle.ids,
            details={"plan": plan.model_dump(mode="json", by_alias=True, exclude_none=True)},
        )


class CommandRunner:
    """Base for tools that are run as a command line program."""

    name: str
    executable: str
    env_var: str

    def __init__(
        self,
        name: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        if name:
            self.name = name
        self.extra_args = tuple(extra_args)
        self.timeout_s = timeout_s

    def _path(self) -> str | None:
        return os.environ.get(self.env_var) or shutil.which(self.executable)

    def unavailable(self) -> str | None:
        if self._path() is None:
            return f"{self.executable} is not installed (or set {self.env_var})"
        return None

    def version(self) -> str | None:
        path = self._path()
        if path is None:
            return None
        try:
            done = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=60, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        lines = (done.stdout or done.stderr).strip().splitlines()
        return lines[-1] if lines else None

    def supports(self, case: LoadedCase) -> bool:
        return True

    def reference_path(self, case: LoadedCase) -> Path:
        path = case.audio_path or case.reference_subtitle_path
        if path is None:
            raise ValueError(f"case {case.case.id} has no reference")
        return path

    def command(self, reference: Path, target: Path, output: Path) -> list[str]:
        raise NotImplementedError

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        # The tool runs inside the work directory, so every path has to be absolute.
        output = (work_dir / case.case.target).resolve()
        command = self.command(self.reference_path(case), case.target_path.resolve(), output)
        started = time.monotonic()
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.timeout_s,
                cwd=work_dir,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RunOutput(
                outcome="failed",
                seconds=time.monotonic() - started,
                error=f"timed out after {self.timeout_s} s",
            )
        seconds = time.monotonic() - started
        log = (done.stderr or done.stdout or "").strip()
        if done.returncode != 0 or not output.exists():
            return RunOutput(
                outcome="failed",
                seconds=seconds,
                error=f"exit code {done.returncode}: {log[-_ERROR_CHARS:]}",
            )
        return RunOutput(output_path=output, seconds=seconds, details={"command": command[1:]})


class FfsubsyncRunner(CommandRunner):
    """ffsubsync: correlates speech activity with line timing. Global offset and rate.

    With `--split-penalty` it also looks for breaks, as alass does.
    """

    name = "ffsubsync"
    executable = "ffsubsync"
    env_var = "RETIMER_FFSUBSYNC"

    def command(self, reference: Path, target: Path, output: Path) -> list[str]:
        return [
            self._path(),
            str(reference),
            "-i",
            str(target),
            "-o",
            str(output),
            *self.extra_args,
        ]


class AlassRunner(CommandRunner):
    """alass: matches timing spans and detects splits, so it handles block shifts."""

    name = "alass"
    executable = "alass-cli"
    env_var = "RETIMER_ALASS"

    def command(self, reference: Path, target: Path, output: Path) -> list[str]:
        return [self._path(), *self.extra_args, str(reference), str(target), str(output)]


def _reference_of(case: LoadedCase) -> dict[str, object]:
    """The reference subtitle of a case as the pipeline takes it: one, or several to choose from."""
    # Imported here for the same reason as the pipeline itself.
    from retimer.choice import Candidate, TrackLabel

    tracks = case.reference_tracks
    if not tracks:
        reference = case.reference_subtitle_path
        return {
            "reference": load(reference) if reference else None,
            "reference_language": case.case.reference.subtitle_language,
        }
    # Each track comes with what the file says of it and nothing else, so
    # none gets the language of the case as a hint.
    return {
        "references": [
            Candidate(
                subtitle=load(path),
                label=TrackLabel(
                    language=track.language,
                    title=track.title,
                    default=track.default,
                    forced=track.forced,
                ),
                filename=path.name,
            )
            for path, track in tracks
        ]
    }


class PipelineRunner:
    """This project's fixed pipeline: anchors, fits and scoring, with no agent deciding."""

    name = "pipeline"
    # Whether the speech model is told the language of the audio. The
    # benchmark leaves it to find out, as it must for a client that says
    # nothing of the file.
    names_audio_language = False

    def __init__(
        self,
        name: str | None = None,
        client: OpenRouter | None = None,
        cache: Cache | None = None,
        **settings: object,
    ) -> None:
        if name:
            self.name = name
        self.client = client
        self.cache = cache
        self.settings = settings

    def unavailable(self) -> str | None:
        if self.client is None and not os.environ.get(OPENROUTER_KEY_ENV):
            return f"{OPENROUTER_KEY_ENV} is not set"
        return None

    def supports(self, case: LoadedCase) -> bool:
        return True

    def _audio_language(self, case: LoadedCase) -> str | None:
        return case.case.reference.audio_language if self.names_audio_language else None

    def _sync(self, case: LoadedCase) -> SyncResult:
        # Imported here: the other systems do not need numpy and start faster without it.
        from retimer.pipeline import Settings, sync

        return sync(
            load(case.target_path),
            audio=case.audio_path,
            options=case.case.options,
            audio_language=self._audio_language(case),
            settings=Settings(**self.settings),
            client=self.client,
            cache=self.cache,
            **_reference_of(case),
        )

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        started = time.monotonic()
        try:
            result = self._sync(case)
        except (ProviderError, AudioError) as error:
            return RunOutput(
                outcome="failed",
                seconds=time.monotonic() - started,
                error=str(error)[-_ERROR_CHARS:],
            )
        seconds = time.monotonic() - started
        report = result.report
        details = {
            "plan": result.plan.model_dump(mode="json", by_alias=True, exclude_none=True),
            "report": report.model_dump(mode="json", exclude={"fits", "coverage"}),
        }
        if result.subtitle is None:
            return RunOutput(
                outcome=result.plan.outcome,
                seconds=seconds,
                cost_usd=report.total_cost_usd,
                details=details,
            )
        output = work_dir / case.case.target
        save(result.subtitle, output)
        return RunOutput(
            outcome=result.plan.outcome,
            output_path=output,
            kept_ids=result.subtitle.ids,
            seconds=seconds,
            cost_usd=report.total_cost_usd,
            details=details,
        )


class AgentRunner(PipelineRunner):
    """The pipeline with a model settling what its draft leaves open.

    The routine steps are the same as the fixed pipeline's. The model is asked
    only about the cases where they leave a question, so the difference to
    `pipeline` is what the model adds, and what it costs.
    """

    name = "agent"

    def __init__(
        self,
        name: str | None = None,
        model: str = "haiku",
        effort: str = "medium",
        consult: str = "auto",
        client: OpenRouter | None = None,
        cache: Cache | None = None,
        model_client: object | None = None,
        **settings: object,
    ) -> None:
        super().__init__(name, client, cache, **settings)
        self.model = model
        self.effort = effort
        self.consult = consult
        self.model_client = model_client

    def unavailable(self) -> str | None:
        reason = super().unavailable()
        if reason:
            return reason
        direct = (os.environ.get(AGENT_PROVIDER_ENV) or "").strip().lower() == "anthropic"
        if self.model_client is None and direct and not os.environ.get(ANTHROPIC_KEY_ENV):
            return f"{ANTHROPIC_KEY_ENV} is not set"
        return None

    def version(self) -> str:
        from retimer.agent import resolve_model

        return f"{resolve_model(self.model)}, effort {self.effort}, consult {self.consult}"

    def _sync(self, case: LoadedCase) -> SyncResult:
        from retimer.agent import AgentSettings, sync_with_agent
        from retimer.pipeline import Settings

        return sync_with_agent(
            load(case.target_path),
            audio=case.audio_path,
            options=case.case.options,
            audio_language=self._audio_language(case),
            settings=Settings(**self.settings),
            agent=AgentSettings(model=self.model, effort=self.effort, consult=self.consult),
            client=self.client,
            cache=self.cache,
            model_client=self.model_client,
            **_reference_of(case),
        )


class QuestionsRunner(PipelineRunner):
    """The fixed pipeline, with what the agent's model would be asked written into the report.

    No model is called, so a run costs nothing. It shows on which files a
    change to the questions takes effect, before a run with the model is
    paid for.
    """

    name = "questions"

    def _sync(self, case: LoadedCase) -> SyncResult:
        from retimer.agent import Workbench
        from retimer.agent.loop import AgentTrace
        from retimer.pipeline import Settings, SyncResult, prepare, propose

        prepared = prepare(
            load(case.target_path),
            audio=case.audio_path,
            options=case.case.options,
            audio_language=self._audio_language(case),
            settings=Settings(**self.settings),
            client=self.client,
            cache=self.cache,
            **_reference_of(case),
        )
        if prepared.refused:
            return propose(prepared).result
        bench = Workbench(prepared)
        trace = AgentTrace(model="none", effort="none")
        trace.questions = bench.open_questions()
        trace.part_asked = bench.part_asked()
        result = bench.baseline.result
        report = result.report.model_copy(deep=True)
        report.agent = trace.model_dump(mode="json")
        return SyncResult(plan=result.plan, subtitle=result.subtitle, report=report)


class ApiRunner:
    """A running service, reached over HTTP as a client reaches it.

    Each case is submitted as a job, waited for, fetched and deleted again. The
    result is measured like every other system's, so this shows what a client
    gets, with everything between the request and the file included. The
    service decides which model its agent runs on.
    """

    name = "api"

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        *,
        agent: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_s: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._key = key
        self.agent = agent
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._client = client

    @property
    def url(self) -> str | None:
        return self._url or os.environ.get(API_URL_ENV)

    @property
    def key(self) -> str | None:
        return self._key or os.environ.get(API_KEY_ENV)

    def unavailable(self) -> str | None:
        if self._client is not None:
            return None
        missing = [
            name for name, value in ((API_URL_ENV, self.url), (API_KEY_ENV, self.key)) if not value
        ]
        return f"{' and '.join(missing)} not set" if missing else None

    def supports(self, case: LoadedCase) -> bool:
        return True

    def _connect(self) -> httpx.Client:
        return httpx.Client(
            base_url=(self.url or "").rstrip("/"),
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=120.0,
        )

    def _send(self, client: httpx.Client, method: str, path: str, deadline: float, **kwargs):
        """One request. When the service says to wait, it is waited and sent again."""
        while True:
            response = client.request(method, path, **kwargs)
            wait_s = float(response.headers.get("retry-after", 1))
            if response.status_code != 429 or time.monotonic() + wait_s > deadline:
                return response
            time.sleep(wait_s)

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        started = time.monotonic()
        deadline = started + self.timeout_s
        spec = case.case
        options: dict[str, object] = spec.options.model_dump()
        if self.agent:
            options["agent"] = self.agent
        files = [("target", (spec.target, case.target_path.read_bytes()))]
        if case.audio_path is not None:
            files.append(("reference_audio", (case.audio_path.name, case.audio_path.read_bytes())))
        if case.reference_tracks:
            # One part for every track, in their order, with what the file says of each.
            options["reference_tracks"] = [
                track.model_dump(include={"language", "title", "default", "forced"})
                for _, track in case.reference_tracks
            ]
            files += [
                ("reference_subtitle", (path.name, path.read_bytes()))
                for path, _ in case.reference_tracks
            ]
        elif case.reference_subtitle_path is not None:
            reference = case.reference_subtitle_path
            files.append(("reference_subtitle", (reference.name, reference.read_bytes())))
            if spec.reference.subtitle_language:
                options["reference_language"] = spec.reference.subtitle_language

        def failed(error: str, **details: object) -> RunOutput:
            return RunOutput(
                outcome="failed",
                seconds=time.monotonic() - started,
                error=error[-_ERROR_CHARS:],
                details=details,
            )

        client = self._client or self._connect()
        link = None
        try:
            response = self._send(
                client,
                "POST",
                "/v1/jobs",
                deadline,
                files=files,
                data={"options": json.dumps(options)},
            )
            if response.status_code != 202:
                return failed(f"the job was refused: {response.status_code} {response.text}")
            job = response.json()
            link = job["links"]["self"]
            pause_s = self.poll_s
            while job["status"] != "finished":
                if time.monotonic() > deadline:
                    return failed(f"the job was still {job['status']} after {self.timeout_s:.0f} s")
                time.sleep(pause_s)
                # A job that takes long is asked about less often.
                pause_s = min(pause_s * 1.5, 3.0)
                response = self._send(client, "GET", link, deadline)
                if response.status_code != 200:
                    return failed(f"the job could not be read: {response.status_code}")
                job = response.json()
            seconds = time.monotonic() - started
            summary = job.get("summary") or {}
            details: dict[str, object] = {
                "job": {key: job[key] for key in ("id", "outcome", "summary", "error")},
            }
            if job["outcome"] == "failed":
                return failed(json.dumps(job.get("error")), **details)
            plan = Plan.model_validate(
                self._send(client, "GET", job["links"]["plan"], deadline).json()
            )
            details["plan"] = plan.model_dump(mode="json", by_alias=True, exclude_none=True)
            produced = RunOutput(
                outcome=job["outcome"],
                seconds=seconds,
                cost_usd=float(summary.get("cost_usd") or 0.0),
                details=details,
            )
            if "result" not in job["links"]:
                return produced
            delivered = self._send(client, "GET", job["links"]["result"], deadline)
            if delivered.status_code != 200:
                return failed(
                    f"the result could not be fetched: {delivered.status_code}", **details
                )
            output = work_dir / spec.target
            output.write_bytes(delivered.content)
            produced.output_path = output
            # The plan is the record of what was done: run here on the same
            # target, it has to give the file the service delivered. It also
            # names the target line behind every line of that file.
            try:
                replayed = apply_plan(load(case.target_path), plan).subtitle
            except PlanError as error:
                return failed(f"the plan of the job does not run: {error}", **details)
            same = dumps(replayed) == delivered.content.decode("utf-8")
            produced.details["file_matches_plan"] = same
            produced.kept_ids = replayed.ids if same else None
            return produced
        except httpx.HTTPError as error:
            return failed(f"{type(error).__name__}: {error}")
        finally:
            if link is not None:
                with contextlib.suppress(httpx.HTTPError):
                    client.delete(link)
            if self._client is None:
                client.close()


def builtin_runners() -> dict[str, Runner]:
    runners: list[Runner] = [
        InputRunner(),
        OracleRunner(),
        FfsubsyncRunner(),
        FfsubsyncRunner("ffsubsync-split", ("--split-penalty",)),
        AlassRunner(),
        PipelineRunner(),
        AgentRunner("agent-opus", model="opus"),
        AgentRunner("agent-sonnet", model="sonnet"),
        AgentRunner("agent-haiku", model="haiku"),
        ApiRunner(),
    ]
    return {runner.name: runner for runner in runners}


DEFAULT_SYSTEMS = ("input", "ffsubsync", "ffsubsync-split", "alass", "pipeline")
